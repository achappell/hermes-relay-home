"""Home-owned conversation claim ports and test storage."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from math import isfinite
from threading import RLock
from typing import Protocol

from hermes_home.domain.arbitration import WakeDecision
from hermes_home.domain.health import HealthDeliveryState
from hermes_home.domain.watch import (
    WATCH_ACTIVITY_STATES,
    WatchSnapshot,
    monotonic_clock,
)


class ConversationClaimConflict(RuntimeError):
    """Raised when a Room is active or a wake claim has already been consumed."""

    def __init__(self, message: str, *, reason: str = "conversation_active") -> None:
        self.reason = reason
        super().__init__(message)


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

    def delivery_state(self, device_id: str) -> HealthDeliveryState: ...


class InMemoryConversationClaimStore:
    """Small deterministic store for application-level contract tests."""

    def __init__(
        self,
        handle_factory: Callable[[], str] | None = None,
        *,
        clock: Callable[[], float] = monotonic_clock,
        route_id: str = "local",
        idle_timeout_seconds: float = 8.0,
    ) -> None:
        if type(route_id) is not str or not 1 <= len(route_id) <= 128:
            raise ValueError("watch route ID must be a non-empty bounded string")
        if (
            type(idle_timeout_seconds) not in (int, float)
            or isinstance(idle_timeout_seconds, bool)
            or not isfinite(float(idle_timeout_seconds))
            or idle_timeout_seconds <= 0
        ):
            raise ValueError("conversation idle timeout must be positive")
        self._handle_factory = handle_factory or (lambda: secrets.token_urlsafe(32))
        self._clock = clock
        self._idle_timeout = float(idle_timeout_seconds)
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
        now = float(self._clock())
        with self._lock:
            self._expire_due_locked(now)
            if decision.claim_id in self._used_claim_ids:
                raise ConversationClaimConflict(
                    "wake claim was already used",
                    reason="duplicate_claim",
                )
            active = [
                (candidate_handle, claim)
                for candidate_handle, claim in self._claims.items()
                if claim["room_id"] == decision.room_id and claim["status"] == "active"
            ]
            if active:
                if decision.claim_kind == "touch" and len(active) == 1:
                    existing_handle, existing = active[0]
                    deadline = existing.get("idle_deadline")
                    if (
                        existing.get("activity") in {"idle", "playback_complete"}
                        and type(deadline) in (int, float)
                        and float(deadline) > now
                    ):
                        self._close_claim_locked(
                            existing_handle,
                            existing,
                            "superseded_by_touch",
                        )
                    else:
                        reason = (
                            "conversation_active"
                            if existing["device_id"] == decision.device_id
                            else "room_busy"
                        )
                        raise ConversationClaimConflict(
                            "Room has an active conversation",
                            reason=reason,
                        )
                else:
                    raise ConversationClaimConflict(
                        "Room has an active conversation",
                        reason="conversation_active",
                    )
            handle = self._handle_factory()
            if type(handle) is not str or not 1 <= len(handle) <= 128:
                raise ValueError("conversation handle is invalid")
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
                "idle_deadline": None,
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
                self._close_claim_locked(handle, claim, reason)
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
                handle = next(
                    candidate
                    for candidate, value in self._claims.items()
                    if value is claim
                )
                self._close_claim_locked(handle, claim, reason)
            return len(active)

    def _close_claim_locked(
        self,
        handle: str,
        claim: dict[str, object],
        reason: str,
    ) -> None:
        claim["status"] = "closed"
        claim["activity"] = "closed"
        claim["idle_deadline"] = None
        claim["close_reason"] = reason
        self._watch_connected_at.pop(handle, None)

    def _expire_due_locked(self, now: float) -> None:
        for handle, claim in self._claims.items():
            deadline = claim.get("idle_deadline")
            if (
                claim["status"] == "active"
                and type(deadline) in (int, float)
                and float(deadline) <= now
            ):
                self._close_claim_locked(handle, claim, "idle_expired")

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
            claim["idle_deadline"] = None
            self._watch_connected_at[handle] = float(self._clock())

    def record_activity(self, handle: str, device_id: str, state: str) -> None:
        if state not in WATCH_ACTIVITY_STATES:
            raise ValueError("conversation activity state is invalid")
        with self._lock:
            self._expire_due_locked(float(self._clock()))
            claim = self._claims.get(handle)
            if (
                claim is None
                or claim["device_id"] != device_id
                or claim["status"] != "active"
            ):
                raise ValueError("conversation claim is no longer active")
            claim["activity"] = state
            if state in {"playback_complete", "idle"}:
                claim["idle_deadline"] = float(self._clock()) + self._idle_timeout
            else:
                claim["idle_deadline"] = None
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
            self._expire_due_locked(float(self._clock()))
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

    def delivery_state(self, device_id: str) -> HealthDeliveryState:
        """Read current delivery state without touching the claim."""
        with self._lock:
            self._expire_due_locked(float(self._clock()))
            matches = [
                claim
                for claim in self._claims.values()
                if claim["device_id"] == device_id and claim["status"] == "active"
            ]
            if not matches:
                return HealthDeliveryState(status="idle")
            if len(matches) != 1:
                return HealthDeliveryState(
                    status="unavailable", reason="delivery_state_unavailable"
                )
            activity = matches[0].get("activity")
            if not isinstance(activity, str) or activity not in {
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
