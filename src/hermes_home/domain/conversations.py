"""Home-owned conversation claim ports and test storage."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from threading import RLock
from typing import Protocol

from hermes_home.domain.arbitration import WakeDecision
from hermes_home.domain.watch import (
    WATCH_ACTIVITY_STATES,
    WatchSnapshot,
    monotonic_clock,
)


class ConversationClaimConflict(RuntimeError):
    """Raised when a Room is active or a wake claim has already been consumed."""


class ConversationClaimStore(Protocol):
    """The storage port used to turn a granted wake into an opaque handle."""

    def create_from_decision(
        self,
        decision: WakeDecision,
        *,
        credential_generation: int | None,
    ) -> str: ...

    def close_claim(
        self,
        handle: str,
        device_id: str | None = None,
        *,
        reason: str = "stopped",
    ) -> bool: ...

    def close_device_claims(
        self,
        device_id: str,
        *,
        current_generation: int | None,
        reason: str,
    ) -> int: ...

    def close_profile_claims(
        self, profile_ids: Iterable[str], *, reason: str
    ) -> int: ...

    def close_mapping_claims(
        self, mapping_ids: Iterable[str], *, reason: str
    ) -> int: ...

    def close_all_claims(self, *, reason: str) -> int: ...

    def mark_open(self, handle: str, device_id: str) -> None: ...

    def record_activity(self, handle: str, device_id: str, state: str) -> None: ...

    def mark_disconnected(self, handle: str, device_id: str) -> None: ...

    def watch_snapshot(
        self,
        device_id: str,
        *,
        configuration_revision: int,
    ) -> WatchSnapshot | None: ...


class InMemoryConversationClaimStore:
    """Small deterministic store for application-level contract tests."""

    def __init__(
        self,
        handle_factory: Callable[[], str] | None = None,
        *,
        clock: Callable[[], float] = monotonic_clock,
        route_id: str = "local",
    ) -> None:
        if type(route_id) is not str or not 1 <= len(route_id) <= 128:
            raise ValueError("watch route ID must be a non-empty bounded string")
        self._handle_factory = handle_factory or (lambda: secrets.token_urlsafe(32))
        self._clock = clock
        self._watch_route = {"class": "home", "id": route_id}
        self._lock = RLock()
        self._claims: dict[str, dict[str, object]] = {}
        self._used_claim_ids: set[str] = set()
        self._watch_connected_at: dict[str, float] = {}

    def create_from_decision(
        self,
        decision: WakeDecision,
        *,
        credential_generation: int | None,
    ) -> str:
        if decision.decision != "granted":
            raise ValueError("only a granted wake can create a conversation claim")
        handle = self._handle_factory()
        if type(handle) is not str or not 1 <= len(handle) <= 128:
            raise ValueError("conversation handle is invalid")
        with self._lock:
            if decision.claim_id in self._used_claim_ids:
                raise ConversationClaimConflict("wake claim was already used")
            if any(
                claim["room_id"] == decision.room_id and claim["status"] == "active"
                for claim in self._claims.values()
            ):
                raise ConversationClaimConflict("Room has an active conversation")
            self._used_claim_ids.add(decision.claim_id)
            self._claims[handle] = {
                "claim_id": decision.claim_id,
                "device_id": decision.device_id,
                "room_id": decision.room_id,
                "wake_mapping_id": decision.wake_mapping_id,
                "profile_id": decision.profile_id,
                "configuration_revision": decision.configuration_revision,
                "credential_generation": credential_generation,
                "status": "active",
                "activity": "ready",
                "session_id": None,
                "close_reason": None,
            }
        return handle

    def close_device_claims(
        self,
        device_id: str,
        *,
        current_generation: int | None,
        reason: str,
    ) -> int:
        return self._close_claims(
            lambda claim: (
                claim["device_id"] == device_id
                and (
                    current_generation is None
                    or claim["credential_generation"] != current_generation
                )
            ),
            reason,
        )

    def close_claim(
        self,
        handle: str,
        device_id: str | None = None,
        *,
        reason: str = "stopped",
    ) -> bool:
        with self._lock:
            claim = self._claims.get(handle)
            if claim is None or (
                device_id is not None and claim["device_id"] != device_id
            ):
                return False
            if claim["status"] == "active":
                claim["status"] = "closed"
                self._watch_connected_at.pop(handle, None)
                claim["close_reason"] = reason
            return True

    def close_profile_claims(self, profile_ids: Iterable[str], *, reason: str) -> int:
        identifiers = set(profile_ids)
        return self._close_claims(
            lambda claim: claim["profile_id"] in identifiers,
            reason,
        )

    def close_mapping_claims(self, mapping_ids: Iterable[str], *, reason: str) -> int:
        identifiers = set(mapping_ids)
        return self._close_claims(
            lambda claim: claim["wake_mapping_id"] in identifiers,
            reason,
        )

    def close_all_claims(self, *, reason: str) -> int:
        return self._close_claims(lambda _claim: True, reason)

    def _close_claims(
        self, predicate: Callable[[dict[str, object]], bool], reason: str
    ) -> int:
        with self._lock:
            active = [
                claim
                for claim in self._claims.values()
                if claim["status"] == "active" and predicate(claim)
            ]
            for claim in active:
                claim["status"] = "closed"
                claim["close_reason"] = reason
                handle = next(
                    candidate
                    for candidate, value in self._claims.items()
                    if value is claim
                )
                self._watch_connected_at.pop(handle, None)
            return len(active)

    def mark_open(self, handle: str, device_id: str) -> None:
        with self._lock:
            claim = self._claims.get(handle)
            if (
                claim is None
                or claim["device_id"] != device_id
                or claim["status"] != "active"
            ):
                raise ValueError("conversation claim is no longer active")
            claim["activity"] = "open"
            self._watch_connected_at[handle] = float(self._clock())

    def record_activity(self, handle: str, device_id: str, state: str) -> None:
        if state not in WATCH_ACTIVITY_STATES:
            raise ValueError("conversation activity state is invalid")
        with self._lock:
            claim = self._claims.get(handle)
            if (
                claim is None
                or claim["device_id"] != device_id
                or claim["status"] != "active"
            ):
                raise ValueError("conversation claim is no longer active")
            claim["activity"] = state
            if handle in self._watch_connected_at:
                self._watch_connected_at[handle] = float(self._clock())

    def mark_disconnected(self, handle: str, device_id: str) -> None:
        with self._lock:
            claim = self._claims.get(handle)
            if claim is not None and claim["device_id"] == device_id:
                self._watch_connected_at.pop(handle, None)

    def watch_snapshot(
        self,
        device_id: str,
        *,
        configuration_revision: int,
    ) -> WatchSnapshot | None:
        with self._lock:
            matches = [
                (handle, claim)
                for handle, claim in self._claims.items()
                if claim["device_id"] == device_id and claim["status"] == "active"
            ]
            if len(matches) != 1:
                return None
            handle, claim = matches[0]
            if claim["configuration_revision"] != configuration_revision:
                return None
            if handle not in self._watch_connected_at:
                return None
            activity = claim.get("activity")
            if not isinstance(activity, str) or activity not in WATCH_ACTIVITY_STATES:
                return None
            session_id = claim.get("session_id")
            return WatchSnapshot(
                device_id=device_id,
                profile_id=str(claim["profile_id"]),
                configuration_revision=configuration_revision,
                credential_generation=claim["credential_generation"],
                activity=activity,
                route=self._watch_route,
                session_present=isinstance(session_id, str) and bool(session_id),
            )
