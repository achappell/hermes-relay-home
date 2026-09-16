"""Tests for the pure approved-route selection policy."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from hermes_home.bridge import (
    ApprovedRoute,
    RouteIdentityMismatchError,
    RouteProbeError,
    RouteProbeResult,
    RouteSelector,
    RouteTimeoutError,
    RouteUnavailableError,
    RouteValidationError,
)

IDENTITY = "household-server-opaque-id"


def route(
    route_class: str,
    route_id: str,
    *,
    enabled: bool = True,
) -> ApprovedRoute:
    return ApprovedRoute(
        route_class,
        route_id,
        f"wss://{route_id}.example.test/api/v1/bridge/ws",
        enabled,
    )


class FakeProbe:
    def __init__(self, outcomes: dict[str, object] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.calls: list[tuple[str, float | None]] = []

    def probe(
        self, candidate: ApprovedRoute, *, timeout: float | None = None
    ) -> object:
        self.calls.append((candidate.id, timeout))
        result = self.outcomes.get(candidate.id, RouteProbeResult("reachable"))
        if isinstance(result, BaseException):
            raise result
        return result


class FakeVerifier:
    def __init__(self, proofs: dict[str, object] | None = None) -> None:
        self.proofs = proofs or {}
        self.calls: list[tuple[str, float | None]] = []

    def verify(
        self, candidate: ApprovedRoute, *, timeout: float | None = None
    ) -> object:
        self.calls.append((candidate.id, timeout))
        result = self.proofs.get(candidate.id, IDENTITY)
        if isinstance(result, BaseException):
            raise result
        return result


def selector(
    routes: list[ApprovedRoute],
    probe: FakeProbe,
    verifier: FakeVerifier,
    **kwargs: object,
) -> RouteSelector:
    return RouteSelector(
        routes,
        expected_identity=IDENTITY,
        probe=probe,
        verifier=verifier,
        **kwargs,
    )


def test_empty_routes_return_redacted_unavailable_selection() -> None:
    probe = FakeProbe()
    verifier = FakeVerifier()

    result = selector([], probe, verifier).select()

    assert result.status == "unavailable"
    assert result.reason == "route_unavailable"
    assert result.selected_route is None
    assert result.attempts == ()
    assert result.to_endpoint() == {
        "status": "unavailable",
        "attempts": [],
        "reason": "route_unavailable",
    }
    assert probe.calls == []
    assert verifier.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("route_class", "internet"),
        ("id", ""),
        ("id", "x" * 129),
        ("url", "https://home.example.test/api/v1/bridge/ws"),
        ("url", "wss://home.example.test/api/v1/bridge"),
        ("url", "wss://user:password@home.example.test/api/v1/bridge/ws"),
        ("url", "wss://home.example.test/api/v1/bridge/ws?token=secret"),
        ("url", "wss://home.example.test/api/v1/bridge/ws#secret"),
        ("url", "wss://home.example.test/api/v1/bridge/ws?"),
        ("url", "wss://home.example.test/api/v1/bridge/ws#"),
        ("enabled", "yes"),
    ],
)
def test_approved_route_rejects_invalid_fields(field: str, value: object) -> None:
    values: dict[str, object] = {
        "route_class": "home",
        "id": "home",
        "url": "wss://home.example.test/api/v1/bridge/ws",
        "enabled": True,
    }
    values[field] = value

    with pytest.raises(RouteValidationError):
        ApprovedRoute(**values)  # type: ignore[arg-type]


def test_approved_route_is_frozen_and_mapping_shape_is_exact() -> None:
    candidate = ApprovedRoute.from_mapping(
        {
            "class": "home",
            "id": "home",
            "url": "wss://home.example.test/api/v1/bridge/ws",
            "enabled": True,
        }
    )

    assert candidate.to_descriptor().to_endpoint() == {"class": "home", "id": "home"}
    with pytest.raises(FrozenInstanceError):
        candidate.id = "changed"  # type: ignore[misc]
    with pytest.raises(RouteValidationError):
        ApprovedRoute.from_mapping(
            {
                "class": "home",
                "id": "home",
                "url": "wss://home.example.test/api/v1/bridge/ws",
                "enabled": True,
                "extra": "reject",
            }
        )


def test_duplicate_ids_and_invalid_expected_identity_are_rejected_before_ports() -> (
    None
):
    probe = FakeProbe()
    verifier = FakeVerifier()

    with pytest.raises(RouteValidationError, match="duplicate route id"):
        selector([route("home", "same"), route("tailscale", "same")], probe, verifier)
    with pytest.raises(RouteValidationError, match="expected Household Identity"):
        RouteSelector([], expected_identity="", probe=probe, verifier=verifier)
    assert probe.calls == []
    assert verifier.calls == []


def test_home_wins_and_lower_priority_routes_are_not_probed() -> None:
    candidates = [
        route("public", "public"),
        route("tailscale", "tail"),
        route("home", "home"),
    ]
    probe = FakeProbe()
    verifier = FakeVerifier()

    result = selector(candidates, probe, verifier, allow_public=True).select()

    assert result.status == "selected"
    assert result.selected_route is not None
    assert result.selected_route.to_endpoint() == {"class": "home", "id": "home"}
    assert [(attempt.route_id, attempt.outcome) for attempt in result.attempts] == [
        ("home", "reachable")
    ]
    assert [call[0] for call in probe.calls] == ["home"]
    assert [call[0] for call in verifier.calls] == ["home"]


def test_ready_selection_serializes_only_the_selected_route_descriptor() -> None:
    secret_url = "wss://secret.example.test/api/v1/bridge/ws"
    probe = FakeProbe()
    verifier = FakeVerifier()

    result = selector(
        [ApprovedRoute("home", "home", secret_url)], probe, verifier
    ).select()

    assert result.to_endpoint() == {
        "status": "selected",
        "attempts": [
            {
                "route": {"class": "home", "id": "home"},
                "outcome": "reachable",
            }
        ],
        "route": {"class": "home", "id": "home"},
    }
    assert secret_url not in json.dumps(result.to_endpoint())
    assert IDENTITY not in json.dumps(result.to_endpoint())


def test_equal_priority_routes_keep_input_order() -> None:
    probe = FakeProbe()
    verifier = FakeVerifier()

    result = selector(
        [route("tailscale", "second"), route("tailscale", "first")],
        probe,
        verifier,
    ).select()

    assert result.selected_route is not None
    assert result.selected_route.id == "second"
    assert [call[0] for call in probe.calls] == ["second"]


def test_home_failure_falls_back_to_tailscale_and_retains_typed_attempts() -> None:
    probe = FakeProbe({"home": ConnectionError("offline")})
    verifier = FakeVerifier()

    result = selector(
        [route("home", "home"), route("tailscale", "tail")], probe, verifier
    ).select()

    assert result.selected_route is not None
    assert result.selected_route.to_endpoint() == {"class": "tailscale", "id": "tail"}
    assert [
        (attempt.route_id, attempt.outcome, attempt.reason)
        for attempt in result.attempts
    ] == [
        ("home", "unavailable", "route_unavailable"),
        ("tail", "reachable", None),
    ]
    assert [call[0] for call in probe.calls] == ["home", "tail"]


def test_public_requires_explicit_opt_in_and_disabled_public_is_never_probed() -> None:
    candidates = [
        route("home", "home", enabled=True),
        route("public", "disabled-public", enabled=False),
        route("public", "enabled-public", enabled=True),
    ]
    probe = FakeProbe({"home": ConnectionError("offline")})
    verifier = FakeVerifier()

    without_public = selector(candidates, probe, verifier).select()
    assert without_public.status == "unavailable"
    assert [call[0] for call in probe.calls] == ["home"]

    probe.calls.clear()
    with_public = selector(candidates, probe, verifier, allow_public=True).select()
    assert with_public.status == "selected"
    assert with_public.selected_route is not None
    assert with_public.selected_route.id == "enabled-public"
    assert [call[0] for call in probe.calls] == ["home", "enabled-public"]


def test_identity_mismatch_is_rejected_before_the_next_route_is_selected() -> None:
    probe = FakeProbe()
    verifier = FakeVerifier({"home": "a-different-household"})

    result = selector(
        [route("home", "home"), route("tailscale", "tail")], probe, verifier
    ).select()

    assert result.selected_route is not None
    assert result.selected_route.id == "tail"
    assert [
        (attempt.route_id, attempt.outcome, attempt.reason)
        for attempt in result.attempts
    ] == [
        ("home", "identity_mismatch", "route_identity_mismatch"),
        ("tail", "reachable", None),
    ]
    assert [call[0] for call in verifier.calls] == ["home", "tail"]


def test_missing_identity_proof_is_rejected_before_fallback() -> None:
    probe = FakeProbe()
    verifier = FakeVerifier({"home": None})

    result = selector(
        [route("home", "home"), route("tailscale", "tail")], probe, verifier
    ).select()

    assert result.selected_route is not None
    assert result.selected_route.id == "tail"
    assert result.attempts[0].outcome == "identity_mismatch"
    assert result.attempts[0].reason == "route_identity_mismatch"


@pytest.mark.parametrize(
    ("route_id", "error", "expected"),
    [
        ("home", RouteTimeoutError("slow"), "timed_out"),
        ("tail", PermissionError("denied"), "unauthorized"),
        ("public", RouteIdentityMismatchError("wrong server"), "identity_mismatch"),
    ],
)
def test_all_failure_outcomes_are_typed_and_aggregated(
    route_id: str,
    error: BaseException,
    expected: str,
) -> None:
    candidates = [
        route("home", "home"),
        route("tailscale", "tail"),
        route("public", "public"),
    ]
    probe = FakeProbe(
        {
            "home": ConnectionError("offline"),
            "tail": ConnectionError("offline"),
            "public": ConnectionError("offline"),
            route_id: error,
        }
    )
    verifier = FakeVerifier()

    result = selector(candidates, probe, verifier, allow_public=True).select()

    assert result.status == "unavailable"
    assert result.reason == "route_unavailable"
    assert result.selected_route is None
    outcomes = {attempt.route_id: attempt.outcome for attempt in result.attempts}
    assert outcomes[route_id] == expected


def test_no_safe_route_retains_every_attempt_in_priority_order() -> None:
    probe = FakeProbe(
        {
            "home": RouteProbeResult("unavailable"),
            "tail": RouteProbeResult("unauthorized"),
            "public": RouteProbeResult("timed_out"),
        }
    )
    verifier = FakeVerifier()

    result = selector(
        [route("public", "public"), route("tailscale", "tail"), route("home", "home")],
        probe,
        verifier,
        allow_public=True,
    ).select()

    assert result.status == "unavailable"
    assert [
        (attempt.route_id, attempt.outcome, attempt.reason)
        for attempt in result.attempts
    ] == [
        ("home", "unavailable", "route_unavailable"),
        ("tail", "unauthorized", "route_unauthorized"),
        ("public", "timed_out", "route_timeout"),
    ]
    assert verifier.calls == []


@pytest.mark.parametrize(
    "outcome", ["unavailable", "unauthorized", "identity_mismatch", "timed_out"]
)
def test_typed_probe_failures_do_not_invoke_identity_verification(
    outcome: str,
) -> None:
    probe = FakeProbe({"home": RouteProbeResult(outcome)})
    verifier = FakeVerifier()

    result = selector([route("home", "home")], probe, verifier).select()

    assert result.status == "unavailable"
    assert result.attempts[0].outcome == outcome
    assert verifier.calls == []


def test_probe_timeout_does_not_call_identity_port_and_passes_a_bound() -> None:
    probe = FakeProbe({"home": TimeoutError("slow")})
    verifier = FakeVerifier()

    result = selector(
        [route("home", "home"), route("tailscale", "tail")], probe, verifier
    ).select()

    assert result.status == "selected"
    assert result.selected_route is not None
    assert result.selected_route.id == "tail"
    assert result.attempts[0].outcome == "timed_out"
    assert [call[0] for call in verifier.calls] == ["tail"]
    assert probe.calls[0][1] is not None
    assert probe.calls[0][1] > 0


def test_identity_timeout_is_typed_and_selection_continues() -> None:
    probe = FakeProbe()
    verifier = FakeVerifier({"home": TimeoutError("slow")})

    result = selector(
        [route("home", "home"), route("tailscale", "tail")], probe, verifier
    ).select()

    assert result.selected_route is not None
    assert result.selected_route.id == "tail"
    assert result.attempts[0].outcome == "timed_out"
    assert verifier.calls[0][1] is not None
    assert verifier.calls[0][1] > 0


def test_malformed_injected_results_stop_before_later_ports() -> None:
    probe = FakeProbe({"home": object()})
    verifier = FakeVerifier()

    with pytest.raises(RouteValidationError, match="probe returned"):
        selector(
            [route("home", "home"), route("tailscale", "tail")], probe, verifier
        ).select()
    assert [call[0] for call in probe.calls] == ["home"]
    assert verifier.calls == []

    probe = FakeProbe()
    verifier = FakeVerifier({"home": object()})
    with pytest.raises(RouteValidationError, match="verifier returned"):
        selector(
            [route("home", "home"), route("tailscale", "tail")], probe, verifier
        ).select()
    assert [call[0] for call in probe.calls] == ["home"]
    assert [call[0] for call in verifier.calls] == ["home"]


def test_descriptors_and_attempts_never_serialize_url_or_identity_proof() -> None:
    secret_url = "wss://secret.example.test/api/v1/bridge/ws"
    secret_identity = "identity-proof-must-not-leak"
    candidate = ApprovedRoute("home", "private", secret_url)
    probe = FakeProbe({"private": ConnectionError("offline")})
    verifier = FakeVerifier({"private": secret_identity})

    result = RouteSelector(
        [candidate],
        expected_identity=IDENTITY,
        probe=probe,
        verifier=verifier,
    ).select()
    serialized = json.dumps(result.to_endpoint())

    assert secret_url not in serialized
    assert secret_identity not in serialized
    assert "url" not in result.to_endpoint()["attempts"][0]["route"]
    assert result.attempts[0].route.to_endpoint() == {
        "class": "home",
        "id": "private",
    }


def test_identity_mismatch_exception_is_typed_without_exposing_exception_text() -> None:
    probe = FakeProbe()
    verifier = FakeVerifier({"home": RouteIdentityMismatchError("secret proof")})

    result = selector([route("home", "home")], probe, verifier).select()

    assert result.attempts[0].outcome == "identity_mismatch"
    assert "secret proof" not in json.dumps(result.to_endpoint())


def test_injected_ports_must_be_callable() -> None:
    with pytest.raises(RouteValidationError, match="route probe port"):
        RouteSelector(
            [], expected_identity=IDENTITY, probe=object(), verifier=FakeVerifier()
        )
    with pytest.raises(RouteValidationError, match="Household Identity verifier port"):
        RouteSelector(
            [], expected_identity=IDENTITY, probe=FakeProbe(), verifier=object()
        )

    class WrongProbe:
        def probe(self, candidate: ApprovedRoute) -> object:
            del candidate
            return True

    with pytest.raises(RouteValidationError, match="incompatible signature"):
        RouteSelector(
            [route("home", "home")],
            expected_identity=IDENTITY,
            probe=WrongProbe(),
            verifier=FakeVerifier(),
        ).select()


def test_probe_failure_cannot_be_used_to_bypass_identity_verification() -> None:
    with pytest.raises(RouteValidationError, match="invalid outcome"):
        RouteProbeError("reachable")

    failure = RouteUnavailableError()
    with pytest.raises(AttributeError):
        failure.outcome = "reachable"  # type: ignore[misc]


def test_selector_uses_an_immutable_route_snapshot() -> None:
    candidates = [route("home", "home"), route("tailscale", "tail")]
    probe = FakeProbe()
    verifier = FakeVerifier()
    route_selector = selector(candidates, probe, verifier)
    candidates.clear()

    result = route_selector.select()

    assert result.selected_route is not None
    assert result.selected_route.id == "home"
    assert [candidate.id for candidate in route_selector.routes] == ["home", "tail"]


def test_unordered_route_collections_are_rejected() -> None:
    with pytest.raises(RouteValidationError, match="iterable"):
        selector({route("home", "home")}, FakeProbe(), FakeVerifier())  # type: ignore[arg-type]


def test_large_timeout_and_clock_values_are_typed_validation_errors() -> None:
    with pytest.raises(RouteValidationError, match="timeout"):
        RouteSelector(
            [],
            expected_identity=IDENTITY,
            probe=FakeProbe(),
            verifier=FakeVerifier(),
            timeout=10**1000,
        )

    with pytest.raises(RouteValidationError, match="clock"):
        selector(
            [route("home", "home")],
            FakeProbe(),
            FakeVerifier(),
            clock=lambda: 10**1000,
        ).select()


def test_late_operational_failure_is_recorded_as_timeout() -> None:
    class LateProbe:
        def probe(
            self, candidate: ApprovedRoute, *, timeout: float | None = None
        ) -> object:
            del candidate, timeout
            raise ConnectionError("arrived after deadline")

    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 0.0 if clock_calls < 3 else 2.0

    result = RouteSelector(
        [route("home", "home")],
        expected_identity=IDENTITY,
        probe=LateProbe(),
        verifier=FakeVerifier(),
        timeout=1.0,
        clock=clock,
    ).select()

    assert result.status == "unavailable"
    assert result.attempts[0].outcome == "timed_out"


def test_custom_clock_can_expire_a_route_between_probe_and_identity_proof() -> None:
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 0.0 if clock_calls < 3 else 2.0

    probe = FakeProbe()
    verifier = FakeVerifier()

    result = selector(
        [route("home", "home"), route("tailscale", "tail")],
        probe,
        verifier,
        timeout=1.0,
        clock=clock,
    ).select()

    assert result.selected_route is not None
    assert result.selected_route.id == "tail"
    assert result.attempts[0].outcome == "timed_out"
