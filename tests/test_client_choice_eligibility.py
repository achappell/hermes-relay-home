"""Paired-client typed-choice eligibility.

A paired personal client (credential scope ``client_claim``, no Room) is
interactive only when an administrator lists it in ``client_devices`` and its
claim was made under the Device's current configuration projection. These tests
drive the real configuration store, credential service, and claim store.
"""

from __future__ import annotations

import json

import pytest

from tests.test_client_claims_api import Home, _grant

BASE_KEYS = ("rooms", "profiles", "wake_mappings", "devices")


@pytest.fixture
def home(tmp_path):
    home = Home(tmp_path, claims_per_device=8)
    # Production wiring (runtime.py) attaches this beside the grant checker.
    home.claims.set_client_revision_resolver(home.service.client_configuration_revision)
    return home


def _publish(home, client_devices=None):
    current = home.configuration.read()
    candidate = {key: current[key] for key in BASE_KEYS}
    if client_devices is not None:
        candidate["client_devices"] = client_devices
    response = home.call(
        "PUT",
        "/api/v1/configuration",
        {
            "schema": 1,
            "expected_revision": current["revision"],
            "snapshot": candidate,
        },
        admin=True,
    )
    assert response.status == 200, response.body
    return response.body["snapshot"]


def _allow(device_id, value=True):
    return [{"id": device_id, "capabilities": {"interactive_choice": value}}]


def _claim(home, material, grant, claim_id):
    revision = home.configuration_for(material)["revision"]
    response = home.call(
        "POST",
        "/api/v1/client-claims",
        {
            "schema": 1,
            "claim_id": claim_id,
            "device_id": material["device_id"],
            "configuration_revision": revision,
            "grant_id": grant["grant_id"],
        },
        credential=material["credential"],
    )
    assert response.status == 200, response.body
    return response.body["conversation_handle"]


def _browser(home):
    material = home.pair("browser-1", ["amanda"], endpoint_type="browser")
    return material, _grant(material, "Amanda")


def _interactive(home, handle, material):
    grant = home.claims.resolve(handle, material["device_id"])
    assert grant is not None, "the claim must stay open"
    assert grant.protected_capabilities == frozenset()
    return grant.interactive_choice


def test_a_paired_client_is_not_interactive_by_default(home) -> None:
    material, grant = _browser(home)

    handle = _claim(home, material, grant, "claim-1")

    assert "client_devices" not in home.configuration.read()
    assert _interactive(home, handle, material) is False


def test_an_opted_in_client_is_interactive_for_a_claim_made_afterwards(home) -> None:
    material, grant = _browser(home)
    _publish(home, _allow(material["device_id"]))

    handle = _claim(home, material, grant, "claim-1")

    assert _interactive(home, handle, material) is True


def test_an_explicit_false_entry_denies(home) -> None:
    material, grant = _browser(home)
    _publish(home, _allow(material["device_id"], False))

    handle = _claim(home, material, grant, "claim-1")

    assert _interactive(home, handle, material) is False


def test_the_allowance_names_the_device_and_does_not_leak_to_another(home) -> None:
    browser, browser_grant = _browser(home)
    other = home.pair("laptop", ["jensen"], endpoint_type="tui")
    _publish(home, _allow(browser["device_id"]))

    other_handle = _claim(home, other, _grant(other, "Jensen"), "claim-other")
    browser_handle = _claim(home, browser, browser_grant, "claim-browser")

    assert _interactive(home, other_handle, other) is False
    assert _interactive(home, browser_handle, browser) is True


def test_a_claim_made_before_the_opt_in_stays_open_but_unadvertised(home) -> None:
    material, grant = _browser(home)
    early = _claim(home, material, grant, "claim-early")
    assert _interactive(home, early, material) is False

    _publish(home, _allow(material["device_id"]))

    assert _interactive(home, early, material) is False
    late = _claim(home, material, grant, "claim-late")
    assert _interactive(home, late, material) is True
    assert _interactive(home, early, material) is False


def test_removing_the_opt_in_leaves_the_claim_open_but_unadvertised(home) -> None:
    material, grant = _browser(home)
    _publish(home, _allow(material["device_id"]))
    handle = _claim(home, material, grant, "claim-1")
    assert _interactive(home, handle, material) is True

    _publish(home, None)

    assert _interactive(home, handle, material) is False
    # A new claim cannot become interactive either.
    again = _claim(home, material, grant, "claim-2")
    assert _interactive(home, again, material) is False


def test_an_unrelated_publish_drops_the_advertisement_until_a_new_claim(home) -> None:
    material, grant = _browser(home)
    _publish(home, _allow(material["device_id"]))
    handle = _claim(home, material, grant, "claim-1")
    assert _interactive(home, handle, material) is True

    _publish(home, _allow(material["device_id"]))

    assert _interactive(home, handle, material) is False
    refreshed = _claim(home, material, grant, "claim-2")
    assert _interactive(home, refreshed, material) is True


def test_projection_drift_from_a_later_grant_does_not_disable_an_opted_in_client(
    home,
) -> None:
    """Regression: a client claim stores the Device's projected revision, which
    runs ahead of the global snapshot revision once a grant changes. Comparing
    the global revision would leave the capability permanently dead."""
    material, _ = _browser(home)
    home.configuration_for(material)  # first projection records the snapshot revision
    added = home.call(
        "POST",
        f"/api/v1/devices/{material['device_id']}/profile-grants",
        {"schema": 1, "profile_id": "spark", "idempotency_key": "drift-1"},
        admin=True,
    )
    assert added.status == 200, added.body
    home.configuration_for(material)  # projects the grant change at the same snapshot
    snapshot = _publish(home, _allow(material["device_id"]))
    projected = home.configuration_for(material)["revision"]
    assert projected > snapshot["revision"], "the drift scenario must be real"
    grants = home.configuration_for(material)["client_grants"]
    spark = next(g for g in grants if g["label"] == "Spark")

    handle = _claim(home, material, spark, "claim-1")

    stored = home.claims.resolve(handle, material["device_id"]).configuration_revision
    assert stored == projected
    assert _interactive(home, handle, material) is True


def test_a_grant_change_after_the_claim_drops_the_advertisement(home) -> None:
    material, grant = _browser(home)
    _publish(home, _allow(material["device_id"]))
    handle = _claim(home, material, grant, "claim-1")
    assert _interactive(home, handle, material) is True

    added = home.call(
        "POST",
        f"/api/v1/devices/{material['device_id']}/profile-grants",
        {"schema": 1, "profile_id": "spark", "idempotency_key": "later-1"},
        admin=True,
    )
    assert added.status == 200, added.body

    assert _interactive(home, handle, material) is False


def test_reading_eligibility_never_advances_the_projection(home) -> None:
    material, grant = _browser(home)
    _publish(home, _allow(material["device_id"]))
    handle = _claim(home, material, grant, "claim-1")
    before = home.configuration_for(material)["revision"]

    for _ in range(3):
        assert _interactive(home, handle, material) is True

    assert home.configuration_for(material)["revision"] == before


def test_device_configuration_never_discloses_client_devices(home) -> None:
    material, _ = _browser(home)
    _publish(home, _allow(material["device_id"]))

    snapshot = home.configuration_for(material)

    assert "client_devices" not in snapshot
    assert "client_devices" not in json.dumps(snapshot)
    assert "interactive_choice" not in json.dumps(snapshot)


def test_a_client_device_entry_cannot_widen_the_browser_credential_scope(home) -> None:
    material, _ = _browser(home)
    _publish(home, _allow(material["device_id"]))

    scope = home.service.current_scope(material["device_id"], material["generation"])

    assert scope.capabilities == ("client_claim",)
    assert not scope.rooms
    assert not scope.wake_mappings


def test_a_client_claim_ignores_a_room_device_registry_entry(home) -> None:
    material, grant = _browser(home)
    configuration = home.configuration.read()
    raw = {key: configuration[key] for key in BASE_KEYS}
    raw["rooms"] = [{"id": "kitchen", "name": "Kitchen"}]
    raw["devices"] = [
        {
            "id": material["device_id"],
            "name": "Shadow",
            "room_id": "kitchen",
            "priority": 1,
            "capabilities": {"wake_claim": True, "interactive_choice": True},
        }
    ]
    home.configuration.replace(
        expected_revision=configuration["revision"], candidate=raw
    )
    handle = _claim(home, material, grant, "claim-1")

    assert _interactive(home, handle, material) is False


def test_an_id_in_both_registries_is_rejected_at_publish(home) -> None:
    material, _ = _browser(home)
    configuration = home.configuration.read()
    candidate = {key: configuration[key] for key in BASE_KEYS}
    candidate["devices"] = [
        {
            "id": material["device_id"],
            "name": "Shadow",
            "room_id": "kitchen",
            "priority": 1,
            "capabilities": {"wake_claim": True},
        }
    ]
    candidate["client_devices"] = _allow(material["device_id"])

    response = home.call(
        "PUT",
        "/api/v1/configuration",
        {
            "schema": 1,
            "expected_revision": configuration["revision"],
            "snapshot": candidate,
        },
        admin=True,
    )

    assert response.status == 400
    assert home.configuration.read()["revision"] == configuration["revision"]


def test_an_opted_in_client_claim_answers_a_typed_choice_through_the_bridge(
    home,
) -> None:
    """The grant from a real client claim reaches the bridge: with Standard
    advertising typed choice, the flags appear and one choose is delivered."""
    from hermes_home.bridge import HomeBridge
    from tests.test_client_claim_close_bridge import ContextAuthenticator
    from tests.test_standard_bridge import FakeJsonSocket, FakeSocketFactory, _event

    material, grant = _browser(home)
    _publish(home, _allow(material["device_id"]))
    handle = _claim(home, material, grant, "claim-1")
    choice = {
        "request_id": "choice-request-1",
        "prompt_kind": "choice",
        "text": "Choose a step.",
        "choice": {
            "object_id": "source-object-1",
            "freshness": "source-freshness-1",
            "operations": ["choose", "explore"],
        },
        "options": [{"id": "inspect", "label": "Inspect"}],
    }
    socket = FakeJsonSocket(
        [
            _event(
                "gateway.ready",
                {"capabilities": {"prompt.choose": True, "prompt.explore": True}},
            ),
            {"jsonrpc": "2.0", "id": "home-1", "result": {"session_id": "runtime-1"}},
            {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "prompt.request",
                    "session_id": "runtime-1",
                    "payload": choice,
                },
            },
            {"jsonrpc": "2.0", "id": "home-3", "result": {"resolved": True}},
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=ContextAuthenticator(material["device_id"], 1),
        conversation_resolver=home.claims.resolve,
        gateway_socket_factory=FakeSocketFactory(socket),
    )

    status = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle=handle,
    )
    bridge.submit_prompt("Run the harmless proof")
    event = bridge.next_event()

    assert status.capabilities["prompt.choose"] is True
    assert status.capabilities["prompt.explore"] is True
    assert event.choice_capabilities == frozenset({"prompt.choose", "prompt.explore"})
    result = bridge.respond_prompt(
        event,
        {
            "operation": "choose",
            "option_id": "inspect",
            "object_id": "source-object-1",
            "freshness": "source-freshness-1",
        },
    )
    assert result == {"resolved": True}
    assert socket.sent[-1]["method"] == "prompt.choose"
    bridge.close()


def test_a_client_that_is_not_opted_in_gets_no_choice_flags_from_the_bridge(
    home,
) -> None:
    from hermes_home.bridge import HomeBridge
    from tests.test_client_claim_close_bridge import ContextAuthenticator
    from tests.test_standard_bridge import FakeJsonSocket, FakeSocketFactory, _event

    material, grant = _browser(home)
    handle = _claim(home, material, grant, "claim-1")
    socket = FakeJsonSocket(
        [
            _event(
                "gateway.ready",
                {"capabilities": {"prompt.choose": True, "prompt.explore": True}},
            ),
            {"jsonrpc": "2.0", "id": "home-1", "result": {"session_id": "runtime-1"}},
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=ContextAuthenticator(material["device_id"], 1),
        conversation_resolver=home.claims.resolve,
        gateway_socket_factory=FakeSocketFactory(socket),
    )

    status = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle=handle,
    )

    assert "prompt.choose" not in status.capabilities
    assert "prompt.explore" not in status.capabilities
    bridge.close()
