from __future__ import annotations

import json

import pytest

from hermes_home.auth.static import StaticCredentialAuthenticator
from hermes_home.bridge.production import (
    ConversationGrantStore,
    create_standard_bridge_factory,
)
from hermes_home.bridge.standard import ConversationGrant, HomeBridge


def _write_grants(path, records: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"schema": 1, "grants": records}),
        encoding="utf-8",
    )


def test_conversation_grant_store_resolves_device_binding_and_persists_session(
    tmp_path,
) -> None:
    path = tmp_path / "conversation-grants.json"
    _write_grants(
        path,
        [
            {
                "handle": "android-sprint-1",
                "device_id": "pixel-6a",
                "profile_id": "amanda",
                "status": "active",
            },
            {
                "handle": "revoked-handle",
                "device_id": "pixel-6a",
                "profile_id": "amanda",
                "status": "revoked",
            },
        ],
    )

    store = ConversationGrantStore(path)

    grant = store.resolve("android-sprint-1", "pixel-6a")
    assert grant == ConversationGrant(
        handle="android-sprint-1",
        device_id="pixel-6a",
        profile_id="amanda",
        status="active",
    )
    assert store.resolve("android-sprint-1", "different-device") is None
    assert store.resolve("missing", "pixel-6a") is None

    assert grant is not None
    store.persist_session(grant, "durable-session-1")

    assert store.resolve("android-sprint-1", "pixel-6a") == ConversationGrant(
        handle="android-sprint-1",
        device_id="pixel-6a",
        profile_id="amanda",
        session_id="durable-session-1",
        status="active",
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["grants"][0]["session_id"] == "durable-session-1"


def test_conversation_grant_store_rejects_malformed_or_duplicate_records(
    tmp_path,
) -> None:
    path = tmp_path / "conversation-grants.json"
    _write_grants(
        path,
        [
            {
                "handle": "same",
                "device_id": "device-1",
                "profile_id": "amanda",
                "status": "active",
            },
            {
                "handle": "same",
                "device_id": "device-2",
                "profile_id": "amanda",
                "status": "active",
            },
        ],
    )

    with pytest.raises(ValueError, match="duplicate handles"):
        ConversationGrantStore(path)


def test_production_factory_keeps_pilot_authority_server_side(tmp_path) -> None:
    path = tmp_path / "conversation-grants.json"
    _write_grants(path, [])
    authenticator = StaticCredentialAuthenticator(
        admin_token="admin-secret",
        device_credentials={"device-secret": "pixel-6a"},
    )

    factory = create_standard_bridge_factory(
        gateway_url="wss://standard.example/api/ws",
        hermes_token="server-secret",
        grants_file=path,
        device_authenticator=authenticator,
    )

    bridge = factory()
    assert isinstance(bridge, HomeBridge)
    assert "server-secret" not in repr(bridge)


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
    path = tmp_path / "conversation-grants.json"
    _write_grants(path, [])

    with pytest.raises(ValueError, match="Standard gateway URL"):
        create_standard_bridge_factory(
            gateway_url=gateway_url,
            hermes_token="server-secret",
            grants_file=path,
            device_authenticator=object(),
        )
