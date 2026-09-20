import json
from copy import deepcopy

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.domain.arbitration import ArbitrationEngine, WakeDecision
from hermes_home.domain.conversations import InMemoryConversationClaimStore
from hermes_home.domain.credentials import CredentialScope, CredentialService
from hermes_home.storage.credentials import InMemoryCredentialStore
from hermes_home.storage.sqlite import SQLiteConfigurationStore

CONFIGURATION = {
    "rooms": [
        {"id": "kitchen", "name": "Kitchen"},
        {"id": "office", "name": "Office"},
    ],
    "profiles": [{"id": "family", "name": "Family", "available": True}],
    "wake_mappings": [
        {
            "id": "hey-hermes",
            "phrase": "Hey Hermes",
            "profile_id": "family",
            "active": True,
        }
    ],
    "devices": [
        {
            "id": "display-kitchen",
            "name": "Kitchen Display",
            "room_id": "kitchen",
            "priority": 1,
            "capabilities": {"wake_claim": True},
        }
    ],
}


def _application(tmp_path, *, capabilities=("watch_view",)):
    configuration_store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    configuration_store.replace(expected_revision=0, candidate=CONFIGURATION)
    claim_store = InMemoryConversationClaimStore(
        handle_factory=lambda: "opaque-watch-conversation",
    )
    application = HomeApplication(
        configuration_store=configuration_store,
        arbitration_engine=ArbitrationEngine(configuration=configuration_store.read),
        admin_token="admin-secret",
        device_credentials={"watch-secret": "watch-phone"},
        conversation_claim_store=claim_store,
        static_device_scopes={
            "watch-phone": CredentialScope.from_values(
                rooms=["kitchen"],
                capabilities=capabilities,
            )
        },
    )
    return application, configuration_store, claim_store


def _open_claim(
    claim_store: InMemoryConversationClaimStore,
    *,
    credential_generation: int | None = None,
    device_id: str = "display-kitchen",
    room_id: str = "kitchen",
    claim_id: str = "watch-claim",
) -> str:
    handle = claim_store.create_from_decision(
        WakeDecision(
            claim_id=claim_id,
            decision="granted",
            arbitration_id="watch-arbitration",
            configuration_revision=1,
            device_id=device_id,
            room_id=room_id,
            wake_mapping_id="hey-hermes",
            profile_id="family",
        ),
        credential_generation=credential_generation,
    )
    claim_store.mark_open(handle, device_id)
    return handle


def _watch(
    application: HomeApplication,
    *,
    method="GET",
    target="display-kitchen",
    authorization="Device watch-secret",
):
    return application.handle(
        method,
        f"/api/v1/devices/{target}/watch",
        {"Authorization": authorization},
        b"",
    )


def _durable_watch_application(tmp_path):
    configuration_store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    configuration_store.replace(expected_revision=0, candidate=CONFIGURATION)
    credential_store = InMemoryCredentialStore()
    service = CredentialService(
        store=credential_store,
        root_secret=b"w" * 32,
        clock=lambda: 1_000.0,
        id_factory=iter(["watch-offer", "watch-request", "watch-device"]).__next__,
        token_factory=iter(["watch-enrollment-code", "watch-secret"]).__next__,
        confirmation_factory=lambda: "ABCD2345",
    )
    offer = service.create_offer()
    request = service.submit_request(
        enrollment_code=offer.enrollment_code,
        endpoint_id="watch-endpoint",
        label="Watch Phone",
        endpoint_type="phone",
        requested_rooms=["kitchen"],
        requested_capabilities=["watch_view"],
        secure_storage="platform_secure_store",
    )
    service.approve_request(
        request.request_id,
        CredentialScope.from_values(rooms=["kitchen"], capabilities=["watch_view"]),
        configured_rooms=["kitchen", "office"],
    )
    material = service.consume_request(
        request.request_id,
        enrollment_code=offer.enrollment_code,
        secure_storage="platform_secure_store",
    )
    claim_store = InMemoryConversationClaimStore(
        handle_factory=lambda: "durable-watch-conversation",
    )
    application = HomeApplication(
        configuration_store=configuration_store,
        arbitration_engine=ArbitrationEngine(configuration=configuration_store.read),
        admin_token="admin-secret",
        device_credentials={},
        credential_service=service,
        conversation_claim_store=claim_store,
    )
    return application, configuration_store, credential_store, claim_store, material


def test_watch_returns_one_bounded_safe_snapshot(tmp_path) -> None:
    application, store, claims = _application(tmp_path)
    try:
        _open_claim(claims)

        response = _watch(application)

        assert response.status == 200
        watch = response.body["watch"]
        assert watch["status"] == "available"
        assert watch["endpoint"] == {
            "id": "display-kitchen",
            "name": "Kitchen Display",
        }
        assert watch["profile_label"] == "Family"
        assert watch["route"] == {"class": "home", "id": "local"}
        assert watch["health"] == "ready"
        assert watch["task"]["summary"] == "Conversation starting"
        assert watch["preview"] == {
            "kind": "safe_state",
            "summary": "Conversation starting",
        }
        encoded = json.dumps(response.body)
        for forbidden in (
            "profile_id",
            "transcript",
            "audio",
            "credential",
            "watch-secret",
        ):
            assert forbidden not in encoded
    finally:
        store.close()


def test_watch_is_unavailable_without_current_state_or_after_disconnect(
    tmp_path,
) -> None:
    application, store, claims = _application(tmp_path)
    try:
        no_state = _watch(application)
        assert no_state.body["watch"]["status"] == "unavailable"
        assert no_state.body["watch"]["reason"] == "no_current_state"

        handle = _open_claim(claims)
        assert _watch(application).body["watch"]["status"] == "available"

        claims.mark_disconnected(handle, "display-kitchen")
        disconnected = _watch(application)
        assert disconnected.body["watch"] == {
            "status": "unavailable",
            "reason": "no_current_state",
            "endpoint": {
                "id": "display-kitchen",
                "name": "Kitchen Display",
            },
        }
        assert claims._claims[handle]["status"] == "active"
    finally:
        store.close()


def test_watch_rejects_missing_capability_and_out_of_scope_target(tmp_path) -> None:
    application, store, _claims = _application(tmp_path, capabilities=("wake_claim",))
    try:
        assert _watch(application).status == 403
    finally:
        store.close()

    second_tmp_path = tmp_path / "out-of-scope"
    second_tmp_path.mkdir()
    application, store, _claims = _application(second_tmp_path)
    try:
        candidate = deepcopy(CONFIGURATION)
        candidate["devices"] = [
            *candidate["devices"],
            {
                "id": "display-office",
                "name": "Office Display",
                "room_id": "office",
                "priority": 1,
                "capabilities": {"wake_claim": True},
            },
        ]
        store.replace(expected_revision=1, candidate=candidate)
        assert _watch(application, target="display-office").status == 404
    finally:
        store.close()


def test_durable_watch_view_authenticates_observer_without_target_generation_coupling(
    tmp_path,
) -> None:
    application, configuration_store, credential_store, claims, material = (
        _durable_watch_application(tmp_path)
    )
    try:
        _open_claim(claims, credential_generation=2)

        response = _watch(
            application,
            authorization=f"Device {material.credential}",
        )

        assert response.status == 200
        assert response.body["watch"]["status"] == "available"
        revoked = application.handle(
            "POST",
            f"/api/v1/devices/{material.device_id}/revoke",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )
        assert revoked.status == 200
        assert (
            _watch(application, authorization=f"Device {material.credential}").status
            == 401
        )
    finally:
        configuration_store.close()
        credential_store.close()


@pytest.mark.parametrize(
    ("activity", "summary"),
    [
        ("capture", "Listening"),
        ("turn", "Processing the current turn"),
        ("playback", "Speaking"),
        ("response_ready", "Response ready"),
        ("idle", "Conversation idle"),
        ("playback_complete", "Conversation ready"),
    ],
)
def test_watch_reports_bounded_activity_summaries(
    tmp_path, activity: str, summary: str
) -> None:
    application, store, claims = _application(tmp_path)
    try:
        handle = _open_claim(claims)
        claims._claims[handle]["session_id"] = "watch-session"
        claims.record_activity(handle, "display-kitchen", activity)

        response = _watch(application)

        assert response.status == 200
        assert response.body["watch"]["task"] == {
            "state": activity,
            "summary": summary,
            "session_present": True,
        }
    finally:
        store.close()


def test_watch_stale_configuration_is_unavailable_without_fallback(tmp_path) -> None:
    application, store, claims = _application(tmp_path)
    try:
        _open_claim(claims)
        store.replace(expected_revision=1, candidate=CONFIGURATION)

        response = _watch(application)

        assert response.status == 200
        assert response.body["watch"]["status"] == "unavailable"
        assert claims._claims["opaque-watch-conversation"]["status"] == "active"
    finally:
        store.close()


def test_watch_route_has_no_control_operation(tmp_path) -> None:
    application, store, claims = _application(tmp_path)
    try:
        _open_claim(claims)

        response = _watch(application, method="POST")

        assert response.status == 404
        assert claims._claims["opaque-watch-conversation"]["status"] == "active"
    finally:
        store.close()
