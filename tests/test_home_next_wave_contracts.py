from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "_bmad-output" / "specs"
CONTRACTS = ROOT / "docs" / "contracts" / "v1"
ARCHITECTURE = (
    ROOT
    / "_bmad-output"
    / "planning-artifacts"
    / "architecture"
    / "architecture-hermes-relay-home-2026-09-12"
)


def test_home_next_wave_contract_packages_are_present():
    expected = (
        SPECS / "spec-home-service-foundation" / "credential-lifecycle.md",
        SPECS / "spec-profile-mapping-conversation-claims" / "SPEC.md",
        SPECS / "spec-profile-mapping-conversation-claims" / "state-machine.md",
        SPECS / "spec-home-bridge-route-roaming" / "SPEC.md",
        SPECS / "spec-home-bridge-route-roaming" / "route-session-state.md",
        SPECS / "spec-household-diagnostics-incident-review" / "SPEC.md",
        SPECS
        / "spec-household-diagnostics-incident-review"
        / "diagnostics-contract.md",
    )

    for artifact in expected:
        assert artifact.is_file(), artifact

    assert (CONTRACTS / "bridge.md").is_file()


def test_route_roaming_contract_targets_pinned_standard_transport():
    route_spec = (SPECS / "spec-home-bridge-route-roaming" / "SPEC.md").read_text()
    route_state = (
        SPECS / "spec-home-bridge-route-roaming" / "route-session-state.md"
    ).read_text()

    for contract in (route_spec, route_state):
        assert "/api/ws" in contract
        assert "/api/audio/speak-stream" in contract
        assert "speech_timing" in contract

    assert "`hello`, `turn`, `interrupt`, `ping`" not in route_spec
    assert "`hello`, `turn`, `interrupt`, `ping`" not in route_state
    assert "`hello_ack`" not in route_state
    assert "baseline has no `speech_timing` event" in route_spec
    assert "No `speech_timing` event in the pinned baseline" in route_state


def test_architecture_links_all_home_next_wave_and_standard_baseline_companions():
    spine = (ARCHITECTURE / "ARCHITECTURE-SPINE.md").read_text()

    expected_links = (
        "../../../specs/spec-home-service-foundation/SPEC.md",
        "../../../specs/spec-profile-mapping-conversation-claims/SPEC.md",
        "../../../specs/spec-home-bridge-route-roaming/SPEC.md",
        "../../../specs/spec-household-diagnostics-incident-review/SPEC.md",
        "../../../specs/spec-standard-hermes-compatibility-migration/standard-baseline.md",
    )
    for link in expected_links:
        assert link in spine

    rubric = (ARCHITECTURE / "reviews" / "review-rubric.md").read_text()
    assert "formal Slice C and Slice D specifications are now written" in rubric
    assert "still need to be written" not in rubric


def test_endpoint_bridge_contract_pins_home_boundary_without_leaking_hermes_identity():
    bridge = (CONTRACTS / "bridge.md").read_text()
    credentials = (
        SPECS / "spec-home-service-foundation" / "credential-lifecycle.md"
    ).read_text()
    migration = (
        SPECS
        / "spec-standard-hermes-compatibility-migration"
        / "SPEC.md"
    ).read_text()

    assert "`/api/v1/bridge/ws`" in bridge
    assert "Authorization: Device <device-credential>" in bridge
    assert '"method": "conversation.open"' in bridge
    assert '"method": "prompt.submit"' in bridge
    assert '"method": "session.interrupt"' in bridge
    assert "runtime Hermes Session ID" in bridge
    assert "raw signed" in bridge
    assert "32 random bytes encoded as unpadded base64url" in credentials
    assert "no endpoint API that converts a personal Hermes bearer" in credentials
    assert "Session-bearing surface migrations use the Home bridge first" in migration
