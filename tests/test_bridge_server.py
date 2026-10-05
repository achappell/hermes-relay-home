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


def _send_rpc(
    client,
    request_id: str,
    method: str,
    params: dict[str, object],
    *,
    diagnostics: dict[str, object] | None = None,
) -> None:
    request = {
        "jsonrpc": "2.0",
        "schema": 1,
        "id": request_id,
        "method": method,
        "params": params,
    }
    if diagnostics is not None:
        request["diagnostics"] = diagnostics
    client.send(json.dumps(request))


def _receive_rpc(client, request_id: str) -> dict[str, object]:
    while True:
        response = json.loads(client.recv(timeout=2))
        if response.get("id") == request_id:
            return response


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


@pytest.mark.parametrize("shutdown_while_parked", [False, True])
def test_healthy_idle_background_disconnect_retains_upstream_until_grace(
    caplog, shutdown_while_parked
):
    bridges = []

    class IdleBridge(ListenerBridge):
        retired = False
        prompt_calls = 0

        def __init__(self):
            super().__init__()
            self.complete = threading.Event()

        def next_event(self):
            while not self.closed.is_set():
                if self.complete.wait(0.01):
                    self.complete.clear()
                    return BridgeEvent(
                        HANDLE,
                        "message.complete",
                        {"status": "completed"},
                        turn_id="turn-after-idle",
                    )
            raise ConnectionError("closed")

        def next_audio(self, *, timeout=None):
            return AudioFrame("unavailable", turn_id="turn-after-idle")

        def retire_failed_upstream(self):
            self.retired = True

        def reauthorize(self, *, headers):
            if headers["Authorization"] != "Device endpoint-secret":
                return BridgeStatus("unavailable", HANDLE, "unauthorized")
            return BridgeStatus("ready", HANDLE)

        def submit_prompt(self, text):
            self.prompt_calls += 1
            return BridgeTurn("turn-after-idle", HANDLE)

    def factory():
        bridge = IdleBridge()
        bridges.append(bridge)
        return bridge

    server = create_bridge_server(
        bridge_factory=factory, host="127.0.0.1", port=0, reconnect_grace_seconds=1.0
    )
    thread = _start(server)
    url = f"ws://127.0.0.1:{server.socket.getsockname()[1]}{BRIDGE_WS_PATH}"
    headers = {"Authorization": "Device endpoint-secret"}

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

    try:
        with connect(url, additional_headers=headers) as client:
            assert request(client, "conversation.open")["status"] == "ready"
        # Model return late within grace (the pilot returned after 106/120s).
        assert not bridges[0].closed.wait(0.2)
        with connect(
            url, additional_headers={"Authorization": "Device wrong"}
        ) as client:
            assert request(client, "conversation.reconnect")["reason"] == "unauthorized"
        assert not bridges[0].closed.wait(0.2)
        with connect(url, additional_headers=headers) as client:
            assert request(client, "conversation.reconnect")["status"] == "ready"
            assert (
                request(client, "prompt.submit", text="After idle")["status"]
                == "submitted"
            )
            bridges[0].complete.set()
            while True:
                completion = json.loads(client.recv(timeout=2))
                if completion.get("method") == "event":
                    assert completion["params"]["event"]["type"] == "message.complete"
                    break
        assert len(bridges) == 1
        assert bridges[0].prompt_calls == 1
        assert not bridges[0].closed.wait(0.2)
        if shutdown_while_parked:
            server.shutdown()
            assert bridges[0].closed.wait(0.2)
        else:
            assert bridges[0].closed.wait(2)
        assert not bridges[0].retired
        assert "Home upstream unavailable" not in caplog.text
    finally:
        server.shutdown()
        thread.join(timeout=2)
        for bridge in bridges:
            bridge.close()


@pytest.mark.parametrize("blocked_phase", ["suspend", "rebuild"])
def test_recovery_keeps_original_deadline_and_honors_deferred_close(
    monkeypatch, blocked_phase
):
    from websockets.exceptions import ConnectionClosed

    from hermes_home.api.bridge_server import _EndpointParkingLot

    parked = threading.Event()
    blocked = threading.Event()
    release = threading.Event()
    retired = threading.Event()
    fail = threading.Event()
    deadlines = []
    endpoints = []
    original_park = _EndpointParkingLot.park

    def observe(self, endpoint, **kwargs):
        original_park(self, endpoint, **kwargs)
        endpoints.append(endpoint)
        with self._lock:
            entry = self._entries.get(HANDLE)
            if entry is not None:
                deadlines.append(entry.expires_at)
        parked.set()

    monkeypatch.setattr(_EndpointParkingLot, "park", observe)

    class RecoveringBridge(ListenerBridge):
        recovery_available = True

        def reauthorize(self, *, headers):
            return BridgeStatus("ready", HANDLE)

        def next_event(self):
            fail.wait(2)
            raise BridgeTransportError("lost")

        def suspend_failed_upstream(self):
            if blocked_phase == "suspend":
                blocked.set()
                assert release.wait(2)
            return True

        def recover_upstream(self, *, headers):
            blocked.set()
            assert release.wait(2)
            return BridgeStatus("ready", HANDLE)

        def retire_failed_upstream(self):
            self.recovery_available = False
            retired.set()
            self.close()

    bridge = RecoveringBridge()
    server = create_bridge_server(
        bridge_factory=lambda: bridge,
        host="127.0.0.1",
        port=0,
        reconnect_grace_seconds=0.5,
    )
    thread = _start(server)
    url = f"ws://127.0.0.1:{server.socket.getsockname()[1]}{BRIDGE_WS_PATH}"
    headers = {"Authorization": "Device endpoint-secret"}
    results = []

    def rpc(method):
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
            try:
                results.append(json.loads(client.recv(timeout=2)))
            except ConnectionClosed:
                results.append("closed")

    worker = None
    try:
        rpc("conversation.open")
        assert parked.wait(1)
        fail.set()
        if blocked_phase == "suspend":
            assert blocked.wait(1)
        else:
            deadline = time.monotonic() + 1
            while not endpoints[0]._recovery_pending and time.monotonic() < deadline:
                time.sleep(0.001)
            assert endpoints[0]._recovery_pending
        worker = threading.Thread(target=rpc, args=("conversation.reconnect",))
        worker.start()
        if blocked_phase == "suspend":
            worker.join(timeout=1)
            assert results[-1]["result"]["status"] == "unavailable"
            assert deadlines == [deadlines[0], deadlines[0]]
            deadline = time.monotonic() + 1
            while not endpoints[0]._close_requested and time.monotonic() < deadline:
                time.sleep(0.001)
            assert endpoints[0]._close_requested
        else:
            assert blocked.wait(1)
            assert retired.wait(1)
        release.set()
        worker.join(timeout=2)
        assert retired.wait(1)
        assert not endpoints[0].has_recoverable_state
        assert not endpoints[0].ready
        assert bridge.closed.is_set()
        if blocked_phase == "rebuild":
            assert results[-1] == "closed"
    finally:
        release.set()
        fail.set()
        if worker is not None:
            worker.join(timeout=2)
        server.shutdown()
        thread.join(timeout=2)
        bridge.close()


def test_create_bridge_server_wires_claim_store_for_parking_and_close(
    tmp_path, monkeypatch
) -> None:
    from hermes_home.api import bridge_server
    from tests.test_client_claim_store import _claim_with_ref, _store

    store = _store(tmp_path)
    handle, claim_ref = _claim_with_ref(store, "claim-server")
    store.mark_open(handle, "laptop")

    lots = []
    original_lot = bridge_server._EndpointParkingLot

    def capture_lot(*args, **kwargs):
        lot = original_lot(*args, **kwargs)
        lots.append(lot)
        return lot

    monkeypatch.setattr(bridge_server, "_EndpointParkingLot", capture_lot)

    class FakeServer:
        def shutdown(self):
            return None

    server = create_bridge_server(
        websocket_serve=lambda *_args, **_kwargs: FakeServer(),
        claim_store=store,
    )

    class Parked:
        conversation_handle = handle
        has_recoverable_state = True

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    parked = Parked()
    try:
        (lot,) = lots
        lot.park(parked)
        assert [view.state for view in store.client_claims("laptop")] == [
            "waiting_to_reconnect"
        ]

        assert store.close_client_claims("laptop", [claim_ref]) == frozenset(
            {claim_ref}
        )
        assert not lot.contains(handle)
        assert parked.closed
        assert store.client_claims("laptop") == []
    finally:
        server.shutdown()
        store.close()


@pytest.mark.parametrize(
    ("early_method", "failure_code", "reason", "reject_adoption"),
    [
        ("conversation.open", "reconnect_required", "reconnect_required", False),
        (
            "conversation.reconnect",
            "transport_unavailable",
            "transport_unavailable",
            True,
        ),
    ],
)
def test_early_rejections_log_one_socket_identity_without_request_or_handle_data(
    tmp_path,
    monkeypatch,
    early_method,
    failure_code,
    reason,
    reject_adoption,
) -> None:
    from hermes_home.api.bridge_server import _EndpointParkingLot
    from hermes_home.bridge.endpoint import BridgeEndpoint
    from hermes_home_diagnostics import OperationalDiagnostics

    parked = threading.Event()
    original_park = _EndpointParkingLot.park

    def observe_park(lot, endpoint, **kwargs):
        original_park(lot, endpoint, **kwargs)
        parked.set()

    monkeypatch.setattr(_EndpointParkingLot, "park", observe_park)
    if reject_adoption:

        def reject(*_args, **_kwargs):
            raise RuntimeError

        monkeypatch.setattr(BridgeEndpoint, "adopt", reject)

    class RecoverableBridge(ListenerBridge):
        def submit_prompt(self, text: str) -> BridgeTurn:
            del text
            return BridgeTurn("home-turn-1", "HOME-OPAQUE-HANDLE-CANARY")

        def next_audio(self, *, timeout=None):
            del timeout
            return AudioFrame("unavailable", turn_id="home-turn-1")

    diagnostics = OperationalDiagnostics(component="home", directory=tmp_path)
    server = create_bridge_server(
        bridge_factory=RecoverableBridge,
        host="127.0.0.1",
        port=0,
        operational_diagnostics=diagnostics,
        reconnect_grace_seconds=2,
    )
    thread = _start(server)
    port = server.socket.getsockname()[1]
    url = f"ws://127.0.0.1:{port}{BRIDGE_WS_PATH}"
    headers = {
        "Authorization": "Device secret-canary",
        "X-Hermes-Diagnostics-Version": "1",
    }
    handle_canary = "HOME-OPAQUE-HANDLE-CANARY"
    request_canary = "HOME-REQUEST-ID-CANARY"

    def request(client, request_id, method, conversation_handle):
        client.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "schema": 1,
                    "id": request_id,
                    "method": method,
                    "params": {"conversation_handle": conversation_handle},
                }
            )
        )
        while True:
            response = json.loads(client.recv(timeout=2))
            if response.get("id") == request_id:
                return response

    try:
        with connect(url, additional_headers=headers) as client:
            assert (
                request(client, "open-first", "conversation.open", handle_canary)[
                    "result"
                ]["status"]
                == "ready"
            )
            client.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "schema": 1,
                        "id": "prompt-first",
                        "method": "prompt.submit",
                        "params": {
                            "conversation_handle": handle_canary,
                            "text": "private prompt text",
                        },
                    }
                )
            )
            while True:
                response = json.loads(client.recv(timeout=2))
                if response.get("id") == "prompt-first":
                    assert response["result"]["status"] == "submitted"
                    break
        assert parked.wait(2)

        with connect(url, additional_headers=headers) as client:
            response = request(client, request_canary, early_method, handle_canary)
            assert response["result"]["reason"] == reason
    finally:
        server.shutdown()
        thread.join(timeout=2)
        diagnostics.close()

    raw = (tmp_path / "home.jsonl").read_text(encoding="utf-8")
    assert request_canary not in raw
    assert handle_canary not in raw
    assert "secret-canary" not in raw
    records = [json.loads(line) for line in raw.splitlines()]
    rejection = next(
        record
        for record in records
        if record["event"] == "rejection_generated"
        and record["failure_code"] == failure_code
    )
    connection_id = rejection["connection_id"]
    assert connection_id.startswith("conn-")
    for event in (
        "connection_opened",
        "response_write_started",
        "response_write_outcome",
        "connection_closed",
    ):
        assert any(
            record["event"] == event and record.get("connection_id") == connection_id
            for record in records
        )


def test_peer_close_before_first_request_is_observed_without_prose(tmp_path) -> None:
    from hermes_home_diagnostics import OperationalDiagnostics

    diagnostics = OperationalDiagnostics(component="home", directory=tmp_path)
    server = create_bridge_server(
        host="127.0.0.1",
        port=0,
        operational_diagnostics=diagnostics,
    )
    thread = _start(server)
    port = server.socket.getsockname()[1]
    try:
        with connect(
            f"ws://127.0.0.1:{port}{BRIDGE_WS_PATH}",
            additional_headers={"Authorization": "Device close-canary"},
        ):
            pass
    finally:
        server.shutdown()
        thread.join(timeout=2)
        diagnostics.close()

    raw = (tmp_path / "home.jsonl").read_text(encoding="utf-8")
    assert "close-canary" not in raw
    records = [json.loads(line) for line in raw.splitlines()]
    opened = next(item for item in records if item["event"] == "connection_opened")
    connection_id = opened["connection_id"]
    observed = next(item for item in records if item["event"] == "transport_observed")
    closed = next(item for item in records if item["event"] == "connection_closed")
    assert observed["connection_id"] == connection_id
    assert closed["connection_id"] == connection_id
    assert observed["exception_category"] == "connection_closed_ok"


def test_client_close_during_submit_records_failed_write_and_finalizes_association(
    tmp_path,
) -> None:
    from hermes_home.observability.client_reports import ClientReportStore
    from hermes_home_diagnostics import OperationalDiagnostics

    handle = "HOME-OPAQUE-HANDLE-CANARY"
    content = "HOME-PRIVATE-PROMPT-CANARY"
    rpc_id = "HOME-PRIVATE-RPC-ID-CANARY"
    request_token = "req-11111111111111111111111111111111"
    device_id = "authenticated-diagnostic-device"
    submit_started = threading.Event()
    release_submit = threading.Event()

    class BlockingSubmitBridge(ListenerBridge):
        def __init__(self) -> None:
            super().__init__()
            self.authenticated_device_id = device_id

        def submit_prompt(self, text: str) -> BridgeTurn:
            assert text == content
            submit_started.set()
            if not release_submit.wait(5):
                raise RuntimeError("test submit gate timed out")
            return BridgeTurn("home-turn-1", handle)

    bridge = BlockingSubmitBridge()
    diagnostics = OperationalDiagnostics(component="home", directory=tmp_path)
    client_reports = ClientReportStore(tmp_path / "client-reports.sqlite")
    server = create_bridge_server(
        bridge_factory=lambda: bridge,
        host="127.0.0.1",
        port=0,
        operational_diagnostics=diagnostics,
        client_reports=client_reports,
    )
    thread = _start(server)
    port = server.socket.getsockname()[1]
    headers = {
        "Authorization": "Device HOME-PRIVATE-AUTH-CANARY",
        "X-Hermes-Diagnostics-Version": "1",
    }

    flow_complete = False

    try:
        with connect(
            f"ws://127.0.0.1:{port}{BRIDGE_WS_PATH}",
            additional_headers=headers,
        ) as client:
            _send_rpc(
                client,
                "open",
                "conversation.open",
                {"conversation_handle": handle},
            )
            opened = _receive_rpc(client, "open")
            connection_id = opened["diagnostics"]["home_connection_id"]
            _send_rpc(
                client,
                rpc_id,
                "prompt.submit",
                {"conversation_handle": handle, "text": content},
                diagnostics={"version": 1, "request_id": request_token},
            )
            assert submit_started.wait(2)
            client.close()

        release_submit.set()
        flow_complete = True
    finally:
        release_submit.set()
        server.shutdown()
        thread.join(timeout=2)
        diagnostics.close()
        if not flow_complete:
            client_reports.close()

    try:
        raw = (tmp_path / "home.jsonl").read_text(encoding="utf-8")
        assert content not in raw
        assert "HOME-PRIVATE-AUTH-CANARY" not in raw
        assert rpc_id not in raw
        assert handle not in raw
        records = [json.loads(line) for line in raw.splitlines()]

        observed = next(
            record
            for record in records
            if record["event"] == "request_observed"
            and record.get("request_id") == request_token
        )
        correlation_id = observed["correlation_id"]
        assert observed["connection_id"] == connection_id
        started = next(
            record
            for record in records
            if record["event"] == "upstream_submit_started"
            and record["correlation_id"] == correlation_id
        )
        outcome = next(
            record
            for record in records
            if record["event"] == "upstream_submit_outcome"
            and record["correlation_id"] == correlation_id
        )
        assert started["connection_id"] == outcome["connection_id"] == connection_id
        assert outcome["outcome"] == "accepted"

        transports = [
            record
            for record in records
            if record["event"] == "transport_observed"
            and record["connection_id"] == connection_id
        ]
        assert len(transports) == 1
        transport = transports[0]
        closed = next(
            record
            for record in records
            if record["event"] == "connection_closed"
            and record["connection_id"] == connection_id
        )
        assert transport["pending_count"] == 0
        assert transport["pending_state"] == "none"
        request_lost = next(
            record
            for record in records
            if record["event"] == "request_transport_lost"
            and record["correlation_id"] == correlation_id
        )
        assert request_lost["pending_state"] == "awaiting_response"
        assert request_lost["outcome"] == "unknown"
        assert closed["connection_id"] == connection_id

        write_outcomes = [
            record["outcome"]
            for record in records
            if record["event"] == "response_write_outcome"
            and record["correlation_id"] == correlation_id
        ]
        assert write_outcomes
        assert "write_returned" not in write_outcomes

        association = None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            association = client_reports.lookup_association(
                device_id, connection_id, request_token
            )
            if association["state"] == "linked":
                break
            time.sleep(0.01)
        assert association["state"] == "linked"
        assert association["correlation_id"] == correlation_id
    finally:
        client_reports.close()


def test_duplicate_diagnostic_request_token_is_ambiguous_without_suppressing_prompts(
    tmp_path,
) -> None:
    from hermes_home.observability.client_reports import ClientReportStore
    from hermes_home_diagnostics import OperationalDiagnostics

    handle = "opaque-home-handle"
    device_id = "authenticated-diagnostic-device"
    request_token = "req-22222222222222222222222222222222"

    class CompletingBridge(ListenerBridge):
        def __init__(self) -> None:
            super().__init__()
            self.authenticated_device_id = device_id
            self.events: queue.Queue[BridgeEvent] = queue.Queue()
            self.prompt_calls: list[str] = []
            self.release_first_submit = threading.Event()

        def submit_prompt(self, text: str) -> BridgeTurn:
            self.prompt_calls.append(text)
            turn_id = f"home-turn-{len(self.prompt_calls)}"
            self.events.put(
                BridgeEvent(
                    handle,
                    "turn.complete",
                    {"status": "completed"},
                    turn_id=turn_id,
                )
            )
            if len(self.prompt_calls) == 1 and not self.release_first_submit.wait(5):
                raise RuntimeError("test terminal event gate timed out")
            return BridgeTurn(turn_id, handle)

        def next_event(self):
            while not self.closed.is_set():
                try:
                    return self.events.get(timeout=0.05)
                except queue.Empty:
                    continue
            raise BridgeTransportError("closed")

    bridge = CompletingBridge()
    diagnostics = OperationalDiagnostics(component="home", directory=tmp_path)
    client_reports = ClientReportStore(tmp_path / "client-reports.sqlite")
    server = create_bridge_server(
        bridge_factory=lambda: bridge,
        host="127.0.0.1",
        port=0,
        operational_diagnostics=diagnostics,
        client_reports=client_reports,
    )
    thread = _start(server)
    port = server.socket.getsockname()[1]
    headers = {
        "Authorization": "Device test-device",
        "X-Hermes-Diagnostics-Version": "1",
    }

    try:
        with connect(
            f"ws://127.0.0.1:{port}{BRIDGE_WS_PATH}",
            additional_headers=headers,
        ) as client:
            _send_rpc(
                client,
                "open",
                "conversation.open",
                {"conversation_handle": handle},
            )
            opened = _receive_rpc(client, "open")
            connection_id = opened["diagnostics"]["home_connection_id"]
            first = {
                "conversation_handle": handle,
                "text": "first prompt",
            }
            second = {
                "conversation_handle": handle,
                "text": "second prompt",
            }
            _send_rpc(
                client,
                "first",
                "prompt.submit",
                first,
                diagnostics={"version": 1, "request_id": request_token},
            )
            first_terminal_seen = False
            while not first_terminal_seen:
                response = json.loads(client.recv(timeout=2))
                if response.get("method") == "event":
                    params = response.get("params")
                    event = params.get("event") if isinstance(params, dict) else None
                    first_terminal_seen = (
                        isinstance(event, dict) and event.get("type") == "turn.complete"
                    )
            bridge.release_first_submit.set()
            first_response = _receive_rpc(client, "first")
            _send_rpc(
                client,
                "second",
                "prompt.submit",
                second,
                diagnostics={"version": 1, "request_id": request_token},
            )
            second_response = _receive_rpc(client, "second")
            assert first_response["result"]["status"] == "submitted"
            assert first_response["diagnostics"]["request_id"] == request_token
            assert second_response["result"]["status"] == "submitted"
            assert "diagnostics" not in second_response
        server.shutdown()
        thread.join(timeout=2)
        diagnostics.close()

        association = None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            association = client_reports.lookup_association(
                device_id, connection_id, request_token
            )
            if association["state"] == "ambiguous":
                break
            time.sleep(0.01)
        assert association["state"] == "ambiguous"
        assert bridge.prompt_calls == ["first prompt", "second prompt"]
        assert diagnostics.status()["correlation_conflicts"] == 1
        assert client_reports.recent()["association_losses"]["conflicts"] == 1

        records = [
            json.loads(line)
            for line in (tmp_path / "home.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        submit_outcomes = [
            record for record in records if record["event"] == "upstream_submit_outcome"
        ]
        assert len(submit_outcomes) == 2
        assert all(
            record["outcome"] == "accepted" and record["connection_id"] == connection_id
            for record in submit_outcomes
        )
    finally:
        bridge.release_first_submit.set()
        server.shutdown()
        thread.join(timeout=2)
        diagnostics.close()
        client_reports.close()
