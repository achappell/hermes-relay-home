from __future__ import annotations

import sqlite3
import time

import pytest

from hermes_home.auth.static import StaticCredentialAuthenticator
from hermes_home.bridge.production import (
    ConversationGrantStore,
    create_standard_bridge_factory,
)
from hermes_home.bridge.standard import ConversationGrant, HomeBridge
from hermes_home.domain.arbitration import WakeDecision


def _configuration() -> dict[str, object]:
    return {
        "revision": 3,
        "rooms": [
            {"id": "kitchen", "name": "Kitchen"},
            {"id": "office", "name": "Office"},
        ],
        "profiles": [
            {"id": "amanda", "name": "Amanda", "available": True},
            {"id": "family", "name": "Family", "available": True},
        ],
        "wake_mappings": [
            {
                "id": "hey-hermes",
                "phrase": "Hey Hermes",
                "profile_id": "amanda",
                "active": True,
            }
        ],
        "devices": [],
    }


def _decision(
    claim_id: str,
    *,
    device_id: str = "pixel-6a",
    room_id: str = "kitchen",
    wake_mapping_id: str = "hey-hermes",
    profile_id: str = "amanda",
    revision: int = 3,
) -> WakeDecision:
    return WakeDecision(
        claim_id=claim_id,
        decision="granted",
        arbitration_id=f"arb-{claim_id}",
        configuration_revision=revision,
        device_id=device_id,
        room_id=room_id,
        wake_mapping_id=wake_mapping_id,
        profile_id=profile_id,
    )


def test_conversation_claim_store_binds_the_winner_and_session(tmp_path) -> None:
    path = tmp_path / "home.sqlite3"
    store = ConversationGrantStore(
        path,
        configuration=_configuration,
        handle_factory=lambda: "opaque-conversation-1",
    )

    handle = store.create_from_decision(_decision("claim-1"), credential_generation=2)
    grant = store.resolve(handle, "pixel-6a")

    assert grant == ConversationGrant(
        handle=handle,
        device_id="pixel-6a",
        profile_id="amanda",
        status="active",
        credential_generation=2,
        configuration_revision=3,
    )
    assert store.resolve(handle, "different-device") is None
    assert store.resolve("missing", "pixel-6a") is None

    assert grant is not None
    store.persist_session(grant, "durable-session-1")

    assert store.resolve(handle, "pixel-6a") == ConversationGrant(
        handle=handle,
        device_id="pixel-6a",
        profile_id="amanda",
        session_id="durable-session-1",
        status="active",
        credential_generation=2,
        configuration_revision=3,
    )
    row = (
        sqlite3.connect(path)
        .execute(
            "SELECT claim_id, profile_id, session_id, credential_generation "
            "FROM conversation_claims WHERE handle = ?",
            (handle,),
        )
        .fetchone()
    )
    assert row == ("claim-1", "amanda", "durable-session-1", 2)
    store.close()


def test_conversation_grant_carries_explicit_interactive_choice_authority(
    tmp_path,
) -> None:
    configuration = _configuration()
    configuration["devices"] = [
        {
            "id": "pixel-6a",
            "name": "Interactive Puck",
            "room_id": "kitchen",
            "priority": 1,
            "capabilities": {
                "wake_claim": True,
                "interactive_choice": True,
            },
        }
    ]
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=lambda: configuration,
        handle_factory=lambda: "interactive-choice-handle",
    )
    handle = store.create_from_decision(
        _decision("interactive-choice-claim"),
        credential_generation=2,
    )

    grant = store.resolve(handle, "pixel-6a")

    assert grant is not None
    assert grant.configuration_revision == 3
    assert grant.interactive_choice is True
    configuration["revision"] = 4
    refreshed = store.resolve(handle, "pixel-6a")
    assert refreshed is not None
    assert refreshed.configuration_revision == 3
    assert refreshed.interactive_choice is False
    store.close()


def test_unopened_claim_expires_after_90_seconds_and_releases_its_room(
    tmp_path,
) -> None:
    now = [100.0]
    handles = iter(["first-handle", "second-handle"])
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        clock=lambda: now[0],
        handle_factory=handles.__next__,
    )
    first = store.create_from_decision(
        _decision("claim-first"), credential_generation=None
    )

    row = store._connection.execute(
        "SELECT idle_deadline, activity FROM conversation_claims WHERE handle = ?",
        (first,),
    ).fetchone()
    assert row == (190.0, "ready")

    now[0] = 190.0
    assert store.resolve(first, "pixel-6a") is None
    closed = store._connection.execute(
        "SELECT status, close_reason FROM conversation_claims WHERE handle = ?",
        (first,),
    ).fetchone()
    assert closed == ("closed", "first_open_expired")

    second = store.create_from_decision(
        _decision("claim-second"), credential_generation=None
    )
    assert store.resolve(second, "pixel-6a") is not None
    store.close()


def test_unopened_claim_timer_releases_room_without_another_request(tmp_path) -> None:
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        first_open_timeout_seconds=0.02,
        handle_factory=lambda: "short-deadline-handle",
    )
    handle = store.create_from_decision(
        _decision("claim-short"), credential_generation=None
    )
    deadline = time.monotonic() + 1.0
    row = None
    while time.monotonic() < deadline:
        row = store._connection.execute(
            "SELECT status, close_reason FROM conversation_claims WHERE handle = ?",
            (handle,),
        ).fetchone()
        if row == ("closed", "first_open_expired"):
            break
        time.sleep(0.005)

    assert row == ("closed", "first_open_expired")
    store.close()


def test_open_claim_does_not_expire_on_the_first_open_deadline(tmp_path) -> None:
    now = [100.0]
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        clock=lambda: now[0],
        handle_factory=lambda: "opened-handle",
    )
    handle = store.create_from_decision(
        _decision("claim-opened"), credential_generation=None
    )

    store.mark_open(handle, "pixel-6a")
    now[0] = 200.0

    assert store.resolve(handle, "pixel-6a") is not None
    assert store._connection.execute(
        "SELECT activity, idle_deadline FROM conversation_claims WHERE handle = ?",
        (handle,),
    ).fetchone() == ("open", None)
    store.close()


def test_production_watch_snapshot_revalidates_connection_revision_and_expiry(
    tmp_path,
) -> None:
    now = [100.0]
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        clock=lambda: now[0],
        idle_timeout_seconds=8,
        handle_factory=lambda: "watch-production-handle",
    )
    handle = store.create_from_decision(
        _decision("watch-production-claim"), credential_generation=2
    )

    assert store.watch_snapshot("pixel-6a", configuration_revision=3) is None
    store.mark_open(handle, "pixel-6a")
    snapshot = store.watch_snapshot("pixel-6a", configuration_revision=3)
    assert snapshot is not None
    assert snapshot.credential_generation == 2
    assert snapshot.activity == "open"
    assert store.watch_snapshot("pixel-6a", configuration_revision=4) is None

    now[0] += 60
    assert store.watch_snapshot("pixel-6a", configuration_revision=3) is not None
    store.mark_disconnected(handle, "pixel-6a")
    assert store.watch_snapshot("pixel-6a", configuration_revision=3) is None
    assert store.resolve(handle, "pixel-6a") is not None

    store.mark_open(handle, "pixel-6a")
    store.record_activity(handle, "pixel-6a", "response_ready")
    store.record_activity(handle, "pixel-6a", "playback_complete")
    now[0] += 8
    assert store.watch_snapshot("pixel-6a", configuration_revision=3) is None
    store.close()


def test_conversation_claim_store_rejects_replay_and_same_room_overlap(
    tmp_path,
) -> None:
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        handle_factory=lambda: "opaque-conversation-1",
    )
    store.create_from_decision(_decision("claim-1"), credential_generation=None)

    with pytest.raises(RuntimeError, match="active conversation"):
        store.create_from_decision(_decision("claim-2"), credential_generation=None)
    with pytest.raises(RuntimeError, match="active conversation"):
        store.create_from_decision(_decision("claim-1"), credential_generation=None)

    independent = ConversationGrantStore(
        tmp_path / "other.sqlite3",
        configuration=_configuration,
        handle_factory=lambda: "opaque-conversation-2",
    )
    assert (
        independent.create_from_decision(
            _decision("claim-1", room_id="office"), credential_generation=None
        )
        == "opaque-conversation-2"
    )
    store.close()
    independent.close()


def test_conversation_claim_store_closes_claims_by_current_authority(tmp_path) -> None:
    handles = iter(
        [
            "generation-one",
            "generation-two",
            "profile-claim",
            "mapping-claim",
            "remaining-claim",
        ]
    )
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        handle_factory=handles.__next__,
    )

    generation_one = store.create_from_decision(
        _decision("claim-generation-one", room_id="kitchen"),
        credential_generation=1,
    )
    generation_two = store.create_from_decision(
        _decision("claim-generation-two", room_id="office"),
        credential_generation=2,
    )
    assert (
        store.close_device_claims(
            "pixel-6a", current_generation=2, reason="endpoint_revoked"
        )
        == 1
    )
    assert store.resolve(generation_one, "pixel-6a") is None
    assert store.resolve(generation_two, "pixel-6a") is not None
    assert (
        store.close_device_claims(
            "pixel-6a", current_generation=None, reason="endpoint_revoked"
        )
        == 1
    )

    profile_claim = store.create_from_decision(
        _decision("claim-profile", profile_id="amanda"),
        credential_generation=None,
    )
    mapping_claim = store.create_from_decision(
        _decision(
            "claim-mapping",
            room_id="office",
            wake_mapping_id="family-alias",
            profile_id="family",
        ),
        credential_generation=None,
    )
    assert store.close_profile_claims(["amanda"], reason="profile_revoked") == 1
    assert store.resolve(profile_claim, "pixel-6a") is None
    assert store.close_mapping_claims(["family-alias"], reason="mapping_revoked") == 1
    assert store.resolve(mapping_claim, "pixel-6a") is None

    store.create_from_decision(_decision("claim-remaining"), credential_generation=None)
    assert store.close_all_claims(reason="configuration_unverified") == 1
    store.close()


def test_independent_rooms_cannot_bind_to_the_same_standard_session(tmp_path) -> None:
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        handle_factory=iter(["kitchen-handle", "office-handle"]).__next__,
    )
    kitchen_handle = store.create_from_decision(
        _decision("claim-kitchen"), credential_generation=None
    )
    office_handle = store.create_from_decision(
        _decision("claim-office", room_id="office"), credential_generation=None
    )
    kitchen_grant = store.resolve(kitchen_handle, "pixel-6a")
    office_grant = store.resolve(office_handle, "pixel-6a")
    assert kitchen_grant is not None
    assert office_grant is not None
    store.persist_session(kitchen_grant, "shared-standard-session")

    with pytest.raises(ValueError, match="already bound"):
        store.persist_session(office_grant, "shared-standard-session")
    store.close()


def test_conversation_claim_activity_only_expires_after_playback_ack(tmp_path) -> None:
    now = [100.0]
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        clock=lambda: now[0],
        idle_timeout_seconds=8,
        handle_factory=lambda: "opaque-conversation-1",
    )
    handle = store.create_from_decision(
        _decision("claim-1"), credential_generation=None
    )

    store.record_activity(handle, "pixel-6a", "capture")
    now[0] = 110.0
    assert store.resolve(handle, "pixel-6a") is not None
    store.record_activity(handle, "pixel-6a", "turn")
    with pytest.raises(ValueError, match="completion was not pending"):
        store.record_activity(handle, "pixel-6a", "playback_complete")

    store.record_activity(handle, "pixel-6a", "response_ready")
    store.record_activity(handle, "pixel-6a", "playback_complete")
    now[0] += 7.9
    assert store.resolve(handle, "pixel-6a") is not None
    store.record_activity(handle, "pixel-6a", "capture")
    now[0] += 20
    assert store.resolve(handle, "pixel-6a") is not None
    store.record_activity(handle, "pixel-6a", "playback")
    store.record_activity(handle, "pixel-6a", "playback_complete")
    now[0] += 8
    assert store.resolve(handle, "pixel-6a") is None
    store.close()


def test_activity_cannot_revive_a_claim_after_its_idle_deadline(tmp_path) -> None:
    now = [100.0]
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        clock=lambda: now[0],
        idle_timeout_seconds=8,
        handle_factory=lambda: "expired-activity-handle",
    )
    handle = store.create_from_decision(
        _decision("claim-idle-expired"), credential_generation=None
    )
    store.mark_open(handle, "pixel-6a")
    store.record_activity(handle, "pixel-6a", "playback")
    store.record_activity(handle, "pixel-6a", "playback_complete")
    now[0] = 108.0

    with pytest.raises(ValueError, match="expired"):
        store.record_activity(handle, "pixel-6a", "capture")

    assert store._connection.execute(
        "SELECT status, close_reason FROM conversation_claims WHERE handle = ?",
        (handle,),
    ).fetchone() == ("closed", "idle_expired")
    store.close()


def test_conversation_claim_keeps_profile_after_mapping_edits_and_closes_on_revoke(
    tmp_path,
) -> None:
    snapshot = _configuration()
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=lambda: snapshot,
        handle_factory=lambda: "opaque-conversation-1",
    )
    handle = store.create_from_decision(
        _decision("claim-1"), credential_generation=None
    )

    snapshot["wake_mappings"] = [
        {
            "id": "hey-hermes",
            "phrase": "Hey Hermes",
            "profile_id": "family",
            "active": True,
        }
    ]
    assert store.resolve(handle, "pixel-6a").profile_id == "amanda"

    snapshot["wake_mappings"] = []
    assert store.resolve(handle, "pixel-6a").profile_id == "amanda"

    snapshot["profiles"] = [
        {"id": "amanda", "name": "Amanda", "available": False},
        {"id": "family", "name": "Family", "available": True},
    ]
    assert store.resolve(handle, "pixel-6a") is None
    row = store._connection.execute(
        "SELECT status, close_reason FROM conversation_claims WHERE handle = ?",
        (handle,),
    ).fetchone()
    assert row == ("closed", "profile_revoked")
    store.close()


def test_conversation_claim_closes_when_its_mapping_is_explicitly_revoked(
    tmp_path,
) -> None:
    snapshot = _configuration()
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=lambda: snapshot,
        handle_factory=lambda: "opaque-conversation-1",
    )
    handle = store.create_from_decision(
        _decision("claim-1"), credential_generation=None
    )
    snapshot["wake_mappings"][0]["active"] = False

    assert store.resolve(handle, "pixel-6a") is None
    row = store._connection.execute(
        "SELECT status, close_reason FROM conversation_claims WHERE handle = ?",
        (handle,),
    ).fetchone()
    assert row == ("closed", "mapping_revoked")
    store.close()


def test_conversation_claim_store_closes_active_claims_after_process_restart(
    tmp_path,
) -> None:
    path = tmp_path / "home.sqlite3"
    first = ConversationGrantStore(
        path,
        configuration=_configuration,
        handle_factory=lambda: "opaque-conversation-1",
    )
    first.create_from_decision(_decision("claim-1"), credential_generation=None)
    first.close()

    restarted = ConversationGrantStore(path, configuration=_configuration)
    assert restarted.resolve("opaque-conversation-1", "pixel-6a") is None
    row = restarted._connection.execute(
        "SELECT status, close_reason FROM conversation_claims WHERE handle = ?",
        ("opaque-conversation-1",),
    ).fetchone()
    assert row == ("closed", "service_restart")
    restarted.close()


def test_production_factory_keeps_standard_authority_server_side(tmp_path) -> None:
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
    )
    authenticator = StaticCredentialAuthenticator(
        admin_token="admin-secret",
        device_credentials={"device-secret": "pixel-6a"},
    )

    factory = create_standard_bridge_factory(
        gateway_url="wss://standard.example/api/ws",
        hermes_token="server-secret",
        conversation_store=store,
        device_authenticator=authenticator,
    )

    bridge = factory()
    assert isinstance(bridge, HomeBridge)
    assert getattr(bridge._conversation_disconnector, "__self__", None) is store
    assert "server-secret" not in repr(bridge)
    bridge.close()
    store.close()


@pytest.mark.parametrize(
    "gateway_url",
    [
        "https://standard.example/api/ws",
        "wss://standard.example/not-api-ws",
        "wss://standard.example/api/ws#fragment",
    ],
)
def test_production_factory_rejects_unsafe_standard_target(
    tmp_path, gateway_url
) -> None:
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
    )

    with pytest.raises(ValueError, match="Standard gateway URL"):
        create_standard_bridge_factory(
            gateway_url=gateway_url,
            hermes_token="server-secret",
            conversation_store=store,
            device_authenticator=object(),
        )
    store.close()
