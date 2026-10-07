"""Persistent, rotating, content-free logging for qwen3_streaming_server.py.

Installed by two calls in the server's main():

    install_logging()                        # right after logging.basicConfig
    start_health_monitor(service, args)      # once the service is built

What it does
- Adds a RotatingFileHandler (10 MB x 5) at ``<server dir>/logs/qwen3-streaming.log``
  (override the directory with QWEN3_LOG_DIR). The file survives restarts.
- Replaces traceback formatting on every root handler so exception *messages*
  are never written (a provider error can echo the synthesized text). Frames and
  the exception type are kept.
- Drops successful ``GET /healthz`` access lines (polled every ~10 s by several
  hosts) and counts them into the periodic health line instead. Non-200 healthz
  responses are still logged.
- Emits ``event=process_start`` / ``event=server_ready`` / ``event=server_exit`` and a periodic
  ``event=health`` line (every 60 s idle, every 10 s while a request holds the
  generation lock) with GPU/CPU/memory, the active request id and age, and the
  time since the last audio chunk / socket write finished.

Nothing here logs request text, prompts, or audio content; per-request timing
lines are produced by the server itself (text length only).
"""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

SERVER_LOGGER_NAME = "qwen3_tts_streaming_server"
LOG_FILE_NAME = "qwen3-streaming.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
IDLE_HEALTH_INTERVAL_S = 60.0
BUSY_HEALTH_INTERVAL_S = 10.0
TICK_S = 2.0

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_REQUEST_EVENT = re.compile(r"request=([0-9a-f]+) event=(request_start|response_complete)\b")
_HEALTHZ_OK = re.compile(r'"GET /healthz HTTP/[0-9.]+" 200\b')


class ContentFreeFormatter(logging.Formatter):
    """Standard format, but tracebacks carry no exception messages."""

    def formatException(self, ei) -> str:  # noqa: N802 - logging API
        out: list[str] = []
        seen: set[int] = set()

        def walk(exc: BaseException | None) -> None:
            if exc is None or id(exc) in seen:
                return
            seen.add(id(exc))
            walk(exc.__cause__ if exc.__cause__ is not None
                 else (None if exc.__suppress_context__ else exc.__context__))
            out.append("Traceback (most recent call last):")
            for frame in traceback.extract_tb(exc.__traceback__):
                out.append(f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}')
                if frame.line:
                    out.append(f"    {frame.line}")
            kind = type(exc)
            out.append(f"{kind.__module__}.{kind.__qualname__}: <message omitted>")

        walk(ei[1])
        return "\n".join(out)


class _State:
    """Shared, lock-free-by-convention counters (single writer per field)."""

    def __init__(self) -> None:
        self.started_monotonic = time.monotonic()
        self.healthz_ok = 0
        self.requests_total = 0
        self.active_request: str | None = None
        self.active_since = 0.0
        self.last_chunk = 0.0
        self.last_write_done = 0.0


STATE = _State()


class _AccessFilter(logging.Filter):
    """Counts/ drops healthz-200 lines and tracks the active request id."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:
            return True
        if _HEALTHZ_OK.search(message):
            STATE.healthz_ok += 1
            return False
        match = _REQUEST_EVENT.search(message)
        if match:
            request_id, event = match.groups()
            now = time.monotonic()
            if event == "request_start":
                STATE.requests_total += 1
                STATE.active_request = request_id
                STATE.active_since = now
            elif STATE.active_request == request_id:
                STATE.active_request = None
        return True


def _log_dir() -> Path:
    configured = os.environ.get("QWEN3_LOG_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parent / "logs"


def install_logging() -> Path:
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME
    root = logging.getLogger()
    formatter = ContentFreeFormatter(_FORMAT)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    root.addHandler(file_handler)
    for handler in root.handlers:
        handler.setFormatter(formatter)
    logging.getLogger(SERVER_LOGGER_NAME).addFilter(_AccessFilter())
    server_log = logging.getLogger(SERVER_LOGGER_NAME)
    server_log.info(
        "event=process_start pid=%d python=%s log_file=%s rotation=%dMBx%d",
        os.getpid(), sys.version.split()[0], log_path, MAX_BYTES // 1048576, BACKUP_COUNT,
    )
    atexit.register(lambda: server_log.info("event=server_exit pid=%d", os.getpid()))
    return log_path


def _gpu_fields() -> str:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        parts = [p.strip() for p in result.stdout.strip().splitlines()[0].split(",")]
        util, mem_used, mem_total, temp, power = parts[:5]
        return (f"gpu_util_pct={util} gpu_mem_mb={mem_used}/{mem_total} "
                f"gpu_temp_c={temp} gpu_power_w={power}")
    except Exception as exc:
        return f"gpu=unavailable({type(exc).__name__})"


def _instrument(service) -> None:
    """Stamp last-chunk / last-write times without touching the synthesis code."""
    metrics = getattr(service, "metrics", None)
    for name, field in (("stream_chunk_observed", "last_chunk"),
                        ("stream_http_write_observed", "last_write_done")):
        original = getattr(metrics, name, None)
        if original is None:
            continue

        def wrapped(*args, _original=original, _field=field, **kwargs):
            setattr(STATE, _field, time.monotonic())
            return _original(*args, **kwargs)

        setattr(metrics, name, wrapped)


def _health_loop(service, model: str, log: logging.Logger) -> None:
    try:
        import psutil
        process = psutil.Process()
        process.cpu_percent(None)
        psutil.cpu_percent(None)
    except Exception:
        psutil = None  # type: ignore[assignment]
        process = None
    lock = getattr(service, "_generation_lock", None)
    last_emit = 0.0
    while True:
        time.sleep(TICK_S)
        now = time.monotonic()
        busy = bool(lock is not None and lock.locked())
        interval = BUSY_HEALTH_INTERVAL_S if busy else IDLE_HEALTH_INTERVAL_S
        if now - last_emit < interval:
            continue
        last_emit = now
        active = STATE.active_request
        fields = [
            "event=health",
            f"pid={os.getpid()}",
            f"model={model}",
            f"uptime_s={now - STATE.started_monotonic:.0f}",
            f"busy={int(busy)}",
            f"active_request={active or 'none'}",
        ]
        if active:
            fields.append(f"active_age_s={now - STATE.active_since:.1f}")
            if STATE.last_chunk >= STATE.active_since:
                fields.append(f"since_chunk_s={now - STATE.last_chunk:.1f}")
            else:
                fields.append("since_chunk_s=none")
            if STATE.last_write_done >= STATE.active_since:
                fields.append(f"since_write_s={now - STATE.last_write_done:.1f}")
        fields.append(f"requests_total={STATE.requests_total}")
        fields.append(f"healthz_ok_total={STATE.healthz_ok}")
        if process is not None:
            try:
                fields.append(f"proc_cpu_pct={process.cpu_percent(None):.0f}")
                fields.append(f"sys_cpu_pct={psutil.cpu_percent(None):.0f}")
                fields.append(f"rss_mb={process.memory_info().rss / 1048576:.0f}")
            except Exception:
                pass
        fields.append(_gpu_fields())
        log.info(" ".join(fields))


def start_health_monitor(service, args) -> None:
    log = logging.getLogger(SERVER_LOGGER_NAME)
    try:
        import torch
        torch_info = f"torch={torch.__version__} cuda={torch.version.cuda}"
    except Exception:
        torch_info = "torch=unknown"
    model = str(getattr(args, "model", "unknown"))
    log.info(
        "event=server_ready pid=%d %s model=%s default_voice=%s device=%s dtype=%s "
        "compile_mode=%s optimizations=%s port=%s",
        os.getpid(),
        torch_info,
        model,
        getattr(service, "default_voice", "?"),
        getattr(args, "device", "?"),
        getattr(args, "dtype", "?"),
        getattr(args, "compile_mode", "?"),
        getattr(args, "streaming_optimizations", "?"),
        getattr(args, "port", "?"),
    )
    _instrument(service)
    threading.Thread(
        target=_health_loop,
        args=(service, model, log),
        name="qwen-health-log",
        daemon=True,
    ).start()
