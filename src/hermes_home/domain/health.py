"""Bounded, content-safe health checks for one Home endpoint."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from threading import Event, Thread
from typing import Literal, Protocol, cast

HEALTH_VIEW_CAPABILITY = "health_view"
HEALTH_CHECK_TIMEOUT_SECONDS = 5.0
MAX_HEALTH_RESPONSE_BYTES = 16_384

HEALTH_STAGE_ORDER = ("route", "authorization", "bridge", "standard", "device_local")
HEALTH_STAGE_NAMES = frozenset(HEALTH_STAGE_ORDER)
HEALTH_STAGE_STATUSES = frozenset(
    {"verified", "unavailable", "unsupported", "stale", "timed_out"}
)
HEALTH_OVERALL_STATUSES = frozenset({"healthy", "degraded", "unavailable"})
HEALTH_DELIVERY_STATUSES = frozenset({"idle", "active", "uncertain", "unavailable"})
HEALTH_ACTIVITIES = frozenset(
    {
        "ready",
        "open",
        "capture",
        "turn",
        "playback",
        "response_ready",
        "playback_complete",
        "idle",
    }
)
HEALTH_REASONS = frozenset(
    {
        "authorization_unavailable",
        "bridge_unavailable",
        "bridge_timeout",
        "delivery_state_unavailable",
        "health_provider_unavailable",
        "probe_failed",
        "probe_timeout",
        "response_too_large",
        "route_identity_mismatch",
        "route_timeout",
        "route_unavailable",
        "route_unauthorized",
        "standard_not_configured",
        "standard_protocol_error",
        "standard_timeout",
        "standard_unavailable",
        "stale_target",
        "unsupported",
    }
)
HEALTH_ACTIONS = frozenset(
    {
        "check_endpoint_capabilities",
        "inspect_home_bridge",
        "inspect_standard_gateway",
        "refresh_configuration",
        "refresh_pairing",
        "retry_health_check",
    }
)
HealthStageName = Literal[
    "route", "authorization", "bridge", "standard", "device_local"
]
HealthStageStatus = Literal[
    "verified", "unavailable", "unsupported", "stale", "timed_out"
]


class HealthValidationError(ValueError):
    """Raised when an internal health result is not safe to expose."""


@dataclass(frozen=True, slots=True)
class HealthProbeResult:
    """One safe result returned by a route, bridge, or device probe."""

    status: HealthStageStatus
    reason: str | None = None
    next_action: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not str or self.status not in HEALTH_STAGE_STATUSES:
            raise HealthValidationError("health probe status is invalid")
        if self.status == "verified":
            if self.reason is not None or self.next_action is not None:
                raise HealthValidationError(
                    "verified health probe cannot contain failure fields"
                )
            return
        if type(self.reason) is not str or self.reason not in HEALTH_REASONS:
            raise HealthValidationError("health probe reason is not allowlisted")
        if type(self.next_action) is not str or self.next_action not in HEALTH_ACTIONS:
            raise HealthValidationError("health probe next action is not allowlisted")

    @classmethod
    def verified(cls) -> HealthProbeResult:
        return cls(status="verified")

    @classmethod
    def unavailable(
        cls,
        reason: str,
        *,
        next_action: str = "retry_health_check",
    ) -> HealthProbeResult:
        return cls(status="unavailable", reason=reason, next_action=next_action)

    @classmethod
    def timed_out(
        cls,
        *,
        reason: str = "probe_timeout",
        next_action: str = "retry_health_check",
    ) -> HealthProbeResult:
        return cls(status="timed_out", reason=reason, next_action=next_action)

    @classmethod
    def unsupported(cls) -> HealthProbeResult:
        return cls(
            status="unsupported",
            reason="unsupported",
            next_action="check_endpoint_capabilities",
        )


@dataclass(frozen=True, slots=True)
class HealthStage:
    """A named, redacted stage in a single health check."""

    name: HealthStageName
    status: HealthStageStatus
    reason: str | None = None
    next_action: str | None = None

    @classmethod
    def from_probe(
        cls, name: HealthStageName, result: HealthProbeResult
    ) -> HealthStage:
        return cls(
            name=name,
            status=result.status,
            reason=result.reason,
            next_action=result.next_action,
        )

    def __post_init__(self) -> None:
        if type(self.name) is not str or self.name not in HEALTH_STAGE_NAMES:
            raise HealthValidationError("health stage name is invalid")
        HealthProbeResult(
            status=self.status,
            reason=self.reason,
            next_action=self.next_action,
        )

    def to_endpoint(self) -> dict[str, object]:
        payload: dict[str, object] = {"name": self.name, "status": self.status}
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.next_action is not None:
            payload["next_action"] = self.next_action
        return payload


@dataclass(frozen=True, slots=True)
class HealthDeliveryState:
    """Safe delivery state kept separate from current endpoint health."""

    status: str
    activity: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not str or self.status not in HEALTH_DELIVERY_STATUSES:
            raise HealthValidationError("health delivery status is invalid")
        if self.activity is not None and (
            type(self.activity) is not str or self.activity not in HEALTH_ACTIVITIES
        ):
            raise HealthValidationError("health delivery activity is invalid")
        if self.status == "unavailable":
            if self.reason != "delivery_state_unavailable":
                raise HealthValidationError(
                    "unavailable delivery state has an invalid reason"
                )
        elif self.reason is not None:
            raise HealthValidationError("delivery state reason is not allowed")
        if self.status == "idle" and self.activity is not None:
            raise HealthValidationError("idle delivery state cannot have activity")
        if self.status == "active" and self.activity is None:
            raise HealthValidationError("active delivery state requires activity")

    def to_endpoint(self) -> dict[str, str]:
        payload = {"status": self.status}
        if self.activity is not None:
            payload["activity"] = self.activity
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True, slots=True)
class HealthResult:
    """The complete bounded result of one health-check request."""

    correlation_id: str
    status: str
    stages: tuple[HealthStage, ...]
    delivery: HealthDeliveryState

    def __post_init__(self) -> None:
        if (
            type(self.correlation_id) is not str
            or not self.correlation_id.startswith("corr-")
            or not 6 <= len(self.correlation_id) <= 128
            or any(
                character
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for character in self.correlation_id
            )
        ):
            raise HealthValidationError("health correlation ID is invalid")
        if type(self.status) is not str or self.status not in HEALTH_OVERALL_STATUSES:
            raise HealthValidationError("health overall status is invalid")
        if type(self.stages) is not tuple or not self.stages:
            raise HealthValidationError("health stages must be a non-empty tuple")
        if len(self.stages) > len(HEALTH_STAGE_ORDER):
            raise HealthValidationError("health stage count exceeds the bound")
        if any(not isinstance(stage, HealthStage) for stage in self.stages):
            raise HealthValidationError("health stage is invalid")
        names = tuple(stage.name for stage in self.stages)
        if len(set(names)) != len(names):
            raise HealthValidationError("health stages must have unique names")
        if not {"route", "authorization", "bridge", "standard"}.issubset(names):
            raise HealthValidationError("health result is missing a required stage")
        stage_results = {
            stage.name: HealthProbeResult(
                status=stage.status,
                reason=stage.reason,
                next_action=stage.next_action,
            )
            for stage in self.stages
        }
        if self.status != _overall_status(stage_results):
            raise HealthValidationError("health overall status does not match stages")
        if not isinstance(self.delivery, HealthDeliveryState):
            raise HealthValidationError("health delivery state is invalid")

    def to_endpoint(self) -> dict[str, object]:
        return {
            "schema": 1,
            "health": {
                "status": self.status,
                "correlation_id": self.correlation_id,
                "stages": [stage.to_endpoint() for stage in self.stages],
                "delivery": self.delivery.to_endpoint(),
            },
        }

    @classmethod
    def response_too_large(cls, correlation_id: str) -> HealthResult:
        stages = tuple(
            HealthStage(
                name=cast(HealthStageName, name),
                status="unavailable",
                reason="response_too_large",
                next_action="retry_health_check",
            )
            for name in HEALTH_STAGE_ORDER
        )
        return cls(
            correlation_id=correlation_id,
            status="unavailable",
            stages=stages,
            delivery=HealthDeliveryState(
                status="unavailable", reason="delivery_state_unavailable"
            ),
        )


class HealthProbeProvider(Protocol):
    """Port for safe, bounded checks owned by Home adapters."""

    def probe(
        self,
        stage: HealthStageName,
        *,
        device_id: str,
        room_id: str,
        timeout: float,
    ) -> HealthProbeResult: ...


class HealthDeliveryProvider(Protocol):
    """Read-only port for current delivery state."""

    def delivery_state(self, device_id: str) -> HealthDeliveryState: ...


def run_health_check(
    *,
    correlation_id: str,
    device_id: str,
    room_id: str,
    authorization: HealthProbeResult,
    provider: HealthProbeProvider | None,
    delivery_provider: HealthDeliveryProvider | None = None,
    timeout: float = HEALTH_CHECK_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> HealthResult:
    """Run every safe stage within one deadline and return only typed values."""
    deadline = _deadline(timeout, clock)
    stages: list[HealthStage] = [HealthStage.from_probe("authorization", authorization)]
    if authorization.status != "verified":
        blocked = HealthProbeResult.unavailable(
            "authorization_unavailable", next_action="refresh_pairing"
        )
        stages.extend(
            HealthStage.from_probe(cast(HealthStageName, stage_name), blocked)
            for stage_name in ("route", "bridge", "standard", "device_local")
        )
        return HealthResult(
            correlation_id=correlation_id,
            status="unavailable",
            stages=tuple(
                sorted(stages, key=lambda stage: HEALTH_STAGE_ORDER.index(stage.name))
            ),
            delivery=HealthDeliveryState(
                status="unavailable", reason="delivery_state_unavailable"
            ),
        )
    stage_results: dict[str, HealthProbeResult] = {"authorization": authorization}
    for stage_name in ("route", "bridge", "standard", "device_local"):
        name = cast(HealthStageName, stage_name)
        remaining = _remaining(deadline, clock)
        if remaining <= 0:
            result = HealthProbeResult.timed_out()
        elif provider is None:
            result = _default_probe(name)
        else:
            result = _invoke_probe(
                provider,
                name,
                device_id=device_id,
                room_id=room_id,
                timeout=remaining,
            )
        stage_results[stage_name] = result
        stages.append(HealthStage.from_probe(name, result))

    delivery = _read_delivery_state(
        delivery_provider,
        device_id=device_id,
        timeout=_remaining(deadline, clock),
    )
    status = _overall_status(stage_results)
    return HealthResult(
        correlation_id=correlation_id,
        status=status,
        stages=tuple(
            sorted(stages, key=lambda stage: HEALTH_STAGE_ORDER.index(stage.name))
        ),
        delivery=delivery,
    )


def bounded_health_body(
    result: HealthResult,
    *,
    max_bytes: int = MAX_HEALTH_RESPONSE_BYTES,
) -> dict[str, object]:
    """Serialize a health result only when the complete document fits its bound."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise HealthValidationError("health response bound must be positive")
    body = result.to_endpoint()
    encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= max_bytes:
        return body
    fallback = HealthResult.response_too_large(result.correlation_id).to_endpoint()
    fallback_encoded = json.dumps(
        fallback, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    if len(fallback_encoded) > max_bytes:
        raise HealthValidationError("health response fallback exceeds its bound")
    return fallback


def _invoke_probe(
    provider: HealthProbeProvider,
    stage: HealthStageName,
    *,
    device_id: str,
    room_id: str,
    timeout: float,
) -> HealthProbeResult:
    try:
        result, timed_out = _bounded_call(
            lambda: provider.probe(
                stage,
                device_id=device_id,
                room_id=room_id,
                timeout=timeout,
            ),
            timeout,
        )
    except Exception:  # noqa: BLE001 - a probe must never escape the safe boundary
        return HealthProbeResult.unavailable(
            _reason_for_stage(stage),
            next_action=_action_for_stage(stage),
        )
    if timed_out:
        return HealthProbeResult.timed_out(
            reason="standard_timeout" if stage == "standard" else "probe_timeout",
            next_action=_action_for_stage(stage),
        )
    if not isinstance(result, HealthProbeResult):
        return HealthProbeResult.unavailable(
            "probe_failed",
            next_action=_action_for_stage(stage),
        )
    return result


def _read_delivery_state(
    provider: HealthDeliveryProvider | None,
    *,
    device_id: str,
    timeout: float,
) -> HealthDeliveryState:
    if provider is None:
        return HealthDeliveryState(status="idle")
    if timeout <= 0:
        return HealthDeliveryState(
            status="unavailable", reason="delivery_state_unavailable"
        )
    try:
        result, timed_out = _bounded_call(
            lambda: provider.delivery_state(device_id),
            timeout,
        )
    except Exception:  # noqa: BLE001 - delivery state must remain content-safe
        return HealthDeliveryState(
            status="unavailable", reason="delivery_state_unavailable"
        )
    if timed_out or not isinstance(result, HealthDeliveryState):
        return HealthDeliveryState(
            status="unavailable", reason="delivery_state_unavailable"
        )
    return result


def _bounded_call(
    operation: Callable[[], object], timeout: float
) -> tuple[object, bool]:
    done = Event()
    state: dict[str, object] = {}

    def run() -> None:
        try:
            state["result"] = operation()
        except Exception as error:  # noqa: BLE001 - mapped to a safe probe result
            state["error"] = error
        finally:
            done.set()

    Thread(target=run, name="hermes-home-health-probe", daemon=True).start()
    if not done.wait(timeout):
        return None, True
    error = state.get("error")
    if isinstance(error, Exception):
        raise error
    return state.get("result"), False


def _deadline(timeout: float, clock: Callable[[], float]) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise HealthValidationError("health timeout must be positive")
    normalized = float(timeout)
    if normalized <= 0 or not isfinite(normalized):
        raise HealthValidationError("health timeout must be positive")
    try:
        deadline = float(clock()) + normalized
    except (OverflowError, TypeError, ValueError) as error:
        raise HealthValidationError("health deadline is invalid") from error
    if not isfinite(deadline):
        raise HealthValidationError("health deadline is invalid")
    return deadline


def _remaining(deadline: float, clock: Callable[[], float]) -> float:
    try:
        remaining = deadline - float(clock())
    except (OverflowError, TypeError, ValueError) as error:
        raise HealthValidationError("health remaining time is invalid") from error
    if not isfinite(remaining):
        raise HealthValidationError("health remaining time is invalid")
    return max(0.0, remaining)


def _default_probe(stage: HealthStageName) -> HealthProbeResult:
    if stage == "route":
        return HealthProbeResult.verified()
    if stage == "device_local":
        return HealthProbeResult.unsupported()
    reason = {
        "bridge": "health_provider_unavailable",
        "standard": "standard_not_configured",
    }[stage]
    return HealthProbeResult.unavailable(reason, next_action=_action_for_stage(stage))


def _reason_for_stage(stage: HealthStageName) -> str:
    return {
        "route": "route_unavailable",
        "authorization": "authorization_unavailable",
        "bridge": "bridge_unavailable",
        "standard": "standard_unavailable",
        "device_local": "unsupported",
    }[stage]


def _action_for_stage(stage: HealthStageName) -> str:
    return {
        "route": "retry_health_check",
        "authorization": "refresh_pairing",
        "bridge": "inspect_home_bridge",
        "standard": "inspect_standard_gateway",
        "device_local": "check_endpoint_capabilities",
    }[stage]


def _overall_status(results: dict[str, HealthProbeResult]) -> str:
    required = ("route", "authorization", "bridge", "standard")
    if any(
        results.get(name) is None or results[name].status != "verified"
        for name in required
    ):
        return "unavailable"
    if results.get("device_local") is None:
        return "degraded"
    if results["device_local"].status != "verified":
        return "degraded"
    return "healthy"


__all__ = [
    "HEALTH_ACTIVITIES",
    "HEALTH_CHECK_TIMEOUT_SECONDS",
    "HEALTH_DELIVERY_STATUSES",
    "HEALTH_REASONS",
    "HEALTH_STAGE_NAMES",
    "HEALTH_STAGE_ORDER",
    "HEALTH_STAGE_STATUSES",
    "HEALTH_VIEW_CAPABILITY",
    "MAX_HEALTH_RESPONSE_BYTES",
    "HealthDeliveryProvider",
    "HealthDeliveryState",
    "HealthProbeProvider",
    "HealthProbeResult",
    "HealthResult",
    "HealthStage",
    "HealthValidationError",
    "bounded_health_body",
    "run_health_check",
]
