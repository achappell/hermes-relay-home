"""SQLite-backed configuration storage."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from threading import RLock

from hermes_home.domain.configuration import (
    ConfigurationMigrationRequired,
    ConfigurationValidationError,
    validate_candidate,
    validate_snapshot,
)

_EMPTY_CONFIGURATION = {
    "revision": 0,
    "rooms": [],
    "profiles": [],
    "wake_mappings": [],
    "devices": [],
}


class RevisionConflict(Exception):
    """Raised when a publish was based on an older configuration revision."""

    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(f"configuration revision is {current_revision}")


class ConfigurationStoreError(RuntimeError):
    """Raised when SQLite cannot safely answer a configuration operation."""


class SQLiteConfigurationStore:
    """Persist the active Home configuration in one SQLite row."""

    def __init__(self, database: str | Path) -> None:
        database_path = Path(database)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._lock = RLock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS configuration (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                revision INTEGER NOT NULL,
                snapshot TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO configuration (id, revision, snapshot) VALUES (1, 0, ?)",
            (json.dumps(_EMPTY_CONFIGURATION),),
        )
        self._connection.commit()

    def read(self) -> dict[str, object]:
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT revision, snapshot FROM configuration WHERE id = 1"
                ).fetchone()
                if row is None:
                    raise RuntimeError("configuration row is missing")
                snapshot = json.loads(row[1])
                if not isinstance(snapshot, dict) or snapshot.get("revision") != row[0]:
                    raise RuntimeError("configuration revision is inconsistent")
                devices = snapshot.get("devices")
                legacy_device_profile = isinstance(devices, list) and any(
                    isinstance(device, Mapping) and "profile_id" in device
                    for device in devices
                )
                if "profiles" not in snapshot or legacy_device_profile:
                    raise ConfigurationMigrationRequired(row[0])
                return validate_snapshot(snapshot)
        except sqlite3.Error as error:
            raise ConfigurationStoreError(
                "configuration database cannot be read"
            ) from error

    def replace(
        self,
        *,
        expected_revision: int,
        candidate: Mapping[str, object],
    ) -> dict[str, object]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ConfigurationValidationError(
                "expected_revision must be a non-negative integer"
            )
        validated_candidate = validate_candidate(candidate)
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    row = self._connection.execute(
                        "SELECT revision FROM configuration WHERE id = 1"
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("configuration row is missing")
                    if row[0] != expected_revision:
                        raise RevisionConflict(row[0])

                    snapshot = validated_candidate
                    snapshot["revision"] = row[0] + 1
                    serialized = json.dumps(snapshot)
                    self._connection.execute(
                        "UPDATE configuration SET revision = ?, snapshot = ? WHERE id = 1",
                        (snapshot["revision"], serialized),
                    )
                    self._connection.commit()
                except Exception:
                    self._connection.rollback()
                    raise
        except sqlite3.Error as error:
            raise ConfigurationStoreError(
                "configuration database cannot be updated"
            ) from error
        return json.loads(serialized)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
