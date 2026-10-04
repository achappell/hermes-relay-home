"""Bounded, device-authenticated connection reports. No free-text event fields."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, RLock, Thread

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

EVENT_FIELDS = frozenset(
    {"time", "name", "launch_id", "code", "duration_ms", "phase", "uncertain"}
)
SCHEMA2_IDS = {
    "event_id": "evt",
    "connection_id": "conn",
    "home_connection_id": "conn",
    "request_id": "req",
    "correlation_id": "corr",
}
SCHEMA2_ENUMS = {
    "correlation_state": {"local_only", "linked", "ambiguous", "unavailable"},
    "leg": {"client_home", "home_proxy", "proxy_standard", "process"},
    "pending_state": {
        "awaiting_write",
        "awaiting_response",
        "response_resolved",
        "stream_active",
        "none",
        "unknown",
    },
}
CLOSE_FIELDS = frozenset(
    {"sent_close_code", "received_close_code", "observed_status_code"}
)
SCHEMA2_FIELDS = (
    EVENT_FIELDS
    | SCHEMA2_IDS.keys()
    | SCHEMA2_ENUMS.keys()
    | CLOSE_FIELDS
    | {"sequence", "response_kind"}
)
SCHEMA2_NAMES = NAMES | {"client_response_received", "client_request_resolved"}
PHASES = frozenset({"open", "reconnect", "submission", "lifecycle"})
SCHEMA2_PHASES = PHASES | {
    "startup",
    "response",
    "stream",
    "closing",
    "closed",
    "shutdown",
}
ORIGIN_FIELDS = frozenset(
    {
        "launch_id",
        "app_version",
        "build_number",
        "os_version",
        "source_revision",
        "artifact_sha256",
        "provenance_status",
    }
)


def validate_report(body: bytes | str, now: float) -> dict:
    if len(body if isinstance(body, bytes) else body.encode()) > MAX_BODY:
        raise ValueError("report too large")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("invalid report")  # noqa: TRY004 - intake uses ValueError for invalid JSON reports
    schema = value.get("schema")
    if type(schema) is not int or schema not in (1, 2):
        raise ValueError("invalid schema")
    fields = {
        "schema",
        "report_id",
        "created_at",
        "app_version",
        "build",
        "platform",
        "os_version",
        "model",
        "events",
    }
    if schema == 2:
        fields.add("origins")
    if set(value) != fields:
        raise ValueError("invalid report")
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
    origins = _origins(value["origins"]) if schema == 2 else set()
    events = value["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= 100:
        raise ValueError("invalid events")
    seen_events = {}
    for event in events:
        if (
            not isinstance(event, dict)
            or set(event) - (SCHEMA2_FIELDS if schema == 2 else EVENT_FIELDS)
            or not {"time", "name", "launch_id"} <= set(event)
        ):
            raise ValueError("invalid event")
        _timestamp(event["time"], now)
        _uuid(event["launch_id"])
        if schema == 2:
            _schema2_event(event, origins, seen_events)
        names = SCHEMA2_NAMES if schema == 2 else NAMES
        if not isinstance(event["name"], str) or event["name"] not in names:
            raise ValueError("invalid event name")
        phases = SCHEMA2_PHASES if schema == 2 else PHASES
        if "phase" in event and (
            not isinstance(event["phase"], str) or event["phase"] not in phases
        ):
            raise ValueError("invalid phase")
        if "uncertain" in event and type(event["uncertain"]) is not bool:
            raise ValueError("invalid uncertainty")
        if "code" in event and (
            not isinstance(event["code"], str) or event["code"] not in CODES
        ):
            raise ValueError("invalid failure code")
        if "duration_ms" in event and (
            type(event["duration_ms"]) is not int
            or not 0 <= event["duration_ms"] <= 86400000
        ):
            raise ValueError("invalid duration")
    return value


def _origins(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError("invalid origins")
    launches = set()
    for origin in value:
        if not isinstance(origin, dict) or set(origin) != ORIGIN_FIELDS:
            raise ValueError("invalid origin")
        _uuid(origin["launch_id"])
        launch = origin["launch_id"].lower()
        if launch in launches:
            raise ValueError("duplicate origin")
        launches.add(launch)
        status = origin["provenance_status"]
        if not isinstance(status, str) or status not in {
            "verified",
            "unverified",
            "unavailable",
        }:
            raise ValueError("invalid provenance status")
        for key in ("app_version", "build_number", "os_version"):
            version = origin[key]
            if version is None:
                if status != "unavailable":
                    raise ValueError("missing source version")
            elif not isinstance(version, str) or not re.fullmatch(
                r"[0-9]{1,8}(?:\.[0-9]{1,8}){0,3}", version
            ):
                raise ValueError("invalid source version")
        for key, pattern in (
            ("source_revision", r"(?:[0-9a-f]{40}|[0-9a-f]{64})"),
            ("artifact_sha256", r"[0-9a-f]{64}"),
        ):
            identity = origin[key]
            if identity is not None and (
                not isinstance(identity, str) or not re.fullmatch(pattern, identity)
            ):
                raise ValueError("invalid source identity")
    return launches


def _schema2_event(event, origins, seen_events):
    if not {"event_id", "sequence"} <= event.keys():
        raise ValueError("missing event identity")
    if event["launch_id"].lower() not in origins:
        raise ValueError("missing event origin")
    if "response_kind" in event and (
        event["name"] not in {"client_response_received", "client_request_resolved"}
        or not isinstance(event["response_kind"], str)
        or event["response_kind"] not in {"accepted", "rejection"}
    ):
        raise ValueError("invalid response kind")
    for key, prefix in SCHEMA2_IDS.items():
        if key in event and (
            not isinstance(event[key], str)
            or not re.fullmatch(prefix + r"-[0-9a-f]{32}", event[key])
        ):
            raise ValueError("invalid event identifier")
    if type(event["sequence"]) is not int or not 0 <= event["sequence"] <= 2**63 - 1:
        raise ValueError("invalid sequence")
    for key, choices in SCHEMA2_ENUMS.items():
        if key in event and (
            not isinstance(event[key], str) or event[key] not in choices
        ):
            raise ValueError("invalid event enum")
    for key in CLOSE_FIELDS:
        code = event.get(key)
        if code is not None and (
            type(code) is not int
            or not 1000 <= code <= 4999
            or (key != "observed_status_code" and code in {1005, 1006, 1015})
        ):
            raise ValueError("invalid close code")
    event_id = event["event_id"]
    previous = seen_events.get(event_id)
    if previous is not None and previous != event:
        raise ValueError("conflicting event identifier")
    seen_events[event_id] = event


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
        raise ValueError("invalid identifier")


def _timestamp(value, now):
    if type(value) not in (int, float) or not now - RETENTION <= value <= now + 300:
        raise ValueError("invalid timestamp")


class ClientReportStore:
    """Bounded protected reports and authenticated request associations."""

    def __init__(self, database: str | Path = ":memory:", *, clock=time.time):
        self._clock = clock
        self._lock = RLock()
        self._db = sqlite3.connect(str(database), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS client_diagnostic_reports (device_id TEXT NOT NULL, report_id TEXT NOT NULL, received_at REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(device_id, report_id))"
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS client_diagnostic_associations (
                device_id TEXT NOT NULL, home_connection_id TEXT NOT NULL,
                request_id TEXT NOT NULL, correlation_id TEXT NOT NULL,
                process_id TEXT NOT NULL, observed_at REAL NOT NULL,
                expires_at REAL NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY(device_id, home_connection_id, request_id))"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS client_diagnostic_association_losses (
                kind TEXT PRIMARY KEY, count INTEGER NOT NULL)"""
        )
        self._db.commit()
        self._association_queue = Queue(maxsize=1024)
        self._queue_lock = Lock()
        self._closing_at = None
        self._queue_losses = {"queue_dropped": 0, "sink_failed": 0}
        self._association_worker = None

    def record_association(
        self,
        device_id: str,
        home_connection_id: str,
        request_id: str,
        correlation_id: str,
        process_id: str,
        *,
        ambiguous: bool = False,
    ) -> None:
        """Enqueue authenticated server facts without waiting for SQLite."""
        for value, prefix in (
            (home_connection_id, "conn"),
            (request_id, "req"),
            (correlation_id, "corr"),
            (process_id, "proc"),
        ):
            if not isinstance(value, str) or not re.fullmatch(
                prefix + r"-[0-9a-f]{32}", value
            ):
                self._queue_loss("sink_failed")
                return
        item = (
            "observe",
            device_id,
            home_connection_id,
            request_id,
            correlation_id,
            process_id,
            self._clock(),
            ambiguous,
        )
        self._enqueue_association(item)

    def finalize_associations(
        self, device_id: str, home_connection_id: str, ambiguous_request_ids
    ) -> None:
        """Finalize only a permanently closed socket's bounded token inventory."""
        if (
            not isinstance(home_connection_id, str)
            or not re.fullmatch(r"conn-[0-9a-f]{32}", home_connection_id)
            or not isinstance(ambiguous_request_ids, (set, frozenset, list, tuple))
            or len(ambiguous_request_ids) > 4096
            or any(
                not isinstance(token, str)
                or not re.fullmatch(r"req-[0-9a-f]{32}", token)
                for token in ambiguous_request_ids
            )
        ):
            self._queue_loss("sink_failed")
            return
        self._enqueue_association(
            ("finalize", device_id, home_connection_id, tuple(ambiguous_request_ids))
        )

    def _enqueue_association(self, item):
        with self._queue_lock:
            if self._closing_at is not None:
                self._queue_losses["queue_dropped"] = min(
                    2**63 - 1, self._queue_losses["queue_dropped"] + 1
                )
                return
            if self._association_worker is None:
                self._association_worker = Thread(
                    target=self._write_associations,
                    name="client-diagnostic-associations",
                    daemon=True,
                )
                try:
                    self._association_worker.start()
                except RuntimeError:
                    self._association_worker = None
                    self._queue_losses["sink_failed"] = min(
                        2**63 - 1, self._queue_losses["sink_failed"] + 1
                    )
                    return
            try:
                self._association_queue.put_nowait(item)
            except Full:
                self._queue_losses["queue_dropped"] = min(
                    2**63 - 1, self._queue_losses["queue_dropped"] + 1
                )

    def _queue_loss(self, kind, count=1):
        with self._queue_lock:
            self._queue_losses[kind] = min(2**63 - 1, self._queue_losses[kind] + count)

    def _write_associations(self):
        try:
            while True:
                with self._queue_lock:
                    closing_at = self._closing_at
                if closing_at is not None and (
                    time.monotonic() >= closing_at or self._association_queue.empty()
                ):
                    while True:
                        try:
                            self._association_queue.get_nowait()
                        except Empty:
                            break
                        self._queue_loss("queue_dropped")
                        self._association_queue.task_done()
                    return
                try:
                    item = self._association_queue.get(timeout=0.01)
                except Empty:
                    continue
                try:
                    if item[0] == "observe":
                        self._persist_association(*item[1:])
                    else:
                        self._finalize_associations(*item[1:])
                except sqlite3.Error, OSError, ValueError, TypeError:
                    self._queue_loss("sink_failed")
                finally:
                    self._association_queue.task_done()
        finally:
            with self._lock:
                self._db.close()

    def _persist_association(
        self,
        device_id,
        home_connection_id,
        request_id,
        correlation_id,
        process_id,
        now,
        ambiguous,
    ):
        with self._lock, self._db:
            current = self._clock()
            self._purge(current)
            if now + RETENTION <= current:
                self._association_loss("expired", 1)
                return
            key = (device_id, home_connection_id, request_id)
            existing = self._db.execute(
                """SELECT 1 FROM client_diagnostic_associations
                   WHERE device_id=? AND home_connection_id=? AND request_id=?""",
                key,
            ).fetchone()
            if existing:
                self._db.execute(
                    """UPDATE client_diagnostic_associations SET state='ambiguous'
                       WHERE device_id=? AND home_connection_id=? AND request_id=?""",
                    key,
                )
                self._association_loss("conflicts", 1)
                return
            self._db.execute(
                "INSERT INTO client_diagnostic_associations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    *key,
                    correlation_id,
                    process_id,
                    now,
                    now + RETENTION,
                    "ambiguous" if ambiguous else "provisional",
                ),
            )
            if ambiguous:
                self._association_loss("conflicts", 1)
            removed = self._db.execute(
                """DELETE FROM client_diagnostic_associations WHERE device_id=?
                   AND rowid NOT IN (
                     SELECT rowid FROM client_diagnostic_associations WHERE device_id=?
                     ORDER BY observed_at DESC, rowid DESC LIMIT 1024)""",
                (device_id, device_id),
            ).rowcount
            removed += self._db.execute(
                """DELETE FROM client_diagnostic_associations WHERE rowid NOT IN (
                     SELECT rowid FROM client_diagnostic_associations
                     ORDER BY observed_at DESC, rowid DESC LIMIT 4096)"""
            ).rowcount
            self._association_loss("evicted", removed)

    def _finalize_associations(
        self, device_id, home_connection_id, ambiguous_request_ids
    ):
        with self._lock, self._db:
            self._purge(self._clock())
            for token in ambiguous_request_ids:
                updated = self._db.execute(
                    """UPDATE client_diagnostic_associations SET state='ambiguous'
                       WHERE device_id=? AND home_connection_id=? AND request_id=?
                       AND state!='ambiguous'""",
                    (device_id, home_connection_id, token),
                ).rowcount
                self._association_loss("conflicts", updated)
            self._db.execute(
                """UPDATE client_diagnostic_associations SET state='linked'
                   WHERE device_id=? AND home_connection_id=? AND state='provisional'""",
                (device_id, home_connection_id),
            )

    def lookup_association(
        self, device_id: str, home_connection_id: str, request_id: str
    ) -> dict:
        with self._lock, self._db:
            now = self._clock()
            self._purge(now)
            return self._lookup_association(
                device_id, home_connection_id, request_id, now
            )

    def _lookup_association(self, device_id, home_connection_id, request_id, now):
        row = self._db.execute(
            """SELECT correlation_id, process_id, observed_at, expires_at, state
               FROM client_diagnostic_associations
               WHERE device_id=? AND home_connection_id=? AND request_id=?""",
            (device_id, home_connection_id, request_id),
        ).fetchone()
        if row is None:
            return {"state": "unavailable"}
        correlation, process, observed, expires, state = row
        if (
            not isinstance(observed, (int, float))
            or not isinstance(expires, (int, float))
            or not now - RETENTION <= observed <= now
            or not now < expires <= observed + RETENTION
            or not isinstance(correlation, str)
            or not re.fullmatch(r"corr-[0-9a-f]{32}", correlation)
            or not isinstance(process, str)
            or not re.fullmatch(r"proc-[0-9a-f]{32}", process)
        ):
            return {"state": "unavailable"}
        if state == "ambiguous":
            return {"state": "ambiguous"}
        if state != "linked":
            return {"state": "unavailable"}
        return {
            "state": "linked",
            "correlation_id": correlation,
            "process_id": process,
            "observed_at": observed,
        }

    def _association_loss(self, kind, count):
        if count:
            self._db.execute(
                """INSERT INTO client_diagnostic_association_losses VALUES (?, ?)
                   ON CONFLICT(kind) DO UPDATE
                   SET count=MIN(9223372036854775807-count, excluded.count)+count""",
                (kind, count),
            )

    def receive(self, device_id: str, body: bytes | str) -> str:
        now = self._clock()
        report = validate_report(body, now)
        report_id = report["report_id"]
        with self._lock, self._db:
            self._purge(now)
            existing = self._db.execute(
                "SELECT payload FROM client_diagnostic_reports WHERE device_id=? AND report_id=?",
                (device_id, report_id),
            ).fetchone()
            if existing:
                if report["schema"] == 2 and json.loads(existing[0]) != report:
                    raise ValueError("conflicting report identifier")
                return report_id
            latest = self._db.execute(
                "SELECT MAX(received_at) FROM client_diagnostic_reports WHERE device_id=?",
                (device_id,),
            ).fetchone()[0]
            if latest is not None and now - latest < 30:
                raise ReportRateLimited()
            if report["schema"] == 2:
                self._reject_conflicting_events(device_id, report)
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

    def _reject_conflicting_events(self, device_id, report):
        events = {event["event_id"]: event for event in report["events"]}
        origins = {origin["launch_id"].lower(): origin for origin in report["origins"]}
        for (payload,) in self._db.execute(
            "SELECT payload FROM client_diagnostic_reports WHERE device_id=?",
            (device_id,),
        ):
            previous = json.loads(payload)
            if previous["schema"] != 2:
                continue
            for event in previous["events"]:
                if event["event_id"] in events and events[event["event_id"]] != event:
                    raise ValueError("conflicting event identifier")
            for origin in previous["origins"]:
                launch = origin["launch_id"].lower()
                if launch in origins and origins[launch] != origin:
                    raise ValueError("conflicting event origin")

    def recent(self) -> dict:
        with self._lock, self._db:
            now = self._clock()
            self._purge(now)
            rows = self._db.execute(
                "SELECT device_id, received_at, payload FROM client_diagnostic_reports ORDER BY received_at DESC, rowid DESC LIMIT 50"
            ).fetchall()
            reports = []
            for device, received, payload in rows:
                report = json.loads(payload)
                associations = []
                for event in report["events"]:
                    association = {"state": "unavailable"}
                    if report["schema"] == 2 and all(
                        key in event for key in ("home_connection_id", "request_id")
                    ):
                        association = self._lookup_association(
                            device,
                            event["home_connection_id"],
                            event["request_id"],
                            now,
                        )
                    associations.append(
                        {"event_id": event.get("event_id"), **association}
                    )
                reports.append(
                    {
                        "device_id": device,
                        "received_at": received,
                        "report": report,
                        "associations": associations,
                    }
                )
            losses = {"expired": 0, "evicted": 0, "conflicts": 0}
            losses.update(
                self._db.execute(
                    "SELECT kind, count FROM client_diagnostic_association_losses"
                ).fetchall()
            )
            with self._queue_lock:
                losses.update(self._queue_losses)
            return {"schema": 1, "reports": reports, "association_losses": losses}

    def _purge(self, now):
        self._db.execute(
            "DELETE FROM client_diagnostic_reports WHERE received_at < ?",
            (now - RETENTION,),
        )
        expired = self._db.execute(
            "DELETE FROM client_diagnostic_associations WHERE expires_at <= ?", (now,)
        ).rowcount
        self._association_loss("expired", expired)

    def close(self):
        with self._queue_lock:
            if self._closing_at is None:
                self._closing_at = time.monotonic() + 0.1
        if self._association_worker is None:
            with self._lock:
                self._db.close()
        else:
            self._association_worker.join(timeout=0.1)


class ReportRateLimited(Exception):
    pass
