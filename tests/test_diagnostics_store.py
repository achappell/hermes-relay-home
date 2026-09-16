import hashlib
from dataclasses import replace

import pytest

from hermes_home.observability.diagnostics import (
    CaptureAuditRecord,
    CaptureScope,
    CaptureStateError,
    DiagnosticEvent,
    DiagnosticsRecorder,
    DiagnosticStoreError,
    IncidentCaptureService,
    LocalRingBuffer,
    UploadAcknowledgement,
)
from hermes_home.storage.diagnostics import (
    SQLiteDiagnosticsStore,
    SQLiteIncidentCaptureStore,
)


def _opaque(prefix: str, value: str) -> str:
    return f"{prefix}-" + hashlib.sha256(value.encode()).hexdigest()[:32]


def _event(
    event_id: str,
    correlation_id: str,
    occurred_at: float,
    *,
    retention_deadline: float | None = None,
) -> DiagnosticEvent:
    event = DiagnosticEvent.create(
        correlation_id=_opaque("corr", correlation_id),
        source="home",
        phase="turn",
        outcome="completed",
        occurred_at=occurred_at,
        retention_deadline=retention_deadline,
    )
    return replace(event, event_id=_opaque("evt", event_id))


def test_sqlite_diagnostics_store_survives_reopen_and_tracks_upload_state(
    tmp_path,
) -> None:
    database = tmp_path / "home.sqlite3"
    store = SQLiteDiagnosticsStore(database, clock=lambda: 100.0)
    store.append(_event("event-01", "corr-01", 100.0), max_events=10)
    store.close()

    reopened = SQLiteDiagnosticsStore(database, clock=lambda: 100.0)
    try:
        assert reopened.events(correlation_id=_opaque("corr", "corr-01"), before=90.0)[
            0
        ].event_id == (_opaque("evt", "event-01"))
        assert reopened.status().queued_event_count == 1

        reopened.mark_uploaded([_opaque("evt", "event-01")], uploaded_at=101.0)

        assert reopened.status().queued_event_count == 0
        assert reopened.status().last_successful_upload_at == 101.0
        assert reopened.status().collector_reachable is True
    finally:
        reopened.close()


def test_sqlite_diagnostics_store_purges_expired_events_and_bounds_records(
    tmp_path,
) -> None:
    store = SQLiteDiagnosticsStore(tmp_path / "home.sqlite3", clock=lambda: 100.0)
    try:
        store.append(
            _event("event-old", "corr-old", 89.0, retention_deadline=89.0),
            max_events=2,
        )
        store.append(_event("event-new", "corr-new", 100.0), max_events=2)

        assert store.purge_expired(before=90.0) == 0
        assert store.append(_event("event-latest", "corr-latest", 100.0), max_events=1)
        assert store.status().dropped_event_count == 1
        assert (
            store.events(correlation_id=_opaque("corr", "corr-old"), before=90.0) == ()
        )
        assert (
            len(
                store.events(correlation_id=_opaque("corr", "corr-latest"), before=90.0)
            )
            == 1
        )
    finally:
        store.close()


def test_sqlite_diagnostics_store_rejects_duplicate_event_ids(tmp_path) -> None:
    store = SQLiteDiagnosticsStore(tmp_path / "home.sqlite3", clock=lambda: 100.0)
    try:
        event = _event("event-01", "corr-01", 100.0)
        store.append(event, max_events=10)

        with pytest.raises(DiagnosticStoreError, match="duplicate"):
            store.append(event, max_events=10)
    finally:
        store.close()


def test_sqlite_store_purges_expired_rows_before_enforcing_capacity(tmp_path) -> None:
    now = [89.0]
    store = SQLiteDiagnosticsStore(tmp_path / "home.sqlite3", clock=lambda: now[0])
    try:
        store.append(
            _event("event-old", "corr-old", 89.0, retention_deadline=95.0),
            max_events=1,
        )
        now[0] = 100.0

        assert (
            store.append(_event("event-new", "corr-new", 100.0), max_events=1) is False
        )
        assert store.status().queued_event_count == 1
        assert store.status().dropped_event_count == 0
    finally:
        store.close()


def test_sqlite_incident_capture_store_persists_bounded_metadata_and_audit(
    tmp_path,
) -> None:
    database = tmp_path / "home.sqlite3"
    scope = CaptureScope("fp-" + "a" * 24, "fp-" + "b" * 24)

    class Allow:
        def authorize(self, scope) -> bool:
            del scope
            return True

    class Current:
        def current_scope(self):
            return scope

    class Sealer:
        def seal(self, evidence, *, capture_id, scope):
            del evidence, capture_id, scope
            return b"sealed"

    class Uploader:
        def upload(self, bundle) -> None:
            del bundle

    store = SQLiteIncidentCaptureStore(database, max_captures=2, max_audit_records=4)
    captures = IncidentCaptureService(
        ring_buffer=LocalRingBuffer(),
        sealer=Sealer(),
        uploader=Uploader(),
        capture_store=store,
        authorizer=Allow(),
        current_task_resolver=Current(),
        clock=lambda: 100.0,
    )
    capture = captures.arm(scope)
    captures.close()
    store.close()

    reopened_store = SQLiteIncidentCaptureStore(database)
    reopened = IncidentCaptureService(
        ring_buffer=LocalRingBuffer(),
        sealer=None,
        uploader=None,
        capture_store=reopened_store,
        authorizer=Allow(),
        current_task_resolver=Current(),
        clock=lambda: 100.0,
    )
    try:
        assert reopened.get(capture.capture_id).state == "armed"
        assert reopened.audit_log()[0].action == "arm"
    finally:
        reopened.close()
        reopened_store.close()


def test_sqlite_incident_capture_store_bounds_audit_records(tmp_path) -> None:
    database = tmp_path / "home.sqlite3"
    store = SQLiteIncidentCaptureStore(database, max_audit_records=2)
    try:
        from hermes_home.observability.diagnostics import CaptureAuditRecord

        for index in range(3):
            store.append_audit(
                CaptureAuditRecord(
                    capture_id=_opaque("capture", str(index)),
                    action="arm",
                    occurred_at=float(index),
                    outcome="accepted",
                )
            )

        assert [record.capture_id for record in store.audit_log()] == [
            _opaque("capture", "1"),
            _opaque("capture", "2"),
        ]
    finally:
        store.close()


def test_sqlite_recorder_uploads_and_finalizes_multiple_persisted_events(
    tmp_path,
) -> None:
    class Collector:
        def __init__(self) -> None:
            self.events = ()

        def upload(self, events, *, idempotency_key: str) -> UploadAcknowledgement:
            self.events = tuple(events)
            return UploadAcknowledgement(
                idempotency_key=idempotency_key,
                event_ids=tuple(event.event_id for event in events),
            )

    store = SQLiteDiagnosticsStore(tmp_path / "home.sqlite3", clock=lambda: 100.0)
    collector = Collector()
    try:
        recorder = DiagnosticsRecorder(
            store=store,
            collector=collector,
            clock=lambda: 100.0,
            max_events=4,
        )
        assert recorder.record(_event("event-01", "corr-upload", 100.0))
        assert recorder.record(_event("event-02", "corr-upload", 100.0))

        result = recorder.flush()

        assert result.uploaded_count == 2
        assert [event.event_id for event in collector.events] == [
            _opaque("evt", "event-01"),
            _opaque("evt", "event-02"),
        ]
        assert store.status().queued_event_count == 0
    finally:
        store.close()


def test_sqlite_capture_lifecycle_survives_restart(tmp_path) -> None:
    database = tmp_path / "home.sqlite3"
    scope = CaptureScope("fp-" + "a" * 24, "fp-" + "b" * 24)

    class Allow:
        def authorize(self, scope) -> bool:
            del scope
            return True

    class Current:
        def current_scope(self):
            return scope

    class Sealer:
        def seal(self, evidence, *, capture_id, scope):
            del evidence, capture_id, scope
            return b"sealed"

    class Uploader:
        def upload(self, bundle) -> None:
            del bundle

    store = SQLiteIncidentCaptureStore(database)
    ring = LocalRingBuffer(clock=lambda: 100.0)
    ring.append(scope, b"private", captured_at=100.0)
    captures = IncidentCaptureService(
        ring_buffer=ring,
        sealer=Sealer(),
        uploader=Uploader(),
        capture_store=store,
        authorizer=Allow(),
        current_task_resolver=Current(),
        clock=lambda: 100.0,
    )
    capture = captures.arm(scope)
    preview = captures.preview(capture.capture_id)
    uploaded = captures.approve(
        capture.capture_id,
        evidence_ids=[preview.evidence[0].evidence_id],
    )
    captures.close()
    store.close()

    reopened_store = SQLiteIncidentCaptureStore(database)
    reopened = IncidentCaptureService(
        ring_buffer=LocalRingBuffer(clock=lambda: 100.0),
        sealer=None,
        uploader=None,
        capture_store=reopened_store,
        authorizer=Allow(),
        current_task_resolver=Current(),
        clock=lambda: 100.0,
    )
    try:
        assert reopened.get(capture.capture_id).state == "uploaded"
        assert (
            reopened.get(capture.capture_id).retention_deadline
            == uploaded.retention_deadline
        )
        assert [record.action for record in reopened.audit_log()] == [
            "arm",
            "preview",
            "approve",
            "approve",
            "approve",
            "approve",
        ]
    finally:
        reopened.close()
        reopened_store.close()


def test_sqlite_capture_bound_refuses_to_evict_active_state(tmp_path) -> None:
    database = tmp_path / "home.sqlite3"
    scope = CaptureScope("fp-" + "a" * 24, "fp-" + "b" * 24)

    class Allow:
        def authorize(self, scope) -> bool:
            del scope
            return True

    class Current:
        def current_scope(self):
            return scope

    store = SQLiteIncidentCaptureStore(database, max_captures=1)
    captures = IncidentCaptureService(
        ring_buffer=LocalRingBuffer(clock=lambda: 100.0),
        sealer=None,
        uploader=None,
        capture_store=store,
        authorizer=Allow(),
        current_task_resolver=Current(),
        clock=lambda: 100.0,
        max_capture_records=1,
    )
    try:
        first = captures.arm(scope)
        with pytest.raises(CaptureStateError, match="bound is full"):
            captures.arm(scope)
        captures.cancel(first.capture_id)
        second = captures.arm(scope)
        assert [capture.capture_id for capture in store.captures()] == [
            second.capture_id
        ]
    finally:
        captures.close()
        store.close()


def test_sqlite_capture_transition_rolls_back_state_when_audit_fails(tmp_path) -> None:
    class FailingAuditStore(SQLiteIncidentCaptureStore):
        def _insert_audit_row(self, record: CaptureAuditRecord) -> None:
            del record
            raise RuntimeError("audit unavailable")

    database = tmp_path / "home.sqlite3"
    scope = CaptureScope("fp-" + "a" * 24, "fp-" + "b" * 24)

    class Allow:
        def authorize(self, scope) -> bool:
            del scope
            return True

    class Current:
        def current_scope(self):
            return scope

    store = FailingAuditStore(database)
    captures = IncidentCaptureService(
        ring_buffer=LocalRingBuffer(clock=lambda: 100.0),
        sealer=None,
        uploader=None,
        capture_store=store,
        authorizer=Allow(),
        current_task_resolver=Current(),
        clock=lambda: 100.0,
    )
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            captures.arm(scope)
        assert store.captures() == ()
        assert captures.audit_log() == ()
    finally:
        captures.close()
        store.close()


def test_sqlite_upload_retry_keeps_idempotency_key_across_restart(tmp_path) -> None:
    class FlakyStore(SQLiteDiagnosticsStore):
        def __init__(self, database, **kwargs) -> None:
            super().__init__(database, **kwargs)
            self.fail_once = True

        def mark_uploaded(self, event_ids, *, uploaded_at: float) -> None:
            if self.fail_once:
                self.fail_once = False
                raise DiagnosticStoreError("finalization unavailable")
            super().mark_uploaded(event_ids, uploaded_at=uploaded_at)

    class Collector:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def upload(self, events, *, idempotency_key: str) -> UploadAcknowledgement:
            self.keys.append(idempotency_key)
            return UploadAcknowledgement(
                idempotency_key=idempotency_key,
                event_ids=tuple(event.event_id for event in events),
            )

    database = tmp_path / "home.sqlite3"
    collector = Collector()
    store = FlakyStore(database, clock=lambda: 100.0)
    recorder = DiagnosticsRecorder(
        store=store,
        collector=collector,
        clock=lambda: 100.0,
    )
    assert recorder.record(_event("event-retry", "corr-retry", 100.0))
    first = recorder.flush()
    store.close()

    reopened_store = SQLiteDiagnosticsStore(database, clock=lambda: 100.0)
    reopened = DiagnosticsRecorder(
        store=reopened_store,
        collector=collector,
        clock=lambda: 100.0,
    )
    try:
        second = reopened.flush()
        assert first.failure_code == "storage_unavailable"
        assert second.uploaded_count == 1
        assert collector.keys[0] == collector.keys[1]
    finally:
        reopened_store.close()


def test_sqlite_capture_store_rejects_malformed_rehydrated_metadata(tmp_path) -> None:
    store = SQLiteIncidentCaptureStore(tmp_path / "home.sqlite3")
    try:
        store._connection.execute(
            """
            INSERT INTO diagnostic_captures (
                capture_id, endpoint_fingerprint, task_fingerprint, state,
                created_at, expires_at, retention_deadline, failure_code,
                entry_count, total_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque("capture", "malformed"),
                "fp-" + "a" * 24,
                "fp-" + "b" * 24,
                "not-a-state",
                100.0,
                200.0,
                None,
                None,
                0,
                0,
            ),
        )
        store._connection.commit()

        with pytest.raises(DiagnosticStoreError, match="metadata is invalid"):
            store.captures()
    finally:
        store.close()
