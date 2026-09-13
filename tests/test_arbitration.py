import pytest

from hermes_home.domain.arbitration import ArbitrationEngine, WakeDecision

CONFIGURATION = {
    "revision": 7,
    "rooms": [{"id": "kitchen", "name": "Kitchen"}],
    "wake_mappings": [{"id": "hey-hermes", "name": "Hey Hermes"}],
    "devices": [
        {
            "id": "puck-kitchen",
            "name": "Kitchen Puck",
            "room_id": "kitchen",
            "profile_id": "family",
            "priority": 1,
            "capabilities": {"wake_claim": True},
        }
    ],
}

TWO_DEVICE_CONFIGURATION = {
    **CONFIGURATION,
    "devices": [
        {
            **CONFIGURATION["devices"][0],
            "id": "puck-near",
            "name": "Near Puck",
            "priority": 2,
        },
        {
            **CONFIGURATION["devices"][0],
            "id": "puck-priority",
            "name": "Priority Puck",
            "priority": 1,
        },
    ],
}

CLAIM = {
    "schema": 1,
    "claim_id": "claim-1",
    "device_id": "puck-kitchen",
    "wake_mapping_id": "hey-hermes",
    "observation": {"detector": "device-local"},
    "acoustic_evidence": {"kind": "opaque-v1", "value": 0.91},
    "availability": "ready",
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def test_first_valid_claim_opens_a_250_millisecond_window() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-1",
    )

    submission = engine.submit(CLAIM, authenticated_device_id="puck-kitchen")

    assert submission.accepted is True
    assert submission.claim_id == "claim-1"
    assert submission.deadline == pytest.approx(0.250)
    assert engine.finalize() is None


def test_single_claim_is_granted_after_the_window_closes() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-1",
    )
    engine.submit(CLAIM, authenticated_device_id="puck-kitchen")

    clock.now = 0.250
    decisions = engine.finalize()

    assert decisions == {
        "claim-1": WakeDecision(
            claim_id="claim-1",
            decision="granted",
            arbitration_id="arb-1",
            configuration_revision=7,
        )
    }


def test_highest_acoustic_proximity_wins_before_priority_breaks_a_tie() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: TWO_DEVICE_CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-2",
    )
    near_claim = {**CLAIM, "claim_id": "claim-near", "device_id": "puck-near"}
    priority_claim = {
        **CLAIM,
        "claim_id": "claim-priority",
        "device_id": "puck-priority",
        "acoustic_evidence": {"kind": "opaque-v1", "value": 0.80},
    }

    assert engine.submit(near_claim, authenticated_device_id="puck-near").accepted
    clock.now = 0.100
    assert engine.submit(
        priority_claim,
        authenticated_device_id="puck-priority",
    ).accepted
    clock.now = 0.250

    decisions = engine.finalize()

    assert decisions["claim-near"].decision == "granted"
    assert decisions["claim-priority"].decision == "denied"
    assert decisions["claim-priority"].reason == "lost_arbitration"


def test_equal_acoustic_proximity_uses_lower_device_priority() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: TWO_DEVICE_CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-3",
    )
    near_claim = {**CLAIM, "claim_id": "claim-near", "device_id": "puck-near"}
    priority_claim = {
        **CLAIM,
        "claim_id": "claim-priority",
        "device_id": "puck-priority",
    }

    engine.submit(near_claim, authenticated_device_id="puck-near")
    engine.submit(priority_claim, authenticated_device_id="puck-priority")
    clock.now = 0.250

    decisions = engine.finalize()

    assert decisions["claim-priority"].decision == "granted"
    assert decisions["claim-near"].decision == "denied"


def test_claim_with_wrong_authenticated_identity_is_denied_without_opening_window() -> (
    None
):
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-4",
    )

    submission = engine.submit(CLAIM, authenticated_device_id="another-device")

    assert submission.accepted is False
    assert submission.reason == "unauthorized"
    assert engine.finalize() is None


def test_late_claim_is_denied_and_does_not_start_a_replacement_round() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-5",
    )
    engine.submit(CLAIM, authenticated_device_id="puck-kitchen")
    clock.now = 0.251

    late = {**CLAIM, "claim_id": "claim-late"}
    submission = engine.submit(late, authenticated_device_id="puck-kitchen")

    assert submission.accepted is False
    assert submission.reason == "late"
    assert engine.finalize() is None


def test_claim_at_the_deadline_is_denied_as_late() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-deadline",
    )
    engine.submit(CLAIM, authenticated_device_id="puck-kitchen")
    clock.now = 0.250

    submission = engine.submit(
        {**CLAIM, "claim_id": "claim-at-deadline"},
        authenticated_device_id="puck-kitchen",
    )

    assert submission.accepted is False
    assert submission.reason == "late"


def test_unavailable_claim_is_denied() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-6",
    )
    unavailable = {**CLAIM, "availability": "busy"}

    submission = engine.submit(
        unavailable,
        authenticated_device_id="puck-kitchen",
    )

    assert submission.accepted is False
    assert submission.reason == "claim_denied"
    assert engine.finalize() is None


def test_claim_for_unknown_wake_mapping_is_denied() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-7",
    )
    unknown_mapping = {**CLAIM, "wake_mapping_id": "missing-mapping"}

    submission = engine.submit(
        unknown_mapping,
        authenticated_device_id="puck-kitchen",
    )

    assert submission.accepted is False
    assert submission.reason == "not_found"


def test_malformed_acoustic_evidence_is_denied() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-8",
    )
    malformed = {
        **CLAIM,
        "acoustic_evidence": {"kind": "opaque-v1", "value": "near"},
    }

    submission = engine.submit(
        malformed,
        authenticated_device_id="puck-kitchen",
    )

    assert submission.accepted is False
    assert submission.reason == "invalid_request"


def test_non_string_availability_is_invalid_request() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-9",
    )
    malformed = {**CLAIM, "availability": True}

    submission = engine.submit(
        malformed,
        authenticated_device_id="puck-kitchen",
    )

    assert submission.accepted is False
    assert submission.reason == "invalid_request"


def test_overflowing_numeric_acoustic_evidence_is_invalid_request() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-overflow",
    )
    malformed = {**CLAIM, "acoustic_evidence": {"value": 10**4300}}

    submission = engine.submit(
        malformed,
        authenticated_device_id="puck-kitchen",
    )

    assert submission.accepted is False
    assert submission.reason == "invalid_request"


def test_claim_id_cannot_be_replayed_after_its_decision_is_consumed() -> None:
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=lambda: CONFIGURATION,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-replay",
    )
    engine.submit(CLAIM, authenticated_device_id="puck-kitchen")
    clock.now = 0.250
    engine.finalize()
    assert engine.decision_for("claim-1") is not None

    clock.now = 0.500
    replay = engine.submit(CLAIM, authenticated_device_id="puck-kitchen")

    assert replay.accepted is False
    assert replay.reason == "duplicate_claim"
