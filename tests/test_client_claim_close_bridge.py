"""HOME-NW-18: what a client claim's bridge and socket see when Home closes it."""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from types import SimpleNamespace

import pytest

from hermes_home.api.bridge_server import _EndpointParkingLot
from hermes_home.auth.static import StaticCredentialAuthenticator
from hermes_home.bridge import BridgeEndpoint, HomeBridge
from hermes_home.bridge.production import ConversationGrantStore
from hermes_home.bridge.standard import BridgeClaimClosed
from hermes_home.domain.arbitration import WakeDecision
from tests.test_client_claim_list_close_api import (
    CapturingDiagnostics,
    InMemoryDiagnosticsStore,
    ListHome,
)
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


class InterruptFailingSocket(BlockingJsonSocket):
    def send_json(self, frame: dict[str, object]) -> None:
        if frame.get("method") == "session.interrupt":
            raise RuntimeError(
                "session_id=runtime-hermes-1 claim_ref=cref-private handle=handle-1"
            )
        super().send_json(frame)


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


class ContextAuthenticator:
    """Durable-credential shape: a device ID plus a credential generation."""

    def __init__(self, device_id: str = "laptop", generation: int = 1) -> None:
        self.device_id = device_id
        self.generation = generation

    def authenticate_device_context(self, headers):
        if headers.get("Authorization") != HEADERS["Authorization"]:
            return None
        return SimpleNamespace(device_id=self.device_id, generation=self.generation)

    def authenticate_device(self, headers):
        context = self.authenticate_device_context(headers)
        return None if context is None else context.device_id


def _store(tmp_path, *, configuration=None, clock=None) -> ConversationGrantStore:
    handles = itertools.count(1)
    refs = itertools.count(1)
    kwargs = {} if clock is None else {"clock": clock}
    return ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=configuration or (lambda: CONFIGURATION),
        handle_factory=lambda: f"handle-{next(handles)}",
        claim_ref_factory=lambda: f"cref-{next(refs)}",
        **kwargs,
    )


def _claim(
    store: ConversationGrantStore, claim_id: str, *, generation: int | None = None
) -> tuple[str, str]:
    return store.create_client_claim(
        claim_id=claim_id,
        device_id="laptop",
        grant_id="grant-amanda",
        profile_id="amanda",
        configuration_revision=1,
        credential_generation=generation,
    )


def _bridge(
    store: ConversationGrantStore,
    factory: SocketFactory,
    *,
    authenticator=None,
    opener=None,
) -> HomeBridge:
    return HomeBridge(
        gateway_url="wss://hermes.example/api/ws",
        hermes_token="server-hermes-secret",
        device_authenticator=authenticator
        or StaticCredentialAuthenticator(
            admin_token="admin-secret",
            device_credentials={"device-secret": "laptop"},
        ),
        conversation_resolver=store.resolve,
        gateway_socket_factory=factory,
        session_persistor=store.persist_session,
        conversation_closer=store.close_claim,
        activity_recorder=store.record_activity,
        conversation_opener=opener or store.mark_open,
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
    diagnostics = CapturingDiagnostics(store=InMemoryDiagnosticsStore())
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        diagnostics=diagnostics,
    )
    try:
        opened = _rpc(endpoint, "conversation.open", conversation_handle=handle)
        assert opened["result"]["status"] == "ready"
        submitted = _rpc(
            endpoint, "prompt.submit", conversation_handle=handle, text="hello"
        )
        assert submitted["result"]["status"] == "submitted"
        session_ref = store.session_ref("grant-amanda", "durable-hermes-1")

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
        captured = caplog.text + json.dumps(
            [event.to_dict() for event in diagnostics.accepted]
        )
        for secret in (
            ref,
            handle,
            handle[:8],
            session_ref,
            "grant-amanda",
            "runtime-hermes-1",
        ):
            assert secret not in captured
    finally:
        endpoint.close()
        store.close()


def test_interrupt_error_logs_only_exception_type_and_no_identifiers(
    tmp_path, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    store = _store(tmp_path)
    handle, ref = _claim(store, "claim-interrupt-error")
    socket = InterruptFailingSocket(_standard_frames())
    bridge = _bridge(store, SocketFactory(socket))
    connection = RecordingConnection()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)
    try:
        _rpc(endpoint, "conversation.open", conversation_handle=handle)
        _rpc(endpoint, "prompt.submit", conversation_handle=handle, text="hello")
        store.close_client_claims("laptop", [ref])
        assert connection.closed.wait(3.0)
        assert "RuntimeError" in caplog.text
        assert "session_id=runtime-hermes-1" not in caplog.text
        for secret in (ref, handle, handle[:8], "grant-amanda", "runtime-hermes-1"):
            assert secret not in caplog.text
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
        assert closer.is_alive()
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


def test_closing_a_parked_claim_evicts_it_and_reconnect_is_stale(
    tmp_path, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    store = _store(tmp_path)
    handle, ref = _claim(store, "claim-1")
    socket = BlockingJsonSocket(_standard_frames())
    bridge = _bridge(store, SocketFactory(socket))
    lot = _EndpointParkingLot(120.0, store)
    store.add_close_listener(lot.evict)
    connection = RecordingConnection()
    diagnostics = CapturingDiagnostics(store=InMemoryDiagnosticsStore())
    endpoint = BridgeEndpoint(
        connection, bridge, headers=HEADERS, route=ROUTE, diagnostics=diagnostics
    )
    try:
        _rpc(endpoint, "conversation.open", conversation_handle=handle)
        endpoint.detach()
        lot.park(endpoint)
        assert lot.contains(handle)
        assert store.client_claims("laptop")[0].state == "waiting_to_reconnect"
        session_ref = store.session_ref("grant-amanda", "durable-hermes-1")

        store.close_client_claims("laptop", [ref])

        assert not lot.contains(handle)
        assert any(frame.get("method") == "session.interrupt" for frame in socket.sent)
        fresh = _bridge(store, SocketFactory())
        status = fresh.open(headers=HEADERS, conversation_handle=handle)
        assert (status.status, status.reason) == ("unavailable", "stale_conversation")
        assert _close_reason(store, handle) == "client_closed"
        captured = caplog.text + json.dumps(
            [event.to_dict() for event in diagnostics.accepted]
        )
        for secret in (
            ref,
            handle,
            handle[:8],
            session_ref,
            "grant-amanda",
            "runtime-hermes-1",
        ):
            assert secret not in captured
    finally:
        endpoint.close()
        store.close()


# Review D1: only a client close (reason client_closed) is terminal. Every other
# revocation keeps the pre-NW-18 outcome: reconnect_required and a 1011 close.


def _wake_claim(store: ConversationGrantStore) -> str:
    return store.create_from_decision(
        WakeDecision(
            claim_id="wake-1",
            decision="granted",
            arbitration_id="arb-1",
            configuration_revision=1,
            device_id="laptop",
            room_id="kitchen",
            wake_mapping_id="hey-hermes",
            profile_id="amanda",
        ),
        credential_generation=1,
    )


ROOM_CONFIGURATION = {
    **CONFIGURATION,
    "rooms": [{"id": "kitchen", "name": "Kitchen"}],
    "wake_mappings": [
        {
            "id": "hey-hermes",
            "phrase": "Hey Hermes",
            "profile_id": "amanda",
            "active": True,
        }
    ],
}


def _device_revoke(store, authenticator, configuration, handle) -> None:
    store.close_device_claims(
        "laptop", current_generation=None, reason="endpoint_revoked"
    )


def _grant_revoke(store, authenticator, configuration, handle) -> None:
    store.close_grant_claims("grant-amanda", reason="grant_revoked")


def _generation_change(store, authenticator, configuration, handle) -> None:
    # The bridge's next revalidation sees a newer credential generation.
    authenticator.generation = 2


def _configuration_change(store, authenticator, configuration, handle) -> None:
    # The bridge's next revalidation finds the Profile unavailable.
    configuration["profiles"] = [{"id": "amanda", "name": "Amanda", "available": False}]


def _configuration_revision_change(store, authenticator, configuration, handle) -> None:
    del authenticator, configuration
    store._connection.execute(
        "UPDATE conversation_claims SET configuration_revision = 2 WHERE handle = ?",
        (handle,),
    )
    store._connection.commit()


@pytest.mark.parametrize(
    ("change", "room_claim", "close_reason"),
    [
        (_device_revoke, False, "endpoint_revoked"),
        (_grant_revoke, False, "grant_revoked"),
        (_generation_change, False, "authorization_revoked"),
        (_configuration_change, False, "profile_revoked"),
        (_configuration_revision_change, False, "authorization_revoked"),
        (_device_revoke, True, "endpoint_revoked"),
    ],
    ids=[
        "device-revoke",
        "grant-revoke",
        "generation-change",
        "configuration-change",
        "configuration-revision-change",
        "room-claim",
    ],
)
def test_other_revocations_keep_the_upstream_failure_outcome(
    tmp_path, change, room_claim, close_reason
) -> None:
    configuration = json.loads(json.dumps(ROOM_CONFIGURATION))
    store = _store(tmp_path, configuration=lambda: configuration)
    if room_claim:
        handle = _wake_claim(store)
    else:
        handle, _ref = _claim(store, "claim-1", generation=1)
    authenticator = ContextAuthenticator()
    socket = BlockingJsonSocket(_standard_frames())
    bridge = _bridge(store, SocketFactory(socket), authenticator=authenticator)
    connection = RecordingConnection()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)
    try:
        opened = _rpc(endpoint, "conversation.open", conversation_handle=handle)
        assert opened["result"]["status"] == "ready"
        _rpc(endpoint, "prompt.submit", conversation_handle=handle, text="hello")

        change(store, authenticator, configuration, handle)

        assert connection.closed.wait(3.0)
        # Measured on 07de094 (pre-NW-18): the event loop retires the upstream
        # and the client socket closes 1011 "upstream unavailable"; nothing on
        # the wire says stale_conversation.
        assert connection.closes == [{"code": 1011, "reason": "upstream unavailable"}]
        assert endpoint._upstream_failed is True
        assert "stale_conversation" not in json.dumps(connection.sent)
        assert store.resolve(handle, "laptop") is None
        reason = store._connection.execute(
            "SELECT close_reason FROM conversation_claims WHERE handle = ?", (handle,)
        ).fetchone()[0]
        assert reason == close_reason
    finally:
        endpoint.close()
        store.close()


# Review P1: parking-marker updates are ordered with entry publish/removal.


class GatedObserver:
    """Delegates to the real store; one marker call blocks until released."""

    def __init__(self, store: ConversationGrantStore, gated: str) -> None:
        self.store = store
        self.gated = gated
        self.entered = threading.Event()
        self.release = threading.Event()

    def _maybe_block(self, name: str) -> None:
        if name == self.gated and not self.entered.is_set():
            self.entered.set()
            assert self.release.wait(3.0)

    def mark_detached(self, handle: str) -> None:
        self._maybe_block("mark_detached")
        self.store.mark_detached(handle)

    def clear_detached(self, handle: str) -> None:
        self._maybe_block("clear_detached")
        self.store.clear_detached(handle)

    def add_close_listener(self, listener) -> None:
        self.store.add_close_listener(listener)


def test_a_take_racing_a_park_never_leaves_a_stale_marker(tmp_path) -> None:
    store = _store(tmp_path)
    handle, _ref = _claim(store, "claim-1")
    store.mark_open(handle, "laptop")
    observer = GatedObserver(store, "mark_detached")
    lot = _EndpointParkingLot(120.0, observer)
    taken = []

    parker = threading.Thread(target=lambda: lot.park(ParkedEndpoint(handle)))
    parker.start()
    assert observer.entered.wait(3.0)
    # The park is mid-marker. An adopting reconnect now takes the endpoint.
    taker = threading.Thread(target=lambda: taken.append(lot.take(handle)))
    taker.start()
    taker.join(0.2)
    observer.release.set()
    parker.join(3.0)
    taker.join(3.0)

    assert taken and taken[0] is not None
    assert handle not in store._detached
    assert [view.state for view in store.client_claims("laptop")] == ["idle"]
    store.close()


def test_a_repark_racing_an_expiry_keeps_the_new_marker(tmp_path) -> None:
    store = _store(tmp_path)
    handle, _ref = _claim(store, "claim-1")
    store.mark_open(handle, "laptop")
    observer = GatedObserver(store, "clear_detached")
    lot = _EndpointParkingLot(120.0, observer)
    lot.park(ParkedEndpoint(handle))

    expirer = threading.Thread(target=lambda: lot._expire(handle))
    expirer.start()
    assert observer.entered.wait(3.0)
    # The old entry is expiring mid-marker; the client's endpoint parks again.
    reparker = threading.Thread(target=lambda: lot.park(ParkedEndpoint(handle)))
    reparker.start()
    reparker.join(0.2)
    observer.release.set()
    expirer.join(3.0)
    reparker.join(3.0)

    assert lot.contains(handle)
    assert handle in store._detached
    lot.close()
    store.close()


def test_close_racing_reconnect_returns_stale_and_preserves_first_reason(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    handle, ref = _claim(store, "claim-reconnect")
    initial = BlockingJsonSocket(_standard_frames())
    reconnecting = BlockingJsonSocket(_standard_frames(), gated_ids={"home-2"})
    bridge = _bridge(store, SocketFactory(initial, reconnecting))
    results = []
    closed = []
    try:
        assert (
            bridge.open(headers=HEADERS, conversation_handle=handle).status == "ready"
        )
        reconnect_thread = threading.Thread(
            target=lambda: results.append(bridge.reconnect(headers=HEADERS))
        )
        reconnect_thread.start()
        _wait_for(
            lambda: any(
                frame.get("method") == "session.resume" for frame in reconnecting.sent
            )
        )
        close_thread = threading.Thread(
            target=lambda: closed.append(store.close_client_claims("laptop", [ref]))
        )
        close_thread.start()
        _wait_for(lambda: not store.client_claims("laptop"))
        assert close_thread.is_alive()
        reconnecting.release.set()
        reconnect_thread.join(5)
        close_thread.join(5)

        assert not reconnect_thread.is_alive()
        assert not close_thread.is_alive()
        assert closed == [frozenset({ref})]
        (status,) = results
        assert (status.status, status.reason) == (
            "unavailable",
            "stale_conversation",
        )
        assert any(
            frame.get("method") == "session.interrupt" for frame in reconnecting.sent
        )
        assert _close_reason(store, handle) == "client_closed"
    finally:
        reconnecting.release.set()
        bridge.close()
        store.close()


def test_http_close_keeps_session_inactive_and_resumable_through_api(
    tmp_path,
) -> None:
    api = ListHome(tmp_path)
    material = api.pair("laptop", ["amanda"])
    grant_id = material["client_grants"][0]["grant_id"]
    created = api.new_claim(material, grant_id, "claim-http-close")
    handle = created["conversation_handle"]
    ref = created["claim_ref"]

    first_socket = BlockingJsonSocket(_standard_frames())
    first_bridge = _bridge(
        api.claims,
        SocketFactory(first_socket),
        authenticator=ContextAuthenticator(material["device_id"]),
    )
    first_connection = RecordingConnection()
    first_endpoint = BridgeEndpoint(
        first_connection, first_bridge, headers=HEADERS, route=ROUTE
    )
    try:
        assert (
            _rpc(first_endpoint, "conversation.open", conversation_handle=handle)[
                "result"
            ]["status"]
            == "ready"
        )
        _rpc(first_endpoint, "prompt.submit", conversation_handle=handle, text="hello")
        session_ref = api.claims.session_ref(grant_id, "durable-hermes-1")
        api.directory.sessions["amanda"] = [
            {
                "id": "durable-hermes-1",
                "title": "Resumable",
                "started_at": 1,
                "message_count": 1,
            }
        ]

        result = api.close(material, [ref])
        assert result.status == 200
        assert result.body["results"] == [{"claim_ref": ref, "result": "closed"}]
        assert first_connection.closed.wait(3.0)

        listed = api.call(
            "POST",
            "/api/v1/client-sessions/list",
            {"schema": 1, "grant_id": grant_id, "limit": 10},
            credential=material["credential"],
        )
        assert listed.status == 200
        assert listed.body["sessions"] == [
            {
                "session_ref": session_ref,
                "title": "Resumable",
                "started_at": 1,
                "message_count": 1,
                "active": False,
            }
        ]

        resumed_claim = api.claim(
            material,
            grant_id,
            "claim-http-resume",
            session={"mode": "resume", "session_ref": session_ref},
        )
        assert resumed_claim.status == 200
        second_socket = BlockingJsonSocket(_standard_frames())
        second_bridge = _bridge(
            api.claims,
            SocketFactory(second_socket),
            authenticator=ContextAuthenticator(material["device_id"]),
        )
        second_connection = RecordingConnection()
        second_endpoint = BridgeEndpoint(
            second_connection, second_bridge, headers=HEADERS, route=ROUTE
        )
        try:
            opened = _rpc(
                second_endpoint,
                "conversation.open",
                conversation_handle=resumed_claim.body["conversation_handle"],
            )
            assert opened["result"]["status"] == "ready"
            assert any(
                frame.get("method") == "session.resume" for frame in second_socket.sent
            )
        finally:
            second_endpoint.close()
    finally:
        first_endpoint.close()
