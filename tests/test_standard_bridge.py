from __future__ import annotations

import math
from collections import deque
from threading import Event, Thread, current_thread
from types import SimpleNamespace

import pytest

from hermes_home.auth.static import StaticCredentialAuthenticator
from hermes_home.bridge import (
    AudioFrame,
    BridgeAuthorizationError,
    BridgeCapabilityUnavailable,
    BridgeProtocolError,
    BridgeRequestRejected,
    BridgeTimeoutError,
    BridgeTransportError,
    BridgeTurn,
    ConversationGrant,
    HomeBridge,
    StandardGatewayClient,
)
from hermes_home.bridge.production import ConversationGrantStore
from hermes_home.domain.arbitration import WakeDecision


class FakeJsonSocket:
    def __init__(
        self,
        incoming: list[dict[str, object]],
        *,
        catalog_pairs: list[str] | None = None,
        inject_catalog: bool = True,
    ) -> None:
        queued = list(incoming)
        self._catalog_pairs = tuple(catalog_pairs or ())
        self._inject_catalog = inject_catalog
        if inject_catalog and queued and _is_gateway_ready(queued[0]):
            shifted = []
            for queued_frame in queued[1:]:
                shifted_frame = dict(queued_frame)
                response_id = shifted_frame.get("id")
                if isinstance(response_id, str) and response_id.startswith("home-"):
                    suffix = response_id.removeprefix("home-")
                    if suffix.isdigit():
                        shifted_frame["id"] = f"home-{int(suffix) + 1}"
                shifted.append(shifted_frame)
            pairs = [
                [f"/{name.lstrip('/')}", f"fixture command {name}"]
                for name in self._catalog_pairs
            ]
            queued = [
                queued[0],
                {
                    "jsonrpc": "2.0",
                    "id": "home-1",
                    "result": {"pairs": pairs},
                },
                *shifted,
            ]
        self.incoming = deque(queued)
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def send_json(self, frame: dict[str, object]) -> None:
        self.sent.append(frame)

    def receive_json(self, timeout: float | None = None) -> dict[str, object]:
        del timeout
        if not self.incoming:
            raise ConnectionError("fixture socket exhausted")
        return self.incoming.popleft()

    def close(self) -> None:
        self.closed = True


def _is_gateway_ready(frame: dict[str, object]) -> bool:
    params = frame.get("params")
    return (
        frame.get("method") == "event"
        and isinstance(params, dict)
        and params.get("type") == "gateway.ready"
    )


class FakeAudioSocket:
    def __init__(self, incoming: list[object]) -> None:
        self.incoming = deque(incoming)
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def send_json(self, frame: dict[str, object]) -> None:
        self.sent.append(frame)

    def receive(self, timeout: float | None = None) -> object:
        del timeout
        if not self.incoming:
            raise ConnectionError("fixture audio socket exhausted")
        return self.incoming.popleft()

    def close(self) -> None:
        self.closed = True


class FakeSocketFactory:
    def __init__(self, socket: FakeJsonSocket) -> None:
        self.socket = socket
        self.urls: list[str] = []

    def open(self, url: str) -> FakeJsonSocket:
        self.urls.append(url)
        return self.socket


class CrossReadJsonSocket(FakeJsonSocket):
    def __init__(self, incoming: list[dict[str, object]]) -> None:
        super().__init__(incoming, inject_catalog=False)

    def receive_json(self, timeout: float | None = None) -> dict[str, object]:
        del timeout
        if current_thread().name == "rpc-request":
            frames = list(self.incoming)
            if len(frames) > 1:
                event = frames.pop(1)
                self.incoming = deque(frames)
                return event
        return super().receive_json()


class CommandLossJsonSocket(FakeJsonSocket):
    def send_json(self, frame: dict[str, object]) -> None:
        if frame.get("method") == "command.dispatch":
            raise ConnectionError("command socket lost")
        super().send_json(frame)


class BlockingCommandSocket(FakeJsonSocket):
    def __init__(
        self,
        incoming: list[dict[str, object]],
        *,
        catalog_pairs: list[str] | None = None,
    ) -> None:
        super().__init__(incoming, catalog_pairs=catalog_pairs)
        self.command_started = Event()
        self.release_command = Event()

    def send_json(self, frame: dict[str, object]) -> None:
        if frame.get("method") == "command.dispatch":
            self.command_started.set()
            self.release_command.wait(timeout=1)
            if self.closed:
                raise ConnectionError("command socket was closed")
        super().send_json(frame)


class SilentJsonSocket(FakeJsonSocket):
    def __init__(self, incoming: list[dict[str, object]]) -> None:
        super().__init__(incoming, inject_catalog=False)

    def receive_json(self, timeout: float | None = None) -> dict[str, object]:
        if self.incoming:
            return self.incoming.popleft()
        raise TimeoutError(f"no frame within {timeout}")


class BlockingSendJsonSocket(SilentJsonSocket):
    def __init__(self, incoming: list[dict[str, object]]) -> None:
        super().__init__(incoming)
        self.send_started = Event()
        self.release_send = Event()

    def send_json(self, frame: dict[str, object]) -> None:
        self.send_started.set()
        self.release_send.wait(timeout=1)
        if self.closed:
            raise ConnectionError("send socket was closed")
        super().send_json(frame)


class BlockingAudioSocketFactory:
    def __init__(self, socket: FakeAudioSocket) -> None:
        self.socket = socket
        self.started = Event()
        self.release = Event()

    def open(self, url: str) -> FakeAudioSocket:
        del url
        self.started.set()
        self.release.wait(timeout=1)
        return self.socket


class SilentAudioSocket(FakeAudioSocket):
    def receive(self, timeout: float | None = None) -> object:
        raise TimeoutError(f"no audio frame within {timeout}")


class FakeAudioSocketFactory:
    def __init__(self, socket: FakeAudioSocket) -> None:
        self.socket = socket
        self.urls: list[str] = []

    def open(self, url: str) -> FakeAudioSocket:
        self.urls.append(url)
        return self.socket


class SequencedSocketFactory:
    def __init__(self, *sockets: FakeJsonSocket) -> None:
        self.sockets = deque(sockets)
        self.urls: list[str] = []

    def open(self, url: str) -> FakeJsonSocket:
        self.urls.append(url)
        if not self.sockets:
            raise ConnectionError("no more gateway sockets")
        return self.sockets.popleft()


def _event(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": event_type, "payload": payload},
    }


def _session_event(
    event_type: str, payload: dict[str, object], session_id: str
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": event_type,
            "session_id": session_id,
            "payload": payload,
        },
    }


def _make_bridge(
    socket_factory,
    *,
    resolver=None,
    audio_factory=None,
    authenticator=None,
    gateway_url="wss://hermes.example/api/ws",
    audio_timeout=30.0,
):
    if authenticator is None:
        authenticator = StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        )
    if resolver is None:
        resolver = lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        )
    return HomeBridge(
        gateway_url=gateway_url,
        hermes_token="server-hermes-secret",
        device_authenticator=authenticator,
        conversation_resolver=resolver,
        gateway_socket_factory=socket_factory,
        audio_socket_factory=audio_factory,
        audio_timeout=audio_timeout,
    )


def test_bridge_ready_keeps_hermes_credential_and_session_identity_server_side():
    gateway_socket = FakeJsonSocket(
        [
            _event(
                "gateway.ready",
                {
                    "heartbeat": True,
                },
            ),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "stored-hermes-1",
                    "messages": [],
                },
            },
        ],
        catalog_pairs=["status"],
    )
    gateway_factory = FakeSocketFactory(gateway_socket)
    authenticator = StaticCredentialAuthenticator(
        admin_token="admin-secret",
        device_credentials={"device-secret": "puck-kitchen"},
    )

    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws?skin=home",
        hermes_token="server-hermes-secret",
        device_authenticator=authenticator,
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=gateway_factory,
    )

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert result.status == "ready"
    assert result.conversation_handle == "opaque-conversation-1"
    assert result.capabilities == {
        "commands": ["status"],
        "heartbeat": True,
        "timing": "absent",
        "interrupt": True,
        "audio": False,
    }
    assert bridge.state == "ready"
    endpoint_payload = result.to_endpoint()
    assert "server-hermes-secret" not in repr(endpoint_payload)
    assert "runtime-hermes-1" not in repr(endpoint_payload)
    assert "family" not in repr(endpoint_payload)
    assert gateway_factory.urls == [
        "wss://hermes.example/api/ws?skin=home&token=server-hermes-secret"
    ]
    assert gateway_socket.sent == [
        {
            "jsonrpc": "2.0",
            "id": "home-1",
            "method": "commands.catalog",
            "params": {},
        },
        {
            "jsonrpc": "2.0",
            "id": "home-2",
            "method": "session.create",
            "params": {"source": "home", "profile": "family"},
        },
    ]


def _persisting_bridge(
    gateway_socket: FakeJsonSocket,
    grant: ConversationGrant,
    persisted: list[tuple[ConversationGrant, str]],
    *,
    fail_persist: bool = False,
) -> HomeBridge:
    def persist(current: ConversationGrant, session_id: str) -> None:
        if fail_persist:
            raise OSError("grant store unavailable")
        persisted.append((current, session_id))

    return HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: grant,
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        session_persistor=persist,
    )


def test_bridge_does_not_bind_a_new_session_to_the_grant_on_open() -> None:
    # Hermes stores no empty Session; an open that ends before any turn must not
    # leave a never-stored, soon-reaped ID on the grant for later resumes.
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"heartbeat": True}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "durable-hermes-1",
                },
            },
        ]
    )
    grant = ConversationGrant(
        handle="opaque-conversation-1",
        device_id="puck-kitchen",
        profile_id="amanda",
    )
    persisted: list[tuple[ConversationGrant, str]] = []
    bridge = _persisting_bridge(gateway_socket, grant, persisted)

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle=grant.handle,
    )
    bridge.close()

    assert result.status == "ready"
    assert persisted == []


def test_bridge_binds_the_new_session_after_the_first_accepted_prompt() -> None:
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"heartbeat": True}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "durable-hermes-1",
                },
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"status": "accepted"}},
        ]
    )
    grant = ConversationGrant(
        handle="opaque-conversation-1",
        device_id="puck-kitchen",
        profile_id="amanda",
    )
    persisted: list[tuple[ConversationGrant, str]] = []
    bridge = _persisting_bridge(gateway_socket, grant, persisted)
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle=grant.handle,
    )

    bridge.submit_prompt("First turn")

    assert persisted == [(grant, "durable-hermes-1")]


def test_bridge_keeps_an_accepted_turn_when_session_binding_cannot_be_saved() -> None:
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"heartbeat": True}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "durable-hermes-1",
                },
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"status": "accepted"}},
        ]
    )
    grant = ConversationGrant(
        handle="opaque-conversation-1",
        device_id="puck-kitchen",
        profile_id="amanda",
    )
    bridge = _persisting_bridge(gateway_socket, grant, [], fail_persist=True)
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle=grant.handle,
    )

    turn = bridge.submit_prompt("First turn")

    assert turn.turn_id == "home-turn-1"
    assert bridge.active_turn_id == "home-turn-1"
    assert bridge.state == "ready"


def test_bridge_still_refreshes_an_already_bound_session_on_resume() -> None:
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"heartbeat": True}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-2",
                    "stored_session_id": "durable-hermes-1",
                },
            },
        ]
    )
    grant = ConversationGrant(
        handle="opaque-conversation-1",
        device_id="puck-kitchen",
        profile_id="amanda",
        session_id="durable-hermes-1",
    )
    persisted: list[tuple[ConversationGrant, str]] = []
    bridge = _persisting_bridge(gateway_socket, grant, persisted)

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle=grant.handle,
    )

    assert result.status == "ready"
    assert gateway_socket.sent[-1]["method"] == "session.resume"
    assert persisted == [(grant, "durable-hermes-1")]


def test_revoked_durable_claim_interrupts_live_standard_work(tmp_path) -> None:
    configuration = {
        "revision": 1,
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
        "devices": [],
    }
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=lambda: configuration,
        handle_factory=lambda: "live-revocation-handle",
    )
    handle = store.create_from_decision(
        WakeDecision(
            claim_id="live-revocation-claim",
            decision="granted",
            arbitration_id="live-revocation-arbitration",
            configuration_revision=1,
            device_id="puck-kitchen",
            room_id="kitchen",
            wake_mapping_id="hey-hermes",
            profile_id="family",
        ),
        credential_generation=None,
    )
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "durable-hermes-1",
                },
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
            {"jsonrpc": "2.0", "id": "home-3", "result": {"accepted": True}},
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=store.resolve,
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        session_persistor=store.persist_session,
        conversation_closer=store.close_claim,
        activity_recorder=store.record_activity,
        conversation_opener=store.mark_open,
        revocation_registrar=store.register_revocation_handler,
        revocation_unregistrar=store.unregister_revocation_handler,
    )

    try:
        assert (
            bridge.open(
                headers={"Authorization": "Device device-secret"},
                conversation_handle=handle,
            ).status
            == "ready"
        )
        bridge.submit_prompt("continue the accepted turn")

        assert store.close_profile_claims(["family"], reason="profile_revoked") == 1

        assert gateway_socket.sent[-1]["method"] == "session.interrupt"
        assert gateway_socket.sent[-1]["params"] == {"session_id": "runtime-hermes-1"}
        assert bridge.state == "unavailable"
        assert store.resolve(handle, "puck-kitchen") is None
    finally:
        bridge.close()
        store.close()


def test_session_binding_persist_failure_keeps_an_accepted_claim_live() -> None:
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "durable-hermes-1",
                },
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
        ]
    )
    grant = ConversationGrant(
        handle="opaque-conversation-1",
        device_id="puck-kitchen",
        profile_id="family",
    )
    closed: list[str] = []

    def fail_persist(_grant, _session_id):
        raise OSError("claim binding could not be persisted")

    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda _handle, _device_id: grant,
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        session_persistor=fail_persist,
        conversation_closer=lambda _handle, _device_id, *, reason: (
            closed.append(reason) or True
        ),
    )

    try:
        result = bridge.open(
            headers={"Authorization": "Device device-secret"},
            conversation_handle=grant.handle,
        )
        assert result.status == "ready"

        turn = bridge.submit_prompt("continue on the granted claim")

        assert turn.turn_id == "home-turn-1"
        assert bridge.active_turn_id == "home-turn-1"
        assert bridge.state == "ready"
        assert closed == []
        assert [frame["method"] for frame in gateway_socket.sent] == [
            "commands.catalog",
            "session.create",
            "prompt.submit",
        ]
    finally:
        bridge.close()


def test_explicit_close_closes_claim_locally_when_standard_is_unavailable() -> None:
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
        ]
    )
    closed: list[str] = []
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        conversation_closer=lambda _handle, _device_id, *, reason: (
            closed.append(reason) or True
        ),
    )
    assert (
        bridge.open(
            headers={"Authorization": "Device device-secret"},
            conversation_handle="opaque-conversation-1",
        ).status
        == "ready"
    )
    bridge._invalidate_binding("authorization_unavailable")

    assert bridge.state == "unavailable"
    assert bridge.close_conversation() is True

    assert closed == ["stopped"]
    assert bridge.state == "disconnected"


def test_standard_gateway_keeps_concurrent_rpc_and_event_reads_ordered():
    gateway_socket = CrossReadJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"operation": "first"},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "session_id": "runtime-hermes-1",
                    "payload": {"text": "ordered"},
                },
            },
        ]
    )
    client = StandardGatewayClient(
        url="wss://hermes.example/api/ws",
        token="server-hermes-secret",
        socket_factory=FakeSocketFactory(gateway_socket),
    )
    client.connect()
    results: dict[str, dict[str, object]] = {}
    events: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def request() -> None:
        try:
            results["request"] = client.request("first", {})
        except Exception as error:  # noqa: BLE001  # pragma: no cover
            errors.append(error)

    def read_event() -> None:
        try:
            events.append(client.next_event())
        except Exception as error:  # noqa: BLE001  # pragma: no cover
            errors.append(error)

    threads = [
        Thread(target=request, name="rpc-request"),
        Thread(target=read_event, name="event-reader"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
    client.close()

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert results == {"request": {"operation": "first"}}
    assert events[0]["type"] == "message.delta"


def test_bridge_rejects_empty_conversation_handle_before_opening_hermes():
    gateway_socket = FakeJsonSocket([])
    gateway_factory = FakeSocketFactory(gateway_socket)
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=gateway_factory,
    )

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="",
    )

    assert result.status == "unavailable"
    assert result.reason == "invalid_request"
    assert gateway_factory.urls == []


def test_bridge_resumes_a_granted_session_instead_of_creating_a_new_one():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-2"},
            },
        ]
    )
    resumed_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-3"},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
            session_id="stored-hermes-1",
        ),
        gateway_socket_factory=SequencedSocketFactory(gateway_socket, resumed_socket),
    )

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert result.status == "ready"
    assert gateway_socket.sent == [
        {
            "jsonrpc": "2.0",
            "id": "home-1",
            "method": "commands.catalog",
            "params": {},
        },
        {
            "jsonrpc": "2.0",
            "id": "home-2",
            "method": "session.resume",
            "params": {
                "session_id": "stored-hermes-1",
                "source": "home",
                "profile": "family",
            },
        },
    ]
    reconnect = bridge.reconnect(headers={"Authorization": "Device device-secret"})

    assert reconnect.status == "ready"
    assert resumed_socket.sent[-1]["params"]["session_id"] == "stored-hermes-1"


def test_bridge_forwards_a_standard_turn_and_separate_pcm_sidecar():
    gateway_socket = FakeJsonSocket(
        [
            _event(
                "gateway.ready",
                {},
            ),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "stored-hermes-1",
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.start",
                    "session_id": "runtime-hermes-1",
                    "payload": {"turn_id": "hermes-turn-1"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "session_id": "runtime-hermes-1",
                    "payload": {"text": "Hello from Hermes."},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "runtime-hermes-1",
                    "payload": {"status": "completed"},
                },
            },
        ],
        catalog_pairs=["status"],
    )
    audio_socket = FakeAudioSocket(
        [
            {"type": "start", "sample_rate": 24_000, "channels": 1},
            b"\x01\x02\x03\x04",
            {"type": "end"},
        ]
    )
    gateway_factory = FakeSocketFactory(gateway_socket)
    audio_factory = FakeAudioSocketFactory(audio_socket)
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=gateway_factory,
        audio_socket_factory=audio_factory,
        turn_id_factory=lambda index: f"home-turn-{index}",
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    turn = bridge.submit_prompt("What is the weather?")

    assert turn.turn_id == "home-turn-1"
    assert gateway_socket.sent[-1] == {
        "jsonrpc": "2.0",
        "id": "home-3",
        "method": "prompt.submit",
        "params": {
            "session_id": "runtime-hermes-1",
            "text": "What is the weather?",
        },
    }
    assert audio_factory.urls == [
        "wss://hermes.example/api/audio/speak-stream?token=server-hermes-secret&profile=family"
    ]

    message_start = bridge.next_event()
    message_delta = bridge.next_event()
    assert message_start.type == "message.start"
    assert message_delta.type == "message.delta"
    assert message_delta.payload == {"text": "Hello from Hermes."}
    assert message_delta.turn_id == "home-turn-1"
    assert message_delta.to_endpoint() == {
        "schema": 1,
        "conversation_handle": "opaque-conversation-1",
        "turn_id": "home-turn-1",
        "event": {"type": "message.delta", "payload": {"text": "Hello from Hermes."}},
    }
    assert audio_socket.sent == [{"text": "Hello from Hermes."}]

    start = bridge.next_audio()
    pcm = bridge.next_audio()
    assert isinstance(start, AudioFrame)
    assert start.kind == "start"
    assert start.sample_rate == 24_000
    assert start.channels == 1
    assert start.sample_width == 2
    assert start.byte_order == "little"
    assert pcm.kind == "pcm"
    assert pcm.data == b"\x01\x02\x03\x04"

    complete = bridge.next_event()
    assert complete.type == "message.complete"
    assert audio_socket.sent == [
        {"text": "Hello from Hermes."},
        {"done": True},
    ]
    assert bridge.next_audio().kind == "end"
    assert audio_socket.closed is True


def test_bridge_dispatches_only_a_command_advertised_by_standard_gateway():
    gateway_socket = FakeJsonSocket(
        [
            _event(
                "gateway.ready",
                {},
            ),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True, "command_id": "command-1"},
            },
        ],
        catalog_pairs=["status"],
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert bridge.dispatch_command("status", "now") == {
        "accepted": True,
        "command_id": "command-1",
    }
    assert gateway_socket.sent[-1] == {
        "jsonrpc": "2.0",
        "id": "home-3",
        "method": "command.dispatch",
        "params": {
            "session_id": "runtime-hermes-1",
            "name": "status",
            "arg": "now",
        },
    }


def test_bridge_marks_command_transport_loss_before_reporting_it():
    gateway_socket = CommandLossJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ],
        catalog_pairs=["status"],
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    with pytest.raises(BridgeTransportError, match="command"):
        bridge.dispatch_command("status")
    assert bridge.state == "disconnected"


def test_old_command_loss_cannot_clobber_a_newly_opened_bridge():
    first_socket = BlockingCommandSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ],
        catalog_pairs=["status"],
    )
    second_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-2"},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=SequencedSocketFactory(first_socket, second_socket),
    )
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    errors: list[BaseException] = []

    def dispatch() -> None:
        try:
            bridge.dispatch_command("status")
        except Exception as error:  # noqa: BLE001  # pragma: no cover
            errors.append(error)

    command_thread = Thread(target=dispatch)
    command_thread.start()
    assert first_socket.command_started.wait(timeout=1)

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-2",
    )
    first_socket.release_command.set()
    command_thread.join(timeout=1)

    assert result.status == "ready"
    assert errors and isinstance(errors[0], BridgeTransportError)
    assert bridge.state == "ready"


def test_bridge_waits_for_standard_terminal_event_after_interrupt_acknowledgement():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-3",
                "result": {"status": "accepted"},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "runtime-hermes-1",
                    "payload": {"status": "interrupted"},
                },
            },
        ]
    )
    audio_socket = FakeAudioSocket([{"type": "end"}])
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        audio_socket_factory=FakeAudioSocketFactory(audio_socket),
        turn_id_factory=lambda index: f"home-turn-{index}",
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Stop if needed")

    assert bridge.interrupt() is True
    assert bridge.active_turn_id == "home-turn-1"
    assert gateway_socket.sent[-1] == {
        "jsonrpc": "2.0",
        "id": "home-4",
        "method": "session.interrupt",
        "params": {"session_id": "runtime-hermes-1"},
    }
    assert audio_socket.sent == [{"stop": True}]

    terminal = bridge.next_event()
    assert terminal.type == "message.complete"
    assert terminal.payload == {"status": "interrupted"}
    assert bridge.next_audio().kind == "end"
    assert bridge.active_turn_id is None


def test_bridge_reconnects_by_resuming_without_replaying_uncertain_prompt():
    first_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "stored-hermes-1",
                },
            },
        ]
    )
    resumed_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-2"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True, "command_id": "reconnected-command"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-3",
                "result": {"accepted": True},
            },
        ],
        catalog_pairs=["status"],
    )
    gateway_factory = SequencedSocketFactory(first_socket, resumed_socket)
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=gateway_factory,
        turn_id_factory=lambda index: f"home-turn-{index}",
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    with pytest.raises(RuntimeError, match="uncertain"):
        bridge.submit_prompt("Do not duplicate me")

    assert first_socket.sent[-1]["method"] == "prompt.submit"
    assert bridge.state == "turn_uncertain"

    result = bridge.reconnect(headers={"Authorization": "Device device-secret"})

    assert result.status == "ready"
    assert result.unresolved_turn_id == "home-turn-1"
    assert result.to_endpoint()["unresolved_turn"]["status"] == "uncertain"
    assert resumed_socket.sent == [
        {
            "jsonrpc": "2.0",
            "id": "home-1",
            "method": "commands.catalog",
            "params": {},
        },
        {
            "jsonrpc": "2.0",
            "id": "home-2",
            "method": "session.resume",
            "params": {
                "session_id": "stored-hermes-1",
                "source": "home",
                "profile": "family",
            },
        },
    ]
    assert bridge.state == "ready"

    assert result.capabilities["commands"] == ["status"]
    assert bridge.dispatch_command("status") == {
        "accepted": True,
        "command_id": "reconnected-command",
    }

    bridge.submit_prompt("Fresh action")
    assert resumed_socket.sent[-1]["method"] == "prompt.submit"
    assert resumed_socket.sent[-1]["params"]["text"] == "Fresh action"


def test_bridge_reauthorizes_a_replacement_endpoint_without_touching_live_turn():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "stored-hermes-1",
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
        ]
    )
    grant = ConversationGrant(
        handle="opaque-conversation-1",
        device_id="puck-kitchen",
        profile_id="family",
        session_id="stored-hermes-1",
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: grant,
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        turn_id_factory=lambda index: f"home-turn-{index}",
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Keep streaming")
    sent_before = list(gateway_socket.sent)

    result = bridge.reauthorize(headers={"Authorization": "Device device-secret"})

    assert result.status == "ready"
    assert result.unresolved_turn == BridgeTurn(
        "home-turn-1", "opaque-conversation-1", "streaming"
    )
    assert bridge.active_turn_id == "home-turn-1"
    assert gateway_socket.sent == sent_before


def test_bridge_filters_events_for_other_sessions_even_when_identity_is_in_payload():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "payload": {
                        "session_id": "runtime-someone-else",
                        "text": "Do not forward this",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "payload": {
                        "session_id": "runtime-hermes-1",
                        "text": "Forward this",
                    },
                },
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Filter by session")

    event = bridge.next_event()
    assert event.payload == {
        "session_id": "runtime-hermes-1",
        "text": "Forward this",
    }
    assert "runtime-hermes-1" not in repr(event.to_endpoint())


def test_bridge_does_not_assign_global_events_to_the_active_turn():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "skin.changed",
                    "payload": {"skin": "dark"},
                },
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Keep global events separate")

    event = bridge.next_event()

    assert event.type == "skin.changed"
    assert event.turn_id is None
    assert bridge.active_turn_id == "home-turn-1"


def test_bridge_fails_closed_on_a_session_event_without_identity():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "payload": {"text": "Identity is required"},
                },
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Require session identity")

    with pytest.raises(BridgeProtocolError, match="session identity"):
        bridge.next_event()
    assert bridge.state == "turn_uncertain"
    assert bridge.active_turn_id is None


def test_bridge_preserves_structured_prompt_identity_and_sensitivity():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "approval.request",
                    "session_id": "runtime-hermes-1",
                    "payload": {
                        "request_id": "approval-1",
                        "sensitivity": "high",
                        "question": "Allow the requested action?",
                        "options": [{"id": "allow", "label": "Allow"}],
                    },
                },
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Run the action")

    prompt = bridge.next_event()
    assert prompt.type == "approval.request"
    assert prompt.correlation_id == "approval-1"
    assert prompt.payload["sensitivity"] == "high"
    assert prompt.to_endpoint() == {
        "schema": 1,
        "conversation_handle": "opaque-conversation-1",
        "turn_id": "home-turn-1",
        "correlation_id": "approval-1",
        "event": {
            "type": "approval.request",
            "payload": {
                "request_id": "approval-1",
                "sensitivity": "high",
                "question": "Allow the requested action?",
                "options": [{"id": "allow", "label": "Allow"}],
            },
        },
    }


def test_bridge_resolves_a_structured_prompt_with_its_correlation_id():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "approval.request",
                    "session_id": "runtime-hermes-1",
                    "payload": {
                        "request_id": "approval-1",
                        "sensitivity": "high",
                        "question": "Allow the requested action?",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "home-3",
                "result": {"resolved": True},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Run the action")
    prompt = bridge.next_event()

    result = bridge.respond_prompt(prompt, {"choice": "allow"})

    assert result == {"resolved": True}
    assert gateway_socket.sent[-1] == {
        "jsonrpc": "2.0",
        "id": "home-4",
        "method": "approval.respond",
        "params": {
            "session_id": "runtime-hermes-1",
            "request_id": "approval-1",
            "choice": "allow",
        },
    }


def test_bridge_gates_typed_choice_by_gateway_capabilities_and_claim_revision():
    choice_payload = {
        "request_id": "choice-request-1",
        "prompt_kind": "choice",
        "text": "Choose an inspection step.",
        "choice": {
            "object_id": "source-choice-1",
            "freshness": "source-freshness-1",
            "operations": ["choose", "explore"],
        },
        "options": [{"id": "inspect", "label": "Inspect"}],
    }
    gateway_socket = FakeJsonSocket(
        [
            _event(
                "gateway.ready",
                {
                    "capabilities": {
                        "prompt.choose": True,
                        "prompt.explore": False,
                    }
                },
            ),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-hidden"},
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "prompt.request",
                    "session_id": "runtime-hermes-hidden",
                    "payload": choice_payload,
                },
            },
            {"jsonrpc": "2.0", "id": "home-3", "result": {"resolved": True}},
        ]
    )
    bridge = _make_bridge(
        FakeSocketFactory(gateway_socket),
        resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
            configuration_revision=17,
            interactive_choice=True,
        ),
    )

    status = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Run the harmless proof")
    event = bridge.next_event()

    assert status.capabilities["prompt.choose"] is True
    assert status.capabilities["prompt.explore"] is False
    assert event.type == "prompt.request"
    assert event.choice_capabilities == frozenset({"prompt.choose"})
    assert event.standard_session_id == "runtime-hermes-hidden"
    assert event.configuration_revision == 17
    assert "runtime-hermes-hidden" not in repr(event)
    with pytest.raises(BridgeProtocolError, match="projected by Home authority"):
        event.to_endpoint()

    result = bridge.respond_prompt(
        event,
        {
            "operation": "choose",
            "option_id": "inspect",
            "object_id": "source-choice-1",
            "freshness": "source-freshness-1",
        },
    )

    assert result == {"resolved": True}
    assert gateway_socket.sent[-1] == {
        "jsonrpc": "2.0",
        "id": "home-4",
        "method": "prompt.choose",
        "params": {
            "session_id": "runtime-hermes-hidden",
            "request_id": "choice-request-1",
            "option_id": "inspect",
            "object_id": "source-choice-1",
            "freshness": "source-freshness-1",
        },
    }
    with pytest.raises(BridgeRequestRejected, match="no longer pending"):
        bridge.respond_prompt(
            event,
            {
                "operation": "choose",
                "option_id": "inspect",
                "object_id": "source-choice-1",
                "freshness": "source-freshness-1",
            },
        )
    bridge.close()


def test_bridge_forwards_new_typed_choice_revision_while_previous_is_pending():
    first_payload = {
        "request_id": "choice-request-1",
        "prompt_kind": "choice",
        "text": "Choose an inspection step.",
        "choice": {
            "object_id": "source-choice-1",
            "freshness": "source-freshness-1",
            "operations": ["choose"],
        },
        "options": [{"id": "inspect", "label": "Inspect"}],
    }
    revised_payload = {
        **first_payload,
        "request_id": "choice-request-2",
        "choice": {
            **first_payload["choice"],
            "freshness": "source-freshness-2",
        },
    }
    gateway_socket = FakeJsonSocket(
        [
            _event(
                "gateway.ready",
                {"capabilities": {"prompt.choose": True}},
            ),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-hidden"},
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
            _session_event("prompt.request", first_payload, "runtime-hermes-hidden"),
            _session_event("prompt.request", revised_payload, "runtime-hermes-hidden"),
        ]
    )
    bridge = _make_bridge(
        FakeSocketFactory(gateway_socket),
        resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
            configuration_revision=17,
            interactive_choice=True,
        ),
    )

    try:
        bridge.open(
            headers={"Authorization": "Device device-secret"},
            conversation_handle="opaque-conversation-1",
        )
        bridge.submit_prompt("Run the harmless proof")
        first = bridge.next_event()
        revised = bridge.next_event()

        assert first.correlation_id == "choice-request-1"
        assert revised.correlation_id == "choice-request-2"
        assert revised.payload["choice"]["freshness"] == "source-freshness-2"
    finally:
        bridge.close()


def test_bridge_rejects_choice_response_after_claim_revision_changes():
    revision = [17]
    gateway_socket = FakeJsonSocket(
        [
            _event(
                "gateway.ready",
                {"capabilities": {"prompt.choose": True}},
            ),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-hidden"},
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
            _session_event(
                "prompt.request",
                {
                    "request_id": "choice-request-1",
                    "prompt_kind": "choice",
                    "text": "Choose an inspection step.",
                    "choice": {
                        "object_id": "source-choice-1",
                        "freshness": "source-freshness-1",
                        "operations": ["choose"],
                    },
                    "options": [{"id": "inspect", "label": "Inspect"}],
                },
                "runtime-hermes-hidden",
            ),
        ]
    )

    def resolve_grant(handle: str, device_id: str) -> ConversationGrant:
        return ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
            configuration_revision=revision[0],
            interactive_choice=True,
        )

    bridge = _make_bridge(FakeSocketFactory(gateway_socket), resolver=resolve_grant)

    try:
        bridge.open(
            headers={"Authorization": "Device device-secret"},
            conversation_handle="opaque-conversation-1",
        )
        bridge.submit_prompt("Run the harmless proof")
        event = bridge.next_event()
        assert event.configuration_revision == 17
        revision[0] = 18

        with pytest.raises(BridgeAuthorizationError, match="stale_conversation"):
            bridge.respond_prompt(
                event,
                {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": "source-choice-1",
                    "freshness": "source-freshness-1",
                },
            )

        assert all(
            frame.get("method") not in {"prompt.choose", "prompt.explore"}
            for frame in gateway_socket.sent
        )
    finally:
        bridge.close()


def test_bridge_dispatches_explore_to_matching_gateway_operation():
    gateway_socket = FakeJsonSocket(
        [
            _event(
                "gateway.ready",
                {"capabilities": {"prompt.explore": True}},
            ),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-hidden"},
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
            _session_event(
                "prompt.request",
                {
                    "request_id": "choice-request-1",
                    "prompt_kind": "choice",
                    "text": "Choose an inspection step.",
                    "choice": {
                        "object_id": "source-choice-1",
                        "freshness": "source-freshness-1",
                        "operations": ["explore"],
                    },
                    "options": [{"id": "inspect", "label": "Inspect"}],
                },
                "runtime-hermes-hidden",
            ),
            {"jsonrpc": "2.0", "id": "home-3", "result": {"resolved": True}},
        ]
    )
    bridge = _make_bridge(
        FakeSocketFactory(gateway_socket),
        resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
            configuration_revision=17,
            interactive_choice=True,
        ),
    )

    try:
        bridge.open(
            headers={"Authorization": "Device device-secret"},
            conversation_handle="opaque-conversation-1",
        )
        bridge.submit_prompt("Run the harmless proof")
        event = bridge.next_event()

        result = bridge.respond_prompt(
            event,
            {
                "operation": "explore",
                "option_id": "inspect",
                "object_id": "source-choice-1",
                "freshness": "source-freshness-1",
            },
        )

        assert result == {"resolved": True}
        assert gateway_socket.sent[-1] == {
            "jsonrpc": "2.0",
            "id": "home-4",
            "method": "prompt.explore",
            "params": {
                "session_id": "runtime-hermes-hidden",
                "request_id": "choice-request-1",
                "option_id": "inspect",
                "object_id": "source-choice-1",
                "freshness": "source-freshness-1",
            },
        }
    finally:
        bridge.close()


def test_bridge_rejects_typed_choice_when_gateway_does_not_advertise_it():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-hidden"},
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "prompt.request",
                    "session_id": "runtime-hermes-hidden",
                    "payload": {
                        "request_id": "choice-request-1",
                        "prompt_kind": "choice",
                        "text": "Choose.",
                        "choice": {
                            "object_id": "source-choice-1",
                            "freshness": "source-freshness-1",
                            "operations": ["choose"],
                        },
                        "options": [{"id": "inspect", "label": "Inspect"}],
                    },
                },
            },
        ]
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    status = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Run the harmless proof")
    event = bridge.next_event()

    assert "prompt.choose" not in status.capabilities
    assert event.choice_capabilities == frozenset()
    with pytest.raises(BridgeCapabilityUnavailable, match="not supported"):
        bridge.respond_prompt(
            event,
            {
                "operation": "choose",
                "option_id": "inspect",
                "object_id": "source-choice-1",
                "freshness": "source-freshness-1",
            },
        )
    assert all(frame.get("method") != "prompt.choose" for frame in gateway_socket.sent)
    bridge.close()


def test_bridge_withholds_choices_from_devices_without_interactive_opt_in():
    gateway_socket = FakeJsonSocket(
        [
            _event(
                "gateway.ready",
                {
                    "capabilities": {
                        "prompt.choose": True,
                        "prompt.explore": True,
                    }
                },
            ),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-hidden"},
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "prompt.request",
                    "session_id": "runtime-hermes-hidden",
                    "payload": {
                        "request_id": "choice-request-1",
                        "prompt_kind": "choice",
                        "text": "Choose.",
                        "choice": {
                            "object_id": "source-choice-1",
                            "freshness": "source-freshness-1",
                            "operations": ["choose"],
                        },
                        "options": [{"id": "inspect", "label": "Inspect"}],
                    },
                },
            },
        ]
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))

    status = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Run the harmless proof")
    event = bridge.next_event()

    assert "prompt.choose" not in status.capabilities
    assert event.choice_capabilities == frozenset()
    with pytest.raises(BridgeCapabilityUnavailable):
        bridge.respond_prompt(
            event,
            {
                "operation": "choose",
                "option_id": "inspect",
                "object_id": "source-choice-1",
                "freshness": "source-freshness-1",
            },
        )
    assert all(frame.get("method") != "prompt.choose" for frame in gateway_socket.sent)
    bridge.close()


def test_explicit_prompt_rejection_is_not_reported_as_uncertain_delivery():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "error": {"code": "busy", "message": "session is busy"},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    with pytest.raises(BridgeRequestRejected, match="rejected"):
        bridge.submit_prompt("Try once")

    assert bridge.state == "ready"
    assert bridge.active_turn_id is None


def test_bridge_feeds_only_new_suffix_of_cumulative_rendered_text_to_audio():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "session_id": "runtime-hermes-1",
                    "payload": {"rendered": "Hello"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "session_id": "runtime-hermes-1",
                    "payload": {"rendered": "Hello world"},
                },
            },
        ]
    )
    audio_socket = FakeAudioSocket([])
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        audio_socket_factory=FakeAudioSocketFactory(audio_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Say hello")

    bridge.next_event()
    bridge.next_event()

    assert audio_socket.sent == [{"text": "Hello"}, {"text": " world"}]


def test_standard_gateway_requires_jsonrpc_2_for_the_ready_event():
    gateway_socket = FakeJsonSocket(
        [
            {
                "method": "event",
                "params": {"type": "gateway.ready", "payload": {}},
            }
        ]
    )
    client = StandardGatewayClient(
        url="wss://hermes.example/api/ws",
        token="server-hermes-secret",
        socket_factory=FakeSocketFactory(gateway_socket),
    )

    with pytest.raises(BridgeProtocolError, match="JSON-RPC"):
        client.connect()
    client.close()


def test_standard_gateway_bounds_ready_handshake_time():
    gateway_socket = SilentJsonSocket([])
    client = StandardGatewayClient(
        url="wss://hermes.example/api/ws",
        token="server-hermes-secret",
        socket_factory=FakeSocketFactory(gateway_socket),
        connect_timeout=0.01,
    )

    with pytest.raises(BridgeTimeoutError, match="gateway.ready"):
        client.connect()
    assert gateway_socket.closed is True


def test_standard_gateway_bounds_rpc_wait_and_supports_ping():
    gateway_socket = SilentJsonSocket([_event("gateway.ready", {"heartbeat": True})])
    client = StandardGatewayClient(
        url="wss://hermes.example/api/ws",
        token="server-hermes-secret",
        socket_factory=FakeSocketFactory(gateway_socket),
        request_timeout=0.01,
    )
    client.connect()

    with pytest.raises(BridgeTimeoutError, match="gateway response"):
        client.ping()
    assert gateway_socket.sent[-1]["method"] == "gateway.ping"
    client.close()


def test_standard_gateway_bounds_event_wait():
    gateway_socket = SilentJsonSocket([_event("gateway.ready", {"heartbeat": True})])
    client = StandardGatewayClient(
        url="wss://hermes.example/api/ws",
        token="server-hermes-secret",
        socket_factory=FakeSocketFactory(gateway_socket),
        event_timeout=0.01,
    )
    client.connect()

    with pytest.raises(BridgeTimeoutError, match="gateway event"):
        client.next_event()
    client.close()


def test_bridge_keeps_a_live_session_when_an_event_poll_times_out() -> None:
    class IdleThenEventGateway:
        def __init__(self) -> None:
            self.calls = 0
            self.closed = False

        @property
        def is_open(self) -> bool:
            return not self.closed

        def next_event(self, timeout: float | None = None) -> dict[str, object]:
            self.calls += 1
            if self.calls == 1:
                raise BridgeTimeoutError("idle event poll")
            return {
                "type": "status.update",
                "session_id": "runtime-hermes-1",
                "payload": {},
            }

        def close(self) -> None:
            self.closed = True

    bridge = _make_bridge(FakeSocketFactory(FakeJsonSocket([])))
    gateway = IdleThenEventGateway()
    with bridge._state_lock:
        bridge._state = "ready"
        bridge._gateway = gateway
        bridge._conversation_handle = "opaque-conversation-1"
        bridge._runtime_session_id = "runtime-hermes-1"

    try:
        event = bridge.next_event()

        assert event.type == "status.update"
        assert event.conversation_handle == "opaque-conversation-1"
        assert gateway.calls == 2
        assert gateway.is_open
        assert bridge.state == "ready"
    finally:
        bridge.close()


def test_bridge_bounds_response_audio_wait():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
        ]
    )
    audio_socket = SilentAudioSocket([])
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        audio_socket_factory=FakeAudioSocketFactory(audio_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Bound the audio wait")

    with pytest.raises(BridgeTimeoutError, match="response audio"):
        bridge.next_audio(timeout=0.01)
    assert audio_socket.closed is True


def test_bridge_treats_message_complete_without_status_as_a_terminal_event():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "runtime-hermes-1",
                    "payload": {"text": "Finished"},
                },
            },
        ]
    )
    audio_socket = FakeAudioSocket([{"type": "end"}])
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        audio_socket_factory=FakeAudioSocketFactory(audio_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Finish this")

    event = bridge.next_event()

    assert event.type == "message.complete"
    assert bridge.state == "ready"
    assert audio_socket.sent == [{"done": True}]
    assert bridge.next_audio().kind == "end"
    assert bridge.active_turn_id is None


def test_bridge_rejects_a_non_object_message_payload_instead_of_completing():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "runtime-hermes-1",
                    "payload": [],
                },
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Finish only with a valid payload")

    with pytest.raises(BridgeTransportError, match="invalid event"):
        bridge.next_event()
    assert bridge.state == "turn_uncertain"
    assert bridge.active_turn_id is None


def test_bridge_rejects_a_prompt_result_without_acceptance_proof():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {}},
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    with pytest.raises(BridgeProtocolError, match="acceptance"):
        bridge.submit_prompt("Do not accept an empty result")
    assert bridge.state == "turn_uncertain"
    assert bridge.active_turn_id is None


def test_bridge_reconnect_discards_active_turn_without_replaying_it():
    first_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "stored-hermes-1",
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
        ]
    )
    resumed_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-2"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=SequencedSocketFactory(first_socket, resumed_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Do not replay this")

    result = bridge.reconnect(headers={"Authorization": "Device device-secret"})

    assert result.status == "ready"
    assert bridge.active_turn_id is None
    bridge.submit_prompt("Fresh action")
    assert [frame["method"] for frame in resumed_socket.sent] == [
        "commands.catalog",
        "session.resume",
        "prompt.submit",
    ]
    assert resumed_socket.sent[-1]["params"]["text"] == "Fresh action"


def test_bridge_requires_reconnect_before_submitting_after_uncertain_delivery():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    with pytest.raises(BridgeTransportError, match="uncertain"):
        bridge.submit_prompt("The first delivery is uncertain")

    sent_count = len(gateway_socket.sent)
    with pytest.raises(RuntimeError, match="not ready"):
        bridge.submit_prompt("Do not send until reconnected")
    assert len(gateway_socket.sent) == sent_count


def test_bridge_closes_reachable_work_when_reconnect_authorization_is_revoked():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    authenticator = StaticCredentialAuthenticator(
        admin_token="admin-secret",
        device_credentials={"device-secret": "puck-kitchen"},
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=authenticator,
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    result = bridge.reconnect(headers={"Authorization": "Device revoked-secret"})

    assert result.status == "unavailable"
    assert result.reason == "unauthorized"
    assert bridge.state == "unavailable"
    assert gateway_socket.closed is True
    with pytest.raises(RuntimeError, match="not ready"):
        bridge.dispatch_command("status")


def test_bridge_never_retargets_a_bound_conversation_to_a_new_profile():
    first_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    resolver_grants = deque(
        [
            ConversationGrant(
                handle="opaque-conversation-1",
                device_id="puck-kitchen",
                profile_id="family",
            ),
            ConversationGrant(
                handle="opaque-conversation-1",
                device_id="puck-kitchen",
                profile_id="office",
            ),
        ]
    )
    gateway_factory = SequencedSocketFactory(first_socket)
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: resolver_grants.popleft(),
        gateway_socket_factory=gateway_factory,
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    result = bridge.reconnect(headers={"Authorization": "Device device-secret"})

    assert result.status == "unavailable"
    assert result.reason == "conversation_mismatch"
    assert gateway_factory.urls == [
        "wss://hermes.example/api/ws?token=server-hermes-secret"
    ]
    assert first_socket.closed is True


def test_bridge_rejects_a_reconnect_to_a_different_durable_session():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    resolver_grants = deque(
        [
            ConversationGrant(
                handle="opaque-conversation-1",
                device_id="puck-kitchen",
                profile_id="family",
                session_id="stored-hermes-1",
            ),
            ConversationGrant(
                handle="opaque-conversation-1",
                device_id="puck-kitchen",
                profile_id="family",
                session_id="stored-hermes-2",
            ),
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: resolver_grants.popleft(),
        gateway_socket_factory=SequencedSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    result = bridge.reconnect(headers={"Authorization": "Device device-secret"})

    assert result.status == "unavailable"
    assert result.reason == "conversation_mismatch"


def test_bridge_rejects_a_new_session_bound_to_a_different_durable_id():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "stored-hermes-other",
                },
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
            session_id="stored-hermes-bound",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert result.status == "unavailable"
    assert result.reason == "conversation_mismatch"
    assert bridge.state == "unavailable"


def test_bridge_does_not_switch_conversations_while_a_turn_is_active():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Keep this binding")

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-2",
    )

    assert result.status == "unavailable"
    assert result.reason == "turn_active"
    assert bridge.active_turn_id == "home-turn-1"
    assert bridge.state == "ready"
    assert gateway_socket.closed is False


def test_bridge_reports_unadvertised_command_as_typed_capability_failure():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {"commands": []}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    with pytest.raises(BridgeCapabilityUnavailable, match="not advertised"):
        bridge.dispatch_command("not-advertised")


def test_bridge_preserves_correlation_from_the_standard_event_envelope():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "approval.request",
                    "session_id": "runtime-hermes-1",
                    "correlation_id": "event-correlation-1",
                    "payload": {"question": "Allow the action?"},
                },
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Run the action")

    event = bridge.next_event()

    assert event.correlation_id == "event-correlation-1"
    assert event.to_endpoint()["correlation_id"] == "event-correlation-1"


def test_bridge_fails_closed_on_conflicting_event_session_identity():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "session_id": "runtime-hermes-1",
                    "payload": {
                        "session_id": "runtime-someone-else",
                        "text": "Do not forward this",
                    },
                },
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Filter identity")

    with pytest.raises(BridgeTransportError, match="invalid event"):
        bridge.next_event()
    assert bridge.state == "turn_uncertain"


def test_bridge_rejects_whitespace_only_prompt_without_sending_it():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    with pytest.raises(ValueError, match="non-empty"):
        bridge.submit_prompt("   \t")
    assert [frame["method"] for frame in gateway_socket.sent] == [
        "commands.catalog",
        "session.create",
    ]


def test_bridge_does_not_treat_an_explicit_interrupt_rejection_as_completion():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-3",
                "result": {"status": "rejected"},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Keep going")

    with pytest.raises(BridgeRequestRejected, match="interrupt"):
        bridge.interrupt()
    assert bridge.active_turn_id == "home-turn-1"
    assert bridge.state == "ready"


def test_standard_gateway_rejects_a_response_without_result_or_error():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {"jsonrpc": "2.0", "id": "home-1"},
        ],
        inject_catalog=False,
    )
    client = StandardGatewayClient(
        url="wss://hermes.example/api/ws",
        token="server-hermes-secret",
        socket_factory=FakeSocketFactory(gateway_socket),
    )

    client.connect()
    with pytest.raises(BridgeProtocolError, match="neither result nor error"):
        client.request("session.create", {"source": "home"})
    client.close()


def test_standard_gateway_rejects_an_event_without_a_type():
    gateway_socket = FakeJsonSocket(
        [
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {"payload": {}},
            }
        ]
    )
    client = StandardGatewayClient(
        url="wss://hermes.example/api/ws",
        token="server-hermes-secret",
        socket_factory=FakeSocketFactory(gateway_socket),
    )

    with pytest.raises(BridgeProtocolError, match="type"):
        client.connect()
    client.close()


def test_bridge_rejects_a_prompt_result_marked_as_not_accepted():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"status": "rejected"},
            },
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    with pytest.raises(BridgeRequestRejected, match="prompt"):
        bridge.submit_prompt("Try once")
    assert bridge.state == "ready"
    assert bridge.active_turn_id is None


def test_bridge_reassembles_pcm_samples_split_across_audio_frames():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
        ]
    )
    audio_socket = FakeAudioSocket(
        [
            {"type": "start", "sample_rate": 24_000, "channels": 1},
            b"\x01",
            b"\x02\x03",
            b"\x04",
        ]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        audio_socket_factory=FakeAudioSocketFactory(audio_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Speak")

    assert bridge.next_audio().kind == "start"
    assert bridge.next_audio().data == b"\x01\x02"
    assert bridge.next_audio().data == b"\x03\x04"


def test_bridge_rejects_audio_with_non_signed_16_bit_metadata():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
        ]
    )
    audio_socket = FakeAudioSocket(
        [{"type": "start", "sample_rate": 24_000, "channels": 1, "sample_width": 4}]
    )
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        audio_socket_factory=FakeAudioSocketFactory(audio_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Speak")

    frame = bridge.next_audio()

    assert frame.kind == "unavailable"
    assert frame.metadata == {"reason": "invalid_audio_metadata"}
    assert audio_socket.closed is True


def test_bridge_does_not_label_pcm_before_audio_metadata_as_usable():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
        ]
    )
    audio_socket = FakeAudioSocket([b"\x01\x02"])
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        audio_socket_factory=FakeAudioSocketFactory(audio_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Speak")

    frame = bridge.next_audio()

    assert frame.kind == "unavailable"
    assert frame.metadata == {"reason": "pcm_before_start"}
    assert audio_socket.closed is True


def test_bridge_releases_a_terminal_turn_when_the_audio_sidecar_dies():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "runtime-hermes-1",
                    "payload": {"status": "completed"},
                },
            },
        ]
    )
    audio_socket = FakeAudioSocket([])
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        audio_socket_factory=FakeAudioSocketFactory(audio_socket),
    )

    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Finish")
    bridge.next_event()

    with pytest.raises(BridgeTransportError, match="audio"):
        bridge.next_audio()
    assert bridge.active_turn_id is None
    assert bridge.state == "ready"


def test_bridge_rejects_unauthorized_open_before_resolving_or_opening_hermes():
    gateway_socket = FakeJsonSocket([])
    gateway_factory = FakeSocketFactory(gateway_socket)
    resolver_calls: list[tuple[str, str]] = []

    def resolver(handle: str, device_id: str) -> ConversationGrant:
        resolver_calls.append((handle, device_id))
        return ConversationGrant(handle, device_id, "family")

    bridge = _make_bridge(
        gateway_factory,
        resolver=resolver,
        authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
    )

    result = bridge.open(
        headers={"Authorization": "Device wrong-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert result.status == "unavailable"
    assert result.reason == "unauthorized"
    assert resolver_calls == []
    assert gateway_factory.urls == []


def test_bridge_accepts_the_pinned_message_start_without_a_payload():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.start",
                    "session_id": "runtime-hermes-1",
                },
            },
        ]
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Start")

    event = bridge.next_event()

    assert event.payload == {}
    assert event.to_endpoint()["event"]["payload"] == {}


def test_standard_gateway_rejects_a_non_standard_gateway_path():
    factory = FakeSocketFactory(FakeJsonSocket([]))
    client = StandardGatewayClient(
        url="wss://hermes.example/voice-session",
        token="server-hermes-secret",
        socket_factory=factory,
    )

    with pytest.raises(BridgeProtocolError, match="/api/ws"):
        client.connect()

    assert factory.urls == []


@pytest.mark.parametrize("timeout", [math.nan, math.inf, -math.inf])
def test_standard_gateway_rejects_non_finite_timeouts(timeout: float):
    with pytest.raises((TypeError, ValueError), match="timeout"):
        StandardGatewayClient(
            url="wss://hermes.example/api/ws",
            token="server-hermes-secret",
            socket_factory=FakeSocketFactory(FakeJsonSocket([])),
            connect_timeout=timeout,
        )


def test_bridge_revalidates_the_home_grant_before_submitting():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    grants = deque(
        [
            ConversationGrant("opaque-conversation-1", "puck-kitchen", "family"),
            None,
        ]
    )
    bridge = _make_bridge(
        FakeSocketFactory(gateway_socket),
        resolver=lambda handle, device_id: grants.popleft(),
    )
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    with pytest.raises(BridgeAuthorizationError, match="stale_conversation"):
        bridge.submit_prompt("Do not send")

    assert bridge.state == "unavailable"
    assert [frame["method"] for frame in gateway_socket.sent] == [
        "commands.catalog",
        "session.create",
        "session.interrupt",
    ]


def test_bridge_maps_a_revoked_resolver_status_to_stale_conversation():
    gateway_factory = FakeSocketFactory(FakeJsonSocket([]))
    bridge = _make_bridge(
        gateway_factory,
        resolver=lambda handle, device_id: ConversationGrant(
            handle, device_id, "family", status="revoked"
        ),
    )

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert result.reason == "stale_conversation"
    assert gateway_factory.urls == []


def test_bridge_rejects_a_returned_profile_binding_mismatch():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "stored-hermes-1",
                    "profile_name": "office",
                },
            },
        ]
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert result.reason == "conversation_mismatch"
    assert bridge.state == "unavailable"


def test_bridge_never_uses_a_runtime_session_id_as_a_resume_identity():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))

    result = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert result.status == "ready"
    assert bridge._resume_session_id is None


def test_bridge_converts_a_result_level_command_rejection():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"status": "rejected", "message": "busy"},
            },
        ],
        catalog_pairs=["status"],
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    with pytest.raises(BridgeRequestRejected, match="command"):
        bridge.dispatch_command("status")


@pytest.mark.parametrize(
    ("event_type", "payload", "response", "operation", "expected"),
    [
        (
            "clarify.request",
            {
                "request_id": "clarify-1",
                "questions": [
                    {
                        "qid": "q1",
                        "question": "Pick one",
                        "choices": ["one", "two"],
                        "multi_select": False,
                    }
                ],
            },
            {"answer": "one", "question_id": "q1"},
            "clarify.respond",
            {"answer": "one", "question_id": "q1"},
        ),
        (
            "secret.request",
            {"request_id": "secret-1", "question": "Secret?"},
            {"value": "shh"},
            "secret.respond",
            {"value": "shh"},
        ),
        (
            "sudo.request",
            {"request_id": "sudo-1", "question": "Password?"},
            {"password": "shh"},
            "sudo.respond",
            {"password": "shh"},
        ),
    ],
)
def test_bridge_maps_structured_prompt_responses_and_rejects_stale_ones(
    event_type: str,
    payload: dict[str, object],
    response: dict[str, object],
    operation: str,
    expected: dict[str, object],
):
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": event_type,
                    "session_id": "runtime-hermes-1",
                    "payload": payload,
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "home-3",
                "result": {"status": "ok"},
            },
        ]
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Answer this")
    prompt = bridge.next_event()

    assert bridge.respond_prompt(prompt, response) == {"status": "ok"}
    assert gateway_socket.sent[-1] == {
        "jsonrpc": "2.0",
        "id": "home-4",
        "method": operation,
        "params": {
            "session_id": "runtime-hermes-1",
            "request_id": payload["request_id"],
            **expected,
        },
    }
    with pytest.raises(BridgeRequestRejected, match="no longer pending"):
        bridge.respond_prompt(prompt, response)


def test_bridge_applies_the_pinned_approval_all_default():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "approval.request",
                    "session_id": "runtime-hermes-1",
                    "payload": {"request_id": "approval-1"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "home-3",
                "result": {"status": "ok"},
            },
        ]
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Approve all")
    prompt = bridge.next_event()

    bridge.respond_prompt(prompt, {"all": True})

    assert gateway_socket.sent[-1]["params"] == {
        "session_id": "runtime-hermes-1",
        "request_id": "approval-1",
        "choice": "deny",
        "all": True,
    }


def test_bridge_expires_a_structured_prompt_and_rejects_a_late_answer():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "secret.request",
                    "session_id": "runtime-hermes-1",
                    "payload": {"request_id": "secret-1"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "secret.expire",
                    "session_id": "runtime-hermes-1",
                    "payload": {"request_id": "secret-1"},
                },
            },
        ]
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Need a secret")
    prompt = bridge.next_event()
    assert bridge.next_event().type == "secret.expire"

    with pytest.raises(BridgeRequestRejected, match="no longer pending"):
        bridge.respond_prompt(prompt, {"value": "late"})


def test_bridge_discards_sequenced_events_from_the_completed_turn():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.start",
                    "session_id": "runtime-hermes-1",
                    "seq": 1,
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "runtime-hermes-1",
                    "seq": 2,
                    "payload": {"status": "completed"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "home-3",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "session_id": "runtime-hermes-1",
                    "seq": 1,
                    "payload": {"text": "old"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.start",
                    "session_id": "runtime-hermes-1",
                    "seq": 3,
                },
            },
        ]
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("First")
    assert bridge.next_event().type == "message.start"
    assert bridge.next_event().type == "message.complete"
    bridge.submit_prompt("Second")

    assert bridge.next_event().type == "message.start"


def test_standard_gateway_records_eof_as_a_typed_reader_failure():
    class EOFJsonSocket(SilentJsonSocket):
        def receive_json(self, timeout: float | None = None) -> dict[str, object]:
            if self.incoming:
                return self.incoming.popleft()
            raise EOFError("peer closed")

    socket = EOFJsonSocket([_event("gateway.ready", {})])
    client = StandardGatewayClient(
        url="wss://hermes.example/api/ws",
        token="server-hermes-secret",
        socket_factory=FakeSocketFactory(socket),
        event_timeout=0.1,
    )
    client.connect()

    with pytest.raises(BridgeTransportError, match="reader"):
        client.next_event()
    client.close()


def test_standard_gateway_bounds_a_blocking_socket_write():
    socket = BlockingSendJsonSocket([_event("gateway.ready", {})])
    client = StandardGatewayClient(
        url="wss://hermes.example/api/ws",
        token="server-hermes-secret",
        socket_factory=FakeSocketFactory(socket),
        request_timeout=0.01,
    )
    client.connect()

    with pytest.raises(BridgeTimeoutError, match="send"):
        client.request("blocked", {})
    assert socket.closed is True
    socket.release_send.set()
    client.close()


def test_standard_gateway_rejects_duplicate_in_flight_request_ids():
    socket = BlockingSendJsonSocket([_event("gateway.ready", {})])
    client = StandardGatewayClient(
        url="wss://hermes.example/api/ws",
        token="server-hermes-secret",
        socket_factory=FakeSocketFactory(socket),
        request_id_factory=lambda index: "same-id",
        request_timeout=1.0,
    )
    client.connect()
    errors: list[BaseException] = []

    def request() -> None:
        try:
            client.request("first", {})
        except BaseException as error:  # noqa: BLE001 - fixture captures the worker result
            errors.append(error)

    first = Thread(target=request)
    first.start()
    assert socket.send_started.wait(timeout=1)
    socket.release_send.set()

    with pytest.raises(BridgeProtocolError, match="duplicate"):
        client.request("second", {})
    client.close()
    first.join(timeout=1)
    assert errors


def test_bridge_feeds_raw_delta_text_to_audio_even_with_a_cumulative_preview():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "session_id": "runtime-hermes-1",
                    "payload": {"text": "Hello", "rendered": "Hello"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "session_id": "runtime-hermes-1",
                    "payload": {"text": " world", "rendered": "Hello world"},
                },
            },
        ]
    )
    audio_socket = FakeAudioSocket([])
    bridge = _make_bridge(
        FakeSocketFactory(gateway_socket),
        audio_factory=FakeAudioSocketFactory(audio_socket),
    )
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Speak")

    bridge.next_event()
    bridge.next_event()

    assert audio_socket.sent == [{"text": "Hello"}, {"text": " world"}]


def test_bridge_rejects_an_unknown_message_complete_status():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "runtime-hermes-1",
                    "payload": {"status": "mystery"},
                },
            },
        ]
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Validate status")

    with pytest.raises(BridgeProtocolError, match="unknown status"):
        bridge.next_event()
    assert bridge.state == "turn_uncertain"


def test_bridge_rejects_a_mismatched_durable_id_returned_during_reconnect():
    first_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-1",
                    "stored_session_id": "stored-hermes-1",
                },
            },
        ]
    )
    resumed_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": "runtime-hermes-2",
                    "stored_session_id": "stored-hermes-other",
                },
            },
        ]
    )
    bridge = _make_bridge(
        SequencedSocketFactory(first_socket, resumed_socket),
        resolver=lambda handle, device_id: ConversationGrant(
            handle, device_id, "family", session_id="stored-hermes-1"
        ),
    )
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    result = bridge.reconnect(headers={"Authorization": "Device device-secret"})

    assert result.status == "unavailable"
    assert result.reason == "conversation_mismatch"


def test_bridge_does_not_make_text_submission_wait_for_optional_audio_setup():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"accepted": True},
            },
        ]
    )
    audio_socket = FakeAudioSocket([])
    audio_factory = BlockingAudioSocketFactory(audio_socket)
    bridge = _make_bridge(
        FakeSocketFactory(gateway_socket),
        audio_factory=audio_factory,
        audio_timeout=0.01,
    )
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    turn = bridge.submit_prompt("Text must win")

    assert turn.turn_id == "home-turn-1"
    assert gateway_socket.sent[-1]["method"] == "prompt.submit"
    assert audio_factory.started.wait(timeout=1)
    audio_factory.release.set()


def test_bridge_discovers_commands_from_the_pinned_standard_catalog():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"heartbeat": True, "skin": "home"}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "pairs": [["/status", "Show session status"]],
                    "commands": {"/status": {"argument_mode": "text", "desktop": None}},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-3",
                "result": {"accepted": True, "command_id": "command-1"},
            },
        ],
        inject_catalog=False,
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))

    status = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert status.capabilities == {
        "commands": ["status"],
        "heartbeat": True,
        "timing": "absent",
        "interrupt": True,
        "audio": False,
    }
    assert [frame["method"] for frame in gateway_socket.sent] == [
        "commands.catalog",
        "session.create",
    ]
    assert gateway_socket.sent[0]["params"] == {}
    assert bridge.dispatch_command("status") == {
        "accepted": True,
        "command_id": "command-1",
    }

    with pytest.raises(BridgeCapabilityUnavailable):
        bridge.dispatch_command("not-advertised")


def test_bridge_ignores_gateway_ready_commands_when_standard_catalog_is_empty():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {"commands": ["status"]}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"pairs": []},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ],
        inject_catalog=False,
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))

    status = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert status.capabilities["commands"] == []
    with pytest.raises(BridgeCapabilityUnavailable):
        bridge.dispatch_command("status")


@pytest.mark.parametrize(
    "pairs",
    [
        [["/status"]],
        [["status", "Missing slash"]],
        [["/status", 42]],
    ],
    ids=["pair-too-short", "name-missing-slash", "description-not-text"],
)
def test_bridge_fails_closed_for_malformed_standard_command_catalog_pairs(pairs):
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {"commands": ["status"]}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"pairs": pairs},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ],
        inject_catalog=False,
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))

    status = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert status.capabilities["commands"] == []
    with pytest.raises(BridgeCapabilityUnavailable):
        bridge.dispatch_command("status")


@pytest.mark.parametrize(
    "catalog_response",
    [
        {"result": {"categories": []}},
        {"error": {"code": -32601, "message": "method not found"}},
    ],
    ids=["catalog-missing", "catalog-rejected"],
)
def test_bridge_fails_closed_when_the_standard_command_catalog_is_unavailable(
    catalog_response: dict[str, object],
):
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {"heartbeat": True}),
            {"jsonrpc": "2.0", "id": "home-1", **catalog_response},
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ],
        inject_catalog=False,
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))

    status = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert status.status == "ready"
    assert status.capabilities["commands"] == []
    with pytest.raises(BridgeCapabilityUnavailable):
        bridge.dispatch_command("status")


def test_bridge_preserves_text_when_the_separate_audio_socket_returns_fallback():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"pairs": []},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-3",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {"type": "message.start", "session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.delta",
                    "session_id": "runtime-hermes-1",
                    "payload": {"text": "Readable text survives."},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "runtime-hermes-1",
                    "payload": {"status": "completed"},
                },
            },
        ],
        inject_catalog=False,
    )
    audio_socket = FakeAudioSocket(
        [
            {"type": "start", "sample_rate": 24_000, "channels": 1},
            b"\x01\x02",
            {"type": "fallback", "reason": "speech unavailable"},
        ]
    )
    bridge = _make_bridge(
        FakeSocketFactory(gateway_socket),
        audio_factory=FakeAudioSocketFactory(audio_socket),
    )
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Keep the text")

    assert bridge.next_event().type == "message.start"
    text_event = bridge.next_event()
    assert text_event.payload == {"text": "Readable text survives."}
    assert bridge.next_audio().kind == "start"
    assert bridge.next_audio().data == b"\x01\x02"
    assert bridge.next_audio().kind == "fallback"
    assert bridge.next_audio().kind == "unavailable"

    assert bridge.next_event().type == "message.complete"
    assert bridge.state == "ready"
    assert audio_socket.closed is True


def test_bridge_preserves_pin_shaped_prompt_identity_until_terminal_completion():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"pairs": []},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "id": "home-3",
                "result": {"accepted": True},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {"type": "message.start", "session_id": "runtime-hermes-1"},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "approval.request",
                    "session_id": "runtime-hermes-1",
                    "payload": {
                        "turn_id": "standard-turn-1",
                        "request_id": "approval-1",
                        "sensitivity": "high",
                        "question": "Run the action?",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "home-4",
                "result": {"status": "ok"},
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "runtime-hermes-1",
                    "payload": {"status": "completed"},
                },
            },
        ],
        inject_catalog=False,
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("Run the action")

    assert bridge.next_event().type == "message.start"
    prompt = bridge.next_event()
    assert prompt.type == "approval.request"
    assert prompt.turn_id == "home-turn-1"
    assert prompt.correlation_id == "approval-1"
    assert prompt.payload["turn_id"] == "standard-turn-1"
    assert prompt.payload["sensitivity"] == "high"

    assert bridge.respond_prompt(prompt, {"choice": "allow"}) == {"status": "ok"}
    assert gateway_socket.sent[-1] == {
        "jsonrpc": "2.0",
        "id": "home-4",
        "method": "approval.respond",
        "params": {
            "session_id": "runtime-hermes-1",
            "request_id": "approval-1",
            "choice": "allow",
        },
    }
    assert bridge.next_event().type == "message.complete"
    assert bridge.active_turn_id is None
    assert bridge.state == "ready"


def test_bridge_records_content_free_activity_and_explicitly_closes_the_claim():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    activity: list[tuple[str, str, str]] = []
    closed: list[tuple[str, str, str]] = []
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle, device_id, "family"
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        activity_recorder=lambda handle, device_id, state: activity.append(
            (handle, device_id, state)
        ),
        conversation_closer=lambda handle, device_id, *, reason: (
            closed.append((handle, device_id, reason)) or True
        ),
    )

    opened = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    assert opened.status == "ready"
    assert bridge.report_activity("capture") is True
    assert bridge.report_activity("playback") is True
    assert bridge.report_activity("playback_complete") is True
    assert bridge.close_conversation() is True

    assert activity == [
        ("opaque-conversation-1", "puck-kitchen", "capture"),
        ("opaque-conversation-1", "puck-kitchen", "playback"),
        ("opaque-conversation-1", "puck-kitchen", "playback_complete"),
    ]
    assert closed == [("opaque-conversation-1", "puck-kitchen", "stopped")]
    assert bridge.state == "disconnected"


def test_bridge_closes_claim_when_standard_session_startup_fails():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "error": {"code": "session_unavailable", "message": "offline"},
            },
        ]
    )
    closed: list[tuple[str, str, str]] = []
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle, device_id, "family"
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        conversation_closer=lambda handle, device_id, *, reason: (
            closed.append((handle, device_id, reason)) or True
        ),
    )

    status = bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )

    assert status.status == "unavailable"
    assert status.reason == "request_rejected"
    assert closed == [
        ("opaque-conversation-1", "puck-kitchen", "session_startup_failed")
    ]


def test_bridge_closes_claim_that_has_no_durable_session_for_reconnect():
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    closed: list[str] = []
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "puck-kitchen"},
        ),
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle, device_id, "family"
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        conversation_closer=lambda handle, device_id, *, reason: (
            closed.append(reason) or True
        ),
    )
    assert (
        bridge.open(
            headers={"Authorization": "Device device-secret"},
            conversation_handle="opaque-conversation-1",
        ).status
        == "ready"
    )

    bridge.close()

    assert closed == ["session_unavailable"]


def test_bridge_revokes_claim_when_the_paired_credential_generation_changes():
    class ContextAuthenticator:
        generation = 1

        def authenticate_device_context(self, headers):
            if headers.get("Authorization") != "Device device-secret":
                return None
            return SimpleNamespace(device_id="puck-kitchen", generation=self.generation)

        def authenticate_device(self, headers):
            context = self.authenticate_device_context(headers)
            return None if context is None else context.device_id

    authenticator = ContextAuthenticator()
    gateway_socket = FakeJsonSocket(
        [
            _event("gateway.ready", {}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
        ]
    )
    closed: list[str] = []
    bridge = HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=authenticator,
        conversation_resolver=lambda handle, device_id: ConversationGrant(
            handle,
            device_id,
            "family",
            credential_generation=1,
        ),
        gateway_socket_factory=FakeSocketFactory(gateway_socket),
        conversation_closer=lambda handle, device_id, *, reason: (
            closed.append(reason) or True
        ),
    )
    assert (
        bridge.open(
            headers={"Authorization": "Device device-secret"},
            conversation_handle="opaque-conversation-1",
        ).status
        == "ready"
    )
    authenticator.generation = 2

    with pytest.raises(BridgeAuthorizationError, match="stale_conversation"):
        bridge.submit_prompt("Do not send")

    assert "authorization_revoked" in closed
    assert all(frame["method"] != "prompt.submit" for frame in gateway_socket.sent)


def _live_turn_socket(*events: dict[str, object]) -> FakeJsonSocket:
    return FakeJsonSocket(
        [
            _event("gateway.ready", {"capabilities": {}}),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {"session_id": "runtime-hermes-1"},
            },
            {"jsonrpc": "2.0", "id": "home-2", "result": {"status": "streaming"}},
            *[
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {"session_id": "runtime-hermes-1", **event},
                }
                for event in events
            ],
        ]
    )


def test_bridge_binds_standard_activity_and_errors_to_the_running_turn():
    gateway_socket = _live_turn_socket(
        {"type": "message.start"},
        {"type": "reasoning.delta", "payload": {"text": "Considering"}},
        {"type": "status.update", "payload": {"kind": "process", "text": "Working"}},
        {"type": "error", "payload": {"message": "provider failed"}},
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    turn = bridge.submit_prompt("hello")

    events = [bridge.next_event() for _ in range(4)]

    assert [event.type for event in events] == [
        "message.start",
        "reasoning.delta",
        "status.update",
        "error",
    ]
    assert {event.turn_id for event in events} == {turn.turn_id}


def test_bridge_accepts_standard_free_text_batch_clarify():
    gateway_socket = _live_turn_socket(
        {"type": "message.start"},
        {
            "type": "clarify.request",
            "payload": {
                "questions": [
                    {
                        "qid": "q0",
                        "question": "What name?",
                        "choices": None,
                        "multi_select": False,
                    }
                ],
                "request_id": "f968a59f",
            },
        },
    )
    bridge = _make_bridge(FakeSocketFactory(gateway_socket))
    bridge.open(
        headers={"Authorization": "Device device-secret"},
        conversation_handle="opaque-conversation-1",
    )
    bridge.submit_prompt("ask me")
    bridge.next_event()

    prompt = bridge.next_event()

    assert prompt.type == "clarify.request"
    assert prompt.correlation_id == "f968a59f"
    with pytest.raises(BridgeProtocolError, match="question_id"):
        bridge.respond_prompt(prompt, {"answer": "probe-file"})
