"""HOME-NW-18: what a client claim's bridge and socket see when Home closes it."""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time

import pytest

from hermes_home.api.bridge_server import _EndpointParkingLot
from hermes_home.auth.static import StaticCredentialAuthenticator
from hermes_home.bridge import BridgeEndpoint, HomeBridge
from hermes_home.bridge.production import ConversationGrantStore
from hermes_home.bridge.standard import BridgeClaimClosed
from tests.test_standard_bridge import FakeJsonSocket, _event

HEADERS = {"Authorization": "Device device-secret"}
ROUTE = {"class": "home", "id": "approved-route-label"}
CONFIGURATION = {
    "revision": 1,
    "rooms": [],
    "profiles": [{"id": "amanda", "name": "Amanda", "available": True}],
    "wake_mappings": [],
    "devices": [],
}


class BlockingJsonSocket(FakeJsonSocket):
    """A Standard socket that stays open when idle, like a real gateway.

    Frames listed in ``gated`` (by index into the queued frames) wait for
    ``release`` before they are delivered.
    """

    def __init__(self, incoming, *, gated_ids=()) -> None:
        super().__init__(incoming)
        self.gated_ids = set(gated_ids)
        self.release = threading.Event()

    def receive_json(self, timeout: float | None = None):
        if self.closed:
            raise ConnectionError("fixture socket closed")
        if self.incoming:
            frame = self.incoming[0]
            if frame.get("id") in self.gated_ids and not self.release.is_set():
                time.sleep(timeout or 0.01)
                raise TimeoutError("gated")
            return self.incoming.popleft()
        time.sleep(timeout or 0.01)
        raise TimeoutError("idle")


class SocketFactory:
    def __init__(self, *sockets: FakeJsonSocket) -> None:
        self.sockets = list(sockets)

    def open(self, url: str) -> FakeJsonSocket:
        del url
        return self.sockets.pop(0)


class RecordingConnection:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closes: list[dict[str, object]] = []
        self.closed = threading.Event()

    def send(self, message, **_kwargs) -> None:
        self.sent.append(message)

    def close(self, **kwargs) -> None:
        self.closes.append(kwargs)
        self.closed.set()


def _standard_frames(session: str = "1") -> list[dict[str, object]]:
    return [
        _event("gateway.ready", {}),
        {
            "jsonrpc": "2.0",
            "id": "home-1",
            "result": {
                "session_id": f"runtime-hermes-{session}",
                "stored_session_id": f"durable-hermes-{session}",
            },
        },
        {"jsonrpc": "2.0", "id": "home-2", "result": {"accepted": True}},
        {"jsonrpc": "2.0", "id": "home-3", "result": {"accepted": True}},
    ]


def _store(tmp_path) -> ConversationGrantStore:
    handles = itertools.count(1)
    refs = itertools.count(1)
    return ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=lambda: CONFIGURATION,
        handle_factory=lambda: f"handle-{next(handles)}",
        claim_ref_factory=lambda: f"cref-{next(refs)}",
    )


def _claim(store: ConversationGrantStore, claim_id: str) -> tuple[str, str]:
    return store.create_client_claim(
        claim_id=claim_id,
        device_id="laptop",
        grant_id="grant-amanda",
        profile_id="amanda",
        configuration_revision=1,
        credential_generation=None,
    )


def _bridge(store: ConversationGrantStore, factory: SocketFactory) -> HomeBridge:
    return HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "laptop"},
        ),
        conversation_resolver=store.resolve,
        gateway_socket_factory=factory,
        session_persistor=store.persist_session,
        conversation_closer=store.close_claim,
        activity_recorder=store.record_activity,
        conversation_opener=store.mark_open,
        conversation_disconnector=store.mark_disconnected,
        revocation_registrar=store.register_revocation_handler,
        revocation_unregistrar=store.unregister_revocation_handler,
    )


def _close_reason(store: ConversationGrantStore, handle: str) -> str:
    return store._connection.execute(
        "SELECT close_reason FROM conversation_claims WHERE handle = ?", (handle,)
    ).fetchone()[0]


def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met")
        time.sleep(0.01)


def _rpc(endpoint: BridgeEndpoint, method: str, **params) -> dict[str, object]:
    response = endpoint.handle_message(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "schema": 1,
                "id": f"{method}-1",
                "method": method,
                "params": params,
            }
        )
    )
    assert isinstance(response, dict)
    return response


def test_closing_a_live_mid_turn_claim_interrupts_and_keeps_the_session(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    handle, ref = _claim(store, "claim-1")
    socket = BlockingJsonSocket(_standard_frames())
    bridge = _bridge(store, SocketFactory(socket))
    try:
        assert bridge.open(headers=HEADERS, conversation_handle=handle).status == (
            "ready"
        )
        bridge.submit_prompt("continue the accepted turn")
        assert store.active_session_ids() == frozenset({"durable-hermes-1"})

        assert store.close_client_claims("laptop", [ref]) == frozenset({ref})

        assert socket.sent[-1]["method"] == "session.interrupt"
        assert socket.sent[-1]["params"] == {"session_id": "runtime-hermes-1"}
        assert bridge.state == "unavailable"
        with pytest.raises(BridgeClaimClosed):
            bridge.next_event()
        with pytest.raises(BridgeClaimClosed):
            bridge.submit_prompt("after close")
        # Follow-on cleanup never rewrites the first close reason.
        bridge.retire_failed_upstream()
        assert _close_reason(store, handle) == "client_closed"
        # The Session stays resumable: free, with its grant-scoped reference.
        assert store.active_session_ids() == frozenset()
        ref_before = store.session_ref("grant-amanda", "durable-hermes-1")
        assert store.session_for_ref("grant-amanda", ref_before) == "durable-hermes-1"
    finally:
        bridge.close()
        store.close()


def test_endpoint_reports_a_closed_claim_as_terminal_stale_conversation(
    tmp_path, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    store = _store(tmp_path)
    handle, ref = _claim(store, "claim-1")
    socket = BlockingJsonSocket(_standard_frames())
    bridge = _bridge(store, SocketFactory(socket))
    connection = RecordingConnection()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)
    try:
        opened = _rpc(endpoint, "conversation.open", conversation_handle=handle)
        assert opened["result"]["status"] == "ready"
        submitted = _rpc(
            endpoint, "prompt.submit", conversation_handle=handle, text="hello"
        )
        assert submitted["result"]["status"] == "submitted"

        store.close_client_claims("laptop", [ref])

        assert connection.closed.wait(3.0)
        assert connection.closes == [{"code": 1000, "reason": "stale_conversation"}]
        assert any(frame.get("method") == "session.interrupt" for frame in socket.sent)
        assert "reconnect_required" not in json.dumps(connection.sent)
        assert endpoint._availability_reason == "stale_conversation"
        assert _close_reason(store, handle) == "client_closed"
        assert "upstream unavailable" not in caplog.text
        assert handle[:8] not in caplog.text
        assert ref not in caplog.text
    finally:
        endpoint.close()
        store.close()


def test_close_racing_an_open_fails_the_open_and_keeps_client_closed(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    handle, ref = _claim(store, "claim-1")
    socket = BlockingJsonSocket(_standard_frames(), gated_ids={"home-2"})
    bridge = _bridge(store, SocketFactory(socket))
    statuses = []
    closed: list[frozenset[str]] = []
    opener = threading.Thread(
        target=lambda: statuses.append(
            bridge.open(headers=HEADERS, conversation_handle=handle)
        )
    )
    closer = threading.Thread(
        target=lambda: closed.append(store.close_client_claims("laptop", [ref]))
    )
    try:
        opener.start()
        _wait_for(lambda: any(f.get("method") == "session.create" for f in socket.sent))
        closer.start()
        _wait_for(lambda: not store.client_claims("laptop"))
        socket.release.set()
        opener.join(5)
        closer.join(5)

        assert closed == [frozenset({ref})]
        (status,) = statuses
        assert (status.status, status.reason) == ("unavailable", "stale_conversation")
        assert socket.sent[-1]["method"] == "session.interrupt"
        assert _close_reason(store, handle) == "client_closed"
    finally:
        bridge.close()
        store.close()


def test_revoking_an_old_claim_never_tears_down_the_current_one(tmp_path) -> None:
    store = _store(tmp_path)
    first, first_ref = _claim(store, "claim-1")
    second, _second_ref = _claim(store, "claim-2")
    bridge = _bridge(
        store,
        SocketFactory(
            BlockingJsonSocket(_standard_frames("1")),
            BlockingJsonSocket(_standard_frames("2")),
        ),
    )
    try:
        assert bridge.open(headers=HEADERS, conversation_handle=first).status == "ready"
        assert (
            bridge.open(headers=HEADERS, conversation_handle=second).status == "ready"
        )

        assert store.close_client_claims("laptop", [first_ref]) == frozenset(
            {first_ref}
        )

        assert bridge.state == "ready"
        assert store.resolve(second, "laptop") is not None
    finally:
        bridge.close()
        store.close()


class ParkedEndpoint:
    def __init__(self, handle: str) -> None:
        self.conversation_handle = handle
        self.has_recoverable_state = True
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def test_parking_marks_the_claim_waiting_and_adoption_clears_it(tmp_path) -> None:
    store = _store(tmp_path)
    handle, _ref = _claim(store, "claim-1")
    store.mark_open(handle, "laptop")
    store.record_activity(handle, "laptop", "turn")
    lot = _EndpointParkingLot(120.0, store)

    lot.park(ParkedEndpoint(handle))
    assert [view.state for view in store.client_claims("laptop")] == [
        "waiting_to_reconnect"
    ]

    assert lot.take(handle) is not None
    assert [view.state for view in store.client_claims("laptop")] == ["replying"]
    store.close()


def test_park_expiry_clears_the_marker_before_the_grace_starts(tmp_path) -> None:
    store = _store(tmp_path)
    handle, _ref = _claim(store, "claim-1")
    store.mark_open(handle, "laptop")
    lot = _EndpointParkingLot(0.05, store)
    endpoint = ParkedEndpoint(handle)

    lot.park(endpoint)
    _wait_for(lambda: endpoint.closed == 1)

    assert handle not in store._detached
    store.close()


def test_closing_a_parked_claim_evicts_it_and_reconnect_is_stale(tmp_path) -> None:
    store = _store(tmp_path)
    handle, ref = _claim(store, "claim-1")
    socket = BlockingJsonSocket(_standard_frames())
    bridge = _bridge(store, SocketFactory(socket))
    lot = _EndpointParkingLot(120.0, store)
    store.add_close_listener(lot.evict)
    connection = RecordingConnection()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)
    try:
        _rpc(endpoint, "conversation.open", conversation_handle=handle)
        endpoint.detach()
        lot.park(endpoint)
        assert lot.contains(handle)
        assert store.client_claims("laptop")[0].state == "waiting_to_reconnect"

        store.close_client_claims("laptop", [ref])

        assert not lot.contains(handle)
        assert any(frame.get("method") == "session.interrupt" for frame in socket.sent)
        fresh = _bridge(store, SocketFactory())
        status = fresh.open(headers=HEADERS, conversation_handle=handle)
        assert (status.status, status.reason) == ("unavailable", "stale_conversation")
        assert _close_reason(store, handle) == "client_closed"
    finally:
        endpoint.close()
        store.close()
