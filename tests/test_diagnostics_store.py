import pytest

from hermes_home.observability.diagnostics import (
    CaptureScope,
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


def _event(
    event_id: str,
    correlation_id: str,
    occurred_at: float,
    *,
    retention_deadline: float | None = None,
) -> DiagnosticEvent:
    return DiagnosticEvent.create(
        event_id=event_id,
        correlation_id=correlation_id,
        source="home",
        phase="turn",
        outcome="completed",
        occurred_at=occurred_at,
        retention_deadline=retention_deadline,
    )


def test_sqlite_diagnostics_store_survives_reopen_and_tracks_upload_state(
    tmp_path,
) -> None:
    database = tmp_path / "home.sqlite3"
    store = SQLiteDiagnosticsStore(database)
    store.append(_event("event-01", "corr-01", 100.0), max_events=10)
    store.close()

    reopened = SQLiteDiagnosticsStore(database)
    try:
        assert reopened.events(correlation_id="corr-01", before=90.0)[0].event_id == (
            "event-01"
        )
        assert reopened.status().queued_event_count == 1

        reopened.mark_uploaded(["event-01"], uploaded_at=101.0)

        assert reopened.status().queued_event_count == 0
        assert reopened.status().last_successful_upload_at == 101.0
        assert reopened.status().collector_reachable is True
    finally:
        reopened.close()


def test_sqlite_diagnostics_store_purges_expired_events_and_bounds_records(
    tmp_path,
) -> None:
    store = SQLiteDiagnosticsStore(tmp_path / "home.sqlite3")
    try:
        store.append(
            _event("event-old", "corr-old", 89.0, retention_deadline=89.0),
            max_events=2,
        )
        store.append(_event("event-new", "corr-new", 100.0), max_events=2)

        assert store.purge_expired(before=90.0) == 1
        assert store.append(_event("event-latest", "corr-latest", 100.0), max_events=1)
        assert store.status().dropped_event_count == 1
        assert store.events(correlation_id="corr-old", before=90.0) == ()
        assert len(store.events(correlation_id="corr-latest", before=90.0)) == 1
    finally:
        store.close()


def test_sqlite_diagnostics_store_rejects_duplicate_event_ids(tmp_path) -> None:
    store = SQLiteDiagnosticsStore(tmp_path / "home.sqlite3")
    try:
        event = _event("event-01", "corr-01", 100.0)
        store.append(event, max_events=10)

        with pytest.raises(DiagnosticStoreError, match="duplicate"):
            store.append(event, max_events=10)
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
                    capture_id=f"capture-{index}",
                    action="arm",
                    occurred_at=float(index),
                    outcome="accepted",
                )
            )

        assert [record.capture_id for record in store.audit_log()] == [
            "capture-1",
            "capture-2",
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

    store = SQLiteDiagnosticsStore(tmp_path / "home.sqlite3")
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
            "event-01",
            "event-02",
        ]
        assert store.status().queued_event_count == 0
    finally:
        store.close()
