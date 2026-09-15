"""SQLite persistence for Home endpoint credential state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import TypeVar

T = TypeVar("T")

_EMPTY_STATE = {
    "schema": 1,
    "offers": [],
    "requests": [],
    "credentials": [],
    "replacements": [],
}


class CredentialStoreError(RuntimeError):
    """Raised when credential state cannot be safely read or committed."""


class SQLiteCredentialStore:
    """Persist credential metadata as one atomically replaced JSON state row."""

    def __init__(self, database: str | Path) -> None:
        database_path = Path(database)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._lock = RLock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS credential_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO credential_state (id, state) VALUES (1, ?)",
            (json.dumps(_EMPTY_STATE, sort_keys=True),),
        )
        self._connection.commit()

    def read_state(self) -> dict[str, object]:
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT state FROM credential_state WHERE id = 1"
                ).fetchone()
                if row is None:
                    raise CredentialStoreError("credential state row is missing")
                return _decode_state(row[0])
        except sqlite3.Error as error:
            raise CredentialStoreError("credential database cannot be read") from error

    def mutate(self, mutation: Callable[[dict[str, object]], T]) -> T:
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    row = self._connection.execute(
                        "SELECT state FROM credential_state WHERE id = 1"
                    ).fetchone()
                    if row is None:
                        raise CredentialStoreError("credential state row is missing")
                    state = _decode_state(row[0])
                    result = mutation(state)
                    serialized = json.dumps(state, sort_keys=True)
                    self._connection.execute(
                        "UPDATE credential_state SET state = ? WHERE id = 1",
                        (serialized,),
                    )
                    self._connection.commit()
                    return result
                except BaseException:
                    self._connection.rollback()
                    raise
        except sqlite3.Error as error:
            raise CredentialStoreError(
                "credential database cannot be updated"
            ) from error

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class InMemoryCredentialStore:
    """Small transactional store for framework-independent domain tests."""

    def __init__(self) -> None:
        self._state = deepcopy(_EMPTY_STATE)
        self._lock = RLock()

    def read_state(self) -> dict[str, object]:
        with self._lock:
            return deepcopy(self._state)

    def mutate(self, mutation: Callable[[dict[str, object]], T]) -> T:
        with self._lock:
            working = deepcopy(self._state)
            result = mutation(working)
            self._state = working
            return result

    def close(self) -> None:
        return None


def _decode_state(serialized: object) -> dict[str, object]:
    try:
        state = json.loads(serialized)
    except (TypeError, json.JSONDecodeError) as error:
        raise CredentialStoreError("credential state is invalid") from error
    if not isinstance(state, dict) or state.get("schema") != 1:
        raise CredentialStoreError("credential state schema is invalid")
    for key in ("offers", "requests", "credentials", "replacements"):
        records = state.get(key)
        if not isinstance(records, list) or any(
            not isinstance(record, dict) for record in records
        ):
            raise CredentialStoreError("credential state shape is invalid")
    return state
