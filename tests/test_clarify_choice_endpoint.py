"""A projected clarify choice at the endpoint: its 3600 s window and its one answer.

The choice authority, endpoint, and bridge here are the real ones. Standard is a
scripted socket that replays frames captured on 2026-10-11 against Standard
Hermes 0.21.1 (`2237be3`).
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import replace
from typing import ClassVar

import pytest

from hermes_home.bridge import BridgeEndpoint, BridgeEvent
from hermes_home.bridge.choice_authority import ChoiceAuthority, ChoiceProtocolError
from hermes_home.bridge.standard import CLARIFY_CHOICE_TTL_SECONDS
from tests.test_bridge_endpoint import (
    FakeBridge,
    FakeConnection,
    _choice_event,
    _deliver_choice_event,
    _message,
    _open,
    _send,
)
from tests.test_clarify_choice_projection import (
    HANDLE as BRIDGE_HANDLE,
)
from tests.test_clarify_choice_projection import (
    HEADERS as BRIDGE_HEADERS,
)
from tests.test_clarify_choice_projection import (
    PINNED_READY,
    SESSION,
    _captured_clarify,
    _captured_events,
    _captured_result,
    _resolver,
)
from tests.test_client_claim_close_bridge import BlockingJsonSocket, RecordingConnection
from tests.test_standard_bridge import FakeSocketFactory, _make_bridge

HANDLE = "opaque-home-handle"
ROUTE = {"class": "home", "id": "approved-route-label"}
HEADERS = {"Authorization": "Device endpoint-secret"}


# -- ChoiceAuthority: per-object window --------------------------------------


def _source_event(
    correlation_id: str,
    *,
    ttl: object = None,
    turn_id: str = "turn-1",
    object_id: str = "source-object-1",
) -> BridgeEvent:
    event = BridgeEvent(
        HANDLE,
        "prompt.request",
        {
            "prompt_kind": "choice",
            "text": "Pick one.",
            "choice": {
                "object_id": object_id,
                "freshness": "clarify",
                "operations": ["choose"],
            },
            "options": [
                {"id": "opt-1", "label": "red"},
                {"id": "opt-2", "label": "green"},
            ],
        },
        turn_id=turn_id,
        correlation_id=correlation_id,
        standard_session_id="runtime-1",
        configuration_revision=3,
        choice_capabilities=frozenset({"prompt.choose"}),
    )
    return replace(event, choice_ttl_seconds=ttl)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _authority(clock: Clock) -> ChoiceAuthority:
    counter = iter(range(1, 1000))
    return ChoiceAuthority(clock=clock, token_factory=lambda: f"tok-{next(counter)}")


def _issue(authority: ChoiceAuthority, event: BridgeEvent):
    public = authority.project(event, handle=HANDLE)
    assert authority.mark_delivered(
        handle=HANDLE, turn_id=event.turn_id, correlation_id=event.correlation_id
    ) == int(public["timeout_s"])
    return public


def _answer(authority: ChoiceAuthority, event: BridgeEvent, public, option="opt-1"):
    return authority.authorize(
        handle=HANDLE,
        turn_id=event.turn_id,
        correlation_id=event.correlation_id,
        event_type="prompt.request",
        response={
            "operation": "choose",
            "option_id": option,
            "object_id": public["choice"]["object_id"],
            "freshness": public["choice"]["freshness"],
        },
        active_turn_id=event.turn_id,
        ready=True,
        event=event,
    )


def test_a_native_typed_choice_keeps_its_300_second_window() -> None:
    clock = Clock()
    authority = _authority(clock)
    event = _source_event("native-1")
    public = authority.project(event, handle=HANDLE)

    assert public["timeout_s"] == 300
    assert (
        authority.mark_delivered(
            handle=HANDLE, turn_id="turn-1", correlation_id="native-1"
        )
        == 300
    )
    clock.now += 300.1
    assert _answer(authority, event, public).reason == "expired"


def test_a_projected_choice_reports_3600_before_and_after_delivery() -> None:
    clock = Clock()
    authority = _authority(clock)
    event = _source_event("projected-1", ttl=CLARIFY_CHOICE_TTL_SECONDS)
    public = authority.project(event, handle=HANDLE)

    assert public["timeout_s"] == 3600
    assert (
        authority.mark_delivered(
            handle=HANDLE, turn_id="turn-1", correlation_id="projected-1"
        )
        == 3600
    )
    clock.now += 1000
    assert (
        authority.mark_delivered(
            handle=HANDLE, turn_id="turn-1", correlation_id="projected-1"
        )
        == 2600
    )


@pytest.mark.parametrize(
    ("elapsed", "outcome"),
    [(0.0, None), (300.1, None), (3599.0, None), (3600.1, "expired")],
)
def test_a_projected_choice_is_answerable_until_3600_seconds(elapsed, outcome) -> None:
    clock = Clock()
    authority = _authority(clock)
    event = _source_event("projected-1", ttl=3600.0)
    public = _issue(authority, event)

    clock.now += elapsed

    assert _answer(authority, event, public).reason == outcome


def test_a_native_choice_expires_while_a_projected_one_beside_it_still_answers() -> (
    None
):
    clock = Clock()
    authority = _authority(clock)
    native = _source_event("native-1", turn_id="turn-1")
    projected = _source_event("projected-1", ttl=3600.0, turn_id="turn-2")
    native_public = _issue(authority, native)
    projected_public = _issue(authority, projected)

    clock.now += 300.1

    assert _answer(authority, native, native_public).reason == "expired"
    assert _answer(authority, projected, projected_public).reason is None


def test_a_projected_answer_is_accepted_once_then_a_duplicate() -> None:
    clock = Clock()
    authority = _authority(clock)
    event = _source_event("projected-1", ttl=3600.0)
    public = _issue(authority, event)

    first = _answer(authority, event, public)
    authority.complete(first)

    assert first.reason is None
    assert _answer(authority, event, public).reason == "duplicate"


def test_a_newer_choice_replaces_a_projected_one_inside_its_long_window() -> None:
    clock = Clock()
    authority = _authority(clock)
    old = _source_event("projected-old", ttl=3600.0, object_id="object-old")
    new = _source_event("projected-new", ttl=3600.0, object_id="object-new")
    old_public = _issue(authority, old)
    clock.now += 100
    new_public = _issue(authority, new)

    assert _answer(authority, old, old_public).reason == "replaced"
    assert _answer(authority, new, new_public).reason is None


def test_explore_is_unsupported_on_a_projected_choice() -> None:
    clock = Clock()
    authority = _authority(clock)
    event = _source_event("projected-1", ttl=3600.0)
    public = _issue(authority, event)
    decision = authority.authorize(
        handle=HANDLE,
        turn_id="turn-1",
        correlation_id="projected-1",
        event_type="prompt.request",
        response={
            "operation": "explore",
            "option_id": "opt-1",
            "object_id": public["choice"]["object_id"],
            "freshness": public["choice"]["freshness"],
        },
        active_turn_id="turn-1",
        ready=True,
        event=event,
    )

    assert public["choice"]["operations"] == ["choose"]
    assert decision.reason == "unsupported"


@pytest.mark.parametrize(
    "ttl",
    [
        0,
        -1,
        0.0,
        math.nan,
        math.inf,
        3600.5,
        86400,
        10**400,
        "3600",
        True,
        [3600],
    ],
)
def test_a_choice_ttl_outside_the_clarify_window_is_refused(ttl) -> None:
    authority = _authority(Clock())

    with pytest.raises(ChoiceProtocolError, match="TTL"):
        authority.project(_source_event("projected-1", ttl=ttl), handle=HANDLE)


@pytest.mark.parametrize("ttl", [1, 300, 300.0, 3600, 3600.0])
def test_a_choice_ttl_up_to_the_clarify_window_is_accepted(ttl) -> None:
    authority = _authority(Clock())

    public = authority.project(_source_event("projected-1", ttl=ttl), handle=HANDLE)

    assert public["timeout_s"] == int(ttl)


def test_a_payload_can_not_set_a_choice_window() -> None:
    clock = Clock()
    authority = _authority(clock)
    event = _source_event("native-1")
    poisoned = replace(
        event,
        payload={
            **event.payload,
            "ttl": 3600,
            "timeout_s": 3600,
            "choice_ttl_seconds": 3600,
            "choice": {**event.payload["choice"], "ttl": 3600, "timeout_s": 3600},
        },
    )

    public = authority.project(poisoned, handle=HANDLE)

    assert public["timeout_s"] == 300


def test_an_expired_projected_object_is_released_with_its_turn() -> None:
    clock = Clock()
    authority = _authority(clock)
    event = _source_event("projected-1", ttl=3600.0)
    public = _issue(authority, event)
    clock.now += 3600.1
    assert _answer(authority, event, public).reason == "expired"

    authority.revoke_turn(handle=HANDLE, turn_id="turn-1")

    assert authority._objects == {} and authority._current == {}


def test_an_idle_pending_hour_adds_no_state_and_no_thread() -> None:
    clock = Clock()
    authority = _authority(clock)
    event = _source_event("projected-1", ttl=3600.0)
    _issue(authority, event)
    objects, correlations = len(authority._objects), len(authority._correlations)
    threads = threading.active_count()

    for _ in range(60):
        clock.now += 60

    assert len(authority._objects) == objects == 1
    assert len(authority._correlations) == correlations == 1
    assert threading.active_count() == threads


def test_reconnect_authority_revocation_ends_a_projected_choice_at_once() -> None:
    clock = Clock()
    authority = _authority(clock)
    event = _source_event("projected-1", ttl=3600.0)
    public = _issue(authority, event)
    clock.now += 5

    authority.revoke_all()

    assert authority._objects == {} and authority._current == {}
    assert _answer(authority, event, public).reason == "revoked"


# -- BridgeEndpoint over a fake bridge ---------------------------------------


def _projected(correlation_id: str = "projected-correlation") -> BridgeEvent:
    event = _choice_event(correlation_id, operations=("choose",))
    return replace(
        event,
        choice_capabilities=frozenset({"prompt.choose"}),
        choice_ttl_seconds=CLARIFY_CHOICE_TTL_SECONDS,
    )


def _respond(endpoint, choice, correlation_id="projected-correlation", **response):
    return _send(
        endpoint,
        jsonrpc="2.0",
        schema=1,
        id="respond-1",
        method="prompt.respond",
        params={
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
            "correlation_id": correlation_id,
            "event_type": "prompt.request",
            "response": {
                "operation": "choose",
                "option_id": "inspect",
                "object_id": choice["object_id"],
                "freshness": choice["freshness"],
                **response,
            },
        },
    )


def _endpoint_at(now, connection=None):
    connection = connection or FakeConnection()
    bridge = FakeBridge()
    tokens = iter([f"home-token-{index}" for index in range(1, 50)])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_clock=lambda: now[0],
        choice_token_factory=tokens.__next__,
    )
    _open(endpoint)
    _send(
        endpoint,
        jsonrpc="2.0",
        schema=1,
        id="prompt-1",
        method="prompt.submit",
        params={"conversation_handle": HANDLE, "text": "ask"},
    )
    return endpoint, bridge, connection


def test_the_endpoint_reports_a_3600_second_window_to_the_client() -> None:
    now = [10.0]
    endpoint, _bridge, connection = _endpoint_at(now)
    try:
        _deliver_choice_event(endpoint, _projected())

        payload = _message(connection)["params"]["event"]["payload"]
        assert payload["timeout_s"] == 3600
        assert payload["choice"]["operations"] == ["choose"]
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    ("elapsed", "expected"), [(300.1, "accepted"), (3599.0, "accepted")]
)
def test_the_endpoint_still_accepts_a_projected_answer_after_300_seconds(
    elapsed, expected
) -> None:
    now = [10.0]
    endpoint, bridge, _connection = _endpoint_at(now)
    try:
        params = _deliver_choice_event(endpoint, _projected())
        choice = params["event"]["payload"]["choice"]
        now[0] += elapsed

        result = _respond(endpoint, choice)

        assert result["result"]["status"] == expected
        assert len(bridge.respond_calls) == 1
    finally:
        endpoint.close()


def test_the_endpoint_expires_a_projected_choice_after_3600_seconds() -> None:
    now = [10.0]
    endpoint, bridge, _connection = _endpoint_at(now)
    try:
        params = _deliver_choice_event(endpoint, _projected())
        choice = params["event"]["payload"]["choice"]
        now[0] += 3600.1

        result = _respond(endpoint, choice)

        assert result["result"]["status"] == "unavailable"
        assert result["result"]["reason"] == "expired"
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


def test_a_native_choice_through_the_endpoint_still_expires_at_300_seconds() -> None:
    now = [10.0]
    endpoint, bridge, _connection = _endpoint_at(now)
    try:
        params = _deliver_choice_event(endpoint, _choice_event("native-correlation"))
        choice = params["event"]["payload"]["choice"]
        now[0] += 300.1

        result = _respond(endpoint, choice, correlation_id="native-correlation")

        assert result["result"]["reason"] == "expired"
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    ("method", "expected_status"),
    [
        ("conversation.reconnect", "ready"),
        ("conversation.close", "closed"),
    ],
)
def test_a_delivered_projected_choice_is_revoked_on_reconnect_or_close(
    method: str, expected_status: str
) -> None:
    now = [10.0]
    endpoint, bridge, _connection = _endpoint_at(now)
    try:
        params = _deliver_choice_event(endpoint, _projected())
        choice = params["event"]["payload"]["choice"]

        control = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="control-1",
            method=method,
            params={"conversation_handle": HANDLE},
        )
        result = _respond(endpoint, choice)

        assert control["result"]["status"] == expected_status
        assert result["result"]["status"] == "unavailable"
        assert result["result"]["reason"] == "revoked"
        assert bridge.prompt_calls == ["ask"]
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


def test_a_parked_projected_choice_gets_its_full_window_when_first_sent() -> None:
    now = [10.0]
    endpoint, bridge, _connection = _endpoint_at(now)
    try:
        endpoint.detach()
        params = endpoint._event_payload(_projected())
        assert endpoint._send_json(
            {"jsonrpc": "2.0", "schema": 1, "method": "event", "params": params}
        )

        now[0] = 1_000.0
        second_connection = FakeConnection()
        endpoint.adopt(second_connection, headers=HEADERS)
        reconnect = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="reconnect-1",
            method="conversation.reconnect",
            params={"conversation_handle": HANDLE},
        )

        choice_payload = _message(second_connection, 1)["params"]["event"]["payload"]
        assert reconnect["result"]["status"] == "ready"
        assert choice_payload["timeout_s"] == 3600
        now[0] = 1_000.0 + 3599.0
        result = _respond(endpoint, choice_payload["choice"])
        assert result["result"]["status"] == "accepted"
        assert len(bridge.respond_calls) == 1
    finally:
        endpoint.close()


# -- Whole path: real bridge, endpoint, authority, captured Standard frames --


class ReleasingSocket(BlockingJsonSocket):
    """A Standard that speaks only after it is asked.

    Real Standard emits turn events after it accepts `prompt.submit` and answers
    `clarify.respond` only when asked. This socket holds each queued frame (and
    so everything behind it) until the request it answers has been sent. Without
    that, the endpoint's event pump, started at `conversation.open`, can read
    queued events before the turn exists and drop them. Ids are the post-shift
    ones: `commands.catalog` takes `home-1`.
    """

    GATES: ClassVar[dict[str, str]] = {
        "home-3": "prompt.submit",
        "home-4": "clarify.respond",
    }

    def __init__(self, frames) -> None:
        super().__init__(frames)
        self.sent_methods: set[str] = set()

    def send_json(self, frame: dict[str, object]) -> None:
        super().send_json(frame)
        method = frame.get("method")
        if isinstance(method, str):
            self.sent_methods.add(method)

    def receive_json(self, timeout: float | None = None):
        if self.closed:
            raise ConnectionError("fixture socket closed")
        if self.incoming:
            frame = self.incoming[0]
            params = frame.get("params")
            waiting_for_submit = (
                frame.get("method") == "event"
                and isinstance(params, dict)
                and params.get("session_id") == SESSION
                and "prompt.submit" not in self.sent_methods
            )
            gate = self.GATES.get(frame.get("id"))
            if waiting_for_submit or (
                gate is not None and gate not in self.sent_methods
            ):
                time.sleep(timeout or 0.01)
                raise TimeoutError("gated")
            return self.incoming.popleft()
        time.sleep(timeout or 0.01)
        raise TimeoutError("idle")


def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met")
        time.sleep(0.005)


def _events(connection: RecordingConnection, kind: str) -> list[dict]:
    found = []
    for raw in list(connection.sent):
        document = json.loads(raw)
        if document.get("method") == "event" and (
            document["params"]["event"]["type"] == kind
        ):
            found.append(document["params"])
    return found


def _frames(events, results):
    frames = [
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "gateway.ready", "payload": PINNED_READY},
        },
        {"jsonrpc": "2.0", "id": "home-1", "result": {"session_id": SESSION}},
        {"jsonrpc": "2.0", "id": "home-2", "result": {"status": "streaming"}},
    ]
    frames += [
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"session_id": SESSION, **event},
        }
        for event in events
    ]
    frames += [
        {"jsonrpc": "2.0", "id": f"home-{3 + index}", "result": result}
        for index, result in enumerate(results)
    ]
    return frames


class Session:
    """One real conversation: HomeBridge + BridgeEndpoint + ChoiceAuthority."""

    def __init__(self, events, results=(), *, state=None) -> None:
        self.socket = ReleasingSocket(_frames(events, results))
        self.now = [10.0]
        self.bridge = _make_bridge(
            FakeSocketFactory(self.socket), resolver=_resolver(state=state)
        )
        self.connection = RecordingConnection()
        self.endpoint = BridgeEndpoint(
            self.connection,
            self.bridge,
            headers=BRIDGE_HEADERS,
            route=ROUTE,
            choice_clock=lambda: self.now[0],
        )

    def start(self) -> dict:
        opened = self._rpc("conversation.open", conversation_handle=BRIDGE_HANDLE)
        assert opened["result"]["status"] == "ready"
        self._rpc("prompt.submit", conversation_handle=BRIDGE_HANDLE, text="ask")
        _wait_for(lambda: _events(self.connection, "prompt.request"))
        return _events(self.connection, "prompt.request")[0]

    def _rpc(self, method, **params):
        response = self.endpoint.handle_message(
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

    def choose(self, prompt: dict, option: str = "opt-1", operation: str = "choose"):
        choice = prompt["event"]["payload"]["choice"]
        return self._rpc(
            "prompt.respond",
            conversation_handle=BRIDGE_HANDLE,
            turn_id=prompt["turn_id"],
            correlation_id=prompt["correlation_id"],
            event_type="prompt.request",
            response={
                "operation": operation,
                "option_id": option,
                "object_id": choice["object_id"],
                "freshness": choice["freshness"],
            },
        )

    def methods(self) -> list[str]:
        return [frame.get("method") for frame in self.socket.sent]

    def close(self) -> None:
        self.endpoint.close()


@pytest.fixture
def session_factory():
    sessions: list[Session] = []

    def make(*args, **kwargs) -> Session:
        session = Session(*args, **kwargs)
        sessions.append(session)
        return session

    yield make
    for session in sessions:
        session.close()


def _color_events():
    return [
        {"type": "message.start"},
        {"type": "clarify.request", "payload": _captured_clarify("Which color?")},
    ]


def test_choose_is_one_clarify_respond_and_a_duplicate_never_reaches_standard(
    session_factory,
) -> None:
    ok = _captured_result("clarify-wire-shapes", "clarify.respond")
    session = session_factory(_color_events(), [ok])
    prompt = session.start()
    payload = prompt["event"]["payload"]

    # What the client is shown: question, labels without the decoration, choose only.
    assert payload["text"] == "Which color?"
    assert payload["options"] == [
        {"id": "opt-1", "label": "red"},
        {"id": "opt-2", "label": "green"},
        {"id": "opt-3", "label": "blue"},
    ]
    assert payload["choice"]["operations"] == ["choose"]
    assert payload["timeout_s"] == 3600
    shown = json.dumps(prompt)
    assert "prompt.explore" not in shown
    assert '"qid"' not in shown and "questions" not in shown
    assert SESSION not in shown
    assert "Recommended" not in shown

    first = session.choose(prompt, "opt-1")
    second = session.choose(prompt, "opt-1")

    assert first["result"]["status"] == "accepted"
    assert first["result"]["operation"] == "choose"
    assert second["result"] == {
        "schema": 1,
        "conversation_handle": BRIDGE_HANDLE,
        "turn_id": prompt["turn_id"],
        "status": "unavailable",
        "reason": "duplicate",
    }
    sent = [f for f in session.socket.sent if f.get("method") == "clarify.respond"]
    assert len(sent) == 1
    assert sent[0]["params"]["answer"] == "red (Recommended)"
    assert sent[0]["params"]["question_id"] == "q0"
    assert session.methods().count("prompt.submit") == 1


def test_explore_and_a_wrong_identity_never_reach_standard(session_factory) -> None:
    ok = _captured_result("clarify-wire-shapes", "clarify.respond")
    session = session_factory(_color_events(), [ok])
    prompt = session.start()
    choice = prompt["event"]["payload"]["choice"]

    explore = session.choose(prompt, "opt-1", operation="explore")
    stale = session._rpc(
        "prompt.respond",
        conversation_handle=BRIDGE_HANDLE,
        turn_id=prompt["turn_id"],
        correlation_id=prompt["correlation_id"],
        event_type="prompt.request",
        response={
            "operation": "choose",
            "option_id": "opt-1",
            "object_id": choice["object_id"],
            "freshness": "not-the-freshness",
        },
    )
    unknown = session.choose(prompt, "opt-9")

    assert explore["result"]["reason"] == "unsupported"
    assert stale["result"]["reason"] == "stale"
    assert unknown["result"]["reason"] == "unknown"
    assert "clarify.respond" not in session.methods()
    # None of them used the object up: the real answer still works.
    assert session.choose(prompt)["result"]["status"] == "accepted"


def test_a_choice_taken_after_the_300_second_mark_still_reaches_standard(
    session_factory,
) -> None:
    ok = _captured_result("clarify-wire-shapes", "clarify.respond")
    session = session_factory(_color_events(), [ok])
    prompt = session.start()
    session.now[0] += 3000

    result = session.choose(prompt, "opt-2")

    assert result["result"]["status"] == "accepted"
    sent = [f for f in session.socket.sent if f.get("method") == "clarify.respond"]
    assert sent[0]["params"]["answer"] == "green"


def test_a_choice_after_3600_seconds_never_reaches_standard(session_factory) -> None:
    session = session_factory(_color_events())
    prompt = session.start()
    session.now[0] += 3600.1

    result = session.choose(prompt)

    assert result["result"]["reason"] == "expired"
    assert "clarify.respond" not in session.methods()


def test_a_late_choice_standard_calls_expired_is_stale_and_never_retried(
    session_factory,
) -> None:
    late = _captured_result("clarify-expire", "clarify.respond", status="expired")
    session = session_factory(_color_events(), [late])
    prompt = session.start()

    first = session.choose(prompt)
    retry = session.choose(prompt)

    assert first["result"]["status"] == "unavailable"
    assert first["result"]["reason"] == "stale"
    assert retry["result"]["status"] == "unavailable"
    sent = [f for f in session.socket.sent if f.get("method") == "clarify.respond"]
    assert len(sent) == 1, "a rejected answer is never resent"
    assert session.methods().count("prompt.submit") == 1


def test_standards_own_clarify_expire_ends_the_choice(session_factory) -> None:
    expired = _captured_events("clarify-expire", "clarify.request", "clarify.expire")
    session = session_factory([{"type": "message.start"}, *expired])
    prompt = session.start()
    _wait_for(lambda: _events(session.connection, "prompt.expire"))
    expiry = _events(session.connection, "prompt.expire")[0]

    result = session.choose(prompt)

    assert expiry["correlation_id"] == prompt["correlation_id"]
    assert expiry["event"]["payload"] == {}
    assert result["result"]["status"] == "unavailable"
    assert result["result"]["reason"] in {"expired", "stale", "unknown"}
    assert "clarify.respond" not in session.methods()


def test_losing_the_interactive_grant_makes_the_answer_unsupported(
    session_factory,
) -> None:
    state = {"interactive": True}
    session = session_factory(_color_events(), state=state)
    prompt = session.start()

    state["interactive"] = False
    result = session.choose(prompt)

    assert result["result"]["status"] == "unavailable"
    assert result["result"]["reason"] == "unsupported"
    assert "clarify.respond" not in session.methods()


def test_the_turn_ending_revokes_a_pending_projected_choice(session_factory) -> None:
    interrupted = _captured_events(
        "clarify-interrupt-pending", "clarify.request", "message.complete"
    )
    session = session_factory([{"type": "message.start"}, *interrupted])
    prompt = session.start()
    _wait_for(lambda: _events(session.connection, "message.complete"))

    result = session.choose(prompt)

    assert result["result"]["status"] == "unavailable"
    assert "clarify.respond" not in session.methods()
