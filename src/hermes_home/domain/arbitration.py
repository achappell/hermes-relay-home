"""Deterministic, bounded wake-claim arbitration."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from threading import RLock
from typing import Literal

from hermes_home.domain.configuration import validate_snapshot

ARBITRATION_WINDOW_SECONDS = 0.250
ClaimDecision = Literal["granted", "denied"]


@dataclass(frozen=True, slots=True)
class ClaimSubmission:
    """The immediate admission result for one claim."""

    claim_id: str
    accepted: bool
    deadline: float | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class WakeDecision:
    """The final, claim-specific arbitration result."""

    claim_id: str
    decision: ClaimDecision
    arbitration_id: str
    configuration_revision: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _EligibleClaim:
    claim_id: str
    device_id: str
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
    """Collect eligible claims for one bounded window and select one winner."""

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
        self._pending: _PendingRound | None = None
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
    ) -> ClaimSubmission:
        with self._lock:
            return self._submit(
                claim,
                authenticated_device_id=authenticated_device_id,
                authorized_rooms=authorized_rooms,
                authorized_wake_claim=authorized_wake_claim,
            )

    def _submit(
        self,
        claim: Mapping[str, object],
        *,
        authenticated_device_id: str | None,
        authorized_rooms: Iterable[str] | None,
        authorized_wake_claim: bool | None,
    ) -> ClaimSubmission:
        """Admit one claim, or return a fail-closed denial reason."""
        claim_id = _claim_id(claim)
        now = self._clock()

        if self._pending is not None and now >= self._pending.deadline:
            self.finalize(now=now)
            return ClaimSubmission(
                claim_id, accepted=False, deadline=None, reason="late"
            )

        if self._pending is None:
            try:
                configuration = validate_snapshot(self._configuration_source())
            except OSError, RuntimeError, TypeError, ValueError:
                return ClaimSubmission(
                    claim_id,
                    accepted=False,
                    deadline=None,
                    reason="service_unavailable",
                )
        else:
            configuration = self._pending.configuration

        try:
            eligible = _validate_claim(
                claim,
                authenticated_device_id=authenticated_device_id,
                configuration=configuration,
                existing_claim_ids=(
                    (
                        {item.claim_id for item in self._pending.claims}
                        if self._pending is not None
                        else set()
                    )
                    | self._accepted_claim_ids
                ),
                authorized_rooms=authorized_rooms,
                authorized_wake_claim=authorized_wake_claim,
            )
        except ClaimValidationError as error:
            return ClaimSubmission(
                claim_id,
                accepted=False,
                deadline=None,
                reason=error.reason,
            )

        if self._pending is None:
            self._pending = _PendingRound(
                configuration=configuration,
                deadline=now + ARBITRATION_WINDOW_SECONDS,
                claims=[],
            )
        eligible = _EligibleClaim(
            claim_id=eligible.claim_id,
            device_id=eligible.device_id,
            acoustic_score=eligible.acoustic_score,
            priority=eligible.priority,
            sequence=self._sequence,
        )
        self._sequence += 1
        self._accepted_claim_ids.add(eligible.claim_id)
        self._pending.claims.append(eligible)
        return ClaimSubmission(
            claim_id=eligible.claim_id,
            accepted=True,
            deadline=self._pending.deadline,
        )

    def finalize(self, *, now: float | None = None) -> dict[str, WakeDecision] | None:
        with self._lock:
            return self._finalize(now=now)

    def _finalize(self, *, now: float | None = None) -> dict[str, WakeDecision] | None:
        """Resolve the pending round once its 250 ms window has elapsed."""
        if self._pending is None:
            return None
        current_time = self._clock() if now is None else now
        if current_time < self._pending.deadline:
            return None

        pending = self._pending
        self._pending = None
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
) -> _EligibleClaim:
    if not isinstance(claim, Mapping):
        raise ClaimValidationError("invalid_request")
    expected_keys = {
        "schema",
        "claim_id",
        "device_id",
        "wake_mapping_id",
        "observation",
        "acoustic_evidence",
        "availability",
    }
    if set(claim) != expected_keys:
        raise ClaimValidationError("invalid_request")

    schema = claim["schema"]
    if type(schema) is not int or schema != 1:
        raise ClaimValidationError("invalid_request")
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
    if wake_mapping_id not in {
        mapping["id"] for mapping in configuration["wake_mappings"]
    }:
        raise ClaimValidationError("not_found")
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
