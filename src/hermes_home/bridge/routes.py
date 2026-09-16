"""Pure policy for selecting an approved Home bridge route.

The selector consumes an immutable set of route candidates and two injected
ports.  It does not open a socket, create a bridge or session, persist route
state, or retain proof material in its result.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from urllib.parse import SplitResult, urlsplit

HOME_BRIDGE_PATH = "/api/v1/bridge/ws"

type RouteAttemptOutcome = Literal[
    "reachable",
    "unavailable",
    "unauthorized",
    "identity_mismatch",
    "timed_out",
]

ROUTE_ATTEMPT_OUTCOMES = frozenset(
    {
        "reachable",
        "unavailable",
        "unauthorized",
        "identity_mismatch",
        "timed_out",
    }
)
ROUTE_CLASSES = frozenset({"home", "tailscale", "public"})
_ROUTE_PRIORITY = {"home": 0, "tailscale": 1, "public": 2}
_FAILURE_REASONS: dict[str, str] = {
    "unavailable": "route_unavailable",
    "unauthorized": "route_unauthorized",
    "identity_mismatch": "route_identity_mismatch",
    "timed_out": "route_timeout",
}


class RouteValidationError(ValueError):
    """Raised when route policy input or an injected port is malformed."""


class RouteProbeError(RuntimeError):
    """A typed failure raised by a route or identity port."""

    __slots__ = ("_outcome",)

    def __init__(self, outcome: RouteAttemptOutcome, message: str | None = None):
        if type(outcome) is not str or outcome not in _FAILURE_REASONS:
            raise RouteValidationError("route probe failure has an invalid outcome")
        object.__setattr__(self, "_outcome", outcome)
        super().__init__(message or outcome)

    @property
    def outcome(self) -> RouteAttemptOutcome:
        return self._outcome


class RouteUnavailableError(RouteProbeError):
    """The route could not be reached."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__("unavailable", message)


class RouteUnauthorizedError(RouteProbeError):
    """The route rejected the probe as unauthorized."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__("unauthorized", message)


class RouteIdentityMismatchError(RouteProbeError):
    """The route proved a different Household Server identity."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__("identity_mismatch", message)


class RouteTimeoutError(RouteProbeError):
    """The route or identity proof exceeded its bound."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__("timed_out", message)


@dataclass(frozen=True, slots=True)
class ApprovedRoute:
    """A validated candidate route held only by the policy layer."""

    route_class: str
    id: str
    url: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.route_class) is not str or self.route_class not in ROUTE_CLASSES:
            raise RouteValidationError(
                "route_class must be one of home, tailscale, or public"
            )

        route_id = _normalise_label(self.id, "route id")
        object.__setattr__(self, "id", route_id)

        if type(self.enabled) is not bool:
            raise RouteValidationError("route enabled must be a boolean")

        object.__setattr__(self, "url", _validate_url(self.url))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ApprovedRoute:
        """Build a route from its contract-shaped mapping."""
        if not isinstance(value, Mapping):
            raise RouteValidationError("route must be an object")
        expected = {"class", "id", "url", "enabled"}
        actual = set(value)
        if actual != expected or any(type(key) is not str for key in actual):
            raise RouteValidationError("route has invalid fields")
        return cls(
            route_class=cast(str, value["class"]),
            id=cast(str, value["id"]),
            url=cast(str, value["url"]),
            enabled=cast(bool, value["enabled"]),
        )

    def to_descriptor(self) -> SelectedRoute:
        """Return the only route data safe for an endpoint-facing result."""
        return SelectedRoute(route_class=self.route_class, id=self.id)


@dataclass(frozen=True, slots=True)
class SelectedRoute:
    """Endpoint-safe route identity; it intentionally has no URL field."""

    route_class: str
    id: str

    def __post_init__(self) -> None:
        if type(self.route_class) is not str or self.route_class not in ROUTE_CLASSES:
            raise RouteValidationError("selected route has an invalid class")
        object.__setattr__(self, "id", _normalise_label(self.id, "route id"))

    @property
    def class_name(self) -> str:
        """The contract's ``class`` field without shadowing Python syntax."""
        return self.route_class

    def to_endpoint(self) -> dict[str, str]:
        return {"class": self.route_class, "id": self.id}


@dataclass(frozen=True, slots=True)
class RouteProbeResult:
    """The redacted result returned by a route reachability port."""

    outcome: RouteAttemptOutcome

    def __post_init__(self) -> None:
        if type(self.outcome) is not str or self.outcome not in ROUTE_ATTEMPT_OUTCOMES:
            raise RouteValidationError("route probe result has an invalid outcome")


class RouteProbe(Protocol):
    """Port for one bounded route reachability attempt.

    Implementations must honor the supplied timeout and return or raise within
    that bound. The policy core passes the bound through rather than creating
    an un-cancellable worker for an arbitrary synchronous callable.
    """

    def probe(
        self, route: ApprovedRoute, *, timeout: float | None = None
    ) -> RouteProbeResult: ...


class HouseholdIdentityVerifier(Protocol):
    """Port for proving the Household Server reached by an approved route.

    Implementations must honor the supplied timeout just like :class:`RouteProbe`.
    """

    def verify(
        self, route: ApprovedRoute, *, timeout: float | None = None
    ) -> str | None: ...


@dataclass(frozen=True, slots=True)
class RouteAttempt:
    """One redacted, typed result in the order routes were attempted."""

    route: SelectedRoute
    outcome: RouteAttemptOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.route, SelectedRoute):
            raise RouteValidationError("route attempt descriptor is invalid")
        if type(self.outcome) is not str or self.outcome not in ROUTE_ATTEMPT_OUTCOMES:
            raise RouteValidationError("route attempt has an invalid outcome")

    @property
    def route_class(self) -> str:
        return self.route.route_class

    @property
    def route_id(self) -> str:
        return self.route.id

    @property
    def reason(self) -> str | None:
        return _FAILURE_REASONS.get(self.outcome)

    @property
    def failure_code(self) -> str | None:
        return self.reason

    def to_endpoint(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "route": self.route.to_endpoint(),
            "outcome": self.outcome,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True, slots=True)
class RouteSelection:
    """The safe outcome of one deterministic route-selection pass."""

    status: Literal["selected", "unavailable"]
    selected_route: SelectedRoute | None
    attempts: tuple[RouteAttempt, ...]
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"selected", "unavailable"}:
            raise RouteValidationError("route selection has an invalid status")
        if self.selected_route is not None and not isinstance(
            self.selected_route, SelectedRoute
        ):
            raise RouteValidationError("selected route descriptor is invalid")
        if type(self.attempts) is not tuple or any(
            not isinstance(attempt, RouteAttempt) for attempt in self.attempts
        ):
            raise RouteValidationError("route selection attempts must be a tuple")
        if self.status == "selected":
            if self.selected_route is None or self.reason is not None:
                raise RouteValidationError("selected route result is incomplete")
        elif self.selected_route is not None or self.reason != "route_unavailable":
            raise RouteValidationError("unavailable route selection is incomplete")

    @property
    def selected(self) -> SelectedRoute | None:
        return self.selected_route

    @property
    def route(self) -> SelectedRoute | None:
        return self.selected_route

    @property
    def available(self) -> bool:
        return self.status == "selected"

    def to_endpoint(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "attempts": [attempt.to_endpoint() for attempt in self.attempts],
        }
        if self.selected_route is not None:
            payload["route"] = self.selected_route.to_endpoint()
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


type RoutePort = Callable[..., object]
type Clock = Callable[[], float]


class RouteSelector:
    """Select the first route that is reachable and proves one identity."""

    def __init__(
        self,
        routes: Iterable[ApprovedRoute | Mapping[str, object]],
        *,
        expected_identity: str,
        probe: RouteProbe | RoutePort | None = None,
        verifier: HouseholdIdentityVerifier | RoutePort | None = None,
        route_probe: RouteProbe | RoutePort | None = None,
        identity_verifier: HouseholdIdentityVerifier | RoutePort | None = None,
        allow_public: bool = False,
        timeout: float | None = 5.0,
        clock: Clock = time.monotonic,
    ) -> None:
        self._routes = _snapshot_routes(routes)
        self._expected_identity = _validate_expected_identity(expected_identity)
        self._probe = _select_port(
            probe, route_probe, "route probe", ("probe", "check")
        )
        self._verifier = _select_port(
            verifier,
            identity_verifier,
            "Household Identity verifier",
            ("verify", "prove", "check"),
        )
        if type(allow_public) is not bool:
            raise RouteValidationError("allow_public must be a boolean")
        self._allow_public = allow_public
        self._timeout = _validate_timeout(timeout)
        if not callable(clock):
            raise RouteValidationError("clock must be callable")
        self._clock = clock

    @property
    def routes(self) -> tuple[ApprovedRoute, ...]:
        """The validated immutable snapshot used by this selector."""
        return self._routes

    def select(self) -> RouteSelection:
        """Evaluate eligible routes in home, Tailscale, public order."""
        attempts: list[RouteAttempt] = []
        for route in self._eligible_routes():
            outcome = self._evaluate(route)
            attempts.append(RouteAttempt(route.to_descriptor(), outcome))
            if outcome == "reachable":
                return RouteSelection(
                    status="selected",
                    selected_route=route.to_descriptor(),
                    attempts=tuple(attempts),
                )

        return RouteSelection(
            status="unavailable",
            selected_route=None,
            attempts=tuple(attempts),
            reason="route_unavailable",
        )

    def select_route(self) -> RouteSelection:
        """Compatibility spelling for callers that prefer a verb-noun name."""
        return self.select()

    def _eligible_routes(self) -> tuple[ApprovedRoute, ...]:
        indexed = enumerate(self._routes)
        return tuple(
            route
            for _, route in sorted(
                (
                    (index, route)
                    for index, route in indexed
                    if route.enabled
                    and (route.route_class != "public" or self._allow_public)
                ),
                key=lambda item: (_ROUTE_PRIORITY[item[1].route_class], item[0]),
            )
        )

    def _evaluate(self, route: ApprovedRoute) -> RouteAttemptOutcome:
        deadline = self._deadline()
        probe_timeout = self._remaining(deadline)
        if probe_timeout is not None and probe_timeout <= 0:
            return "timed_out"
        try:
            result = self._probe(
                route,
                timeout=probe_timeout,
            )
        except TypeError as error:
            raise RouteValidationError(
                "route probe port has an incompatible signature"
            ) from error
        except (
            RouteProbeError,
            TimeoutError,
            PermissionError,
            ConnectionError,
            OSError,
        ) as error:
            return self._failure_outcome(error, deadline)

        outcome = _normalise_probe_result(result)
        if self._expired(deadline):
            return "timed_out"
        if outcome != "reachable":
            return outcome
        verifier_timeout = self._remaining(deadline)
        if verifier_timeout is not None and verifier_timeout <= 0:
            return "timed_out"

        try:
            proof = self._verifier(
                route,
                timeout=verifier_timeout,
            )
        except TypeError as error:
            raise RouteValidationError(
                "Household Identity verifier has an incompatible signature"
            ) from error
        except (
            RouteProbeError,
            TimeoutError,
            PermissionError,
            ConnectionError,
            OSError,
        ) as error:
            return self._failure_outcome(error, deadline)

        if self._expired(deadline):
            return "timed_out"
        return _compare_identity(proof, self._expected_identity)

    def _failure_outcome(
        self, error: Exception, deadline: float | None
    ) -> RouteAttemptOutcome:
        outcome = _outcome_for_exception(error)
        return "timed_out" if self._expired(deadline) else outcome

    def _deadline(self) -> float | None:
        if self._timeout is None:
            return None
        now = _finite_number(self._clock(), "clock")
        try:
            deadline = now + self._timeout
        except (OverflowError, ValueError) as error:
            raise RouteValidationError("clock deadline must be finite") from error
        if not math.isfinite(deadline):
            raise RouteValidationError("clock deadline must be finite")
        return deadline

    def _remaining(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        now = _finite_number(self._clock(), "clock")
        remaining = deadline - now
        if not math.isfinite(remaining):
            raise RouteValidationError("clock remaining time must be finite")
        return max(0.0, remaining)

    def _expired(self, deadline: float | None) -> bool:
        if deadline is None:
            return False
        return self._remaining(deadline) <= 0


ApprovedRouteSelector = RouteSelector


def select_approved_route(
    routes: Iterable[ApprovedRoute | Mapping[str, object]],
    *,
    expected_identity: str,
    probe: RouteProbe | RoutePort,
    verifier: HouseholdIdentityVerifier | RoutePort,
    allow_public: bool = False,
    timeout: float | None = 5.0,
    clock: Clock = time.monotonic,
) -> RouteSelection:
    """Select one approved route without constructing any bridge state."""
    return RouteSelector(
        routes,
        expected_identity=expected_identity,
        probe=probe,
        verifier=verifier,
        allow_public=allow_public,
        timeout=timeout,
        clock=clock,
    ).select()


def _snapshot_routes(
    routes: Iterable[ApprovedRoute | Mapping[str, object]],
) -> tuple[ApprovedRoute, ...]:
    if isinstance(routes, (str, bytes, Mapping, set, frozenset)):
        raise RouteValidationError("routes must be an iterable of route objects")
    try:
        candidates = tuple(routes)
    except TypeError as error:
        raise RouteValidationError("routes must be iterable") from error

    snapshot: list[ApprovedRoute] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, ApprovedRoute):
            route = candidate
        elif isinstance(candidate, Mapping):
            route = ApprovedRoute.from_mapping(candidate)
        else:
            raise RouteValidationError("route must be an ApprovedRoute or object")
        if route.id in seen_ids:
            raise RouteValidationError(f"duplicate route id {route.id!r}")
        seen_ids.add(route.id)
        snapshot.append(route)
    return tuple(snapshot)


def _normalise_label(value: object, field: str) -> str:
    if type(value) is not str:
        raise RouteValidationError(f"{field} must be a string")
    normalised = value.strip()
    if not 1 <= len(normalised) <= 128:
        raise RouteValidationError(f"{field} must be 1-128 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalised):
        raise RouteValidationError(f"{field} must not contain control characters")
    return normalised


def _validate_url(value: object) -> str:
    if type(value) is not str:
        raise RouteValidationError("route url must be a string")
    url = value.strip()
    if not url or any(character.isspace() for character in url):
        raise RouteValidationError("route url must be a non-blank URL")
    if "?" in url or "#" in url:
        raise RouteValidationError("route url must not contain query or fragment")
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise RouteValidationError("route url must not contain control characters")
    try:
        parts = urlsplit(url)
        _validate_url_parts(parts)
        _ = parts.port
    except (TypeError, ValueError) as error:
        raise RouteValidationError("route url is invalid") from error
    return url


def _validate_url_parts(parts: SplitResult) -> None:
    if parts.scheme.casefold() not in {"ws", "wss"}:
        raise RouteValidationError("route url must use ws or wss")
    if not parts.netloc or parts.hostname is None:
        raise RouteValidationError("route url must include a host")
    if parts.username is not None or parts.password is not None:
        raise RouteValidationError("route url must not contain userinfo")
    if parts.query or parts.fragment:
        raise RouteValidationError("route url must not contain query or fragment")
    if parts.path != HOME_BRIDGE_PATH:
        raise RouteValidationError(f"route url must end at {HOME_BRIDGE_PATH}")


def _validate_expected_identity(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise RouteValidationError("expected Household Identity must be non-empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RouteValidationError(
            "expected Household Identity must not contain control characters"
        )
    return value


def _validate_timeout(value: object) -> float | None:
    if value is None:
        return None
    if type(value) not in {int, float}:
        raise RouteValidationError("route timeout must be a positive finite number")
    timeout = _finite_number(value, "route timeout")
    if timeout <= 0:
        raise RouteValidationError("route timeout must be a positive finite number")
    return timeout


def _finite_number(value: object, field: str) -> float:
    if type(value) not in {int, float}:
        raise RouteValidationError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise RouteValidationError(f"{field} must be a finite number") from error
    if not math.isfinite(number):
        raise RouteValidationError(f"{field} must be a finite number")
    return number


def _select_port(
    primary: object | None,
    alias: object | None,
    label: str,
    method_names: tuple[str, ...],
) -> RoutePort:
    if primary is not None and alias is not None:
        raise RouteValidationError(f"provide only one {label} port")
    port = primary if primary is not None else alias
    for method_name in method_names:
        method = getattr(port, method_name, None)
        if callable(method):
            return cast(RoutePort, method)
    if callable(port):
        return cast(RoutePort, port)
    raise RouteValidationError(f"{label} port must be callable")


def _normalise_probe_result(value: object) -> RouteAttemptOutcome:
    if type(value) is bool:
        return "reachable" if value else "unavailable"
    if isinstance(value, RouteProbeResult):
        return value.outcome
    if type(value) is str and value in ROUTE_ATTEMPT_OUTCOMES:
        return cast(RouteAttemptOutcome, value)
    if isinstance(value, Mapping) and set(value) == {"outcome"}:
        outcome = value["outcome"]
        if type(outcome) is str and outcome in ROUTE_ATTEMPT_OUTCOMES:
            return cast(RouteAttemptOutcome, outcome)
    raise RouteValidationError("route probe returned a malformed result")


def _compare_identity(proof: object, expected: str) -> RouteAttemptOutcome:
    if proof is None:
        return "identity_mismatch"
    if type(proof) is not str:
        raise RouteValidationError("identity verifier returned a malformed result")
    return "reachable" if proof == expected else "identity_mismatch"


def _outcome_for_exception(error: Exception) -> RouteAttemptOutcome:
    if isinstance(error, RouteProbeError):
        outcome = error.outcome
        if type(outcome) is not str or outcome not in _FAILURE_REASONS:
            raise RouteValidationError(
                "route probe failure has an invalid outcome"
            ) from error
        return outcome
    if isinstance(error, TimeoutError):
        return "timed_out"
    if isinstance(error, PermissionError):
        return "unauthorized"
    if isinstance(error, (ConnectionError, OSError)):
        return "unavailable"
    raise RouteValidationError("route port raised an undeclared exception") from error


__all__ = [
    "HOME_BRIDGE_PATH",
    "ROUTE_ATTEMPT_OUTCOMES",
    "ROUTE_CLASSES",
    "ApprovedRoute",
    "ApprovedRouteSelector",
    "HouseholdIdentityVerifier",
    "RouteAttempt",
    "RouteAttemptOutcome",
    "RouteIdentityMismatchError",
    "RouteProbe",
    "RouteProbeError",
    "RouteProbeResult",
    "RouteSelection",
    "RouteSelector",
    "RouteTimeoutError",
    "RouteUnauthorizedError",
    "RouteUnavailableError",
    "RouteValidationError",
    "SelectedRoute",
    "select_approved_route",
]
