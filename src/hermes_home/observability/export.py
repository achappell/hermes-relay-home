"""Local append-only JSONL export of safe events and client reports.

This is the concrete collector behind HOME-NW-06-diagnostics-exporter. It is a
Home-host-local store, not a remote service: an acknowledgement means the
batch was written and ``fsync``-ed locally. Nothing here retries, replays or
blocks live conversation work; every failure is isolated, counted, and left
for the bounded queue and status to report honestly.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from queue import Full, Queue

from hermes_home.observability.client_reports import RETENTION as _REPORT_RETENTION
from hermes_home.observability.diagnostics import (
    EVENT_RETENTION_SECONDS,
    DiagnosticEvent,
    UploadAcknowledgement,
)
from hermes_home.observability.metrics import MetricsRegistry

SAFE_EVENT = "safe_event"
CLIENT_REPORT = "client_report"
BATCH_LEDGER = "batch_ledger"

SAFE_EVENT_RETENTION_SECONDS = EVENT_RETENTION_SECONDS
CLIENT_REPORT_RETENTION_SECONDS = _REPORT_RETENTION

MAX_BATCH_EVENTS = 256
MAX_BATCH_BYTES = 1_048_576
MAX_REMEMBERED_KEYS = 4096
DEFAULT_MAX_FILE_BYTES = 4 * 1_048_576
DEFAULT_MAX_FILES = 8
DEFAULT_INTERVAL_SECONDS = 30.0
DEFAULT_BACKOFF_MIN_SECONDS = 30.0
DEFAULT_BACKOFF_MAX_SECONDS = 900.0
DEFAULT_SWEEP_INTERVAL_SECONDS = 3600.0
DEFAULT_MAX_BATCHES_PER_TICK = 32
DEFAULT_SHUTDOWN_SECONDS = 2.0

_PREFIX = {SAFE_EVENT: "safe-events", CLIENT_REPORT: "client-reports"}
_RETENTION = {
    SAFE_EVENT: SAFE_EVENT_RETENTION_SECONDS,
    CLIENT_REPORT: CLIENT_REPORT_RETENTION_SECONDS,
}
_DAY_SECONDS = 24 * 60 * 60


class ExportError(RuntimeError):
    """The local export store could not durably accept a write."""


def encode_record(record: Mapping[str, object]) -> str:
    """Return one compact, finite-number JSON line (without the newline)."""
    return json.dumps(record, separators=(",", ":"), sort_keys=True, allow_nan=False)


class JsonlExportStore:
    """Size- and day-rotated JSONL files, one stream per record class.

    File names are ``<prefix>.<UTC day>.<seq>.jsonl``. Retention deletes a file
    only when every line it could hold is already past the stream's retention,
    so no line younger than retention is ever removed by the sweep.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_files: int = DEFAULT_MAX_FILES,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        if type(max_file_bytes) is not int or max_file_bytes < 1:
            raise ValueError("export file size bound must be positive")
        if type(max_files) is not int or max_files < 1:
            raise ValueError("export file count bound must be positive")
        self._directory = Path(directory)
        self._clock = clock
        self._max_file_bytes = max_file_bytes
        self._max_files = max_files
        self._metrics = metrics
        self._lock = threading.RLock()
        self._unverified_tails: set[Path] = set()
        self._patterns = {
            stream: re.compile(rf"^{re.escape(prefix)}\.(\d{{8}})\.(\d{{4}})\.jsonl$")
            for stream, prefix in _PREFIX.items()
        }
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            # Home must still start; the first write retries and reports.
            del error

    @property
    def directory(self) -> Path:
        return self._directory

    def write(
        self,
        stream: str,
        lines: Sequence[str],
        *,
        records: int | None = None,
    ) -> None:
        """Append lines durably (written and ``fsync``-ed) or raise ExportError."""
        if stream not in _PREFIX:
            raise ValueError("unknown export stream")
        payload = b"".join(_line_bytes(line) for line in lines)
        if not payload:
            return
        try:
            with self._lock:
                self._directory.mkdir(parents=True, exist_ok=True)
                path, created = self._active_path(stream, len(payload))
                self._append(path, payload)
                if created:
                    _fsync_directory(self._directory)
                self._enforce_capacity(stream)
        except OSError as error:
            self._inc("hermes_home_export_write_failures_total", stream)
            raise ExportError("export store write failed") from error
        self._inc(
            "hermes_home_export_records_written_total",
            stream,
            value=len(lines) if records is None else records,
        )

    def sweep(self) -> int:
        """Delete files wholly past each stream's retention; return the count."""
        threshold_base = self._clock()
        removed = 0
        with self._lock:
            for stream, retention in _RETENTION.items():
                for path, day, _ in self._files(stream):
                    if _day_end(day) > threshold_base - retention:
                        continue
                    try:
                        path.unlink()
                    except OSError:
                        continue
                    removed += 1
                    self._inc(
                        "hermes_home_export_files_removed_total",
                        stream,
                        reason="retention",
                    )
        return removed

    def read_ledger_keys(self, limit: int) -> list[str]:
        """Return up to ``limit`` recent batch idempotency keys, oldest first."""
        found: list[str] = []
        with self._lock:
            for path, _, _ in reversed(self._files(SAFE_EVENT)):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line in reversed(text.splitlines()):
                    if '"batch_ledger"' not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    key = record.get("idempotency_key")
                    if record.get("record_type") == BATCH_LEDGER and isinstance(
                        key, str
                    ):
                        found.append(key)
                        if len(found) >= limit:
                            return found[::-1]
        return found[::-1]

    def _files(self, stream: str) -> list[tuple[Path, str, int]]:
        pattern = self._patterns[stream]
        entries: list[tuple[Path, str, int]] = []
        try:
            names = os.listdir(self._directory)
        except OSError:
            return []
        for name in names:
            match = pattern.match(name)
            if match:
                entries.append((self._directory / name, match[1], int(match[2])))
        entries.sort(key=lambda entry: (entry[1], entry[2]))
        return entries

    def _active_path(self, stream: str, incoming: int) -> tuple[Path, bool]:
        day = datetime.fromtimestamp(self._clock(), UTC).strftime("%Y%m%d")
        todays = [entry for entry in self._files(stream) if entry[1] == day]
        prefix = _PREFIX[stream]
        if todays:
            path, _, seq = todays[-1]
            size = path.stat().st_size
            if size == 0 or size + incoming <= self._max_file_bytes:
                return path, False
            seq += 1
        else:
            seq = 1
        if seq > 9999:
            raise OSError("export file sequence exhausted for the day")
        return self._directory / f"{prefix}.{day}.{seq:04d}.jsonl", True

    def _append(self, path: Path, payload: bytes) -> None:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            if path in self._unverified_tails:
                payload = _heal_tail(path) + payload
            view = memoryview(payload)
            try:
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
                os.fsync(descriptor)
            except OSError:
                self._unverified_tails.add(path)
                raise
            self._unverified_tails.discard(path)
        finally:
            os.close(descriptor)

    def _enforce_capacity(self, stream: str) -> None:
        files = self._files(stream)
        for path, _, _ in files[: max(0, len(files) - self._max_files)]:
            try:
                path.unlink()
            except OSError:
                continue
            self._inc(
                "hermes_home_export_files_removed_total",
                stream,
                reason="capacity",
            )

    def _inc(
        self, name: str, stream: str, *, value: float = 1.0, **labels: str
    ) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics.inc(
                name, labels={"record_type": stream, **labels}, value=value
            )
        except Exception as error:  # noqa: BLE001 - metrics are best effort
            del error


class FileEventCollector:
    """``EventCollector`` writing safe events and a batch ledger to the store."""

    def __init__(
        self,
        store: JsonlExportStore,
        *,
        max_batch_events: int = MAX_BATCH_EVENTS,
        max_batch_bytes: int = MAX_BATCH_BYTES,
        max_keys: int = MAX_REMEMBERED_KEYS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._max_batch_events = max_batch_events
        self._max_batch_bytes = max_batch_bytes
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: OrderedDict[str, None] = OrderedDict.fromkeys(
            store.read_ledger_keys(max_keys)
        )

    def upload(
        self,
        events: Sequence[DiagnosticEvent],
        *,
        idempotency_key: str,
    ) -> UploadAcknowledgement:
        event_ids = tuple(event.event_id for event in events)
        if not events or len(events) > self._max_batch_events:
            raise ExportError("export batch size is outside its bound")
        with self._lock:
            if idempotency_key in self._keys:
                # Already durable: acknowledge exactly, write nothing again.
                return UploadAcknowledgement(idempotency_key, event_ids)
            lines = [
                encode_record({"record_type": SAFE_EVENT, **event.to_dict()})
                for event in events
            ]
            lines.append(
                encode_record(
                    {
                        "record_type": BATCH_LEDGER,
                        "idempotency_key": idempotency_key,
                        "event_ids": list(event_ids),
                        "exported_at": self._clock(),
                    }
                )
            )
            if sum(len(line) + 1 for line in lines) > self._max_batch_bytes:
                raise ExportError("export batch exceeds its byte bound")
            self._store.write(SAFE_EVENT, lines, records=len(events))
            self._keys[idempotency_key] = None
            while len(self._keys) > self._max_keys:
                self._keys.popitem(last=False)
        return UploadAcknowledgement(idempotency_key, event_ids)


class ClientReportExporter:
    """Write accepted client reports off the intake thread; never block it."""

    def __init__(
        self,
        store: JsonlExportStore,
        *,
        metrics: MetricsRegistry | None = None,
        capacity: int = 1024,
    ) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("report export queue capacity must be positive")
        self._store = store
        self._metrics = metrics
        self._queue: Queue[tuple[str, float, Mapping[str, object]] | None] = Queue(
            capacity
        )
        self._closing = False
        self._worker = threading.Thread(
            target=self._run,
            name="hermes-home-client-report-export",
            daemon=True,
        )
        self._worker.start()

    def submit(
        self, device_id: str, received_at: float, report: Mapping[str, object]
    ) -> bool:
        """Queue one accepted report; return False when it was dropped."""
        if self._closing:
            self._drop("closing")
            return False
        try:
            self._queue.put_nowait((device_id, received_at, report))
        except Full:
            self._drop("queue_full")
            return False
        return True

    def close(self, timeout: float = DEFAULT_SHUTDOWN_SECONDS) -> None:
        self._closing = True
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            self._queue.put(None, timeout=max(0.0, deadline - time.monotonic()))
        except Full:
            pass
        self._worker.join(max(0.0, deadline - time.monotonic()))

    def _drop(self, reason: str) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics.inc(
                "hermes_home_export_records_dropped_total",
                labels={"record_type": CLIENT_REPORT, "reason": reason},
            )
        except Exception as error:  # noqa: BLE001 - metrics are best effort
            del error

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                self._write(*item)
            finally:
                self._queue.task_done()

    def _write(
        self, device_id: str, received_at: float, report: Mapping[str, object]
    ) -> None:
        try:
            line = encode_record(
                {
                    "record_type": CLIENT_REPORT,
                    "device_id": device_id,
                    "received_at": received_at,
                    "report": report,
                }
            )
        except TypeError, ValueError:
            self._drop("encode")
            return
        try:
            self._store.write(CLIENT_REPORT, [line])
        except ExportError:
            return  # the store has already counted the failed write


class ExportScheduler:
    """Drain the safe-event queue into the collector on its own thread."""

    def __init__(
        self,
        recorder: object,
        store: JsonlExportStore,
        *,
        metrics: MetricsRegistry | None = None,
        clock: Callable[[], float] = time.time,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        backoff_min_seconds: float = DEFAULT_BACKOFF_MIN_SECONDS,
        backoff_max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS,
        jitter: Callable[[], float] = random.random,
        batch_events: int = MAX_BATCH_EVENTS,
        max_batches_per_tick: int = DEFAULT_MAX_BATCHES_PER_TICK,
        sweep_interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self._recorder = recorder
        self._store = store
        self._metrics = metrics
        self._clock = clock
        self._interval = interval_seconds
        self._backoff_min = backoff_min_seconds
        self._backoff_max = backoff_max_seconds
        self._jitter = jitter
        self._batch_events = batch_events
        self._max_batches = max_batches_per_tick
        self._sweep_interval = sweep_interval_seconds
        self._next_sweep = 0.0
        self._failures = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._final_deadline: float | None = None

    def run_once(self, *, deadline: float | None = None) -> float:
        """Run one drain pass; return the delay before the next pass."""
        self._sweep_if_due()
        if self._drain(deadline):
            self._failures = 0
            return self._interval
        self._failures += 1
        exponent = min(self._failures - 1, 20)
        base = min(self._backoff_max, self._backoff_min * 2**exponent)
        return min(self._backoff_max, base * (0.8 + 0.4 * self._jitter()))

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="hermes-home-diagnostics-export",
            daemon=True,
        )
        self._thread.start()

    def close(self, timeout: float = DEFAULT_SHUTDOWN_SECONDS) -> None:
        """Stop the loop after one bounded final drain; never wait past timeout."""
        thread = self._thread
        if thread is None:
            return
        self._final_deadline = time.monotonic() + max(0.0, timeout)
        self._stop.set()
        thread.join(max(0.0, timeout))

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        delay = 0.0
        while not self._stop.wait(delay):
            try:
                delay = self.run_once()
            except Exception:  # noqa: BLE001 - the export loop must survive
                delay = self._interval
        try:
            self.run_once(deadline=self._final_deadline)
        except Exception as error:  # noqa: BLE001 - shutdown drain is best effort
            del error

    def _drain(self, deadline: float | None) -> bool:
        """Return True when idle or fully drained, False on a failed attempt."""
        recorder = self._recorder
        for _ in range(self._max_batches):
            if deadline is not None and time.monotonic() >= deadline:
                return True
            try:
                result = recorder.flush(limit=self._batch_events)  # type: ignore[attr-defined]
                if result.uploaded_count > 0:
                    self._mark_attempt()
                    continue
                queued = recorder.status().queued_event_count  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - failure is reported, never raised
                self._mark_attempt()
                return False
            if queued == 0:
                return True
            self._mark_attempt()
            return False
        return True

    def _mark_attempt(self) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics.set(
                "hermes_home_diagnostics_export_last_attempt_timestamp_seconds",
                self._clock(),
            )
        except Exception as error:  # noqa: BLE001 - metrics are best effort
            del error

    def _sweep_if_due(self) -> None:
        now = time.monotonic()
        if now < self._next_sweep:
            return
        self._next_sweep = now + self._sweep_interval
        try:
            self._store.sweep()
        except Exception as error:  # noqa: BLE001 - retention is retried next pass
            del error


class DiagnosticsExport:
    """Owned export resources for one Home process."""

    def __init__(
        self,
        directory: str | Path,
        *,
        metrics: MetricsRegistry,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._metrics = metrics
        self._clock = clock
        self._interval = interval_seconds
        self.store = JsonlExportStore(directory, clock=clock, metrics=metrics)
        self.collector = FileEventCollector(self.store, clock=clock)
        self.reports = ClientReportExporter(self.store, metrics=metrics)
        self._scheduler: ExportScheduler | None = None

    def start(self, recorder: object) -> None:
        """Start the independent drain thread for an already built recorder."""
        if self._scheduler is not None:
            return
        self._scheduler = ExportScheduler(
            recorder,
            self.store,
            metrics=self._metrics,
            clock=self._clock,
            interval_seconds=self._interval,
        )
        self._scheduler.start()

    def close(self, timeout: float = DEFAULT_SHUTDOWN_SECONDS) -> None:
        """Final bounded drain and stop; total wait stays near ``timeout``."""
        deadline = time.monotonic() + max(0.0, timeout)
        if self._scheduler is not None:
            self._scheduler.close(timeout)
        self.reports.close(max(0.05, deadline - time.monotonic()))


def _line_bytes(line: str) -> bytes:
    if "\n" in line or "\r" in line:
        raise ValueError("export line must not contain a newline")
    return line.encode("utf-8") + b"\n"


def _day_end(day: str) -> float:
    start = datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC)
    return start.timestamp() + _DAY_SECONDS


def _heal_tail(path: Path) -> bytes:
    """Return a newline when a previous failed write left a partial last line."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                return b""
            handle.seek(-1, os.SEEK_END)
            return b"" if handle.read(1) == b"\n" else b"\n"
    except OSError:
        return b"\n"


def _fsync_directory(directory: Path) -> None:
    if sys.platform == "win32":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError as error:
        del error
    finally:
        os.close(descriptor)
