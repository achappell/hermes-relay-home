"""Bounded, device-authenticated connection reports. No free-text event fields."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from threading import RLock

MAX_BODY = 65536
RETENTION = 7 * 86400
NAMES = frozenset(
    {
        "launch",
        "active",
        "inactive",
        "background",
        "connection_lost",
        "connection_ready",
        "connection_failed",
        "request_started",
        "request_completed",
        "request_failed",
    }
)
CODES = frozenset(
    {
        "unknown",
        "transport_unavailable",
        "transport_timeout",
        "hermes_unavailable",
        "protocol_error",
        "conversation_mismatch",
        "stale_conversation",
        "request_rejected",
        "reconnect_required",
        "unauthorized",
        "forbidden",
        "capability_unavailable",
        "invalid_request",
    }
)


def validate_report(body: bytes | str, now: float) -> dict:
    if len(body if isinstance(body, bytes) else body.encode()) > MAX_BODY:
        raise ValueError("report too large")
    value = json.loads(body)
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "report_id",
        "created_at",
        "app_version",
        "build",
        "platform",
        "os_version",
        "model",
        "events",
    }:
        raise ValueError("invalid report")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise ValueError("invalid schema")
    _uuid(value["report_id"])
    _timestamp(value["created_at"], now)
    for key in ("app_version", "build", "os_version"):
        if not isinstance(value[key], str) or not re.fullmatch(
            r"[0-9]{1,8}(?:\.[0-9]{1,8}){0,3}", value[key]
        ):
            raise ValueError("invalid version")
    if value["platform"] not in {"ios", "macos"}:
        raise ValueError("invalid platform")
    if not isinstance(value["model"], str) or not re.fullmatch(
        r"(?:iPhone|iPad|Mac)[0-9]{1,3},[0-9]{1,3}|arm64|x86_64|unknown", value["model"]
    ):
        raise ValueError("invalid model")
    events = value["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= 100:
        raise ValueError("invalid events")
    for event in events:
        if (
            not isinstance(event, dict)
            or set(event)
            - {"time", "name", "launch_id", "code", "duration_ms", "phase", "uncertain"}
            or not {"time", "name", "launch_id"} <= set(event)
        ):
            raise ValueError("invalid event")
        _timestamp(event["time"], now)
        _uuid(event["launch_id"])
        if event["name"] not in NAMES:
            raise ValueError("invalid event name")
        if "phase" in event and event["phase"] not in {
            "open",
            "reconnect",
            "submission",
            "lifecycle",
        }:
            raise ValueError("invalid phase")
        if "uncertain" in event and type(event["uncertain"]) is not bool:
            raise ValueError("invalid uncertainty")
        if "code" in event and event["code"] not in CODES:
            raise ValueError("invalid failure code")
        if "duration_ms" in event and (
            type(event["duration_ms"]) is not int
            or not 0 <= event["duration_ms"] <= 86400000
        ):
            raise ValueError("invalid duration")
    return value


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
        raise ValueError("invalid identifier")


def _timestamp(value, now):
    if type(value) not in (int, float) or not now - RETENTION <= value <= now + 300:
        raise ValueError("invalid timestamp")


class ClientReportStore:
    """One SQLite table, capped globally and per device, expired on every access."""

    def __init__(self, database: str | Path = ":memory:", *, clock=time.time):
        self._clock = clock
        self._lock = RLock()
        self._db = sqlite3.connect(str(database), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS client_diagnostic_reports (device_id TEXT NOT NULL, report_id TEXT NOT NULL, received_at REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(device_id, report_id))"
        )
        self._db.commit()

    def receive(self, device_id: str, body: bytes | str) -> str:
        now = self._clock()
        report = validate_report(body, now)
        report_id = report["report_id"]
        with self._lock, self._db:
            self._purge(now)
            if self._db.execute(
                "SELECT 1 FROM client_diagnostic_reports WHERE device_id=? AND report_id=?",
                (device_id, report_id),
            ).fetchone():
                return report_id
            latest = self._db.execute(
                "SELECT MAX(received_at) FROM client_diagnostic_reports WHERE device_id=?",
                (device_id,),
            ).fetchone()[0]
            if latest is not None and now - latest < 30:
                raise ReportRateLimited()
            self._db.execute(
                "INSERT INTO client_diagnostic_reports VALUES (?, ?, ?, ?)",
                (device_id, report_id, now, json.dumps(report, separators=(",", ":"))),
            )
            self._db.execute(
                "DELETE FROM client_diagnostic_reports WHERE device_id=? AND rowid NOT IN (SELECT rowid FROM client_diagnostic_reports WHERE device_id=? ORDER BY received_at DESC, rowid DESC LIMIT 100)",
                (device_id, device_id),
            )
            self._db.execute(
                "DELETE FROM client_diagnostic_reports WHERE rowid NOT IN (SELECT rowid FROM client_diagnostic_reports ORDER BY received_at DESC, rowid DESC LIMIT 1000)"
            )
        return report_id

    def recent(self) -> dict:
        with self._lock, self._db:
            self._purge(self._clock())
            rows = self._db.execute(
                "SELECT device_id, received_at, payload FROM client_diagnostic_reports ORDER BY received_at DESC, rowid DESC LIMIT 50"
            ).fetchall()
        return {
            "schema": 1,
            "reports": [
                {
                    "device_id": device,
                    "received_at": received,
                    "report": json.loads(payload),
                }
                for device, received, payload in rows
            ],
        }

    def _purge(self, now):
        self._db.execute(
            "DELETE FROM client_diagnostic_reports WHERE received_at < ?",
            (now - RETENTION,),
        )

    def close(self):
        with self._lock:
            self._db.close()


class ReportRateLimited(Exception):
    pass
