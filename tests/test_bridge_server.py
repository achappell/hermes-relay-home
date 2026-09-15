"""Live local-listener checks for the Home bridge WebSocket server."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping

import pytest
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from hermes_home.api.bridge_server import (
    BRIDGE_WS_PATH,
    MAX_BRIDGE_MESSAGE_BYTES,
    create_bridge_server,
)
from hermes_home.bridge import BridgeStatus, BridgeTransportError

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
