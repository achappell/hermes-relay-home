import json
import os
import stat
import sys
import threading
import time

import pytest

from hermes_home.observability.diagnostics import (
    EVENT_RETENTION_SECONDS,
    DiagnosticEvent,
    DiagnosticsRecorder,
    InMemoryDiagnosticsStore,
    MetricsRegistry,
    UploadAcknowledgement,
)
from hermes_home.observability.export import (
    CLIENT_REPORT,
    CLIENT_REPORT_RETENTION_SECONDS,
    SAFE_EVENT,
    ClientReportExporter,
    DiagnosticsExport,
    ExportError,
    ExportScheduler,
    FileEventCollector,
    JsonlExportStore,
)
from hermes_home.storage.diagnostics import SQLiteDiagnosticsStore

DAY = 24 * 60 * 60
NOW = 1_790_000_000.0


def make_event(label: str = "x", *, at: float = NOW):
    return DiagnosticEvent.create(
        correlation_id="corr-" + (label.encode().hex() + "0" * 32)[:32],
        source="home",
        phase="telemetry",
        outcome="queued",
        occurred_at=at,
    )


def read_records(directory, prefix):
    records = []
    for path in sorted(directory.glob(f"{prefix}.*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            records.append(json.loads(line))
    return records


def test_store_appends_lines_and_rolls_by_size(tmp_path):
    store = JsonlExportStore(tmp_path, clock=lambda: NOW, max_file_bytes=100)

    for index in range(6):
        store.write(SAFE_EVENT, [json.dumps({"n": index, "pad": "x" * 30})])

    files = sorted(tmp_path.glob("safe-events.*.jsonl"))
    assert len(files) > 1
    assert all(path.stat().st_size <= 100 for path in files)
    assert [record["n"] for record in read_records(tmp_path, "safe-events")] == list(
        range(6)
    )


def test_store_rolls_to_a_new_file_each_utc_day(tmp_path):
    now = [NOW]
    store = JsonlExportStore(tmp_path, clock=lambda: now[0])

    store.write(SAFE_EVENT, ['{"a":1}'])
    now[0] += DAY
    store.write(SAFE_EVENT, ['{"a":2}'])

    assert len(list(tmp_path.glob("safe-events.*.jsonl"))) == 2


def test_store_evicts_the_oldest_file_beyond_the_file_bound(tmp_path):
    metrics = MetricsRegistry()
    store = JsonlExportStore(
        tmp_path,
        clock=lambda: NOW,
        max_file_bytes=40,
        max_files=3,
        metrics=metrics,
    )

    for index in range(10):
        store.write(SAFE_EVENT, [json.dumps({"n": index, "pad": "y" * 20})])

    assert len(list(tmp_path.glob("safe-events.*.jsonl"))) == 3
    assert read_records(tmp_path, "safe-events")[-1]["n"] == 9
    assert (
        'hermes_home_export_files_removed_total{reason="capacity",record_type="safe_event"}'
        in metrics.render()
    )


def test_store_fsyncs_before_returning(tmp_path, monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    store = JsonlExportStore(tmp_path, clock=lambda: NOW)

    store.write(SAFE_EVENT, ['{"a":1}'])

    assert calls


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
def test_store_creates_owner_only_files(tmp_path):
    store = JsonlExportStore(tmp_path, clock=lambda: NOW)

    store.write(SAFE_EVENT, ['{"a":1}'])

    path = next(tmp_path.glob("safe-events.*.jsonl"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_store_write_failure_raises_export_error_and_is_counted(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    metrics = MetricsRegistry()
    store = JsonlExportStore(blocker, clock=lambda: NOW, metrics=metrics)

    with pytest.raises(ExportError):
        store.write(SAFE_EVENT, ['{"a":1}'])

    assert (
        'hermes_home_export_write_failures_total{record_type="safe_event"} 1'
        in metrics.render()
    )


def test_sweep_deletes_only_files_wholly_past_each_streams_retention(tmp_path):
    now = [NOW]
    store = JsonlExportStore(tmp_path, clock=lambda: now[0])
    # Write one file per stream on a day well in the past, one for "now".
    now[0] = NOW - 20 * DAY
    store.write(SAFE_EVENT, ['{"old":1}'])
    store.write(CLIENT_REPORT, ['{"old":1}'])
    now[0] = NOW - 10 * DAY
    store.write(SAFE_EVENT, ['{"mid":1}'])
    store.write(CLIENT_REPORT, ['{"mid":1}'])
    now[0] = NOW
    store.write(SAFE_EVENT, ['{"new":1}'])
    store.write(CLIENT_REPORT, ['{"new":1}'])

    removed = store.sweep()

    # Safe events (14 days): the 20-day-old file goes, the 10-day-old one stays.
    safe = [record for record in read_records(tmp_path, "safe-events")]
    assert safe == [{"mid": 1}, {"new": 1}]
    # Client reports (7 days): both older files go.
    reports = read_records(tmp_path, "client-reports")
    assert reports == [{"new": 1}]
    assert removed == 3
    assert EVENT_RETENTION_SECONDS == 14 * DAY
    assert CLIENT_REPORT_RETENTION_SECONDS == 7 * DAY


def test_sweep_never_deletes_a_file_that_may_hold_unexpired_lines(tmp_path):
    now = [NOW]
    store = JsonlExportStore(tmp_path, clock=lambda: now[0])
    store.write(SAFE_EVENT, ['{"a":1}'])
    # Just under retention plus the file's own day span: still kept.
    now[0] = NOW + EVENT_RETENTION_SECONDS
    assert store.sweep() == 0
    assert read_records(tmp_path, "safe-events") == [{"a": 1}]


def test_collector_writes_safe_events_with_a_ledger_and_exact_acknowledgement(
    tmp_path,
):
    store = JsonlExportStore(tmp_path, clock=lambda: NOW)
    collector = FileEventCollector(store)
    events = [make_event("a"), make_event("b")]

    ack = collector.upload(events, idempotency_key="upload-" + "1" * 32)

    assert ack == UploadAcknowledgement(
        "upload-" + "1" * 32, tuple(event.event_id for event in events)
    )
    records = read_records(tmp_path, "safe-events")
    assert [record["record_type"] for record in records] == [
        "safe_event",
        "safe_event",
        "batch_ledger",
    ]
    assert records[0] == {"record_type": "safe_event", **events[0].to_dict()}
    assert records[2]["idempotency_key"] == "upload-" + "1" * 32
    assert records[2]["event_ids"] == [event.event_id for event in events]


def test_collector_replay_of_the_same_key_writes_no_duplicate_lines(tmp_path):
    store = JsonlExportStore(tmp_path, clock=lambda: NOW)
    collector = FileEventCollector(store)
    events = [make_event("a")]
    key = "upload-" + "2" * 32

    first = collector.upload(events, idempotency_key=key)
    replay = collector.upload(events, idempotency_key=key)

    assert replay == first
    assert len(read_records(tmp_path, "safe-events")) == 2


def test_collector_replay_after_restart_writes_no_duplicate_lines(tmp_path):
    events = [make_event("a")]
    key = "upload-" + "3" * 32
    FileEventCollector(JsonlExportStore(tmp_path, clock=lambda: NOW)).upload(
        events, idempotency_key=key
    )

    restarted = FileEventCollector(JsonlExportStore(tmp_path, clock=lambda: NOW))
    ack = restarted.upload(events, idempotency_key=key)

    assert ack.event_ids == (events[0].event_id,)
    assert len(read_records(tmp_path, "safe-events")) == 2


def test_collector_rejects_oversize_batches_without_writing(tmp_path):
    store = JsonlExportStore(tmp_path, clock=lambda: NOW)
    too_many = FileEventCollector(store, max_batch_events=2)
    with pytest.raises(ExportError):
        too_many.upload(
            [make_event("a"), make_event("b"), make_event("c")],
            idempotency_key="upload-" + "4" * 32,
        )
    too_big = FileEventCollector(store, max_batch_bytes=64)
    with pytest.raises(ExportError):
        too_big.upload([make_event("a")], idempotency_key="upload-" + "5" * 32)

    assert list(tmp_path.glob("*.jsonl")) == []


def test_collector_failure_does_not_remember_the_key(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    collector = FileEventCollector(JsonlExportStore(blocker, clock=lambda: NOW))
    key = "upload-" + "6" * 32

    with pytest.raises(ExportError):
        collector.upload([make_event("a")], idempotency_key=key)
    blocker.unlink()
    ack = collector.upload([make_event("a")], idempotency_key=key)

    assert ack.idempotency_key == key
    assert len(read_records(blocker, "safe-events")) == 2


def recorder_with_export(tmp_path, *, metrics=None, store=None, max_events=4096):
    metrics = metrics or MetricsRegistry()
    export_store = JsonlExportStore(tmp_path, clock=lambda: NOW, metrics=metrics)
    recorder = DiagnosticsRecorder(
        store=store or InMemoryDiagnosticsStore(clock=lambda: NOW),
        metrics=metrics,
        collector=FileEventCollector(export_store),
        clock=lambda: NOW,
        max_events=max_events,
    )
    return recorder, metrics


def test_recorder_flush_through_the_file_collector_drains_the_queue(tmp_path):
    recorder, metrics = recorder_with_export(tmp_path)
    for label in ("a", "b", "c"):
        recorder.record(make_event(label))

    result = recorder.flush()

    assert result.uploaded_count == 3
    assert result.collector_reachable is True
    status = recorder.status()
    assert status.queued_event_count == 0
    assert status.last_successful_upload_at == NOW
    assert status.collector_configured is True
    assert status.to_dict()["collector_state"] == "reachable"
    assert len(read_records(tmp_path, "safe-events")) == 4
    assert "hermes_home_diagnostics_collector_configured 1" in metrics.render()


def test_status_says_not_configured_without_a_collector():
    metrics = MetricsRegistry()
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(clock=lambda: NOW),
        metrics=metrics,
        clock=lambda: NOW,
    )
    recorder.record(make_event("a"))

    status = recorder.status()

    assert status.collector_configured is False
    assert status.collector_reachable is False
    body = status.to_dict()
    assert body["collector_configured"] is False
    assert body["collector_state"] == "not_configured"
    assert status.queued_event_count == 1
    assert "hermes_home_diagnostics_collector_configured 0" in metrics.render()


def test_status_distinguishes_unreachable_from_not_configured(tmp_path):
    class Failing:
        def upload(self, events, *, idempotency_key):
            raise OSError("down")

    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(clock=lambda: NOW),
        collector=Failing(),
        clock=lambda: NOW,
    )
    recorder.record(make_event("a"))
    recorder.flush()

    body = recorder.status().to_dict()

    assert body["collector_configured"] is True
    assert body["collector_state"] == "unreachable"


def test_a_configured_collector_with_an_empty_queue_is_idle_not_unreachable():
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(clock=lambda: NOW),
        collector=ScriptedCollector(),
        clock=lambda: NOW,
    )

    body = recorder.status().to_dict()

    assert body["collector_configured"] is True
    assert body["collector_reachable"] is False
    assert body["collector_state"] == "idle"


def test_eviction_counts_distinguish_before_and_after_upload(tmp_path):
    for store in (
        InMemoryDiagnosticsStore(clock=lambda: NOW),
        SQLiteDiagnosticsStore(tmp_path / "d.db", clock=lambda: NOW),
    ):
        recorder, metrics = recorder_with_export(
            tmp_path / type(store).__name__, store=store, max_events=2
        )
        recorder.record(make_event("a"))
        recorder.record(make_event("b"))
        recorder.flush()  # a and b are now uploaded
        recorder.record(make_event("c"))  # evicts uploaded "a"
        recorder.record(make_event("d"))  # evicts uploaded "b"
        recorder.record(make_event("e"))  # evicts un-uploaded "c"

        status = recorder.status()

        assert status.dropped_event_count == 3
        assert status.dropped_unuploaded_event_count == 1
        assert status.to_dict()["dropped_unuploaded_event_count"] == 1
        rendered = metrics.render()
        assert (
            'hermes_home_diagnostics_events_evicted_total{state="after_upload"} 2'
            in rendered
        )
        assert (
            'hermes_home_diagnostics_events_evicted_total{state="before_upload"} 1'
            in rendered
        )


def test_sqlite_store_migrates_the_unuploaded_drop_column(tmp_path):
    import sqlite3

    database = tmp_path / "old.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE diagnostic_state (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "dropped_event_count INTEGER NOT NULL DEFAULT 0, "
        "rejected_event_count INTEGER NOT NULL DEFAULT 0, "
        "last_successful_upload_at REAL, "
        "collector_reachable INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "INSERT INTO diagnostic_state (id, dropped_event_count) VALUES (1, 7)"
    )
    connection.commit()
    connection.close()

    store = SQLiteDiagnosticsStore(database, clock=lambda: NOW)
    status = store.status()

    assert status.dropped_event_count == 7
    assert status.dropped_unuploaded_event_count == 0
    store.close()


class ScriptedCollector:
    def __init__(self):
        self.fail = False
        self.batches = []

    def upload(self, events, *, idempotency_key):
        if self.fail:
            raise OSError("down")
        self.batches.append(len(events))
        return UploadAcknowledgement(idempotency_key, tuple(e.event_id for e in events))


def scheduler_fixture(tmp_path, *, collector=None, batch=256, **kwargs):
    metrics = MetricsRegistry()
    collector = collector or ScriptedCollector()
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(clock=lambda: NOW),
        metrics=metrics,
        collector=collector,
        clock=lambda: NOW,
    )
    store = JsonlExportStore(tmp_path, clock=lambda: NOW, metrics=metrics)
    scheduler = ExportScheduler(
        recorder,
        store,
        metrics=metrics,
        clock=lambda: NOW,
        jitter=lambda: 0.5,
        batch_events=batch,
        **kwargs,
    )
    return recorder, collector, scheduler, metrics


def test_scheduler_drains_multiple_bounded_batches_in_one_tick(tmp_path):
    recorder, collector, scheduler, metrics = scheduler_fixture(tmp_path, batch=256)
    for index in range(600):
        recorder.record(make_event(f"e{index}"))

    delay = scheduler.run_once()

    assert collector.batches == [256, 256, 88]
    assert delay == 30.0
    assert recorder.status().queued_event_count == 0
    assert (
        "hermes_home_diagnostics_export_last_attempt_timestamp_seconds "
        in metrics.render()
    )


def test_scheduler_treats_an_empty_queue_as_idle_not_failure(tmp_path):
    _, collector, scheduler, metrics = scheduler_fixture(tmp_path)

    assert scheduler.run_once() == 30.0

    assert collector.batches == []
    assert 'outcome="unavailable"' not in metrics.render()


def test_scheduler_backs_off_exponentially_with_a_cap_and_resets(tmp_path):
    recorder, collector, scheduler, _ = scheduler_fixture(tmp_path)
    recorder.record(make_event("a"))
    collector.fail = True

    delays = [scheduler.run_once() for _ in range(7)]

    assert delays == [30.0, 60.0, 120.0, 240.0, 480.0, 900.0, 900.0]
    collector.fail = False
    assert scheduler.run_once() == 30.0
    assert collector.batches == [1]
    assert recorder.status().queued_event_count == 0
    collector_fail_events = recorder.status().dropped_event_count
    assert collector_fail_events == 0


def test_scheduler_jitter_is_bounded_by_the_cap(tmp_path):
    recorder, collector, scheduler, _ = scheduler_fixture(tmp_path)
    scheduler._jitter = lambda: 1.0
    recorder.record(make_event("a"))
    collector.fail = True

    delays = [scheduler.run_once() for _ in range(9)]

    assert max(delays) <= 900.0
    assert all(delay > 0 for delay in delays)


def test_scheduler_thread_flushes_and_final_drain_is_bounded(tmp_path):
    recorder, collector, scheduler, _ = scheduler_fixture(
        tmp_path, interval_seconds=0.05
    )
    scheduler.start()
    try:
        recorder.record(make_event("a"))
        deadline = time.monotonic() + 3
        while collector.batches == [] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert collector.batches == [1]
        recorder.record(make_event("b"))
    finally:
        started = time.monotonic()
        scheduler.close(timeout=2.0)
        elapsed = time.monotonic() - started

    assert elapsed < 2.5
    assert recorder.status().queued_event_count == 0  # final drain exported "b"
    assert not scheduler.is_alive()


def test_scheduler_close_does_not_wait_past_its_bound_for_a_stuck_collector(tmp_path):
    release = threading.Event()

    class Stuck:
        def upload(self, events, *, idempotency_key):
            release.wait(30)
            raise OSError("released")

    recorder, _, scheduler, _ = scheduler_fixture(
        tmp_path, collector=Stuck(), interval_seconds=0.01
    )
    recorder.record(make_event("a"))
    scheduler.start()
    time.sleep(0.1)

    started = time.monotonic()
    scheduler.close(timeout=0.3)
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 1.5


def test_client_report_exporter_writes_the_stored_payload_with_device_and_time(
    tmp_path,
):
    store = JsonlExportStore(tmp_path, clock=lambda: NOW)
    exporter = ClientReportExporter(store)
    body = {"schema": 1, "report_id": "r-1", "events": []}

    assert exporter.submit("device-a", NOW, body) is True
    exporter.close(timeout=2.0)

    assert read_records(tmp_path, "client-reports") == [
        {
            "record_type": "client_report",
            "device_id": "device-a",
            "received_at": NOW,
            "report": body,
        }
    ]


def test_client_report_exporter_never_raises_or_blocks_when_full_or_failing(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    metrics = MetricsRegistry()
    store = JsonlExportStore(blocker, clock=lambda: NOW, metrics=metrics)
    exporter = ClientReportExporter(store, metrics=metrics, capacity=2)
    gate = threading.Event()
    real_write = store.write
    store.write = lambda *a, **k: (gate.wait(5), real_write(*a, **k))[1]

    started = time.monotonic()
    results = [exporter.submit("d", NOW, {"i": i}) for i in range(20)]
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert results.count(False) >= 10
    gate.set()
    exporter.close(timeout=2.0)
    rendered = metrics.render()
    assert (
        'hermes_home_export_records_dropped_total{reason="queue_full",'
        'record_type="client_report"}' in rendered
    )
    assert (
        'hermes_home_export_write_failures_total{record_type="client_report"}'
        in rendered
    )


def test_diagnostics_export_facade_wires_collector_reports_and_close(tmp_path):
    metrics = MetricsRegistry()
    export = DiagnosticsExport(
        tmp_path / "export", metrics=metrics, interval_seconds=0.05
    )
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(), metrics=metrics, collector=export.collector
    )
    export.start(recorder)
    try:
        recorder.record(make_event("a", at=time.time()))
        export.reports.submit("device-a", time.time(), {"schema": 1})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and recorder.status().queued_event_count:
            time.sleep(0.02)
    finally:
        export.close(timeout=2.0)

    directory = tmp_path / "export"
    assert recorder.status().queued_event_count == 0
    assert read_records(directory, "client-reports")[0]["device_id"] == "device-a"
    assert [r["record_type"] for r in read_records(directory, "safe-events")] == [
        "safe_event",
        "batch_ledger",
    ]
