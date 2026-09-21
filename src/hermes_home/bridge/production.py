"""Production ports for the Home-to-Standard Hermes bridge."""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from threading import RLock, Timer
from urllib.parse import urlsplit

from websockets.sync.client import connect

from hermes_home.bridge.standard import (
    STANDARD_GATEWAY_PATH,
    AudioSocket,
    BridgeProtocolError,
    BridgeTimeoutError,
    BridgeTransportError,
    ConversationGrant,
    GatewayRPCError,
    HomeBridge,
    JsonSocket,
    StandardGatewayClient,
)
from hermes_home.domain.arbitration import WakeDecision
from hermes_home.domain.conversations import ConversationClaimConflict
from hermes_home.domain.credentials import CredentialScope, RevocationEvent
from hermes_home.domain.health import (
    HealthDeliveryState,
    HealthProbeResult,
    HealthStageName,
)
from hermes_home.domain.watch import (
    WATCH_ACTIVITY_STATES,
    WatchSnapshot,
)
from hermes_home.storage.sqlite import (
    ConfigurationMigrationRequired,
    ConfigurationStoreError,
)

MAX_IDENTIFIER_LENGTH = 128
DEFAULT_FIRST_OPEN_TIMEOUT_SECONDS = 90.0
LOGGER = logging.getLogger(__name__)


class ConversationGrantStore:
    """Persist Home-owned, claim-bound conversation authority in SQLite."""

    def __init__(
        self,
        database: str | Path,
        *,
        configuration: Callable[[], Mapping[str, object]],
        clock: Callable[[], float] = time.monotonic,
        idle_timeout_seconds: float = 8.0,
        first_open_timeout_seconds: float = DEFAULT_FIRST_OPEN_TIMEOUT_SECONDS,
        route_id: str = "local",
        handle_factory: Callable[[], str] | None = None,
        credential_scope_resolver: Callable[[str, int], CredentialScope | None]
        | None = None,
    ) -> None:
        self._configuration = configuration
        self._credential_scope_resolver = credential_scope_resolver
        self._clock = clock
        self._idle_timeout = _positive_timeout(idle_timeout_seconds)
        self._first_open_timeout = _positive_timeout(first_open_timeout_seconds)
        if type(route_id) is not str or not 1 <= len(route_id) <= MAX_IDENTIFIER_LENGTH:
            raise ValueError("watch route ID must be a non-empty bounded string")
        self._watch_route = {"class": "home", "id": route_id}
        self._handle_factory = handle_factory or (lambda: secrets.token_urlsafe(32))
        self._lock = RLock()
        self._revocation_handlers: dict[str, set[Callable[[str], None]]] = {}
        self._watch_connected_at: dict[str, float] = {}
        database_path = Path(database)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_claims (
                handle TEXT PRIMARY KEY,
                claim_id TEXT NOT NULL UNIQUE,
                device_id TEXT NOT NULL,
                room_id TEXT NOT NULL,
                wake_mapping_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                configuration_revision INTEGER NOT NULL,
                credential_generation INTEGER,
                session_id TEXT,
                status TEXT NOT NULL,
                activity TEXT NOT NULL,
                idle_deadline REAL,
                close_reason TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        # A Home process restart ends active conversations. Route transport
        # reconnects within this process still resolve the same durable claim.
        self._connection.execute(
            "UPDATE conversation_claims SET status = 'closed', "
            "activity = 'closed', idle_deadline = NULL, "
            "close_reason = 'service_restart' WHERE status = 'active'"
        )
        self._connection.commit()
        self._timers: dict[str, Timer] = {}

    def create_from_decision(
        self,
        decision: WakeDecision,
        *,
        credential_generation: int | None,
    ) -> str:
        if decision.decision != "granted":
            raise ValueError("only a granted wake can create a conversation claim")
        if credential_generation is not None and (
            type(credential_generation) is not int or credential_generation < 1
        ):
            raise ValueError("credential generation must be a positive integer")
        now = _finite_time(self._clock())
        first_open_deadline = now + self._first_open_timeout
        superseded_handle: str | None = None
        with self._lock:
            self._expire_due_locked(now)
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                active = self._connection.execute(
                    "SELECT handle, device_id, activity, idle_deadline "
                    "FROM conversation_claims "
                    "WHERE room_id = ? AND status = 'active' LIMIT 1",
                    (decision.room_id,),
                ).fetchone()
                if active is not None:
                    active_handle, active_device_id, activity, idle_deadline = active
                    idle_tail = (
                        decision.claim_kind == "touch"
                        and activity == "idle"
                        and idle_deadline is not None
                        and idle_deadline > now
                    )
                    if idle_tail:
                        cursor = self._connection.execute(
                            "UPDATE conversation_claims SET status = 'closed', "
                            "activity = 'closed', idle_deadline = NULL, "
                            "close_reason = 'superseded_by_touch', updated_at = ? "
                            "WHERE handle = ? AND status = 'active' "
                            "AND activity = 'idle' AND idle_deadline > ?",
                            (now, active_handle, now),
                        )
                        if cursor.rowcount != 1:
                            self._connection.rollback()
                            raise ConversationClaimConflict(
                                "Room has an active conversation",
                                reason="room_busy",
                            )
                        superseded_handle = active_handle
                    else:
                        self._connection.rollback()
                        reason = (
                            "conversation_active"
                            if decision.claim_kind != "touch"
                            or active_device_id == decision.device_id
                            else "room_busy"
                        )
                        raise ConversationClaimConflict(
                            "room has an active conversation",
                            reason=reason,
                        )
                handle = _identifier(self._handle_factory(), "conversation handle")
                self._connection.execute(
                    """
                    INSERT INTO conversation_claims (
                        handle, claim_id, device_id, room_id, wake_mapping_id,
                        profile_id, configuration_revision, credential_generation,
                        session_id, status, activity, idle_deadline, close_reason,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'active', 'ready', ?, NULL, ?, ?)
                    """,
                    (
                        handle,
                        decision.claim_id,
                        decision.device_id,
                        decision.room_id,
                        decision.wake_mapping_id,
                        decision.profile_id,
                        decision.configuration_revision,
                        credential_generation,
                        first_open_deadline,
                        now,
                        now,
                    ),
                )
                self._connection.commit()
                if superseded_handle is not None:
                    self._cancel_timer_locked(superseded_handle)
                    self._watch_connected_at.pop(superseded_handle, None)
                    self._revocation_handlers.pop(superseded_handle, None)
            except sqlite3.IntegrityError as error:
                self._connection.rollback()
                raise ConversationClaimConflict(
                    "claim was already used",
                    reason="duplicate_claim",
                ) from error
            except sqlite3.Error as error:
                self._connection.rollback()
                raise OSError("cannot create Home conversation claim") from error
            self._schedule_timer_locked(handle, first_open_deadline, now)
        return handle

    def resolve(self, handle: str, device_id: str) -> ConversationGrant | None:
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT device_id, room_id, wake_mapping_id, profile_id, "
                    "session_id, status, idle_deadline, credential_generation, activity, "
                    "configuration_revision "
                    "FROM conversation_claims WHERE handle = ?",
                    (handle,),
                ).fetchone()
            except sqlite3.Error as error:
                raise OSError("cannot read Home conversation claim") from error
            if row is None or row[0] != device_id or row[5] != "active":
                return None
            now = _finite_time(self._clock())
            if row[6] is not None and now >= row[6]:
                reason = "first_open_expired" if row[8] == "ready" else "idle_expired"
                self._close_locked(handle, reason, now)
                return None
            try:
                snapshot = self._configuration()
                profiles = {profile["id"]: profile for profile in snapshot["profiles"]}
                profile = profiles.get(row[3])
                if profile is None or not profile["available"]:
                    self._close_locked(handle, "profile_revoked", now)
                    return None
                devices = {
                    device["id"]: device for device in snapshot.get("devices", [])
                }
                device = devices.get(device_id)
                protected_capabilities = frozenset()
                capability_revision = None
                if self._credential_scope_resolver is not None and type(row[7]) is int:
                    credential_scope = self._credential_scope_resolver(
                        device_id, row[7]
                    )
                    if isinstance(credential_scope, CredentialScope):
                        configured_capabilities = (
                            device.get("capabilities", {})
                            if isinstance(device, Mapping)
                            else {}
                        )
                        protected_capabilities = frozenset(
                            capability
                            for capability in (
                                "sensitive_entry",
                                "consequence_confirm",
                            )
                            if (
                                configured_capabilities.get(capability) is True
                                and capability in credential_scope.capabilities
                            )
                        )
                        capability_revision = snapshot.get("revision")
                interactive_choice = (
                    snapshot.get("revision") == row[9]
                    and device is not None
                    and device["capabilities"].get("interactive_choice", False) is True
                )
                mappings = {
                    mapping["id"]: mapping for mapping in snapshot["wake_mappings"]
                }
                current_mapping = mappings.get(row[2])
                if current_mapping is not None and not current_mapping["active"]:
                    self._close_locked(handle, "mapping_revoked", now)
                    return None
            except ConfigurationMigrationRequired:
                self._close_locked(handle, "configuration_migration", now)
                return None
            except ConfigurationStoreError as error:
                raise OSError("cannot verify Home conversation authority") from error
            except KeyError, RuntimeError, TypeError, ValueError:
                return None
            return ConversationGrant(
                handle=handle,
                device_id=device_id,
                profile_id=row[3],
                session_id=row[4],
                status="active",
                credential_generation=row[7],
                configuration_revision=row[9],
                interactive_choice=interactive_choice,
                protected_capabilities=protected_capabilities,
                capability_revision=capability_revision,
            )

    def persist_session(self, grant: ConversationGrant, session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("durable Standard session ID must be non-empty")
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT device_id, profile_id, session_id, status, "
                    "credential_generation "
                    "FROM conversation_claims WHERE handle = ?",
                    (grant.handle,),
                ).fetchone()
                if (
                    row is None
                    or row[0] != grant.device_id
                    or row[1] != grant.profile_id
                ):
                    raise ValueError("conversation claim binding changed")
                if row[4] != grant.credential_generation:
                    raise ValueError("conversation credential generation changed")
                if row[3] != "active":
                    raise ValueError("conversation claim is no longer active")
                if row[2] not in (None, session_id):
                    raise ValueError("conversation claim Session changed")
                other_session = self._connection.execute(
                    "SELECT 1 FROM conversation_claims WHERE session_id = ? "
                    "AND status = 'active' AND handle != ? LIMIT 1",
                    (session_id, grant.handle),
                ).fetchone()
                if other_session is not None:
                    raise ValueError("Standard Session is already bound to a claim")
                self._connection.execute(
                    "UPDATE conversation_claims SET session_id = ?, updated_at = ? "
                    "WHERE handle = ? AND status = 'active'",
                    (session_id, _finite_time(self._clock()), grant.handle),
                )
                self._connection.commit()
            except ValueError:
                self._connection.rollback()
                raise
            except sqlite3.Error as error:
                self._connection.rollback()
                raise OSError("cannot persist Home conversation Session") from error

    def mark_open(self, handle: str, device_id: str) -> None:
        """Clear the first-open deadline after a Standard Session is ready."""

        now = _finite_time(self._clock())
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT status, activity, idle_deadline FROM conversation_claims "
                    "WHERE handle = ? AND device_id = ?",
                    (handle, device_id),
                ).fetchone()
                if row is None or row[0] != "active":
                    self._connection.rollback()
                    raise ValueError("conversation claim is no longer active")
                if row[2] is not None and now >= row[2]:
                    reason = (
                        "first_open_expired" if row[1] == "ready" else "idle_expired"
                    )
                    self._connection.execute(
                        "UPDATE conversation_claims SET status = 'closed', "
                        "activity = 'closed', idle_deadline = NULL, "
                        "close_reason = ?, updated_at = ? "
                        "WHERE handle = ? AND status = 'active'",
                        (reason, now, handle),
                    )
                    self._connection.commit()
                    self._cancel_timer_locked(handle)
                    raise ValueError("conversation claim has expired")
                self._connection.execute(
                    "UPDATE conversation_claims SET activity = 'open', "
                    "idle_deadline = NULL, updated_at = ? "
                    "WHERE handle = ? AND status = 'active'",
                    (now, handle),
                )
                self._connection.commit()
            except ValueError:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
            except sqlite3.Error as error:
                self._connection.rollback()
                raise OSError("cannot mark Home conversation open") from error
            self._cancel_timer_locked(handle)
            self._watch_connected_at[handle] = now

    def mark_disconnected(self, handle: str, device_id: str) -> None:
        """Remove the ephemeral connected marker without closing the claim."""
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT device_id FROM conversation_claims WHERE handle = ?",
                    (handle,),
                ).fetchone()
            except sqlite3.Error as error:
                raise OSError("cannot read Home conversation claim") from error
            if row is not None and row[0] == device_id:
                self._watch_connected_at.pop(handle, None)

    def register_revocation_handler(
        self,
        handle: str,
        handler: Callable[[str], None],
    ) -> bool:
        if not callable(handler):
            raise TypeError("revocation handler must be callable")
        now = _finite_time(self._clock())
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT status, activity, idle_deadline FROM conversation_claims "
                    "WHERE handle = ?",
                    (handle,),
                ).fetchone()
                if row is None or row[0] != "active":
                    return False
                if row[2] is not None and now >= row[2]:
                    reason = (
                        "first_open_expired" if row[1] == "ready" else "idle_expired"
                    )
                    self._connection.execute(
                        "UPDATE conversation_claims SET status = 'closed', "
                        "activity = 'closed', idle_deadline = NULL, "
                        "close_reason = ?, updated_at = ? "
                        "WHERE handle = ? AND status = 'active'",
                        (reason, now, handle),
                    )
                    self._connection.commit()
                    self._cancel_timer_locked(handle)
                    return False
            except sqlite3.Error as error:
                raise OSError("cannot register Home conversation revocation") from error
            self._revocation_handlers.setdefault(handle, set()).add(handler)
            return True

    def unregister_revocation_handler(
        self,
        handle: str,
        handler: Callable[[str], None],
    ) -> None:
        with self._lock:
            handlers = self._revocation_handlers.get(handle)
            if handlers is None:
                return
            handlers.discard(handler)
            if not handlers:
                self._revocation_handlers.pop(handle, None)

    def on_revoked(self, event: RevocationEvent) -> None:
        """Interrupt active work after durable credential revocation."""

        self.close_device_claims(
            event.device_id,
            current_generation=None,
            reason="endpoint_revoked",
        )

    def record_activity(self, handle: str, device_id: str, state: str) -> None:
        if state not in {
            "capture",
            "turn",
            "playback",
            "response_ready",
            "playback_complete",
            "idle",
        }:
            raise ValueError("conversation activity state is invalid")
        now = _finite_time(self._clock())
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT activity, status, idle_deadline FROM conversation_claims WHERE handle = ? "
                    "AND device_id = ?",
                    (handle, device_id),
                ).fetchone()
            except sqlite3.Error as error:
                self._connection.rollback()
                raise OSError("cannot read Home conversation activity") from error
            if row is None or row[1] != "active":
                self._connection.rollback()
                raise ValueError("conversation claim is no longer active")
            if row[2] is not None and now >= row[2]:
                reason = "first_open_expired" if row[0] == "ready" else "idle_expired"
                try:
                    self._connection.execute(
                        "UPDATE conversation_claims SET status = 'closed', "
                        "activity = 'closed', idle_deadline = NULL, "
                        "close_reason = ?, updated_at = ? "
                        "WHERE handle = ? AND status = 'active'",
                        (reason, now, handle),
                    )
                    self._connection.commit()
                except sqlite3.Error as error:
                    self._connection.rollback()
                    raise OSError("cannot expire Home conversation activity") from error
                self._cancel_timer_locked(handle)
                raise ValueError("conversation claim has expired")
            if state == "playback_complete" and row[0] not in {
                "playback",
                "response_ready",
            }:
                self._connection.rollback()
                raise ValueError("playback completion was not pending")
            if state in {"playback_complete", "idle"}:
                idle_deadline = now + self._idle_timeout
                activity = "idle" if state in {"playback_complete", "idle"} else state
            else:
                idle_deadline = None
                activity = state
            try:
                cursor = self._connection.execute(
                    "UPDATE conversation_claims SET activity = ?, idle_deadline = ?, "
                    "updated_at = ? WHERE handle = ? AND status = 'active' "
                    "AND (idle_deadline IS NULL OR idle_deadline > ?)",
                    (activity, idle_deadline, now, handle, now),
                )
                if cursor.rowcount != 1:
                    self._connection.rollback()
                    raise ValueError("conversation claim has expired")
                self._connection.commit()
            except ValueError:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
            except sqlite3.Error as error:
                self._connection.rollback()
                raise OSError("cannot update Home conversation activity") from error
            self._cancel_timer_locked(handle)
            if idle_deadline is not None:
                self._schedule_timer_locked(handle, idle_deadline, now)
            if handle in self._watch_connected_at:
                self._watch_connected_at[handle] = now

    def watch_snapshot(
        self,
        device_id: str,
        *,
        configuration_revision: int,
    ) -> WatchSnapshot | None:
        """Return one connected, non-expired claim as content-free Watch state."""
        now = _finite_time(self._clock())
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT handle, profile_id, configuration_revision, "
                    "credential_generation, session_id, activity, idle_deadline "
                    "FROM conversation_claims "
                    "WHERE device_id = ? AND status = 'active' "
                    "AND (idle_deadline IS NULL OR idle_deadline > ?)",
                    (device_id, now),
                ).fetchall()
            except sqlite3.Error as error:
                raise OSError("cannot read Home Watch state") from error
            if len(rows) != 1:
                return None
            (
                handle,
                profile_id,
                revision,
                generation,
                session_id,
                activity,
                _deadline,
            ) = rows[0]
            if revision != configuration_revision:
                return None
            if handle not in self._watch_connected_at:
                return None
            if activity not in WATCH_ACTIVITY_STATES:
                return None
            return WatchSnapshot(
                device_id=device_id,
                profile_id=profile_id,
                configuration_revision=revision,
                credential_generation=generation,
                activity=activity,
                route=self._watch_route,
                session_present=isinstance(session_id, str) and bool(session_id),
            )

    def delivery_state(self, device_id: str) -> HealthDeliveryState:
        """Read one active claim without changing its timers or status."""
        now = _finite_time(self._clock())
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT activity FROM conversation_claims "
                    "WHERE device_id = ? AND status = 'active' "
                    "AND (idle_deadline IS NULL OR idle_deadline > ?)",
                    (device_id, now),
                ).fetchall()
            except sqlite3.Error as error:
                raise OSError("cannot read Home delivery state") from error
            if not rows:
                return HealthDeliveryState(status="idle")
            if len(rows) != 1:
                return HealthDeliveryState(
                    status="unavailable", reason="delivery_state_unavailable"
                )
            activity = rows[0][0]
            if activity not in {
                "ready",
                "open",
                "capture",
                "turn",
                "playback",
                "response_ready",
                "playback_complete",
                "idle",
            }:
                return HealthDeliveryState(
                    status="unavailable", reason="delivery_state_unavailable"
                )
            return HealthDeliveryState(status="active", activity=activity)

    def close_claim(
        self,
        handle: str,
        device_id: str | None = None,
        *,
        reason: str = "stopped",
    ) -> bool:
        now = _finite_time(self._clock())
        with self._lock:
            try:
                if device_id is None:
                    row = self._connection.execute(
                        "SELECT 1 FROM conversation_claims WHERE handle = ?",
                        (handle,),
                    ).fetchone()
                else:
                    row = self._connection.execute(
                        "SELECT 1 FROM conversation_claims WHERE handle = ? AND device_id = ?",
                        (handle, device_id),
                    ).fetchone()
            except sqlite3.Error as error:
                raise OSError("cannot read Home conversation claim") from error
            if row is None:
                return False
            self._close_locked(handle, reason, now)
            return True

    def close_device_claims(
        self,
        device_id: str,
        *,
        current_generation: int | None,
        reason: str,
    ) -> int:
        if current_generation is None:
            return self._close_matching_claims(
                "device_id = ?",
                (device_id,),
                reason,
            )
        return self._close_matching_claims(
            "device_id = ? AND (credential_generation IS NULL "
            "OR credential_generation != ?)",
            (device_id, current_generation),
            reason,
        )

    def close_profile_claims(
        self,
        profile_ids: Iterable[str],
        *,
        reason: str,
    ) -> int:
        identifiers = tuple(set(profile_ids))
        if not identifiers:
            return 0
        placeholders = ", ".join("?" for _ in identifiers)
        return self._close_matching_claims(
            f"profile_id IN ({placeholders})",
            identifiers,
            reason,
        )

    def close_mapping_claims(
        self,
        mapping_ids: Iterable[str],
        *,
        reason: str,
    ) -> int:
        identifiers = tuple(set(mapping_ids))
        if not identifiers:
            return 0
        placeholders = ", ".join("?" for _ in identifiers)
        return self._close_matching_claims(
            f"wake_mapping_id IN ({placeholders})",
            identifiers,
            reason,
        )

    def close_all_claims(self, *, reason: str) -> int:
        return self._close_matching_claims("1 = 1", (), reason)

    def _close_matching_claims(
        self,
        predicate: str,
        values: tuple[object, ...],
        reason: str,
    ) -> int:
        now = _finite_time(self._clock())
        notifications: list[tuple[Callable[[str], None], str]] = []
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                handles = self._connection.execute(
                    "SELECT handle FROM conversation_claims "
                    f"WHERE status = 'active' AND ({predicate})",
                    values,
                ).fetchall()
                if not handles:
                    self._connection.commit()
                    return 0
                cursor = self._connection.execute(
                    "UPDATE conversation_claims SET status = 'closed', "
                    "activity = 'closed', idle_deadline = NULL, "
                    "close_reason = ?, updated_at = ? "
                    f"WHERE status = 'active' AND ({predicate})",
                    (reason, now, *values),
                )
                self._connection.commit()
            except sqlite3.Error as error:
                self._connection.rollback()
                raise OSError("cannot revoke Home conversation claims") from error
            for (handle,) in handles:
                self._cancel_timer_locked(handle)
                self._watch_connected_at.pop(handle, None)
                for handler in self._revocation_handlers.pop(handle, set()):
                    notifications.append((handler, reason))
            closed_count = cursor.rowcount
        for handler, close_reason in notifications:
            try:
                handler(close_reason)
            except Exception:
                LOGGER.exception("conversation claim revocation handler failed")
        return closed_count

    def _expire(self, handle: str, deadline: float) -> None:
        now = _finite_time(self._clock())
        with self._lock:
            try:
                self._connection.execute(
                    "UPDATE conversation_claims SET status = 'closed', "
                    "activity = 'closed', idle_deadline = NULL, "
                    "close_reason = CASE WHEN activity = 'ready' "
                    "THEN 'first_open_expired' ELSE 'idle_expired' END, updated_at = ? "
                    "WHERE handle = ? AND status = 'active' AND idle_deadline = ? "
                    "AND idle_deadline <= ?",
                    (now, handle, deadline, now),
                )
                self._connection.commit()
            except sqlite3.Error:
                return
            self._timers.pop(handle, None)
            self._watch_connected_at.pop(handle, None)

    def _expire_due_locked(self, now: float) -> None:
        try:
            rows = self._connection.execute(
                "SELECT handle FROM conversation_claims "
                "WHERE status = 'active' AND idle_deadline IS NOT NULL AND idle_deadline <= ?",
                (now,),
            ).fetchall()
            self._connection.execute(
                "UPDATE conversation_claims SET status = 'closed', "
                "activity = 'closed', idle_deadline = NULL, "
                "close_reason = CASE WHEN activity = 'ready' "
                "THEN 'first_open_expired' ELSE 'idle_expired' END, updated_at = ? "
                "WHERE status = 'active' AND idle_deadline IS NOT NULL AND idle_deadline <= ?",
                (now, now),
            )
            self._connection.commit()
        except sqlite3.Error as error:
            raise OSError("cannot expire Home conversation claims") from error
        for (handle,) in rows:
            self._cancel_timer_locked(handle)
            self._watch_connected_at.pop(handle, None)

    def _close_locked(self, handle: str, reason: str, now: float) -> None:
        try:
            self._connection.execute(
                "UPDATE conversation_claims SET status = 'closed', activity = 'closed', "
                "close_reason = ?, idle_deadline = NULL, updated_at = ? "
                "WHERE handle = ?",
                (reason, now, handle),
            )
            self._connection.commit()
        except sqlite3.Error as error:
            raise OSError("cannot close Home conversation claim") from error
        self._cancel_timer_locked(handle)
        self._watch_connected_at.pop(handle, None)
        self._revocation_handlers.pop(handle, None)

    def _cancel_timer_locked(self, handle: str) -> None:
        timer = self._timers.pop(handle, None)
        if timer is not None:
            timer.cancel()

    def _schedule_timer_locked(self, handle: str, deadline: float, now: float) -> None:
        self._cancel_timer_locked(handle)
        timer = Timer(
            max(0.0, deadline - now),
            self._expire,
            args=(handle, deadline),
        )
        timer.daemon = True
        self._timers[handle] = timer
        timer.start()

    def close(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._watch_connected_at.clear()
            self._revocation_handlers.clear()
            self._connection.close()


@dataclass(slots=True)
class WebsocketsJsonSocket:
    """Adapt one synchronous ``websockets`` connection to the JSON port."""

    connection: object

    def send_json(self, frame: Mapping[str, object]) -> None:
        self.connection.send(  # type: ignore[attr-defined]
            json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        )

    def receive_json(self, timeout: float | None = None) -> Mapping[str, object]:
        raw = self.connection.recv(timeout=timeout)  # type: ignore[attr-defined]
        if not isinstance(raw, str):
            raise BridgeProtocolError("Standard gateway returned a non-JSON frame")
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise BridgeProtocolError(
                "Standard gateway returned invalid JSON"
            ) from error
        if not isinstance(frame, Mapping):
            raise BridgeProtocolError("Standard gateway JSON frame is not an object")
        return dict(frame)

    def close(self) -> None:
        self.connection.close()  # type: ignore[attr-defined]


@dataclass(slots=True)
class WebsocketsAudioSocket:
    """Adapt one synchronous ``websockets`` connection to the audio port."""

    connection: object

    def send_json(self, frame: Mapping[str, object]) -> None:
        self.connection.send(  # type: ignore[attr-defined]
            json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        )

    def receive(self, timeout: float | None = None) -> object:
        raw = self.connection.recv(timeout=timeout)  # type: ignore[attr-defined]
        if isinstance(raw, bytes):
            return raw
        if not isinstance(raw, str):
            raise BridgeProtocolError("Standard audio returned an invalid frame")
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise BridgeProtocolError("Standard audio returned invalid JSON") from error
        if not isinstance(frame, Mapping):
            raise BridgeProtocolError("Standard audio JSON frame is not an object")
        return dict(frame)

    def close(self) -> None:
        self.connection.close()  # type: ignore[attr-defined]


class WebsocketsJsonSocketFactory:
    """Open bounded synchronous Standard JSON gateway connections."""

    def __init__(self, *, open_timeout: float = 10.0) -> None:
        self._open_timeout = open_timeout

    def open(self, url: str) -> JsonSocket:
        return WebsocketsJsonSocket(
            connect(url, open_timeout=self._open_timeout, max_size=1_048_576)
        )


class WebsocketsAudioSocketFactory:
    """Open synchronous Standard response-audio connections."""

    def __init__(self, *, open_timeout: float = 30.0) -> None:
        self._open_timeout = open_timeout

    def open(self, url: str) -> AudioSocket:
        return WebsocketsAudioSocket(
            connect(url, open_timeout=self._open_timeout, max_size=4 * 1_048_576)
        )


class StandardHealthProbeProvider:
    """Run a fresh, non-conversation Standard readiness probe.

    The route and bridge checks are deliberately separate from the Standard
    socket check. A configured route selector may prove the approved Home
    route; without one, reaching the authenticated Home HTTP handler proves
    the local route boundary. The Standard check uses only ``gateway.ready``
    and ``gateway.ping`` and always closes the temporary gateway client.
    """

    def __init__(
        self,
        *,
        gateway_url: str,
        hermes_token: str,
        socket_factory: object | None = None,
        route_selector: object | None = None,
        bridge_probe: Callable[[str, str, float], HealthProbeResult] | None = None,
        bridge_configured: bool = False,
        connect_timeout: float = 5.0,
    ) -> None:
        _validate_gateway_url(gateway_url)
        if not isinstance(hermes_token, str) or not hermes_token.strip():
            raise ValueError("Standard gateway token must be non-empty")
        if type(bridge_configured) is not bool:
            raise ValueError("bridge_configured must be a boolean")
        self._gateway_url = gateway_url
        self._hermes_token = hermes_token
        self._socket_factory = socket_factory or WebsocketsJsonSocketFactory(
            open_timeout=connect_timeout
        )
        self._route_selector = route_selector
        self._bridge_probe = bridge_probe
        self._bridge_configured = bridge_configured
        self._connect_timeout = _positive_timeout(connect_timeout)

    def probe(
        self,
        stage: HealthStageName,
        *,
        device_id: str,
        room_id: str,
        timeout: float,
    ) -> HealthProbeResult:
        if stage == "route":
            return self._probe_route(timeout)
        if stage == "bridge":
            return self._probe_bridge(device_id, room_id, timeout)
        if stage == "standard":
            return self._probe_standard(timeout)
        return HealthProbeResult.unsupported()

    def _probe_route(self, timeout: float) -> HealthProbeResult:
        del timeout
        selector = self._route_selector
        if selector is None:
            return HealthProbeResult.verified()
        try:
            select = selector.select
            selection = select()
        except AttributeError, OSError, RuntimeError, TypeError, ValueError:
            return HealthProbeResult.unavailable(
                "route_unavailable", next_action="retry_health_check"
            )
        if getattr(selection, "status", None) == "selected":
            return HealthProbeResult.verified()
        reason = getattr(selection, "safe_failure_reason", None)
        if reason is None:
            reason = getattr(selection, "reason", None)
        reason_map = {
            "route_identity_mismatch": "route_identity_mismatch",
            "route_timeout": "route_timeout",
            "route_unauthorized": "route_unauthorized",
            "route_unavailable": "route_unavailable",
        }
        safe_reason = reason_map.get(reason, "route_unavailable")
        action = (
            "retry_health_check"
            if safe_reason != "route_unauthorized"
            else "refresh_pairing"
        )
        if safe_reason == "route_identity_mismatch":
            action = "refresh_pairing"
        return HealthProbeResult.unavailable(safe_reason, next_action=action)

    def _probe_bridge(
        self,
        device_id: str,
        room_id: str,
        timeout: float,
    ) -> HealthProbeResult:
        if self._bridge_probe is not None:
            try:
                result = self._bridge_probe(device_id, room_id, timeout)
            except OSError, RuntimeError, TypeError, ValueError:
                return HealthProbeResult.unavailable(
                    "bridge_unavailable", next_action="inspect_home_bridge"
                )
            return (
                result
                if isinstance(result, HealthProbeResult)
                else HealthProbeResult.unavailable(
                    "bridge_unavailable", next_action="inspect_home_bridge"
                )
            )
        if not self._bridge_configured:
            return HealthProbeResult.unavailable(
                "bridge_unavailable", next_action="inspect_home_bridge"
            )
        return HealthProbeResult.verified()

    def _probe_standard(self, timeout: float) -> HealthProbeResult:
        if timeout <= 0:
            return HealthProbeResult.timed_out(
                reason="standard_timeout", next_action="inspect_standard_gateway"
            )
        client = StandardGatewayClient(
            url=self._gateway_url,
            token=self._hermes_token,
            socket_factory=self._socket_factory,
            connect_timeout=min(self._connect_timeout, timeout),
            request_timeout=timeout,
            event_timeout=timeout,
        )
        try:
            client.probe_readiness(timeout=timeout)
            return HealthProbeResult.verified()
        except BridgeTimeoutError:
            return HealthProbeResult.timed_out(
                reason="standard_timeout", next_action="inspect_standard_gateway"
            )
        except TimeoutError:
            return HealthProbeResult.timed_out(
                reason="standard_timeout", next_action="inspect_standard_gateway"
            )
        except BridgeProtocolError:
            return HealthProbeResult.unavailable(
                "standard_protocol_error", next_action="inspect_standard_gateway"
            )
        except GatewayRPCError, BridgeTransportError, ConnectionError, OSError:
            return HealthProbeResult.unavailable(
                "standard_unavailable", next_action="inspect_standard_gateway"
            )
        except RuntimeError, TypeError, ValueError:
            return HealthProbeResult.unavailable(
                "standard_unavailable", next_action="inspect_standard_gateway"
            )
        finally:
            client.close()


def create_standard_bridge_factory(
    *,
    gateway_url: str,
    hermes_token: str,
    conversation_store: ConversationGrantStore,
    device_authenticator,
    connect_timeout: float = 10.0,
    audio_timeout: float = 30.0,
) -> Callable[[], HomeBridge]:
    """Build the production HomeBridge factory for one approved Standard target."""

    _validate_gateway_url(gateway_url)
    if not isinstance(hermes_token, str) or not hermes_token.strip():
        raise ValueError("Standard gateway token must be non-empty")
    gateway_factory = WebsocketsJsonSocketFactory(open_timeout=connect_timeout)
    audio_factory = WebsocketsAudioSocketFactory(open_timeout=audio_timeout)

    def factory() -> HomeBridge:
        return HomeBridge(
            gateway_url=gateway_url,
            hermes_token=hermes_token,
            device_authenticator=device_authenticator,
            conversation_resolver=conversation_store.resolve,
            gateway_socket_factory=gateway_factory,
            audio_socket_factory=audio_factory,
            session_persistor=conversation_store.persist_session,
            conversation_closer=conversation_store.close_claim,
            activity_recorder=conversation_store.record_activity,
            conversation_opener=conversation_store.mark_open,
            conversation_disconnector=conversation_store.mark_disconnected,
            revocation_registrar=conversation_store.register_revocation_handler,
            revocation_unregistrar=conversation_store.unregister_revocation_handler,
            audio_timeout=audio_timeout,
        )

    return factory


def _validate_gateway_url(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Standard gateway URL must be non-empty")
    parts = urlsplit(value)
    if parts.scheme not in {"ws", "wss"} or not parts.netloc:
        raise ValueError("Standard gateway URL must use ws or wss")
    if parts.path.rstrip("/") != STANDARD_GATEWAY_PATH:
        raise ValueError(f"Standard gateway URL must end in {STANDARD_GATEWAY_PATH}")
    if parts.fragment:
        raise ValueError("Standard gateway URL must not contain a fragment")


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if (
        len(normalized) > MAX_IDENTIFIER_LENGTH
        or "\r" in normalized
        or "\n" in normalized
    ):
        raise ValueError(f"{label} is too long or contains a line break")
    return normalized


def _finite_time(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("conversation clock value must be finite")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError("conversation clock value must be finite") from error
    if not isfinite(normalized):
        raise ValueError("conversation clock value must be finite")
    return normalized


def _positive_timeout(value: object) -> float:
    normalized = _finite_time(value)
    if normalized <= 0:
        raise ValueError("conversation idle timeout must be positive")
    return normalized


__all__ = [
    "ConversationClaimConflict",
    "ConversationGrantStore",
    "WebsocketsAudioSocket",
    "WebsocketsAudioSocketFactory",
    "WebsocketsJsonSocket",
    "WebsocketsJsonSocketFactory",
    "create_standard_bridge_factory",
]
