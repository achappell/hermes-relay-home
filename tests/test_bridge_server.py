"""Live local-listener checks for the Home bridge WebSocket server."""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Mapping

import pytest
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from hermes_home.api.bridge_server import (
    BRIDGE_WS_PATH,
    MAX_BRIDGE_MESSAGE_BYTES,
    create_bridge_server,
)
from hermes_home.bridge import (
    AudioFrame,
    BridgeEvent,
    BridgeStatus,
    BridgeTransportError,
    BridgeTurn,
)

HANDLE = "opaque-home-handle"


class ListenerBridge:
    def __init__(self) -> None:
        self.closed = threading.Event()
        self.open_headers: Mapping[str, str] | None = None

    def open(
        self,
        *,
        headers: Mapping[str, str],
        conversation_handle: str,
    ) -> BridgeStatus:
        self.open_headers = dict(headers)
        return BridgeStatus(
            "ready",
            conversation_handle,
            capabilities={"commands": [], "heartbeat": True, "timing": "absent"},
        )

    def reconnect(self, *, headers: Mapping[str, str]) -> BridgeStatus:
        del headers
        return BridgeStatus("ready", HANDLE)

    def next_event(self):
        self.closed.wait()
        raise BridgeTransportError("closed")

    def next_audio(self, *, timeout=None):
        del timeout
        self.closed.wait()
        raise BridgeTransportError("closed")

    def close(self) -> None:
        self.closed.set()


def _start(server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_live_bridge_server_accepts_only_the_versioned_route_and_closes_bridges() -> (
    None
):
    bridges: list[ListenerBridge] = []

    def factory() -> ListenerBridge:
        bridge = ListenerBridge()
        bridges.append(bridge)
        return bridge

    server = create_bridge_server(
        bridge_factory=factory,
        route={"class": "home", "id": "local-test"},
        host="127.0.0.1",
        port=0,
    )
    thread = _start(server)

    try:
        port = server.socket.getsockname()[1]
        with connect(
            f"ws://127.0.0.1:{port}{BRIDGE_WS_PATH}",
            additional_headers={"Authorization": "Device endpoint-secret"},
        ) as client:
            client.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "schema": 1,
                        "id": "open-1",
                        "method": "conversation.open",
                        "params": {"conversation_handle": HANDLE},
                    }
                )
            )
            response = json.loads(client.recv())
            assert response["result"]["status"] == "ready"
            assert response["result"]["route"] == {
                "class": "home",
                "id": "local-test",
            }
            assert bridges[0].open_headers is not None
            assert bridges[0].open_headers["Authorization"] == (
                "Device endpoint-secret"
            )
            assert "endpoint-secret" not in json.dumps(response)
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(bridges) == 1
    assert bridges[0].closed.is_set()


def test_live_server_requires_reconnect_and_adopts_the_parked_turn() -> None:
    class RecoverableBridge(ListenerBridge):
        def __init__(self) -> None:
            super().__init__()
            self.prompt_calls: list[str] = []
            self.reauthorize_calls = 0

        def submit_prompt(self, text: str) -> BridgeTurn:
            self.prompt_calls.append(text)
            return BridgeTurn("home-turn-1", HANDLE)

        def next_audio(self, *, timeout=None):
            del timeout
            return AudioFrame("unavailable", turn_id="home-turn-1")

        def reauthorize(self, *, headers: Mapping[str, str]) -> BridgeStatus:
            assert headers["Authorization"] == "Device endpoint-secret"
            self.reauthorize_calls += 1
            return BridgeStatus(
                "ready",
                HANDLE,
                unresolved_turn=BridgeTurn("home-turn-1", HANDLE, "streaming"),
            )

    bridges: list[RecoverableBridge] = []

    def factory() -> RecoverableBridge:
        bridge = RecoverableBridge()
        bridges.append(bridge)
        return bridge

    server = create_bridge_server(
        bridge_factory=factory,
        host="127.0.0.1",
        port=0,
        reconnect_grace_seconds=0.5,
    )
    thread = _start(server)
    port = server.socket.getsockname()[1]
    url = f"ws://127.0.0.1:{port}{BRIDGE_WS_PATH}"
    headers = {"Authorization": "Device endpoint-secret"}

    def receive_id(client, request_id: str) -> dict[str, object]:
        while True:
            message = json.loads(client.recv())
            if message.get("id") == request_id:
                return message

    try:
        with connect(url, additional_headers=headers) as client:
            client.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "schema": 1,
                        "id": "open-1",
                        "method": "conversation.open",
                        "params": {"conversation_handle": HANDLE},
                    }
                )
            )
            assert receive_id(client, "open-1")["result"]["status"] == "ready"
            client.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "schema": 1,
                        "id": "prompt-1",
                        "method": "prompt.submit",
                        "params": {
                            "conversation_handle": HANDLE,
                            "text": "survive this drop",
                        },
                    }
                )
            )
            assert receive_id(client, "prompt-1")["result"]["status"] == "submitted"

        time.sleep(0.05)
        with connect(url, additional_headers=headers) as client:
            client.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "schema": 1,
                        "id": "open-2",
                        "method": "conversation.open",
                        "params": {"conversation_handle": HANDLE},
                    }
                )
            )
            result = json.loads(client.recv())["result"]
            assert result["status"] == "unavailable"
            assert result["reason"] == "reconnect_required"

        with connect(url, additional_headers=headers) as client:
            client.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "schema": 1,
                        "id": "reconnect-1",
                        "method": "conversation.reconnect",
                        "params": {"conversation_handle": HANDLE},
                    }
                )
            )
            result = json.loads(client.recv())["result"]
            assert result["status"] == "ready"
            assert result["unresolved_turn"]["status"] == "streaming"

        assert len(bridges) == 1
        assert bridges[0].prompt_calls == ["survive this drop"]
        assert bridges[0].reauthorize_calls == 1
        deadline = time.monotonic() + 1.0
        while not bridges[0].closed.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bridges[0].closed.is_set()
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_failed_adoption_does_not_extend_the_original_reconnect_grace() -> None:
    class RecoverableBridge(ListenerBridge):
        def __init__(self) -> None:
            super().__init__()
            self.reauthorize_calls = 0

        def submit_prompt(self, text: str) -> BridgeTurn:
            del text
            return BridgeTurn("home-turn-1", HANDLE)

        def next_audio(self, *, timeout=None):
            del timeout
            return AudioFrame("unavailable", turn_id="home-turn-1")

        def reauthorize(self, *, headers: Mapping[str, str]) -> BridgeStatus:
            self.reauthorize_calls += 1
            if headers.get("Authorization") != "Device endpoint-secret":
                return BridgeStatus("unavailable", HANDLE, "unauthorized")
            return BridgeStatus(
                "ready",
                HANDLE,
                unresolved_turn=BridgeTurn("home-turn-1", HANDLE, "streaming"),
            )

    bridges: list[RecoverableBridge] = []

    def factory() -> RecoverableBridge:
        bridge = RecoverableBridge()
        bridges.append(bridge)
        return bridge

    server = create_bridge_server(
        bridge_factory=factory,
        host="127.0.0.1",
        port=0,
        reconnect_grace_seconds=1.0,
    )
    thread = _start(server)
    port = server.socket.getsockname()[1]
    url = f"ws://127.0.0.1:{port}{BRIDGE_WS_PATH}"
    good_headers = {"Authorization": "Device endpoint-secret"}
    bad_headers = {"Authorization": "Device wrong-secret"}

    try:
        with connect(url, additional_headers=good_headers) as client:
            client.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "schema": 1,
                        "id": "open-1",
                        "method": "conversation.open",
                        "params": {"conversation_handle": HANDLE},
                    }
                )
            )
            assert json.loads(client.recv())["result"]["status"] == "ready"
            client.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "schema": 1,
                        "id": "prompt-1",
                        "method": "prompt.submit",
                        "params": {"conversation_handle": HANDLE, "text": "keep alive"},
                    }
                )
            )
            assert json.loads(client.recv())["result"]["status"] == "submitted"

        drop_time = time.monotonic()
        time.sleep(0.55)
        with connect(url, additional_headers=bad_headers) as client:
            client.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "schema": 1,
                        "id": "reconnect-unauthorized",
                        "method": "conversation.reconnect",
                        "params": {"conversation_handle": HANDLE},
                    }
                )
            )
            result = json.loads(client.recv())["result"]
            assert result["status"] == "unavailable"
            assert result["reason"] == "unauthorized"

        remaining = max(0.0, drop_time + 1.3 - time.monotonic())
        time.sleep(remaining)
        assert bridges[0].closed.is_set()
        assert bridges[0].reauthorize_calls == 1
        assert len(bridges) == 1
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_bridge_server_rejects_wrong_path_and_malformed_device_auth() -> None:
    server = create_bridge_server(host="127.0.0.1", port=0)
    thread = _start(server)
    port = server.socket.getsockname()[1]

    try:
        with (
            pytest.raises(InvalidStatus),
            connect(
                f"ws://127.0.0.1:{port}/wrong",
                additional_headers={"Authorization": "Device secret"},
            ),
        ):
            pass
        with (
            pytest.raises(InvalidStatus),
            connect(
                f"ws://127.0.0.1:{port}{BRIDGE_WS_PATH}",
                additional_headers={"Authorization": "Bearer secret"},
            ),
        ):
            pass
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_bridge_server_passes_the_one_mebibyte_bound_to_websockets() -> None:
    captured: dict[str, object] = {}

    class FakeServer:
        socket = None

        def serve_forever(self) -> None:
            return None

    def fake_serve(handler, host, port, **kwargs):
        captured.update(kwargs)
        captured["handler"] = handler
        captured["host"] = host
        captured["port"] = port
        return FakeServer()

    server = create_bridge_server(
        host="127.0.0.1",
        port=8766,
        websocket_serve=fake_serve,
    )

    assert captured["max_size"] == MAX_BRIDGE_MESSAGE_BYTES
    assert captured["max_size"] == 1_048_576
    assert callable(captured["handler"])
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8766
    assert server.socket is None


@pytest.mark.parametrize(
    "headers",
    [
        None,
        {},
        {"Authorization": ""},
        [
            ("Authorization", "Device first"),
            ("Authorization", "Device second"),
        ],
    ],
    ids=["missing", "empty", "blank", "duplicate"],
)
def test_bridge_server_rejects_missing_or_ambiguous_device_auth(headers) -> None:
    created = []
    server = create_bridge_server(
        bridge_factory=lambda: created.append(True),
        host="127.0.0.1",
        port=0,
    )
    thread = _start(server)
    port = server.socket.getsockname()[1]

    try:
        kwargs = {} if headers is None else {"additional_headers": headers}
        with pytest.raises(InvalidStatus):
            connect(f"ws://127.0.0.1:{port}{BRIDGE_WS_PATH}", **kwargs)
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert created == []


@pytest.mark.parametrize("idle", [False, True])
def test_parked_upstream_failure_is_terminal_through_live_server(monkeypatch, idle):
    from hermes_home.api.bridge_server import _EndpointParkingLot

    parked = threading.Event()
    retired = threading.Event()
    parked_endpoints = []
    original_park = _EndpointParkingLot.park

    def observe_park(self, endpoint, **kwargs):
        original_park(self, endpoint, **kwargs)
        parked_endpoints.append(endpoint)
        parked.set()

    monkeypatch.setattr(_EndpointParkingLot, "park", observe_park)
    events = queue.Queue()
    prompt_calls = []
    bridges = []

    class FailingBridge(ListenerBridge):
        def open(self, *, headers, conversation_handle):
            if retired.is_set():
                return BridgeStatus(
                    "unavailable", conversation_handle, "stale_conversation"
                )
            return super().open(
                headers=headers, conversation_handle=conversation_handle
            )

        def submit_prompt(self, text):
            prompt_calls.append(text)
            return BridgeTurn("turn-1", HANDLE)

        def next_event(self):
            while not self.closed.is_set():
                try:
                    event = events.get(timeout=0.01)
                except queue.Empty:
                    continue
                if isinstance(event, Exception):
                    raise event
                return event
            raise ConnectionError("closed")

        def next_audio(self, *, timeout=None):
            return AudioFrame("unavailable", turn_id="turn-1")

        def retire_failed_upstream(self):
            retired.set()

    def factory():
        bridge = FailingBridge()
        bridges.append(bridge)
        return bridge

    def request(client, method, **params):
        client.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "schema": 1,
                    "id": method,
                    "method": method,
                    "params": {"conversation_handle": HANDLE, **params},
                }
            )
        )
        return json.loads(client.recv(timeout=2))["result"]

    server = create_bridge_server(
        bridge_factory=factory, host="127.0.0.1", port=0, reconnect_grace_seconds=10
    )
    thread = _start(server)
    url = f"ws://127.0.0.1:{server.socket.getsockname()[1]}{BRIDGE_WS_PATH}"
    headers = {"Authorization": "Device endpoint-secret"}
    try:
        with connect(url, additional_headers=headers) as client:
            assert request(client, "conversation.open")["status"] == "ready"
            assert (
                request(client, "prompt.submit", text="Only once")["status"]
                == "submitted"
            )
        assert parked.wait(2)
        if idle:
            events.put(
                BridgeEvent(
                    conversation_handle=HANDLE,
                    type="message.complete",
                    turn_id="turn-1",
                    payload={"status": "completed"},
                )
            )
            deadline = time.monotonic() + 2
            while parked_endpoints[0].has_active_turn and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not parked_endpoints[0].has_active_turn
        events.put(BridgeTransportError("dead while parked"))
        assert retired.wait(2)
        assert bridges[0].closed.wait(2)
        for _ in range(5):
            with connect(url, additional_headers=headers) as client:
                result = request(client, "conversation.reconnect")
                assert result["status"] == "unavailable"
                assert result["reason"] == "stale_conversation"
        assert len(parked_endpoints) == 1
        assert prompt_calls == ["Only once"]
    finally:
        server.shutdown()
        thread.join(timeout=2)
        for bridge in bridges:
            bridge.close()


def test_healthy_idle_background_disconnect_uses_fresh_bridge_on_return(caplog):
    bridges = []

    class IdleBridge(ListenerBridge):
        retired = False

        def retire_failed_upstream(self):
            self.retired = True

    def factory():
        bridge = IdleBridge()
        bridges.append(bridge)
        return bridge

    server = create_bridge_server(bridge_factory=factory, host="127.0.0.1", port=0)
    thread = _start(server)
    url = f"ws://127.0.0.1:{server.socket.getsockname()[1]}{BRIDGE_WS_PATH}"
    headers = {"Authorization": "Device endpoint-secret"}
    try:
        for method in ("conversation.open", "conversation.reconnect"):
            with connect(url, additional_headers=headers) as client:
                client.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "schema": 1,
                            "id": method,
                            "method": method,
                            "params": {"conversation_handle": HANDLE},
                        }
                    )
                )
                assert json.loads(client.recv(timeout=2))["result"]["status"] == "ready"
            assert bridges[-1].closed.wait(2)
        assert len(bridges) == 2
        assert all(not bridge.retired for bridge in bridges)
        assert "Home upstream unavailable" not in caplog.text
    finally:
        server.shutdown()
        thread.join(timeout=2)
