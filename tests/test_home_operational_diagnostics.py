from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

from hermes_home_diagnostics import (
    OperationalDiagnostics,
    SafeTransportLogHandler,
    close_fields,
)


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_log_has_one_provenance_header_and_typed_lifecycle_records(
    tmp_path: Path,
) -> None:
    diagnostics = OperationalDiagnostics(component="home", directory=tmp_path)
    diagnostics.emit(
        "connection_opened",
        leg="client_home",
        connection_id="conn-" + "1" * 32,
        correlation_state="local_only",
        phase="open",
    )
    diagnostics.close()

    records = _records(tmp_path / "home.jsonl")
    events = [record["event"] for record in records]
    assert events.count("provenance_header") == 1
    assert events.count("process_started") == 1
    assert events.count("process_stopping") == 1
    assert records[0]["artifact_sha256"] is None
    assert records[0]["provenance_status"] == "unavailable"


def test_envelope_keys_cannot_be_overridden_and_settings_are_copied(
    tmp_path: Path,
) -> None:
    canary = "RAW-ENVELOPE-CANARY"
    settings = {"ping_timeout": 5}
    diagnostics = OperationalDiagnostics(
        component="home", directory=tmp_path, settings=settings
    )
    settings["ping_timeout"] = canary

    assert not diagnostics.emit(
        "request_observed",
        correlation_id="corr-" + "2" * 32,
        connection_id="conn-" + "1" * 32,
        phase="submission",
        operation="prompt_submit",
        trigger="explicit",
        correlation_state="local_only",
        event_id=canary,
    )
    diagnostics.close()

    raw = (tmp_path / "home.jsonl").read_text(encoding="utf-8")
    assert canary not in raw
    records = _records(tmp_path / "home.jsonl")
    assert diagnostics.status()["schema_rejected"] == 1
    assert all(record.get("settings") == {"ping_timeout": 5} for record in records)


def test_transport_handler_discards_message_and_exception_text(tmp_path: Path) -> None:
    canary = "RAW-TRANSPORT-CANARY"
    diagnostics = OperationalDiagnostics(component="proxy", directory=tmp_path)
    handler = SafeTransportLogHandler(
        diagnostics, leg="home_proxy", connection_id="conn-" + "1" * 32
    )
    handler.emit(
        logging.LogRecord("websockets", logging.WARNING, __file__, 1, canary, (), None)
    )
    error = RuntimeError(canary)
    record = logging.LogRecord(
        "websockets",
        logging.WARNING,
        __file__,
        1,
        canary,
        (),
        (type(error), error, error.__traceback__),
    )
    handler.emit(record)
    diagnostics.close()

    raw = (tmp_path / "proxy.jsonl").read_text(encoding="utf-8")
    assert canary not in raw
    transport = [
        item
        for item in _records(tmp_path / "proxy.jsonl")
        if item["event"] == "transport_observed"
    ]
    assert len(transport) == 1
    assert transport[0]["phase"] == "unknown"
    assert transport[0]["classification"] == "unknown"
    assert "pending_count" not in transport[0]


def test_close_projection_uses_actual_frames_and_order_without_reasons() -> None:
    canary = "RAW-CLOSE-REASON-CANARY"
    error = ConnectionClosedOK(
        Close(1000, canary),
        Close(1000, canary),
        rcvd_then_sent=True,
    )

    fields = close_fields(error=error)

    assert fields["received_close_code"] == 1000
    assert fields["sent_close_code"] == 1000
    assert fields["observed_status_code"] == 1000
    assert fields["close_order"] == "received_first"
    assert canary not in repr(fields)


def test_restart_rotates_active_file_before_writing_new_process_header(
    tmp_path: Path,
) -> None:
    first = OperationalDiagnostics(component="home", directory=tmp_path)
    first_process = first.process_id
    first.close()

    second = OperationalDiagnostics(component="home", directory=tmp_path)
    second_process = second.process_id
    second.emit(
        "connection_opened",
        leg="client_home",
        connection_id="conn-" + "3" * 32,
        correlation_state="local_only",
        phase="open",
    )
    second.close()

    active = _records(tmp_path / "home.jsonl")
    previous = _records(tmp_path / "home.jsonl.1")
    assert active[0]["event"] == "provenance_header"
    assert {record["process_id"] for record in active} == {second_process}
    assert previous[0]["event"] == "provenance_header"
    assert {record["process_id"] for record in previous} == {first_process}


def test_rotation_and_retention_use_bounded_files_and_oldest_event_time(
    tmp_path: Path,
) -> None:
    now = 1_700_000_000.0
    diagnostics = OperationalDiagnostics(
        component="home",
        directory=tmp_path,
        active_bytes=2048,
        backups=2,
        retention_seconds=10,
        clock=lambda: now,
    )
    for index in range(12):
        diagnostics.emit(
            "connection_opened",
            leg="client_home",
            connection_id=f"conn-{index:032x}",
            correlation_state="local_only",
            phase="open",
        )
    diagnostics.close()

    files = [tmp_path / "home.jsonl"] + [
        tmp_path / f"home.jsonl.{index}" for index in range(1, 3)
    ]
    assert all(path.is_file() and path.stat().st_size <= 2048 for path in files)
    assert diagnostics.status()["rotation_evicted"] > 0
    diagnostics._purge_old(now + 11)
    assert not any(path.exists() for path in files)
    assert diagnostics.status()["rotation_evicted"] > 0


def test_idle_worker_emits_queued_loss_snapshot(tmp_path: Path) -> None:
    monotonic = [0.0]
    diagnostics = OperationalDiagnostics(
        component="home",
        directory=tmp_path,
        clock=lambda: 1_700_000_000.0,
        monotonic=lambda: monotonic[0],
    )
    diagnostics.add_loss("queue_dropped")
    monotonic[0] = 61.0
    try:
        deadline = time.monotonic() + 3
        losses: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            path = tmp_path / "home.jsonl"
            if path.exists():
                try:
                    records = _records(path)
                except json.JSONDecodeError:
                    time.sleep(0.02)
                    continue
                losses = [
                    item for item in records if item["event"] == "diagnostics_loss"
                ]
                if losses:
                    break
            time.sleep(0.02)
        assert losses
        assert losses[0]["queue_dropped"] == 1
    finally:
        diagnostics.close()


def test_restart_preserves_truncated_tail_but_evicts_interior_corruption(
    tmp_path: Path,
) -> None:
    first = OperationalDiagnostics(component="home", directory=tmp_path)
    first.close()
    active = tmp_path / "home.jsonl"
    complete = active.read_bytes()
    truncated = complete + b'{"event":"connection_'
    active.write_bytes(truncated)
    corrupt_backup = tmp_path / "home.jsonl.1"
    corrupt_backup.write_bytes(complete + b"invalid interior\n" + complete)

    second = OperationalDiagnostics(component="home", directory=tmp_path)
    second.close()

    assert (tmp_path / "home.jsonl.1").read_bytes() == truncated
    assert not (tmp_path / "home.jsonl.2").exists()
    assert second.status()["rotation_evicted"] > 0
    assert _records(active)[0]["process_id"] == second.process_id
