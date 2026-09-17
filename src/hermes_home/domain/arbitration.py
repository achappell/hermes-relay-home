"""Deterministic, bounded wake-claim arbitration."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from math import isfinite
from threading import RLock
from typing import Literal

from hermes_home.domain.configuration import (
    ConfigurationMigrationRequired,
    validate_snapshot,
)

ARBITRATION_WINDOW_SECONDS = 0.250
ClaimDecision = Literal["granted", "denied"]


@dataclass(frozen=True, slots=True)
class ClaimSubmission:
    """The immediate admission result for one claim."""

    claim_id: str
    accepted: bool
    deadline: float | None
    reason: str | None = None
    current_revision: int | None = None


@dataclass(frozen=True, slots=True)
class WakeDecision:
    """The final claim result and its Home-only routing context."""

    claim_id: str
    decision: ClaimDecision
    arbitration_id: str
    configuration_revision: int
    reason: str | None = None
    device_id: str = ""
    room_id: str = ""
    wake_mapping_id: str = ""
    profile_id: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class _EligibleClaim:
    claim_id: str
    device_id: str
    room_id: str
    wake_mapping_id: str
    profile_id: str
    acoustic_score: float
    priority: int
    sequence: int


@dataclass(slots=True)
class _PendingRound:
    configuration: dict[str, object]
    deadline: float
    claims: list[_EligibleClaim]


class ClaimValidationError(ValueError):
    """Raised internally when a claim must be denied."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ArbitrationEngine:
    """Collect one bounded round per Room and select one winner per round."""

    def __init__(
        self,
        *,
        configuration: Callable[[], Mapping[str, object]],
        clock: Callable[[], float] = time.monotonic,
        arbitration_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._configuration_source = configuration
        self._clock = clock
        self._arbitration_id_factory = arbitration_id_factory or _default_arbitration_id
        self._lock = RLock()
        self._pending_by_room: dict[str, _PendingRound] = {}
        self._resolved: dict[str, WakeDecision] = {}
        self._accepted_claim_ids: set[str] = set()
        self._sequence = 0

    def submit(
        self,
        claim: Mapping[str, object],
        *,
        authenticated_device_id: str | None,
        authorized_rooms: Iterable[str] | None = None,
        authorized_wake_claim: bool | None = None,
        authorized_wake_mappings: Iterable[str] | None = None,
    ) -> ClaimSubmission:
        with self._lock:
            return self._submit(
                claim,
                authenticated_device_id=authenticated_device_id,
                authorized_rooms=authorized_rooms,
                authorized_wake_claim=authorized_wake_claim,
                authorized_wake_mappings=authorized_wake_mappings,
            )

    def _submit(
        self,
        claim: Mapping[str, object],
        *,
        authenticated_device_id: str | None,
        authorized_rooms: Iterable[str] | None,
        authorized_wake_claim: bool | None,
        authorized_wake_mappings: Iterable[str] | None,
    ) -> ClaimSubmission:
        claim_id = _claim_id(claim)
        now = self._clock()
        try:
            current_configuration = validate_snapshot(self._configuration_source())
        except ConfigurationMigrationRequired as error:
            return ClaimSubmission(
                claim_id,
                accepted=False,
                deadline=None,
                reason="configuration_migration_required",
                current_revision=error.current_revision,
            )
        except OSError, RuntimeError, TypeError, ValueError:
            return ClaimSubmission(
                claim_id,
                accepted=False,
                deadline=None,
                reason="service_unavailable",
            )

        device_id = claim.get("device_id") if isinstance(claim, Mapping) else None
        if type(device_id) is not str:
            try:
                _validate_claim(
                    claim,
                    authenticated_device_id=authenticated_device_id,
                    configuration=current_configuration,
                    existing_claim_ids=self._accepted_claim_ids,
                    authorized_rooms=authorized_rooms,
                    authorized_wake_claim=authorized_wake_claim,
                    authorized_wake_mappings=authorized_wake_mappings,
                )
            except ClaimValidationError as error:
                return ClaimSubmission(claim_id, False, None, error.reason)
            return ClaimSubmission(claim_id, False, None, "not_found")

        current_devices = {
            device["id"]: device for device in current_configuration["devices"]
        }
        current_device = current_devices.get(device_id)
        if current_device is None:
            return ClaimSubmission(claim_id, False, None, "not_found")
        room_id = current_device["room_id"]

        expired_rooms: set[str] = set()
        for pending_room, pending in tuple(self._pending_by_room.items()):
            if now >= pending.deadline:
                self._finalize_room(pending_room)
                expired_rooms.add(pending_room)
        if room_id in expired_rooms:
            return ClaimSubmission(claim_id, False, None, "late")

        pending = self._pending_by_room.get(room_id)
        if (
            pending is not None
            and pending.configuration["revision"] != current_configuration["revision"]
        ):
            self._finalize_room(room_id)
            pending = None
        configuration = (
            current_configuration if pending is None else pending.configuration
        )
        try:
            eligible = _validate_claim(
                claim,
                authenticated_device_id=authenticated_device_id,
                configuration=configuration,
                existing_claim_ids=(
                    {item.claim_id for item in pending.claims}
                    if pending is not None
                    else set()
                )
                | self._accepted_claim_ids,
                authorized_rooms=authorized_rooms,
                authorized_wake_claim=authorized_wake_claim,
                authorized_wake_mappings=authorized_wake_mappings,
            )
        except ClaimValidationError as error:
            return ClaimSubmission(claim_id, False, None, error.reason)

        if pending is None:
            pending = _PendingRound(
                configuration=configuration,
                deadline=now + ARBITRATION_WINDOW_SECONDS,
                claims=[],
            )
            self._pending_by_room[room_id] = pending
        eligible = _EligibleClaim(
            claim_id=eligible.claim_id,
            device_id=eligible.device_id,
            room_id=eligible.room_id,
            wake_mapping_id=eligible.wake_mapping_id,
            profile_id=eligible.profile_id,
            acoustic_score=eligible.acoustic_score,
            priority=eligible.priority,
            sequence=self._sequence,
        )
        self._sequence += 1
        self._accepted_claim_ids.add(eligible.claim_id)
        pending.claims.append(eligible)
        return ClaimSubmission(
            claim_id=eligible.claim_id,
            accepted=True,
            deadline=pending.deadline,
        )

    def finalize(self, *, now: float | None = None) -> dict[str, WakeDecision] | None:
        with self._lock:
            current_time = self._clock() if now is None else now
            decisions: dict[str, WakeDecision] = {}
            for room_id, pending in tuple(self._pending_by_room.items()):
                if current_time >= pending.deadline:
                    decisions.update(self._finalize_room(room_id) or {})
            return decisions or None

    def _finalize_room(self, room_id: str) -> dict[str, WakeDecision] | None:
        pending = self._pending_by_room.pop(room_id, None)
        if pending is None:
            return None

        arbitration_id = self._arbitration_id_factory()
        winner = min(
            pending.claims,
            key=lambda claim: (-claim.acoustic_score, claim.priority, claim.sequence),
        )
        decisions = {
            claim.claim_id: WakeDecision(
                claim_id=claim.claim_id,
                decision="granted" if claim.claim_id == winner.claim_id else "denied",
                arbitration_id=arbitration_id,
                configuration_revision=pending.configuration["revision"],
                reason=None
                if claim.claim_id == winner.claim_id
                else "lost_arbitration",
                device_id=claim.device_id,
                room_id=claim.room_id,
                wake_mapping_id=claim.wake_mapping_id,
                profile_id=claim.profile_id,
            )
            for claim in pending.claims
        }
        self._resolved.update(decisions)
        return decisions

    def decision_for(self, claim_id: str) -> WakeDecision | None:
        """Return and consume a finalized decision for one claim."""
        with self._lock:
            return self._resolved.pop(claim_id, None)


def _validate_claim(
    claim: Mapping[str, object],
    *,
    authenticated_device_id: str | None,
    configuration: Mapping[str, object],
    existing_claim_ids: set[str],
    authorized_rooms: Iterable[str] | None,
    authorized_wake_claim: bool | None,
    authorized_wake_mappings: Iterable[str] | None,
) -> _EligibleClaim:
    if not isinstance(claim, Mapping):
        raise ClaimValidationError("invalid_request")
    expected_keys = {
        "schema",
        "claim_id",
        "device_id",
        "wake_mapping_id",
        "configuration_revision",
        "observation",
        "acoustic_evidence",
        "availability",
    }
    if set(claim) != expected_keys:
        raise ClaimValidationError("invalid_request")

    schema = claim["schema"]
    if type(schema) is not int or schema != 1:
        raise ClaimValidationError("invalid_request")
    configuration_revision = claim["configuration_revision"]
    if type(configuration_revision) is not int or configuration_revision < 0:
        raise ClaimValidationError("invalid_request")
    if configuration_revision != configuration["revision"]:
        raise ClaimValidationError("stale_configuration")

    claim_id = _require_identifier(claim["claim_id"])
    device_id = _require_identifier(claim["device_id"])
    wake_mapping_id = _require_identifier(claim["wake_mapping_id"])
    if claim_id in existing_claim_ids:
        raise ClaimValidationError("duplicate_claim")
    if authenticated_device_id != device_id:
        raise ClaimValidationError("unauthorized")
    if not isinstance(claim["observation"], Mapping) or not claim["observation"]:
        raise ClaimValidationError("invalid_request")
    acoustic_evidence = claim["acoustic_evidence"]
    if not isinstance(acoustic_evidence, Mapping) or not acoustic_evidence:
        raise ClaimValidationError("invalid_request")
    if type(claim["availability"]) is not str:
        raise ClaimValidationError("invalid_request")
    if claim["availability"] != "ready":
        raise ClaimValidationError("claim_denied")

    devices = {device["id"]: device for device in configuration["devices"]}
    mappings = {mapping["id"]: mapping for mapping in configuration["wake_mappings"]}
    profiles = {profile["id"]: profile for profile in configuration["profiles"]}
    device = devices.get(device_id)
    if device is None:
        raise ClaimValidationError("not_found")
    if not device["capabilities"]["wake_claim"]:
        raise ClaimValidationError("claim_denied")
    if authorized_wake_claim is not None and (
        not authorized_wake_claim
        or authorized_rooms is None
        or device["room_id"] not in authorized_rooms
    ):
        raise ClaimValidationError("forbidden")
    if authorized_wake_mappings is not None and wake_mapping_id not in set(
        authorized_wake_mappings
    ):
        raise ClaimValidationError("forbidden")

    mapping = mappings.get(wake_mapping_id)
    if mapping is None:
        raise ClaimValidationError("not_found")
    if not mapping["active"]:
        raise ClaimValidationError("stale_mapping")
    profile = profiles.get(mapping["profile_id"])
    if profile is None or not profile["available"]:
        raise ClaimValidationError("claim_denied")

    value = acoustic_evidence.get("value")
    if type(value) not in (int, float) or isinstance(value, bool):
        raise ClaimValidationError("invalid_request")
    try:
        acoustic_score = float(value)
    except OverflowError:
        raise ClaimValidationError("invalid_request") from None
    if not isfinite(acoustic_score):
        raise ClaimValidationError("invalid_request")
    return _EligibleClaim(
        claim_id=claim_id,
        device_id=device_id,
        room_id=device["room_id"],
        wake_mapping_id=wake_mapping_id,
        profile_id=profile["id"],
        acoustic_score=acoustic_score,
        priority=device["priority"],
        sequence=0,
    )


def _claim_id(claim: Mapping[str, object]) -> str:
    if isinstance(claim, Mapping) and type(claim.get("claim_id")) is str:
        return claim["claim_id"]
    return ""


def _require_identifier(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise ClaimValidationError("invalid_request")
    return value


def _default_arbitration_id() -> str:
    return f"arb-{uuid.uuid4().hex}"
