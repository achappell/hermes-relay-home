from threading import Event, Thread

import pytest

from hermes_home.observability.diagnostics import (
    CaptureScope,
    CaptureStateError,
    DiagnosticEvent,
    DiagnosticsRecorder,
    DiagnosticStoreError,
    DiagnosticValidationError,
    IncidentCaptureService,
    InMemoryDiagnosticsStore,
    LocalRingBuffer,
    MetricsRegistry,
    UploadAcknowledgement,
)

ENDPOINT_FINGERPRINT = "fp-" + "a" * 24
TASK_FINGERPRINT = "fp-" + "b" * 24
OTHER_ENDPOINT_FINGERPRINT = "fp-" + "c" * 24


class AllowCapture:
    def authorize(self, scope: CaptureScope) -> bool:
        return True


class FixedCurrentScope:
    def __init__(self, scope: CaptureScope) -> None:
        self._scope = scope

    def current_scope(self) -> CaptureScope:
        return self._scope


def _capture_auth(scope: CaptureScope) -> dict[str, object]:
    return {
        "authorizer": AllowCapture(),
        "current_task_resolver": FixedCurrentScope(scope),
    }


def test_safe_event_serializes_only_allowed_lifecycle_fields() -> None:
    event = DiagnosticEvent.create(
        correlation_id="corr-01",
        source="endpoint",
        phase="turn",
        outcome="started",
        occurred_at=100.0,
        endpoint_fingerprint=ENDPOINT_FINGERPRINT,
        route_class="home",
        route_id="local",
        service_version="0.2.0",
    )

    assert event.to_dict() == {
        "schema": 1,
        "event_id": event.event_id,
        "correlation_id": "corr-01",
        "source": "endpoint",
        "phase": "turn",
        "outcome": "started",
        "occurred_at": 100.0,
        "endpoint_fingerprint": ENDPOINT_FINGERPRINT,
        "route_class": "home",
        "route_id": "local",
        "service_version": "0.2.0",
        "retention_class": "events",
    }
    assert "prompt" not in event.to_dict()
    assert "credential" not in event.to_dict()


def test_safe_event_rejects_content_bearing_or_unknown_fields() -> None:
    with pytest.raises(DiagnosticValidationError, match="prompt"):
        DiagnosticEvent.from_mapping(
            {
                "schema": 1,
                "event_id": "event-01",
                "correlation_id": "corr-01",
                "source": "home",
                "phase": "turn",
                "outcome": "failed",
                "occurred_at": 100.0,
                "prompt": "private text",
            }
        )


def test_recorder_counts_rejected_events_without_raising_to_live_work() -> None:
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(),
        clock=lambda: 100.0,
    )

    accepted = recorder.record(
        {
            "schema": 1,
            "event_id": "event-01",
            "correlation_id": "corr-01",
            "source": "home",
            "phase": "turn",
            "outcome": "completed",
            "occurred_at": 100.0,
        }
    )
    rejected = recorder.record(
        {
            "schema": 1,
            "event_id": "event-02",
            "correlation_id": "corr-01",
            "source": "home",
            "phase": "turn",
            "outcome": "completed",
            "occurred_at": 100.0,
            "transcript": "private text",
        }
    )

    assert accepted is True
    assert rejected is False
    assert recorder.status().rejected_event_count == 1
    assert len(recorder.timeline("corr-01")) == 1


def test_recorder_survives_a_rejection_counter_store_failure() -> None:
    class BrokenRejectionStore(InMemoryDiagnosticsStore):
        def record_rejection(self) -> None:
            raise DiagnosticStoreError("rejection state unavailable")

    recorder = DiagnosticsRecorder(
        store=BrokenRejectionStore(),
        clock=lambda: 100.0,
    )

    assert (
        recorder.record(
            {
                "schema": 1,
                "event_id": "event-01",
                "correlation_id": "corr-01",
                "source": "home",
                "phase": "turn",
                "outcome": "completed",
                "occurred_at": 100.0,
                "prompt": "private text",
            }
        )
        is False
    )


def test_recorder_survives_a_pending_store_failure_during_flush() -> None:
    class BrokenPendingStore(InMemoryDiagnosticsStore):
        def pending_events(self, *, limit: int, before: float):
            del limit, before
            raise DiagnosticStoreError("pending state unavailable")

    recorder = DiagnosticsRecorder(
        store=BrokenPendingStore(),
        clock=lambda: 100.0,
    )

    result = recorder.flush()

    assert result.uploaded_count == 0
    assert result.collector_reachable is False


def test_recorder_purges_expired_events_and_exposes_bounded_loss() -> None:
    now = [89.0]
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(),
        clock=lambda: now[0],
        max_events=1,
        event_retention_seconds=10,
    )

    recorder.record(
        DiagnosticEvent.create(
            correlation_id="corr-old",
            source="home",
            phase="turn",
            outcome="completed",
            occurred_at=89.0,
        )
    )
    now[0] = 100.0
    recorder.record(
        DiagnosticEvent.create(
            correlation_id="corr-new",
            source="home",
            phase="turn",
            outcome="completed",
            occurred_at=100.0,
        )
    )

    status = recorder.status()

    assert recorder.timeline("corr-old") == ()
    assert len(recorder.timeline("corr-new")) == 1
    assert status.dropped_event_count == 0

    recorder.record(
        DiagnosticEvent.create(
            correlation_id="corr-latest",
            source="home",
            phase="turn",
            outcome="completed",
            occurred_at=100.0,
        )
    )

    assert recorder.status().dropped_event_count == 1
    assert recorder.timeline("corr-new") == ()
    assert len(recorder.timeline("corr-latest")) == 1


def test_recorder_flushes_safe_events_and_reports_collector_failure() -> None:
    class Collector:
        def __init__(self) -> None:
            self.events = ()
            self.fail = False

        def upload(self, events, *, idempotency_key: str) -> UploadAcknowledgement:
            if self.fail:
                raise RuntimeError("collector down")
            self.events = tuple(events)
            return UploadAcknowledgement(
                idempotency_key=idempotency_key,
                event_ids=tuple(event.event_id for event in events),
            )

    collector = Collector()
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(),
        collector=collector,
        clock=lambda: 100.0,
    )
    recorder.record(
        DiagnosticEvent.create(
            correlation_id="corr-upload",
            source="home",
            phase="telemetry",
            outcome="queued",
            occurred_at=100.0,
        )
    )

    uploaded = recorder.flush()

    assert uploaded.uploaded_count == 1
    assert uploaded.collector_reachable is True
    assert recorder.status().queued_event_count == 0
    assert collector.events[0].correlation_id == "corr-upload"

    collector.fail = True
    recorder.record(
        DiagnosticEvent.create(
            correlation_id="corr-failed-upload",
            source="home",
            phase="telemetry",
            outcome="queued",
            occurred_at=100.0,
        )
    )

    failed = recorder.flush()

    assert failed == type(failed)(uploaded_count=0, collector_reachable=False)
    assert recorder.status().queued_event_count == 1
    assert recorder.status().collector_reachable is False


def test_incident_capture_previews_a_bounded_scope_before_sealing_and_uploading() -> (
    None
):
    now = [100.0]
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    ring = LocalRingBuffer(max_age_seconds=60, max_entries=3, max_bytes=100)
    ring.append(scope, b"first", captured_at=30.0)
    ring.append(scope, b"second", captured_at=95.0)
    sealed = []
    uploaded = []

    class Sealer:
        def seal(self, evidence, *, capture_id, scope):
            sealed.append((tuple(evidence), capture_id, scope))
            return b"encrypted-bundle"

    class Uploader:
        def upload(self, bundle) -> None:
            uploaded.append(bundle)

    captures = IncidentCaptureService(
        ring_buffer=ring,
        sealer=Sealer(),
        uploader=Uploader(),
        clock=lambda: now[0],
        **_capture_auth(scope),
    )

    capture = captures.arm(scope)
    preview = captures.preview(capture.capture_id)

    assert preview.state == "previewing"
    assert preview.entry_count == 1
    assert preview.total_bytes == len(b"second")
    assert sealed == []
    assert uploaded == []

    approved = captures.approve(
        capture.capture_id,
        evidence_ids=[preview.evidence[0].evidence_id],
    )

    assert approved.state == "uploaded"
    assert sealed[0][0] == (b"second",)
    assert uploaded[0].capture_id == capture.capture_id
    assert approved.retention_deadline == 100.0 + 7 * 24 * 60 * 60


def test_incident_capture_is_fixed_to_one_scope_and_audits_preserve_and_delete() -> (
    None
):
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    other_scope = CaptureScope(OTHER_ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    ring = LocalRingBuffer()
    ring.append(scope, b"private", captured_at=100.0)

    class Sealer:
        def seal(self, evidence, *, capture_id, scope):
            return b"sealed"

    class Uploader:
        def upload(self, bundle) -> None:
            return None

    captures = IncidentCaptureService(
        ring_buffer=ring,
        sealer=Sealer(),
        uploader=Uploader(),
        clock=lambda: 100.0,
        **_capture_auth(scope),
    )
    capture = captures.arm(scope)

    with pytest.raises(CaptureStateError, match="scope"):
        captures.preview(capture.capture_id, scope=other_scope)

    preview = captures.preview(capture.capture_id)
    captures.approve(
        capture.capture_id,
        evidence_ids=[preview.evidence[0].evidence_id],
    )
    preserved = captures.preserve(capture.capture_id)

    assert preserved.state == "preserved"
    assert preserved.retention_deadline is None
    assert captures.audit_log()[-1].action == "preserve"

    deleted = captures.delete(capture.capture_id)

    assert deleted.state == "deleted"
    assert deleted.encrypted_payload is None
    assert captures.audit_log()[-1].action == "delete"
    assert "private" not in repr(deleted)


def test_uploaded_incident_expires_at_its_seven_day_deadline() -> None:
    now = [100.0]
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    ring = LocalRingBuffer()
    ring.append(scope, b"private", captured_at=100.0)

    class Sealer:
        def seal(self, evidence, *, capture_id, scope):
            return b"sealed"

    class Uploader:
        def upload(self, bundle) -> None:
            return None

    captures = IncidentCaptureService(
        ring_buffer=ring,
        sealer=Sealer(),
        uploader=Uploader(),
        clock=lambda: now[0],
        **_capture_auth(scope),
    )
    capture = captures.arm(scope)
    captures.preview(capture.capture_id)
    preview = captures.preview(capture.capture_id)
    uploaded = captures.approve(
        capture.capture_id,
        evidence_ids=[preview.evidence[0].evidence_id],
    )
    assert uploaded.retention_deadline is not None

    now[0] = uploaded.retention_deadline

    with pytest.raises(CaptureStateError, match="expired"):
        captures.get(capture.capture_id)
    assert captures.audit_log()[-1].action == "expire"


def test_safe_event_rejects_reversible_identifiers_and_untyped_failure_codes() -> None:
    with pytest.raises(DiagnosticValidationError, match="opaque fingerprint"):
        DiagnosticEvent.create(
            correlation_id="corr-unsafe",
            source="home",
            phase="turn",
            outcome="failed",
            occurred_at=100.0,
            endpoint_fingerprint="device-123",
        )

    with pytest.raises(DiagnosticValidationError, match="failure code"):
        DiagnosticEvent.create(
            correlation_id="corr-unsafe",
            source="home",
            phase="turn",
            outcome="failed",
            occurred_at=100.0,
            failure_code="private-error-detail",
        )

    for failure_code in ("conflict", "expired_or_consumed"):
        event = DiagnosticEvent.create(
            correlation_id="corr-safe-code",
            source="home",
            phase="request",
            outcome="rejected",
            occurred_at=100.0,
            failure_code=failure_code,
        )
        assert event.failure_code == failure_code

    with pytest.raises(DiagnosticValidationError, match="route ID"):
        DiagnosticEvent.create(
            correlation_id="corr-unsafe",
            source="home",
            phase="turn",
            outcome="failed",
            occurred_at=100.0,
            route_id="device-123",
        )


def test_recorder_rejects_an_event_already_past_its_retention_deadline() -> None:
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(),
        clock=lambda: 100.0,
    )
    event = DiagnosticEvent.create(
        correlation_id="corr-expired",
        source="home",
        phase="turn",
        outcome="completed",
        occurred_at=100.0,
        retention_deadline=100.0,
    )

    assert recorder.record(event) is False
    assert recorder.status().rejected_event_count == 1
    assert recorder.timeline("corr-expired") == ()


def test_ring_loss_is_timestamp_aware_and_exposed_in_status_and_metrics() -> None:
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    ring = LocalRingBuffer(max_age_seconds=60, max_entries=1, max_bytes=100)
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(),
        metrics=MetricsRegistry(),
        clock=lambda: 200.0,
        ring_buffer=ring,
    )

    ring.append(scope, b"first", captured_at=100.0)
    ring.append(scope, b"second", captured_at=101.0)
    ring.append(scope, b"stale", captured_at=0.0)
    assert [entry.evidence for entry in ring.entries(scope, now=200.0)] == []

    status = recorder.status()
    rendered = recorder._metrics.render()

    assert status.ring_evicted_entry_count == 1
    assert status.ring_expired_entry_count == 1
    assert status.ring_out_of_order_drop_count == 1
    assert "hermes_home_diagnostics_ring_entries_evicted_total 1" in rendered
    assert "hermes_home_diagnostics_ring_entries_expired_total 1" in rendered
    assert "hermes_home_diagnostics_ring_entries_out_of_order_total 1" in rendered


def test_flush_uses_the_recorder_bound_as_its_default_batch_limit() -> None:
    class Collector:
        def upload(self, events, *, idempotency_key: str) -> UploadAcknowledgement:
            return UploadAcknowledgement(
                idempotency_key=idempotency_key,
                event_ids=tuple(event.event_id for event in events),
            )

    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(),
        collector=Collector(),
        clock=lambda: 100.0,
        max_events=2,
    )
    for correlation_id in ("corr-one", "corr-two"):
        assert recorder.record(
            DiagnosticEvent.create(
                correlation_id=correlation_id,
                source="home",
                phase="telemetry",
                outcome="queued",
                occurred_at=100.0,
            )
        )

    assert recorder.flush().uploaded_count == 2


def test_diagnostics_metrics_failures_never_escape_record_or_flush() -> None:
    class BrokenMetrics:
        def inc(self, *args, **kwargs) -> None:
            del args, kwargs
            raise RuntimeError("metrics down")

        def set(self, *args, **kwargs) -> None:
            del args, kwargs
            raise RuntimeError("metrics down")

    class Collector:
        def upload(self, events, *, idempotency_key: str) -> UploadAcknowledgement:
            return UploadAcknowledgement(
                idempotency_key=idempotency_key,
                event_ids=tuple(event.event_id for event in events),
            )

    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(),
        metrics=BrokenMetrics(),  # type: ignore[arg-type]
        collector=Collector(),
        clock=lambda: 100.0,
    )

    assert recorder.record(
        DiagnosticEvent.create(
            correlation_id="corr-metrics",
            source="home",
            phase="turn",
            outcome="completed",
            occurred_at=100.0,
        )
    )
    assert recorder.flush().uploaded_count == 1
    assert recorder.status().queued_event_count == 0


def test_empty_flush_refreshes_queue_depth_after_expiry_purge() -> None:
    now = [89.0]
    metrics = MetricsRegistry()
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(),
        metrics=metrics,
        clock=lambda: now[0],
        event_retention_seconds=10,
    )
    recorder.record(
        DiagnosticEvent.create(
            correlation_id="corr-expiring",
            source="home",
            phase="turn",
            outcome="queued",
            occurred_at=89.0,
        )
    )
    now[0] = 100.0

    result = recorder.flush()

    assert result.uploaded_count == 0
    assert "hermes_home_diagnostics_queue_depth 0" in metrics.render()


def test_recorder_does_not_hold_its_lock_during_collector_io() -> None:
    started = Event()
    release = Event()

    class BlockingCollector:
        def upload(self, events, *, idempotency_key: str) -> UploadAcknowledgement:
            started.set()
            assert release.wait(timeout=1)
            return UploadAcknowledgement(
                idempotency_key=idempotency_key,
                event_ids=tuple(event.event_id for event in events),
            )

    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(),
        collector=BlockingCollector(),
        clock=lambda: 100.0,
    )
    recorder.record(
        DiagnosticEvent.create(
            correlation_id="corr-blocking-one",
            source="home",
            phase="telemetry",
            outcome="queued",
            occurred_at=100.0,
        )
    )
    upload_result: list[object] = []
    thread = Thread(target=lambda: upload_result.append(recorder.flush()))
    thread.start()
    assert started.wait(timeout=1)

    assert recorder.record(
        DiagnosticEvent.create(
            correlation_id="corr-blocking-two",
            source="home",
            phase="telemetry",
            outcome="queued",
            occurred_at=100.0,
        )
    )
    release.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert upload_result[0] == type(upload_result[0])(1, True)


def test_upload_retry_reuses_the_same_idempotency_key_after_store_failure() -> None:
    class FlakyStore(InMemoryDiagnosticsStore):
        fail_once = True

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

    collector = Collector()
    recorder = DiagnosticsRecorder(
        store=FlakyStore(),
        collector=collector,
        clock=lambda: 100.0,
    )
    recorder.record(
        DiagnosticEvent.create(
            correlation_id="corr-retry",
            source="home",
            phase="telemetry",
            outcome="queued",
            occurred_at=100.0,
        )
    )

    assert recorder.flush().collector_reachable is False
    assert recorder.flush().uploaded_count == 1
    assert collector.keys[0] == collector.keys[1]


def test_ring_quota_remains_bounded_under_continued_activity() -> None:
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    ring = LocalRingBuffer(max_age_seconds=60, max_entries=2, max_bytes=5)

    for index in range(10):
        ring.append(scope, bytes([index]), captured_at=100.0 + index)

    entries = ring.entries(scope, now=109.0)

    assert len(entries) <= 2
    assert sum(len(entry.evidence) for entry in entries) <= 5
    assert ring.status().evicted_entry_count == 8


def test_capture_approval_seals_only_the_selected_preview_entries() -> None:
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    ring = LocalRingBuffer(max_age_seconds=60)
    ring.append(scope, b"first", captured_at=100.0)
    ring.append(scope, b"second", captured_at=101.0)
    sealed: list[tuple[bytes, ...]] = []

    class Sealer:
        def seal(self, evidence, *, capture_id, scope):
            del capture_id, scope
            sealed.append(tuple(evidence))
            return b"sealed"

    class Uploader:
        def upload(self, bundle) -> None:
            del bundle

    captures = IncidentCaptureService(
        ring_buffer=ring,
        sealer=Sealer(),
        uploader=Uploader(),
        clock=lambda: 101.0,
        **_capture_auth(scope),
    )
    capture = captures.arm(scope)
    preview = captures.preview(capture.capture_id)

    captures.approve(
        capture.capture_id,
        evidence_ids=[preview.evidence[0].evidence_id],
    )

    assert sealed == [(b"first",)]


def test_incident_capture_requires_authorization_and_current_scope() -> None:
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)

    class Deny:
        def authorize(self, scope) -> bool:
            del scope
            return False

    class Current:
        def current_scope(self):
            return scope

    captures = IncidentCaptureService(
        ring_buffer=LocalRingBuffer(),
        sealer=None,
        uploader=None,
        authorizer=Deny(),
        current_task_resolver=Current(),
        clock=lambda: 100.0,
    )

    with pytest.raises(CaptureStateError, match="not authorized"):
        captures.arm(scope)


def test_incident_capture_remote_lifecycle_is_injected() -> None:
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    ring = LocalRingBuffer()
    ring.append(scope, b"private", captured_at=100.0)
    calls: list[tuple[str, str]] = []

    class Sealer:
        def seal(self, evidence, *, capture_id, scope):
            del evidence, scope
            return b"sealed"

    class Uploader:
        def upload(self, bundle) -> None:
            del bundle

    class Lifecycle:
        def preserve(self, capture_id: str) -> None:
            calls.append(("preserve", capture_id))

        def delete(self, capture_id: str) -> None:
            calls.append(("delete", capture_id))

    captures = IncidentCaptureService(
        ring_buffer=ring,
        sealer=Sealer(),
        uploader=Uploader(),
        bundle_lifecycle=Lifecycle(),
        clock=lambda: 100.0,
        **_capture_auth(scope),
    )
    capture = captures.arm(scope)
    preview = captures.preview(capture.capture_id)
    captures.approve(
        capture.capture_id,
        evidence_ids=[preview.evidence[0].evidence_id],
    )
    captures.preserve(capture.capture_id)
    captures.delete(capture.capture_id)

    assert [action for action, _ in calls] == ["preserve", "delete"]


def test_incident_capture_reaps_expiry_through_runtime_scheduler() -> None:
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    now = [100.0]

    class Scheduler:
        def __init__(self) -> None:
            self.callback = None
            self.handle = None

        def register(self, callback, *, interval_seconds: float):
            self.callback = callback
            self.interval_seconds = interval_seconds
            self.handle = "expiry-handle"
            return self.handle

        def unregister(self, handle) -> None:
            self.unregistered = handle

    scheduler = Scheduler()
    captures = IncidentCaptureService(
        ring_buffer=LocalRingBuffer(),
        sealer=None,
        uploader=None,
        capture_ttl_seconds=10,
        expiry_scheduler=scheduler,
        clock=lambda: now[0],
        **_capture_auth(scope),
    )
    capture = captures.arm(scope)
    now[0] = 111.0

    assert scheduler.callback is not None
    assert scheduler.callback() == 1
    assert captures.get(capture.capture_id).state == "expired"
    captures.close()
    assert scheduler.unregistered == "expiry-handle"


def test_incident_capture_cleans_private_state_on_sealing_and_upload_failure() -> None:
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)

    class FailingSealer:
        def seal(self, evidence, *, capture_id, scope):
            del evidence, capture_id, scope
            raise RuntimeError("sealer down")

    class Uploader:
        def upload(self, bundle) -> None:
            del bundle
            raise RuntimeError("uploader down")

    for sealer, expected_code in (
        (FailingSealer(), "encryption_failed"),
        (
            type(
                "Sealer",
                (),
                {"seal": lambda self, evidence, *, capture_id, scope: b"sealed"},
            )(),
            "upload_failed",
        ),
    ):
        ring = LocalRingBuffer()
        ring.append(scope, b"private", captured_at=100.0)
        captures = IncidentCaptureService(
            ring_buffer=ring,
            sealer=sealer,
            uploader=Uploader(),
            clock=lambda: 100.0,
            **_capture_auth(scope),
        )
        capture = captures.arm(scope)
        preview = captures.preview(capture.capture_id)
        failed = captures.approve(
            capture.capture_id,
            evidence_ids=[preview.evidence[0].evidence_id],
        )

        assert failed.state == "failed"
        assert failed.failure_code == expected_code
        assert failed.encrypted_payload is None
        assert capture.capture_id not in captures._evidence


def test_incident_capture_rechecks_expiry_before_upload() -> None:
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    now = [100.0]
    uploaded: list[object] = []

    class Sealer:
        def seal(self, evidence, *, capture_id, scope):
            del evidence, capture_id, scope
            now[0] = 401.0
            return b"sealed"

    class Uploader:
        def upload(self, bundle) -> None:
            uploaded.append(bundle)

    ring = LocalRingBuffer()
    ring.append(scope, b"private", captured_at=100.0)
    captures = IncidentCaptureService(
        ring_buffer=ring,
        sealer=Sealer(),
        uploader=Uploader(),
        clock=lambda: now[0],
        **_capture_auth(scope),
    )
    capture = captures.arm(scope)
    preview = captures.preview(capture.capture_id)

    with pytest.raises(CaptureStateError, match="expired"):
        captures.approve(
            capture.capture_id,
            evidence_ids=[preview.evidence[0].evidence_id],
        )

    assert uploaded == []


def test_incident_capture_cancellation_discards_staged_evidence() -> None:
    scope = CaptureScope(ENDPOINT_FINGERPRINT, TASK_FINGERPRINT)
    ring = LocalRingBuffer()
    ring.append(scope, b"private", captured_at=100.0)
    captures = IncidentCaptureService(
        ring_buffer=ring,
        sealer=None,
        uploader=None,
        clock=lambda: 100.0,
        **_capture_auth(scope),
    )
    capture = captures.arm(scope)
    preview = captures.preview(capture.capture_id)

    cancelled = captures.cancel(capture.capture_id)

    assert cancelled.state == "cancelled"
    assert preview.evidence
    assert capture.capture_id not in captures._evidence


def test_timestamp_overflow_is_reported_as_typed_validation() -> None:
    with pytest.raises(DiagnosticValidationError, match="finite"):
        DiagnosticEvent.create(
            correlation_id="corr-overflow",
            source="home",
            phase="turn",
            outcome="completed",
            occurred_at=10**1000,
        )
