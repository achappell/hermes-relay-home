"""Standalone content-safe operational diagnostics shared by Home and proxy."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import platform
import queue
import re
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1
MAX_RECORD_BYTES = 2048
QUEUE_CAPACITY = 1024
ACTIVE_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 4
RETENTION_SECONDS = 14 * 86400
_PROCESS = re.compile(r"proc-[0-9a-f]{32}\Z")
_CONNECTION = re.compile(r"conn-[0-9a-f]{32}\Z")
_CORRELATION = re.compile(r"corr-[0-9a-f]{32}\Z")
_REQUEST = re.compile(r"req-[0-9a-f]{32}\Z")
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_VERSION = re.compile(r"[A-Za-z0-9.+_-]{1,64}\Z")
_FAILURE_CODES = frozenset(
    {
        "audio_fallback",
        "audio_unavailable",
        "authorization_unavailable",
        "capability_unavailable",
        "claim_denied",
        "conflict",
        "conversation_mismatch",
        "encryption_failed",
        "encryption_unavailable",
        "expired_or_consumed",
        "forbidden",
        "hermes_unavailable",
        "invalid_request",
        "not_found",
        "protocol_error",
        "reconnect_required",
        "request_rejected",
        "service_unavailable",
        "stale_conversation",
        "transport_timeout",
        "transport_unavailable",
        "unauthorized",
        "upload_failed",
        "upload_unavailable",
    }
)
_ENUMS = {
    "failure_code": _FAILURE_CODES,
    "provenance_status": {"verified", "unverified", "unavailable"},
    "component": {"home", "proxy"},
    "correlation_state": {"local_only", "linked", "ambiguous", "unavailable"},
    "leg": {"client_home", "home_proxy", "proxy_standard", "process"},
    "phase": {
        "startup",
        "open",
        "reconnect",
        "submission",
        "response",
        "stream",
        "closing",
        "closed",
        "shutdown",
        "unknown",
    },
    "initiator": {"local", "peer", "local_operator", "process_shutdown", "unknown"},
    "close_trigger": {
        "operator",
        "shutdown",
        "counterpart_closed",
        "keepalive",
        "transport_error",
        "unknown",
    },
    "classification": {
        "submit_unavailable",
        "keepalive_pong_timeout",
        "normal_shutdown",
        "timeout_during_close",
        "transport_error",
        "unknown",
    },
    "close_order": {"sent_first", "received_first", "simultaneous_or_unknown"},
    "exception_category": {
        "connection_closed_ok",
        "connection_closed_error",
        "timeout",
        "os_error",
        "cancelled",
        "protocol_error",
        "other",
        "none",
    },
    "cause_category": {
        "connection_closed_ok",
        "connection_closed_error",
        "timeout",
        "os_error",
        "cancelled",
        "protocol_error",
        "other",
        "none",
    },
    "outcome": {
        "accepted",
        "unavailable",
        "rejected",
        "interrupted",
        "unknown",
        "write_returned",
        "failed",
        "cancelled",
        "skipped_closed",
        "completed",
    },
    "ready_kind": {"open", "reconnect"},
    "operation": {"prompt_submit"},
    "trigger": {"explicit"},
    "origin": {"readiness_gate", "upstream_submit", "authorization", "validation"},
    "response_kind": {"accepted", "rejection"},
    "direction": {"home_to_standard", "standard_to_home"},
    "frame_kind": {"text", "binary"},
    "pending_state": {
        "awaiting_write",
        "awaiting_response",
        "response_resolved",
        "stream_active",
        "none",
        "unknown",
    },
    "text_state": {"completed", "unavailable", "not_requested", "unknown"},
    "audio_state": {"completed", "unavailable", "not_requested", "unknown"},
    "sink_state": {"ok", "degraded", "unavailable"},
}
_EVENTS = {
    "provenance_header": {"leg", "phase"},
    "process_started": {"leg", "phase"},
    "process_stopping": {"initiator", "leg", "phase"},
    "connection_opened": {"leg", "connection_id", "correlation_state"},
    "connection_ready": {"leg", "connection_id", "correlation_state", "ready_kind"},
    "request_observed": {
        "correlation_id",
        "connection_id",
        "phase",
        "operation",
        "trigger",
        "correlation_state",
    },
    "upstream_submit_started": {"correlation_id", "connection_id", "leg", "phase"},
    "upstream_submit_outcome": {
        "correlation_id",
        "connection_id",
        "leg",
        "phase",
        "outcome",
    },
    "rejection_generated": {
        "correlation_id",
        "connection_id",
        "phase",
        "failure_code",
        "origin",
        "unavailable_latch",
    },
    "response_write_started": {
        "correlation_id",
        "connection_id",
        "phase",
        "response_kind",
    },
    "response_write_outcome": {
        "correlation_id",
        "connection_id",
        "phase",
        "response_kind",
        "outcome",
    },
    "transport_observed": {"leg", "phase", "classification"},
    "connection_closed": {"leg", "phase", "classification"},
    "request_transport_lost": {
        "correlation_id",
        "connection_id",
        "phase",
        "pending_state",
        "outcome",
    },
    "recovery_observed": {"connection_id", "correlation_id", "phase", "ready_kind"},
    "proxy_message_write_outcome": {
        "connection_id",
        "peer_connection_id",
        "leg",
        "phase",
        "direction",
        "frame_kind",
        "outcome",
    },
    "turn_terminal": {
        "correlation_id",
        "phase",
        "outcome",
        "text_state",
        "audio_state",
    },
    "client_response_received": {
        "request_id",
        "phase",
        "response_kind",
        "pending_state",
    },
    "client_request_resolved": {
        "request_id",
        "phase",
        "response_kind",
        "failure_code",
        "pending_state",
        "uncertain",
    },
    "diagnostics_loss": {
        "queue_dropped",
        "schema_rejected",
        "sink_failed",
        "rotation_evicted",
        "correlation_conflicts",
        "sink_state",
    },
}
_OPTIONAL = {
    "process_started": {"build_number"},
    "connection_opened": {"peer_connection_id", "home_connection_id", "phase"},
    "connection_ready": {"peer_connection_id", "home_connection_id", "phase"},
    "request_observed": {"request_id", "peer_connection_id"},
    "upstream_submit_started": {"peer_connection_id"},
    "upstream_submit_outcome": {
        "peer_connection_id",
        "failure_code",
        "exception_category",
        "duration_ms",
    },
    "rejection_generated": {"request_id"},
    "response_write_started": {"request_id"},
    "response_write_outcome": {
        "request_id",
        "failure_code",
        "exception_category",
        "duration_ms",
    },
    "transport_observed": {
        "connection_id",
        "peer_connection_id",
        "home_connection_id",
        "correlation_state",
        "initiator",
        "close_trigger",
        "sent_close_code",
        "received_close_code",
        "observed_status_code",
        "close_order",
        "pending_count",
        "pending_saturated",
        "pending_state",
        "exception_category",
        "cause_category",
        "duration_ms",
    },
    "connection_closed": {
        "connection_id",
        "peer_connection_id",
        "home_connection_id",
        "correlation_state",
        "initiator",
        "close_trigger",
        "sent_close_code",
        "received_close_code",
        "observed_status_code",
        "close_order",
        "pending_count",
        "pending_saturated",
        "pending_state",
        "exception_category",
        "cause_category",
        "duration_ms",
    },
    "request_transport_lost": {"request_id"},
    "recovery_observed": {"peer_connection_id"},
    "proxy_message_write_outcome": {
        "home_connection_id",
        "correlation_state",
        "failure_code",
        "exception_category",
    },
    "turn_terminal": set(),
    "client_response_received": {
        "correlation_id",
        "connection_id",
        "home_connection_id",
        "correlation_state",
    },
    "client_request_resolved": {
        "correlation_id",
        "connection_id",
        "home_connection_id",
        "correlation_state",
    },
    "diagnostics_loss": set(),
}
_ENVELOPE_FIELDS = {
    "schema",
    "time_utc",
    "sequence",
    "elapsed_ms",
    "event_id",
    "process_id",
    "component",
    "event",
    "build_ref",
    "app_version",
    "build_number",
    "source_revision",
    "artifact_sha256",
    "provenance_status",
    "runtime_version",
    "websocket_version",
    "clock_source",
    "settings",
}


def new_connection_id() -> str:
    return f"conn-{uuid.uuid4().hex}"


def _category(error: BaseException | None) -> str:
    if error is None:
        return "none"
    name = type(error).__name__
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, OSError):
        return "os_error"
    if isinstance(error, (InterruptedError, KeyboardInterrupt)):
        return "cancelled"
    if name == "ConnectionClosedOK":
        return "connection_closed_ok"
    if name.startswith("ConnectionClosed"):
        return "connection_closed_error"
    if "Protocol" in name:
        return "protocol_error"
    return "other"


def exception_fields(error: BaseException | None) -> dict[str, str]:
    cause = None if error is None else error.__cause__ or error.__context__
    return {"exception_category": _category(error), "cause_category": _category(cause)}


def close_fields(
    connection: object | None = None, error: BaseException | None = None
) -> dict[str, Any]:
    """Project supported close-frame facts, distinguishing no-frame 1006."""
    sent = received = status = None
    sent_order = received_order = None
    source = connection if connection is not None else error
    if source is not None:
        protocol = getattr(source, "protocol", None)
        close = getattr(protocol, "close_rcvd_then_sent", None)
        if close is None:
            close = getattr(source, "received_then_sent", None)
        received_close = getattr(close, "received", None)
        sent_close = getattr(close, "sent", None)
        if received_close is None:
            received_close = getattr(source, "rcvd", None)
        if sent_close is None:
            sent_close = getattr(source, "sent", None)
        if error is not None and source is not error:
            if received_close is None:
                received_close = getattr(error, "rcvd", None)
            if sent_close is None:
                sent_close = getattr(error, "sent", None)
        received = _close_code(received_close)
        sent = _close_code(sent_close)
        if received is not None:
            received_order = 0
        if sent is not None:
            received_first = getattr(protocol, "close_rcvd_then_sent", None)
            if received_first is None:
                received_first = getattr(source, "rcvd_then_sent", None)
            if received_first is None and error is not None:
                received_first = getattr(error, "rcvd_then_sent", None)
            sent_order = 1 if received is not None and received_first is True else 0
            if received is not None and received_first is False:
                received_order = 1
        raw_code = getattr(source, "close_code", None)
        if raw_code is None:
            received_frame = getattr(source, "rcvd", None)
            if received_frame is None and error is not None:
                received_frame = getattr(error, "rcvd", None)
            if received_frame is not None:
                raw_code = getattr(received_frame, "code", None)
            elif hasattr(source, "rcvd") or (
                error is not None and hasattr(error, "rcvd")
            ):
                raw_code = 1006
            elif error is not None:
                raw_code = getattr(error, "close_code", None)
                if raw_code is None:
                    error_frame = getattr(error, "rcvd", None)
                    raw_code = (
                        None
                        if error_frame is None
                        else getattr(error_frame, "code", None)
                    )
                if raw_code is None and hasattr(error, "rcvd"):
                    raw_code = 1006
        if (
            type(raw_code) is int
            and 1000 <= raw_code <= 4999
            and raw_code not in {1005, 1006, 1015}
        ):
            status = raw_code
        elif raw_code == 1006:
            status = 1006
    order = "simultaneous_or_unknown"
    if sent is not None and received is not None:
        order = (
            "sent_first"
            if sent_order < received_order
            else "received_first"
            if received_order < sent_order
            else order
        )
    return {
        "sent_close_code": sent,
        "received_close_code": received,
        "observed_status_code": status,
        "close_order": order,
        **exception_fields(error),
    }


def _close_code(close: object | None) -> int | None:
    code = getattr(close, "code", None) if close is not None else None
    if type(code) is int and 1000 <= code <= 4999 and code not in {1005, 1006, 1015}:
        return code
    return None


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short diagnostics write")
        view = view[written:]


def _restrict_file_mode(descriptor: int, path: Path) -> None:
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, 0o600)
    else:
        os.chmod(path, 0o600)


def _validate(record: dict[str, Any]) -> None:
    if (
        type(record.get("schema")) is not int
        or record["schema"] != SCHEMA
        or type(record.get("sequence")) is not int
        or record["sequence"] < 0
    ):
        raise ValueError("invalid envelope")
    event = record.get("event")
    if record.get("component") not in _ENUMS["component"] or event not in _EVENTS:
        raise ValueError("invalid event")
    required_envelope = {
        "schema",
        "time_utc",
        "sequence",
        "elapsed_ms",
        "event_id",
        "process_id",
        "component",
        "event",
        "build_ref",
        "runtime_version",
        "clock_source",
        "provenance_status",
    }
    allowed = _ENVELOPE_FIELDS | _EVENTS[event] | _OPTIONAL.get(event, set())
    if (
        set(record) - allowed
        or not required_envelope.issubset(record)
        or not _EVENTS[event].issubset(record)
    ):
        raise ValueError("invalid event fields")
    for key, choices in _ENUMS.items():
        if key in record and (
            type(record[key]) is not str or record[key] not in choices
        ):
            raise ValueError("invalid enum")
    for key, regex in (
        ("process_id", _PROCESS),
        ("build_ref", _PROCESS),
        ("connection_id", _CONNECTION),
        ("peer_connection_id", _CONNECTION),
        ("home_connection_id", _CONNECTION),
        ("correlation_id", _CORRELATION),
        ("request_id", _REQUEST),
    ):
        if (
            key in record
            and record[key] is not None
            and (type(record[key]) is not str or regex.fullmatch(record[key]) is None)
        ):
            raise ValueError("invalid identifier")
    if (
        type(record.get("event_id")) is not str
        or re.fullmatch(r"evt-[0-9a-f]{32}", record["event_id"]) is None
    ):
        raise ValueError("invalid event ID")
    if (
        record["build_ref"] != record["process_id"]
        or record["clock_source"] != "system"
    ):
        raise ValueError("invalid provenance")
    if (
        type(record["runtime_version"]) is not str
        or _VERSION.fullmatch(record["runtime_version"]) is None
    ):
        raise ValueError("invalid runtime version")
    for key in (
        "sequence",
        "elapsed_ms",
        "duration_ms",
        "pending_count",
        "build_number",
        "queue_dropped",
        "schema_rejected",
        "sink_failed",
        "rotation_evicted",
        "correlation_conflicts",
    ):
        value = record.get(key)
        if value is not None and (
            type(value) is not int or not 0 <= value <= 2**63 - 1
        ):
            raise ValueError("invalid count")
    for key in ("sent_close_code", "received_close_code"):
        value = record.get(key)
        if value is not None and (
            type(value) is not int
            or not 1000 <= value <= 4999
            or value in {1005, 1006, 1015}
        ):
            raise ValueError("invalid frame code")
    if "observed_status_code" in record:
        value = record["observed_status_code"]
        if value is not None and (type(value) is not int or not 1000 <= value <= 4999):
            raise ValueError("invalid status code")
    if type(record.get("time_utc")) is not str or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", record["time_utc"]
    ):
        raise ValueError("invalid timestamp")
    datetime.strptime(record["time_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )
    for key in ("app_version", "websocket_version"):
        if record.get(key) is not None and (
            type(record[key]) is not str or _VERSION.fullmatch(record[key]) is None
        ):
            raise ValueError("invalid version")
    if record.get("source_revision") is not None and (
        type(record["source_revision"]) is not str
        or _REVISION.fullmatch(record["source_revision"]) is None
    ):
        raise ValueError("invalid revision")
    if record.get("artifact_sha256") is not None and (
        type(record["artifact_sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", record["artifact_sha256"]) is None
    ):
        raise ValueError("invalid digest")
    for key in ("unavailable_latch", "uncertain", "pending_saturated"):
        if key in record and type(record[key]) is not bool:
            raise ValueError("invalid boolean")
    if "settings" in record:
        settings = record["settings"]
        if type(settings) is not dict or set(settings) - {
            "ping_interval",
            "ping_timeout",
            "close_timeout",
        }:
            raise ValueError("invalid settings")
        if any(
            type(value) not in (int, float)
            or not 0 <= value <= 86400
            or not math.isfinite(value)
            for value in settings.values()
            if value is not None
        ):
            raise ValueError("invalid settings")


class OperationalDiagnostics:
    """Best-effort JSONL sink with bounded nonblocking submission."""

    def __init__(
        self,
        *,
        component: str,
        directory: str | Path,
        source_files: tuple[str | Path, ...] = (),
        app_version: str | None = None,
        source_revision: str | None = None,
        websocket_version: str | None = None,
        settings: dict[str, int | float | None] | None = None,
        queue_capacity: int = QUEUE_CAPACITY,
        active_bytes: int = ACTIVE_BYTES,
        backups: int = BACKUP_COUNT,
        retention_seconds: int = RETENTION_SECONDS,
        clock: Any = time.time,
        monotonic: Any = time.monotonic,
    ):
        if (
            component not in _ENUMS["component"]
            or type(queue_capacity) is not int
            or not 1 <= queue_capacity <= QUEUE_CAPACITY
            or type(active_bytes) is not int
            or not MAX_RECORD_BYTES <= active_bytes <= ACTIVE_BYTES
            or type(backups) is not int
            or not 0 <= backups <= BACKUP_COUNT
            or type(retention_seconds) is not int
            or not 1 <= retention_seconds <= RETENTION_SECONDS
        ):
            raise ValueError("invalid diagnostics limits")
        self.component = component
        self.directory = Path(directory)
        self._queue: queue.Queue[bytes | None] = queue.Queue(queue_capacity)
        self._active_bytes, self._backups, self._retention = (
            active_bytes,
            backups,
            retention_seconds,
        )
        self._clock, self._monotonic, self._started = clock, monotonic, monotonic()
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._last_loss_emit = self._monotonic()
        self._loss = Counter(
            queue_dropped=0,
            schema_rejected=0,
            sink_failed=0,
            rotation_evicted=0,
            correlation_conflicts=0,
        )
        self._sink_state = "ok"
        self._closing = False
        self.process_id = f"proc-{uuid.uuid4().hex}"
        self._app_version = app_version if _version_ok(app_version) else None
        self._source_revision = (
            source_revision
            if type(source_revision) is str and _REVISION.fullmatch(source_revision)
            else None
        )
        self._websocket_version = (
            websocket_version if _version_ok(websocket_version) else None
        )
        settings_values = settings if isinstance(settings, dict) else {}
        allowed_settings = {"ping_interval", "ping_timeout", "close_timeout"}
        self._settings = {
            key: value
            for key, value in settings_values.items()
            if key in allowed_settings
            and (
                value is None
                or (
                    type(value) in (int, float)
                    and 0 <= value <= 86400
                    and math.isfinite(value)
                )
            )
        }
        self._provenance = self._provenance_for(source_files)
        self._active_path = self.directory / f"{component}.jsonl"
        self._header = self._encode(
            self._base("provenance_header", leg="process", phase="startup")
        )
        self._worker = threading.Thread(
            target=self._run, name=f"{component}-diagnostics", daemon=True
        )
        self._worker.start()
        self.emit("process_started", leg="process", phase="startup")

    def _provenance_for(self, sources: tuple[str | Path, ...]) -> dict[str, Any]:
        digest = hashlib.sha256()
        artifact = None
        try:
            paths = sorted(
                (Path(source) for source in sources),
                key=lambda path: path.as_posix(),
            )
            paths = [path for path in paths if path.is_file()]
            if paths:
                root = Path(os.path.commonpath([str(path.parent) for path in paths]))
                for path in sorted(
                    paths,
                    key=lambda item: item.relative_to(root).as_posix(),
                ):
                    name = path.relative_to(root).as_posix().encode("utf-8")
                    file_digest = hashlib.sha256()
                    size = 0
                    with path.open("rb") as stream:
                        while chunk := stream.read(65536):
                            file_digest.update(chunk)
                            size += len(chunk)
                    digest.update(len(name).to_bytes(4, "big"))
                    digest.update(name)
                    digest.update(size.to_bytes(8, "big"))
                    digest.update(file_digest.digest())
                artifact = digest.hexdigest()
        except (OSError, ValueError):
            artifact = None
        # A configured checkout revision is not a binding to loaded bytes.
        return {
            "artifact_sha256": artifact,
            "source_revision": self._source_revision,
            "provenance_status": (
                "unverified" if artifact or self._source_revision else "unavailable"
            ),
        }

    def _base(self, event: str, **fields: Any) -> dict[str, Any]:
        if event not in _EVENTS or set(fields) - (
            _EVENTS[event] | _OPTIONAL.get(event, set())
        ):
            raise ValueError("invalid event fields")
        with self._sequence_lock:
            self._sequence += 1
            sequence = self._sequence
        timestamp = (
            datetime.fromtimestamp(
                self._clock(),
                timezone.utc,
            )
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        record = {
            "schema": SCHEMA,
            "time_utc": timestamp,
            "sequence": sequence,
            "elapsed_ms": max(0, int((self._monotonic() - self._started) * 1000)),
            "event_id": f"evt-{uuid.uuid4().hex}",
            "process_id": self.process_id,
            "component": self.component,
            "event": event,
            "build_ref": self.process_id,
            "runtime_version": platform.python_version(),
            "clock_source": "system",
            **self._provenance,
        }
        if self._app_version:
            record["app_version"] = self._app_version
        if self._websocket_version:
            record["websocket_version"] = self._websocket_version
        if self._settings:
            record["settings"] = dict(self._settings)
        record.update(
            {key: value for key, value in fields.items() if value is not None}
        )
        return record

    @staticmethod
    def _encode(record: dict[str, Any]) -> bytes:
        return (
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        ).encode("utf-8")

    def emit(self, event: str, **fields: Any) -> bool:
        if self._closing:
            self._add_loss("queue_dropped")
            return False
        try:
            record = self._base(event, **fields)
            _validate(record)
            encoded = self._encode(record)
            if len(encoded) > MAX_RECORD_BYTES:
                raise ValueError("record size")
        except (ValueError, TypeError, OverflowError):
            self._add_loss("schema_rejected")
            return False
        try:
            self._queue.put_nowait(encoded)
            return True
        except queue.Full:
            self._add_loss("queue_dropped")
            return False

    def _add_loss(self, key: str, count: int = 1) -> None:
        if key not in self._loss or type(count) is not int or count <= 0:
            return
        with self._state_lock:
            self._loss[key] = min(2**63 - 1, self._loss[key] + count)
            self._sink_state = "degraded"

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                **self._loss,
                "sink_state": self._sink_state,
                "queue_depth": self._queue.qsize(),
            }

    def _run(self) -> None:
        last_sweep = self._monotonic()
        try:
            self._purge_old(self._clock())
        except Exception:  # noqa: BLE001 - diagnostics sink failure is isolated
            self._add_loss("sink_failed")
            with self._state_lock:
                self._sink_state = "unavailable"
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                try:
                    if self._monotonic() - last_sweep >= 60:
                        self._purge_old(self._clock())
                        last_sweep = self._monotonic()
                        self._emit_loss_snapshot()
                except Exception:  # noqa: BLE001 - diagnostics sink failure is isolated
                    self._add_loss("sink_failed")
                    with self._state_lock:
                        self._sink_state = "unavailable"
                continue
            try:
                if item is None:
                    return
                if self._monotonic() - last_sweep >= 60:
                    self._purge_old(self._clock())
                    last_sweep = self._monotonic()
                self._write(item)
                self._emit_loss_snapshot()
            except Exception:  # noqa: BLE001 - diagnostics sink failure is isolated
                self._add_loss("sink_failed")
                with self._state_lock:
                    self._sink_state = "unavailable"
            finally:
                self._queue.task_done()

    def add_loss(self, counter: str, count: int = 1) -> None:
        if (
            counter
            not in {
                "queue_dropped",
                "schema_rejected",
                "sink_failed",
                "rotation_evicted",
                "correlation_conflicts",
            }
            or type(count) is not int
            or count < 0
        ):
            return
        with self._state_lock:
            self._loss[counter] = min(2**63 - 1, self._loss[counter] + count)
            if count:
                self._sink_state = "degraded"

    def _active_file_matches_process(self) -> bool:
        descriptor = os.open(
            self._active_path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            line = os.read(descriptor, MAX_RECORD_BYTES + 1).split(b"\n", 1)[0]
        finally:
            os.close(descriptor)
        if not line:
            return True
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return (
            isinstance(record, dict)
            and record.get("event") == "provenance_header"
            and record.get("process_id") == self.process_id
        )

    def _write(self, encoded: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        if self._active_path.exists() and not self._active_file_matches_process():
            self._rotate()
        if (
            self._active_path.exists()
            and self._active_path.stat().st_size + len(encoded) > self._active_bytes
        ):
            self._rotate()
        if len(self._header) + len(encoded) > self._active_bytes:
            self._add_loss("schema_rejected")
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self._active_path, flags, 0o600)
        try:
            _restrict_file_mode(descriptor, self._active_path)
            if os.fstat(descriptor).st_size == 0:
                _write_all(descriptor, self._header)
            _write_all(descriptor, encoded)
        finally:
            os.close(descriptor)
        with self._state_lock:
            self._sink_state = "ok"

    def _emit_loss_snapshot(self) -> None:
        now = self._monotonic()
        if now - self._last_loss_emit < 60:
            return
        status = self.status()
        counters = {
            key: status[key]
            for key in (
                "queue_dropped",
                "schema_rejected",
                "sink_failed",
                "rotation_evicted",
                "correlation_conflicts",
            )
        }
        if not any(counters.values()):
            return
        record = self._base(
            "diagnostics_loss",
            **counters,
            sink_state="degraded",
        )
        _validate(record)
        encoded = self._encode(record)
        if len(encoded) > MAX_RECORD_BYTES:
            return
        try:
            self._queue.put_nowait(encoded)
        except queue.Full:
            self._add_loss("queue_dropped")
            return
        self._last_loss_emit = now

    def _rotate(self) -> None:
        oldest = self.directory / f"{self._active_path.name}.{self._backups}"
        if self._backups and oldest.exists():
            oldest.unlink()
            self._add_loss("rotation_evicted")
        for index in range(self._backups - 1, 0, -1):
            source = self.directory / f"{self._active_path.name}.{index}"
            if source.exists():
                source.replace(self.directory / f"{self._active_path.name}.{index + 1}")
        if self._active_path.exists():
            if self._backups:
                self._active_path.replace(
                    self.directory / f"{self._active_path.name}.1"
                )
            else:
                self._active_path.unlink()
                self._add_loss("rotation_evicted")

    def _purge_old(self, now: float) -> None:
        if not self.directory.exists():
            return
        cutoff = now - self._retention
        paths = [self._active_path] + [
            self.directory / f"{self._active_path.name}.{index}"
            for index in range(1, self._backups + 1)
        ]
        for path in paths:
            try:
                if not path.exists() and not path.is_symlink():
                    continue
                if path.is_symlink():
                    path.unlink()
                    self._add_loss("rotation_evicted")
                    continue
                stat = path.stat()
                if stat.st_size > self._active_bytes:
                    path.unlink()
                    self._add_loss("rotation_evicted")
                    continue
                contents = path.read_bytes()
                lines = contents.splitlines()
                if lines and not contents.endswith(b"\n"):
                    lines.pop()
                if not lines:
                    path.unlink()
                    self._add_loss("rotation_evicted")
                    continue
                oldest_event = None
                invalid_line = False
                for line in lines[1:]:
                    try:
                        record = json.loads(line)
                        timestamp = record.get("time_utc")
                        event_time = datetime.fromisoformat(
                            timestamp.replace("Z", "+00:00")
                        ).timestamp()
                    except (
                        AttributeError,
                        TypeError,
                        ValueError,
                        OverflowError,
                        json.JSONDecodeError,
                    ):
                        invalid_line = True
                        continue
                    oldest_event = (
                        event_time
                        if oldest_event is None
                        else min(oldest_event, event_time)
                    )
                expired = invalid_line or (
                    oldest_event < cutoff
                    if oldest_event is not None
                    else stat.st_mtime < cutoff
                )
                if expired:
                    dropped_records = max(1, len(lines) - 1)
                    path.unlink()
                    self._add_loss("rotation_evicted", dropped_records)
            except OSError:
                self._add_loss("sink_failed")

    def close(self, timeout: float = 0.1) -> None:
        with self._state_lock:
            if self._closing:
                return
            self._closing = True
        record = self._base(
            "process_stopping",
            initiator="process_shutdown",
            leg="process",
            phase="shutdown",
        )
        _validate(record)
        stopping = self._encode(record)
        try:
            self._queue.put_nowait(stopping)
        except queue.Full:
            self._add_loss("queue_dropped")
        safe_timeout = (
            max(0.0, min(timeout, 0.1))
            if type(timeout) in (int, float) and math.isfinite(timeout)
            else 0.0
        )
        deadline = time.monotonic() + safe_timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(min(0.001, max(0.0, deadline - time.monotonic())))
        if self._queue.unfinished_tasks:
            dropped = 0
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    dropped += 1
                self._queue.task_done()
            if dropped:
                self._add_loss("queue_dropped", dropped)
        try:
            self._queue.put_nowait(None)
        except queue.Full:  # pragma: no cover - queue was drained above
            self._add_loss("queue_dropped")
        self._worker.join(max(0.0, deadline - time.monotonic()))


def _version_ok(value: str | None) -> bool:
    return value is None or (
        type(value) is str and _VERSION.fullmatch(value) is not None
    )


class SafeTransportLogHandler(logging.Handler):
    """Discard prose; only use structured exception information."""

    def __init__(
        self,
        diagnostics: OperationalDiagnostics,
        *,
        leg: str,
        connection_id: str | None = None,
    ):
        super().__init__()
        self.diagnostics = diagnostics
        self.leg = leg
        self.connection_id = (
            connection_id
            if type(connection_id) is str and _CONNECTION.fullmatch(connection_id)
            else None
        )

    def emit(self, record: logging.LogRecord) -> None:
        error = (
            record.exc_info[1]
            if record.exc_info and isinstance(record.exc_info[1], BaseException)
            else None
        )
        if error is None:
            return
        details = exception_fields(error)
        classification = (
            "timeout_during_close"
            if details["exception_category"] == "connection_closed_ok"
            and details["cause_category"] == "timeout"
            else "unknown"
        )
        self.diagnostics.emit(
            "transport_observed",
            leg=self.leg,
            connection_id=self.connection_id,
            correlation_state="unavailable",
            phase="unknown",
            initiator="unknown",
            close_trigger="unknown",
            classification=classification,
            pending_state="unknown",
            **close_fields(error=error),
        )
