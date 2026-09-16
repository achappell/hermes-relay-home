"""SQLite persistence for the Home diagnostics review boundary."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import RLock

from hermes_home.observability.diagnostics import (
    MAX_SAFE_COUNT,
    CaptureAuditRecord,
    CaptureRecord,
    CaptureScope,
    DiagnosticEvent,
    DiagnosticsStatus,
    DiagnosticStoreError,
    _event_expired,
    _require_timestamp,
)

_ACTIVE_CAPTURE_STATES = ("armed", "previewing", "approved", "encrypting", "uploading")


class SQLiteDiagnosticsStore:
    """Persist bounded safe events and upload metadata in the Home database."""

    def __init__(
        self,
        database: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._lock = RLock()
        connection: sqlite3.Connection | None = None
        try:
            database_path = Path(database)
            database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(database_path, check_same_thread=False)
            self._connection = connection
            self._clock = clock
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_events (
                    event_id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    payload TEXT NOT NULL,
                    uploaded INTEGER NOT NULL DEFAULT 0 CHECK (uploaded IN (0, 1))
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS diagnostic_events_correlation
                ON diagnostic_events (correlation_id, occurred_at, event_id)
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    dropped_event_count INTEGER NOT NULL DEFAULT 0,
                    rejected_event_count INTEGER NOT NULL DEFAULT 0,
                    last_successful_upload_at REAL,
                    collector_reachable INTEGER NOT NULL DEFAULT 0
                        CHECK (collector_reachable IN (0, 1))
                )
                """
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO diagnostic_state (id) VALUES (1)"
            )
            self._connection.commit()
        except (OSError, sqlite3.Error) as error:
            if connection is not None:
                connection.close()
            raise DiagnosticStoreError(
                "diagnostics database cannot be initialized"
            ) from error

    def append(
        self,
        event: DiagnosticEvent,
        *,
        max_events: int,
        before: float | None = None,
    ) -> bool:
        if type(max_events) is not int or not 1 <= max_events <= 2**63 - 1:
            raise ValueError("diagnostic event bound must be positive")
        current = self._clock() if before is None else before
        _require_timestamp(current, "diagnostic store clock")
        self.purge_expired(before=float(current))
        if _event_expired(event, before=float(current)):
            return False
        serialized = json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"))
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    duplicate = self._connection.execute(
                        "SELECT 1 FROM diagnostic_events WHERE event_id = ?",
                        (event.event_id,),
                    ).fetchone()
                    if duplicate is not None:
                        raise DiagnosticStoreError("duplicate diagnostic event ID")

                    dropped = False
                    while (
                        self._connection.execute(
                            "SELECT COUNT(*) FROM diagnostic_events"
                        ).fetchone()[0]
                        >= max_events
                    ):
                        removed = self._connection.execute(
                            """
                            DELETE FROM diagnostic_events
                            WHERE rowid = (
                                SELECT rowid FROM diagnostic_events
                                ORDER BY occurred_at, rowid LIMIT 1
                            )
                            """
                        ).rowcount
                        if removed != 1:
                            raise DiagnosticStoreError(
                                "diagnostic event bound could not be enforced"
                            )
                        self._connection.execute(
                            """
                            UPDATE diagnostic_state
                            SET dropped_event_count = dropped_event_count + 1
                            WHERE id = 1
                            """
                        )
                        dropped = True

                    self._connection.execute(
                        """
                        INSERT INTO diagnostic_events
                            (event_id, correlation_id, occurred_at, payload)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            event.correlation_id,
                            event.occurred_at,
                            serialized,
                        ),
                    )
                    self._connection.commit()
                    return dropped
                except BaseException:
                    self._connection.rollback()
                    raise
        except DiagnosticStoreError:
            raise
        except sqlite3.IntegrityError as error:
            raise DiagnosticStoreError("duplicate diagnostic event ID") from error
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "diagnostics database cannot be updated"
            ) from error

    def events(
        self,
        *,
        correlation_id: str,
        before: float,
    ) -> tuple[DiagnosticEvent, ...]:
        _require_timestamp(before, "diagnostic events clock")
        self.purge_expired(before=before)
        try:
            with self._lock:
                rows = self._connection.execute(
                    """
                    SELECT payload FROM diagnostic_events
                    WHERE correlation_id = ?
                    ORDER BY occurred_at, rowid
                    """,
                    (correlation_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise DiagnosticStoreError("diagnostics database cannot be read") from error
        return tuple(_decode_event(row[0]) for row in rows)

    def pending_events(
        self,
        *,
        limit: int,
        before: float,
    ) -> tuple[DiagnosticEvent, ...]:
        if type(limit) is not int or not 1 <= limit <= MAX_SAFE_COUNT:
            raise ValueError("diagnostic upload limit must be positive")
        _require_timestamp(before, "diagnostic pending clock")
        self.purge_expired(before=before)
        try:
            with self._lock:
                rows = self._connection.execute(
                    """
                    SELECT payload FROM diagnostic_events
                    WHERE uploaded = 0
                    ORDER BY occurred_at, rowid
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error as error:
            raise DiagnosticStoreError("diagnostics database cannot be read") from error
        return tuple(_decode_event(row[0]) for row in rows)

    def mark_uploaded(self, event_ids: Sequence[str], *, uploaded_at: float) -> None:
        _require_timestamp(uploaded_at, "uploaded_at")
        requested = tuple(event_ids)
        if not requested:
            return
        if len(set(requested)) != len(requested):
            raise DiagnosticStoreError("duplicate diagnostic event IDs")
        placeholders = ",".join("?" for _ in requested)
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    known = {
                        row[0]
                        for row in self._connection.execute(
                            f"SELECT event_id FROM diagnostic_events WHERE event_id IN ({placeholders})",
                            requested,
                        ).fetchall()
                    }
                    if known != set(requested):
                        raise DiagnosticStoreError("diagnostic event ID is unknown")
                    self._connection.execute(
                        f"UPDATE diagnostic_events SET uploaded = 1 WHERE event_id IN ({placeholders})",
                        requested,
                    )
                    self._connection.execute(
                        """
                        UPDATE diagnostic_state
                        SET last_successful_upload_at = ?, collector_reachable = 1
                        WHERE id = 1
                        """,
                        (uploaded_at,),
                    )
                    self._connection.commit()
                except BaseException:
                    self._connection.rollback()
                    raise
        except DiagnosticStoreError:
            raise
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "diagnostics upload state cannot be updated"
            ) from error

    def mark_collector_unreachable(self) -> None:
        try:
            with self._lock:
                self._connection.execute(
                    "UPDATE diagnostic_state SET collector_reachable = 0 WHERE id = 1"
                )
                self._connection.commit()
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "diagnostics collector state cannot be updated"
            ) from error

    def purge_expired(self, *, before: float) -> int:
        _require_timestamp(before, "diagnostic purge clock")
        try:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT event_id, payload FROM diagnostic_events"
                ).fetchall()
                expired_ids = [
                    event_id
                    for event_id, payload in rows
                    if _event_expired(_decode_event(payload), before=before)
                ]
                if not expired_ids:
                    return 0
                placeholders = ",".join("?" for _ in expired_ids)
                cursor = self._connection.execute(
                    f"DELETE FROM diagnostic_events WHERE event_id IN ({placeholders})",
                    tuple(expired_ids),
                )
                self._connection.commit()
                return cursor.rowcount
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "expired diagnostics cannot be removed"
            ) from error

    def status(self, *, before: float | None = None) -> DiagnosticsStatus:
        current = self._clock() if before is None else before
        _require_timestamp(current, "diagnostic status clock")
        self.purge_expired(before=float(current))
        try:
            with self._lock:
                queued = self._connection.execute(
                    "SELECT COUNT(*) FROM diagnostic_events WHERE uploaded = 0"
                ).fetchone()[0]
                row = self._connection.execute(
                    """
                    SELECT dropped_event_count, rejected_event_count,
                           last_successful_upload_at, collector_reachable
                    FROM diagnostic_state WHERE id = 1
                    """
                ).fetchone()
                if row is None:
                    raise DiagnosticStoreError("diagnostics state row is missing")
        except DiagnosticStoreError:
            raise
        except sqlite3.Error as error:
            raise DiagnosticStoreError("diagnostics database cannot be read") from error
        return DiagnosticsStatus(
            enabled=True,
            last_successful_upload_at=row[2],
            queued_event_count=queued,
            collector_reachable=bool(row[3]),
            dropped_event_count=row[0],
            rejected_event_count=row[1],
        )

    def record_rejection(self) -> None:
        try:
            with self._lock:
                self._connection.execute(
                    """
                    UPDATE diagnostic_state
                    SET rejected_event_count = rejected_event_count + 1
                    WHERE id = 1
                    """
                )
                self._connection.commit()
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "diagnostics rejection state cannot be updated"
            ) from error

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class SQLiteIncidentCaptureStore:
    """Persist bounded capture metadata and non-content audit records."""

    def __init__(
        self,
        database: str | Path,
        *,
        max_captures: int = 1024,
        max_audit_records: int = 4096,
    ) -> None:
        if type(max_captures) is not int or not 1 <= max_captures <= MAX_SAFE_COUNT:
            raise ValueError("capture record bound must be positive")
        if (
            type(max_audit_records) is not int
            or not 1 <= max_audit_records <= MAX_SAFE_COUNT
        ):
            raise ValueError("capture audit bound must be positive")
        self._lock = RLock()
        self._max_captures = max_captures
        self._max_audit_records = max_audit_records
        connection: sqlite3.Connection | None = None
        try:
            database_path = Path(database)
            database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(database_path, check_same_thread=False)
            self._connection = connection
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_captures (
                    capture_id TEXT PRIMARY KEY,
                    endpoint_fingerprint TEXT NOT NULL,
                    task_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    retention_deadline REAL,
                    failure_code TEXT,
                    entry_count INTEGER NOT NULL,
                    total_bytes INTEGER NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_capture_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    outcome TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS diagnostic_capture_audit_order
                ON diagnostic_capture_audit (occurred_at, audit_id)
                """
            )
            self._connection.commit()
        except (OSError, sqlite3.Error) as error:
            if connection is not None:
                connection.close()
            raise DiagnosticStoreError(
                "incident capture database cannot be initialized"
            ) from error

    def captures(self) -> tuple[CaptureRecord, ...]:
        try:
            with self._lock:
                rows = self._connection.execute(
                    """
                    SELECT capture_id, endpoint_fingerprint, task_fingerprint,
                           state, created_at, expires_at, retention_deadline,
                           failure_code, entry_count, total_bytes
                    FROM diagnostic_captures
                    ORDER BY created_at, capture_id
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise DiagnosticStoreError("incident captures cannot be read") from error
        try:
            return tuple(
                CaptureRecord(
                    capture_id=row[0],
                    scope=CaptureScope(row[1], row[2]),
                    state=row[3],
                    created_at=row[4],
                    expires_at=row[5],
                    retention_deadline=row[6],
                    failure_code=row[7],
                    entry_count=row[8],
                    total_bytes=row[9],
                )
                for row in rows
            )
        except (TypeError, ValueError) as error:
            raise DiagnosticStoreError(
                "incident capture metadata is invalid"
            ) from error

    def save_capture(self, capture: CaptureRecord) -> None:
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._save_capture_row(capture)
                    self._trim_captures()
                    self._connection.commit()
                except BaseException:
                    self._connection.rollback()
                    raise
        except DiagnosticStoreError:
            raise
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "incident capture metadata cannot be saved"
            ) from error

    def save_capture_and_audit(
        self, capture: CaptureRecord, record: CaptureAuditRecord
    ) -> None:
        if capture.capture_id != record.capture_id:
            raise DiagnosticStoreError("capture and audit IDs do not match")
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._save_capture_row(capture)
                    self._insert_audit_row(record)
                    self._trim_captures()
                    self._trim_audit()
                    self._connection.commit()
                except BaseException:
                    self._connection.rollback()
                    raise
        except DiagnosticStoreError:
            raise
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "incident capture transition cannot be saved"
            ) from error

    def remove_capture(self, capture_id: str) -> None:
        try:
            with self._lock:
                self._connection.execute(
                    "DELETE FROM diagnostic_captures WHERE capture_id = ?",
                    (capture_id,),
                )
                self._connection.commit()
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "incident capture metadata cannot be removed"
            ) from error

    def remove_capture_and_audit(
        self, capture_id: str, record: CaptureAuditRecord
    ) -> None:
        if capture_id != record.capture_id:
            raise DiagnosticStoreError("capture and audit IDs do not match")
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._connection.execute(
                        "DELETE FROM diagnostic_captures WHERE capture_id = ?",
                        (capture_id,),
                    )
                    self._insert_audit_row(record)
                    self._trim_audit()
                    self._connection.commit()
                except BaseException:
                    self._connection.rollback()
                    raise
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "incident capture deletion cannot be saved"
            ) from error

    def append_audit(self, record: CaptureAuditRecord) -> None:
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._insert_audit_row(record)
                    self._trim_audit()
                    self._connection.commit()
                except BaseException:
                    self._connection.rollback()
                    raise
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "incident capture audit cannot be saved"
            ) from error

    def _save_capture_row(self, capture: CaptureRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO diagnostic_captures (
                capture_id, endpoint_fingerprint, task_fingerprint, state,
                created_at, expires_at, retention_deadline, failure_code,
                entry_count, total_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(capture_id) DO UPDATE SET
                endpoint_fingerprint = excluded.endpoint_fingerprint,
                task_fingerprint = excluded.task_fingerprint,
                state = excluded.state,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at,
                retention_deadline = excluded.retention_deadline,
                failure_code = excluded.failure_code,
                entry_count = excluded.entry_count,
                total_bytes = excluded.total_bytes
            """,
            (
                capture.capture_id,
                capture.scope.endpoint_fingerprint,
                capture.scope.task_fingerprint,
                capture.state,
                capture.created_at,
                capture.expires_at,
                capture.retention_deadline,
                capture.failure_code,
                capture.entry_count,
                capture.total_bytes,
            ),
        )

    def _insert_audit_row(self, record: CaptureAuditRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO diagnostic_capture_audit
                (capture_id, action, occurred_at, outcome)
            VALUES (?, ?, ?, ?)
            """,
            (
                record.capture_id,
                record.action,
                record.occurred_at,
                record.outcome,
            ),
        )

    def audit_log(self) -> tuple[CaptureAuditRecord, ...]:
        try:
            with self._lock:
                rows = self._connection.execute(
                    """
                    SELECT capture_id, action, occurred_at, outcome
                    FROM diagnostic_capture_audit
                    ORDER BY audit_id
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise DiagnosticStoreError(
                "incident capture audit cannot be read"
            ) from error
        try:
            return tuple(CaptureAuditRecord(*row) for row in rows)
        except (TypeError, ValueError) as error:
            raise DiagnosticStoreError(
                "incident capture audit metadata is invalid"
            ) from error

    def _trim_captures(self) -> None:
        count = self._connection.execute(
            "SELECT COUNT(*) FROM diagnostic_captures"
        ).fetchone()[0]
        excess = count - self._max_captures
        if excess <= 0:
            return
        ids = self._connection.execute(
            """
            SELECT capture_id FROM diagnostic_captures
            WHERE state NOT IN (?, ?, ?, ?, ?)
            ORDER BY created_at, capture_id LIMIT ?
            """,
            (*_ACTIVE_CAPTURE_STATES, excess),
        ).fetchall()
        if len(ids) != excess:
            raise DiagnosticStoreError(
                "incident capture bound is full of active captures"
            )
        self._connection.executemany(
            "DELETE FROM diagnostic_captures WHERE capture_id = ?",
            ids,
        )

    def _trim_audit(self) -> None:
        count = self._connection.execute(
            "SELECT COUNT(*) FROM diagnostic_capture_audit"
        ).fetchone()[0]
        excess = count - self._max_audit_records
        if excess <= 0:
            return
        ids = self._connection.execute(
            """
            SELECT audit_id FROM diagnostic_capture_audit
            ORDER BY audit_id LIMIT ?
            """,
            (excess,),
        ).fetchall()
        self._connection.executemany(
            "DELETE FROM diagnostic_capture_audit WHERE audit_id = ?",
            ids,
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _decode_event(serialized: object) -> DiagnosticEvent:
    try:
        payload = json.loads(serialized)
        if not isinstance(payload, dict):
            raise TypeError("event payload is not an object")
        return DiagnosticEvent.from_mapping(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise DiagnosticStoreError("diagnostic event is invalid") from error
