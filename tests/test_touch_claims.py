import json

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.bridge.production import ConversationGrantStore
from hermes_home.domain.arbitration import ArbitrationEngine, WakeDecision
from hermes_home.domain.conversations import InMemoryConversationClaimStore
from hermes_home.domain.credentials import CredentialScope, TouchBinding
from hermes_home.storage.sqlite import SQLiteConfigurationStore

CONFIGURATION = {
    "rooms": [{"id": "kitchen", "name": "Kitchen"}],
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
            "id": "puck-kitchen",
            "name": "Kitchen Puck",
            "room_id": "kitchen",
            "priority": 1,
            "capabilities": {"wake_claim": True},
        },
        {
            "id": "touch-kitchen",
            "name": "Kitchen Touch",
            "room_id": "kitchen",
            "priority": 2,
            "capabilities": {"wake_claim": False},
        },
    ],
}

TOUCH_CLAIM = {
    "schema": 1,
    "claim_id": "touch-claim-1",
    "device_id": "touch-kitchen",
    "configuration_revision": 1,
    "initiation": {"kind": "tap", "observed_at_ms": 1_720_000_000_000},
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _scope(*capabilities: str, binding: bool = True) -> CredentialScope:
    return CredentialScope.from_values(
        rooms=["kitchen"],
        capabilities=capabilities,
        touch_binding=(
            {"room_id": "kitchen", "profile_id": "family"} if binding else None
        ),
    )


def _application(tmp_path, *, scope: CredentialScope, handles=None):
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    store.replace(expected_revision=0, candidate=CONFIGURATION)
    clock = FakeClock()
    claims = InMemoryConversationClaimStore(
        handle_factory=(iter(handles).__next__ if handles is not None else None),
        clock=clock.monotonic,
    )
    engine = ArbitrationEngine(
        configuration=store.read,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-wake-1",
    )
    application = HomeApplication(
        configuration_store=store,
        arbitration_engine=engine,
        admin_token="admin-secret",
        device_credentials={
            "touch-secret": "touch-kitchen",
            "puck-secret": "puck-kitchen",
        },
        static_device_scopes={
            "touch-kitchen": scope,
            "puck-kitchen": CredentialScope.from_values(
                rooms=["kitchen"],
                capabilities=["wake_claim"],
                wake_mappings=["hey-hermes"],
            ),
        },
        conversation_claim_store=claims,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    return application, store, claims


def _post_touch(application, claim=TOUCH_CLAIM, *, credential="touch-secret"):
    return application.handle(
        "POST",
        "/api/v1/touch-claims",
        {
            "Authorization": f"Device {credential}",
            "Content-Type": "application/json",
        },
        json.dumps(claim).encode(),
    )


def _post_wake(application, *, claim_id="wake-claim-1"):
    return application.handle(
        "POST",
        "/api/v1/wake-claims",
        {
            "Authorization": "Device puck-secret",
            "Content-Type": "application/json",
        },
        json.dumps(
            {
                "schema": 1,
                "claim_id": claim_id,
                "device_id": "puck-kitchen",
                "wake_mapping_id": "hey-hermes",
                "configuration_revision": 1,
                "observation": {"detector": "device-local"},
                "acoustic_evidence": {"kind": "opaque-v1", "value": 0.91},
                "availability": "ready",
            }
        ).encode(),
    )


def test_touch_scope_round_trips_a_bounded_room_profile_binding() -> None:
    scope = _scope("touch_claim")

    assert scope.touch_binding == TouchBinding("kitchen", "family")
    assert (
        CredentialScope.from_values(
            rooms=["kitchen"],
            capabilities=["touch_claim"],
            touch_binding={"room_id": "kitchen", "profile_id": "family"},
        )
        == scope
    )


def test_touch_claim_grants_an_opaque_handle_without_arbitration_metadata(
    tmp_path,
) -> None:
    application, store, claims = _application(
        tmp_path,
        scope=_scope("touch_claim"),
        handles=["touch-handle"],
    )

    try:
        response = _post_touch(application)

        assert response.status == 200
        assert response.body == {
            "schema": 1,
            "claim_id": "touch-claim-1",
            "decision": "granted",
            "configuration_revision": 1,
            "conversation_handle": "touch-handle",
        }
        assert claims._claims["touch-handle"]["room_id"] == "kitchen"
        assert claims._claims["touch-handle"]["profile_id"] == "family"
        assert "profile_id" not in json.dumps(response.body)
    finally:
        store.close()


@pytest.mark.parametrize(
    "scope",
    [
        _scope(binding=False),
        CredentialScope.from_values(rooms=["kitchen"], capabilities=[]),
    ],
)
def test_touch_claim_requires_a_capability_and_binding(tmp_path, scope) -> None:
    application, store, _claims = _application(tmp_path, scope=scope)

    try:
        response = _post_touch(application)

        assert response.status == 403
        assert response.body == {
            "schema": 1,
            "error": {"code": "touch_claim_unavailable"},
        }
    finally:
        store.close()


def test_touch_claim_rejects_stale_revision_and_unavailable_profile(tmp_path) -> None:
    application, store, _claims = _application(tmp_path, scope=_scope("touch_claim"))

    try:
        stale = _post_touch(
            application,
            {**TOUCH_CLAIM, "configuration_revision": 0},
        )
        assert stale.status == 409
        assert stale.body == {"schema": 1, "error": {"code": "stale_configuration"}}

        store.replace(
            expected_revision=1,
            candidate={
                **CONFIGURATION,
                "profiles": [{"id": "family", "name": "Family", "available": False}],
            },
        )
        unavailable = _post_touch(
            application,
            {**TOUCH_CLAIM, "configuration_revision": 2},
        )
        assert unavailable.status == 409
        assert unavailable.body == {
            "schema": 1,
            "error": {"code": "profile_unavailable"},
        }
    finally:
        store.close()


@pytest.mark.parametrize("activity", ["capture", "turn", "playback", "response_ready"])
def test_touch_into_a_live_room_is_denied_without_disturbing_the_active_claim(
    tmp_path, activity
) -> None:
    application, store, claims = _application(
        tmp_path,
        scope=_scope("touch_claim"),
        handles=["wake-handle"],
    )

    try:
        wake = _post_wake(application)
        claims.mark_open(wake.body["conversation_handle"], "puck-kitchen")
        claims.record_activity(
            wake.body["conversation_handle"], "puck-kitchen", activity
        )

        response = _post_touch(application)

        assert response.status == 409
        assert response.body == {"schema": 1, "error": {"code": "room_busy"}}
        assert claims._claims["wake-handle"]["status"] == "active"
        assert claims._claims["wake-handle"]["activity"] == activity
    finally:
        store.close()


def test_second_touch_from_the_same_device_is_conversation_active(tmp_path) -> None:
    application, store, _claims = _application(
        tmp_path,
        scope=_scope("touch_claim"),
        handles=["first-touch", "second-touch"],
    )

    try:
        first = _post_touch(application)
        second = _post_touch(
            application,
            {**TOUCH_CLAIM, "claim_id": "touch-claim-2"},
        )

        assert first.status == 200
        assert second.status == 409
        assert second.body == {
            "schema": 1,
            "error": {"code": "conversation_active"},
        }
    finally:
        store.close()


def test_touch_supersedes_only_the_idle_tail(tmp_path) -> None:
    application, store, claims = _application(
        tmp_path,
        scope=_scope("touch_claim"),
        handles=["wake-handle", "touch-handle"],
    )

    try:
        wake = _post_wake(application)
        wake_handle = wake.body["conversation_handle"]
        claims.mark_open(wake_handle, "puck-kitchen")
        claims.record_activity(wake_handle, "puck-kitchen", "response_ready")
        claims.record_activity(wake_handle, "puck-kitchen", "playback_complete")

        response = _post_touch(application)

        assert response.status == 200
        assert response.body["conversation_handle"] == "touch-handle"
        assert claims._claims[wake_handle]["status"] == "closed"
        assert claims._claims[wake_handle]["close_reason"] == "superseded_by_touch"
        assert claims._claims["touch-handle"]["status"] == "active"
    finally:
        store.close()


def test_touch_request_is_strict_and_does_not_accept_profile_or_session_ids(
    tmp_path,
) -> None:
    application, store, _claims = _application(tmp_path, scope=_scope("touch_claim"))

    try:
        response = _post_touch(
            application,
            {**TOUCH_CLAIM, "profile_id": "family"},
        )

        assert response.status == 400
        assert response.body == {"schema": 1, "error": {"code": "invalid_request"}}
    finally:
        store.close()


def test_production_claim_store_allows_a_touch_to_take_the_idle_tail(tmp_path) -> None:
    now = [100.0]
    handles = iter(["wake-handle", "touch-handle"])
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=lambda: {"revision": 1, **CONFIGURATION},
        clock=lambda: now[0],
        handle_factory=handles.__next__,
    )

    try:
        first = store.create_from_decision(
            WakeDecision(
                claim_id="wake-claim-1",
                decision="granted",
                arbitration_id="arb-1",
                configuration_revision=1,
                device_id="puck-kitchen",
                room_id="kitchen",
                wake_mapping_id="hey-hermes",
                profile_id="family",
            ),
            credential_generation=None,
        )
        store.mark_open(first, "puck-kitchen")
        store.record_activity(first, "puck-kitchen", "response_ready")
        store.record_activity(first, "puck-kitchen", "playback_complete")

        second = store.create_from_decision(
            WakeDecision(
                claim_id="touch-claim-1",
                decision="granted",
                arbitration_id="touch",
                configuration_revision=1,
                device_id="touch-kitchen",
                room_id="kitchen",
                profile_id="family",
                claim_kind="touch",
            ),
            credential_generation=None,
        )

        assert second == "touch-handle"
        assert store._connection.execute(
            "SELECT status, close_reason FROM conversation_claims WHERE handle = ?",
            (first,),
        ).fetchone() == ("closed", "superseded_by_touch")
    finally:
        store.close()


def test_expired_idle_tail_is_released_before_a_touch_claim(tmp_path) -> None:
    now = [100.0]
    handles = iter(["wake-handle", "touch-handle"])
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=lambda: {"revision": 1, **CONFIGURATION},
        clock=lambda: now[0],
        handle_factory=handles.__next__,
    )

    try:
        first = store.create_from_decision(
            WakeDecision(
                claim_id="wake-claim-1",
                decision="granted",
                arbitration_id="arb-1",
                configuration_revision=1,
                device_id="puck-kitchen",
                room_id="kitchen",
                wake_mapping_id="hey-hermes",
                profile_id="family",
            ),
            credential_generation=None,
        )
        store.mark_open(first, "puck-kitchen")
        store.record_activity(first, "puck-kitchen", "response_ready")
        store.record_activity(first, "puck-kitchen", "playback_complete")
        now[0] = 109.0

        second = store.create_from_decision(
            WakeDecision(
                claim_id="touch-claim-1",
                decision="granted",
                arbitration_id="touch",
                configuration_revision=1,
                device_id="touch-kitchen",
                room_id="kitchen",
                profile_id="family",
                claim_kind="touch",
            ),
            credential_generation=None,
        )

        assert second == "touch-handle"
        assert store._connection.execute(
            "SELECT status, close_reason FROM conversation_claims WHERE handle = ?",
            (first,),
        ).fetchone() == ("closed", "idle_expired")
    finally:
        store.close()
