"""Project a Standard single-select clarify as a typed choice (bridge layer).

Frames come from `tests/fixtures/standard/clarify-*.json`, captured on
2026-10-11 against Standard Hermes 0.21.1 (`2237be3`). The Standard Session ID
in a fixture is replaced by this module's runtime Session.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Self

import pytest

from hermes_home.bridge import (
    BridgeCapabilityUnavailable,
    BridgeProtocolError,
    BridgeRequestRejected,
)
from hermes_home.bridge.standard import (
    CLARIFY_CHOICE_TTL_SECONDS,
    ConversationGrant,
    _capabilities,
    _clarify_projection_offered,
    _project_clarify,
    _strip_recommended,
)
from tests.test_standard_bridge import (
    FakeJsonSocket,
    FakeSocketFactory,
    SequencedSocketFactory,
    _make_bridge,
)

FIXTURES = Path(__file__).parent / "fixtures" / "standard"
HEADERS = {"Authorization": "Device device-secret"}
HANDLE = "opaque-conversation-1"
SESSION = "runtime-hermes-1"
# What pinned Standard sends: no `capabilities` object at all.
PINNED_READY = {"change_events": True, "heartbeat": True, "replay_epoch": "e"}


def _frames(name: str) -> list[dict]:
    return json.loads((FIXTURES / f"{name}.json").read_text())["frames"]


def _captured_events(name: str, *types: str) -> list[dict[str, object]]:
    """Standard events of the wanted types, as `{type, payload}`, in order."""
    events = []
    for entry in _frames(name):
        frame = entry.get("frame") or {}
        params = frame.get("params") or {}
        if entry["dir"] == "in" and params.get("type") in types:
            events.append({"type": params["type"], "payload": params["payload"]})
    return events


def _captured_clarify(question: str, name: str = "clarify-wire-shapes") -> dict:
    for event in _captured_events(name, "clarify.request"):
        questions = event["payload"]["questions"]
        if questions[0]["question"] == question:
            return event["payload"]
    raise AssertionError(f"no captured clarify for {question!r}")


def _captured_result(name: str, method: str, *, status: str | None = None) -> dict:
    """The result frame that answered a captured `method` request."""
    frames = _frames(name)
    for entry in frames:
        frame = entry.get("frame") or {}
        if entry["dir"] == "out" and frame.get("method") == method:
            answer = next(
                other["frame"]
                for other in frames
                if other["dir"] == "in"
                and other["conn"] == entry["conn"]
                and other["frame"].get("id") == frame["id"]
            )
            if status is None or answer["result"].get("status") == status:
                return dict(answer["result"])
    raise AssertionError(f"no captured {method} result with status {status!r}")


def _resolver(*, interactive: bool = True, state: dict | None = None):
    def resolve(handle: str, device_id: str) -> ConversationGrant:
        flag = state["interactive"] if state is not None else interactive
        return ConversationGrant(
            handle=handle,
            device_id=device_id,
            profile_id="family",
            configuration_revision=9,
            interactive_choice=flag,
        )

    return resolve


def _socket(events, results=(), ready=None) -> FakeJsonSocket:
    frames = [
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "gateway.ready",
                "payload": PINNED_READY if ready is None else ready,
            },
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
    return FakeJsonSocket(frames)


class IdleJsonSocket(FakeJsonSocket):
    """Keep a captured Standard connection open after its frames are consumed."""

    def receive_json(self, timeout: float | None = None) -> dict[str, object]:
        if self.closed:
            raise ConnectionError("fixture socket closed")
        if self.incoming:
            return self.incoming.popleft()
        time.sleep(timeout or 0.01)
        raise TimeoutError("fixture idle")


def _bridge(socket, **kwargs):
    return _make_bridge(FakeSocketFactory(socket), resolver=_resolver(**kwargs))


def _opened(bridge):
    status = bridge.open(headers=HEADERS, conversation_handle=HANDLE)
    bridge.submit_prompt("ask me")
    return status


def _choose(event, option: str = "opt-1", operation: str = "choose") -> dict:
    return {
        "operation": operation,
        "option_id": option,
        "object_id": event.payload["choice"]["object_id"],
        "freshness": event.payload["choice"]["freshness"],
    }


def _clarify_events(payload: dict) -> list[dict]:
    return [{"type": "message.start"}, {"type": "clarify.request", "payload": payload}]


def _methods(socket: FakeJsonSocket) -> list[str]:
    return [frame.get("method") for frame in socket.sent]


# -- projection --------------------------------------------------------------


def test_eligible_single_select_clarify_is_projected_as_typed_choice() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(_clarify_events(payload))
    bridge = _bridge(socket)
    status = _opened(bridge)

    assert bridge.next_event().type == "message.start"
    event = bridge.next_event()

    assert status.capabilities["prompt.choose"] is True
    assert "prompt.explore" not in status.capabilities
    assert event.type == "prompt.request"
    assert event.correlation_id == payload["request_id"]
    assert event.choice_capabilities == frozenset({"prompt.choose"})
    assert event.choice_ttl_seconds == CLARIFY_CHOICE_TTL_SECONDS == 3600.0
    assert event.payload["prompt_kind"] == "choice"
    assert event.payload["text"] == "Which color?"
    assert event.payload["choice"]["operations"] == ["choose"]
    assert event.payload["options"] == [
        {"id": "opt-1", "label": "red"},
        {"id": "opt-2", "label": "green"},
        {"id": "opt-3", "label": "blue"},
    ]
    # One representation only: no clarify.request is forwarded for it.
    assert "clarify.request" not in json.dumps(event.payload)
    assert "q0" not in json.dumps(event.payload)
    bridge.close()


def test_choose_sends_one_clarify_respond_with_the_exact_wire_choice_and_qid() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(
        _clarify_events(payload),
        [_captured_result("clarify-wire-shapes", "clarify.respond")],
    )
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    result = bridge.respond_prompt(event, _choose(event, "opt-1"))

    assert result == {"status": "ok", "remaining": []}
    assert socket.sent[-1] == {
        "jsonrpc": "2.0",
        "id": "home-4",
        "method": "clarify.respond",
        "params": {
            "session_id": SESSION,
            "request_id": payload["request_id"],
            # The first choice carries Standard's decoration; Home answers with
            # the exact wire string, not the display label.
            "answer": "red (Recommended)",
            "question_id": "q0",
        },
    }
    assert _methods(socket).count("prompt.submit") == 1
    assert "prompt.choose" not in _methods(socket)
    with pytest.raises(BridgeRequestRejected, match="no longer pending"):
        bridge.respond_prompt(event, _choose(event, "opt-1"))
    bridge.close()


def test_response_uses_standard_request_id_when_event_correlation_differs() -> None:
    payload = _captured_clarify("Which color?")
    correlation_id = "home-choice-correlation"
    socket = _socket(
        [
            {"type": "message.start"},
            {
                "type": "clarify.request",
                "payload": payload,
                "correlation_id": correlation_id,
            },
        ],
        [_captured_result("clarify-wire-shapes", "clarify.respond")],
    )
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    assert event.correlation_id == correlation_id
    assert event.payload["request_id"] == payload["request_id"]
    bridge.respond_prompt(event, _choose(event))

    assert socket.sent[-1]["method"] == "clarify.respond"
    assert socket.sent[-1]["params"]["request_id"] == payload["request_id"]
    bridge.close()


@pytest.mark.parametrize(("option", "answer"), [("opt-2", "green"), ("opt-3", "blue")])
def test_each_option_answers_with_its_own_stored_choice_text(option, answer) -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(_clarify_events(payload), [{"status": "ok", "remaining": []}])
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    bridge.respond_prompt(event, _choose(event, option))

    assert socket.sent[-1]["params"]["answer"] == answer
    bridge.close()


def test_a_lone_choice_is_projected_and_a_two_choice_label_is_stripped() -> None:
    lone = _captured_clarify("Only one?")
    socket = _socket(_clarify_events(lone))
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    assert event.payload["options"] == [{"id": "opt-1", "label": "solo"}]
    bridge.close()


def test_an_unknown_or_out_of_range_option_never_reaches_standard() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(_clarify_events(payload))
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    for option in ("opt-4", "opt-0", "1", "red", "opt-1; drop"):
        with pytest.raises((BridgeRequestRejected, BridgeProtocolError)):
            bridge.respond_prompt(event, _choose(event, option))

    assert "clarify.respond" not in _methods(socket)
    bridge.close()


def test_explore_is_never_offered_or_sent() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(_clarify_events(payload))
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    with pytest.raises(BridgeCapabilityUnavailable):
        bridge.respond_prompt(event, _choose(event, "opt-1", operation="explore"))

    assert "clarify.respond" not in _methods(socket)
    assert "prompt.explore" not in _methods(socket)
    bridge.close()


def test_a_stale_object_identity_never_reaches_standard() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(_clarify_events(payload))
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()
    stale = {**_choose(event), "freshness": "not-the-freshness"}

    with pytest.raises(BridgeRequestRejected):
        bridge.respond_prompt(event, stale)

    assert "clarify.respond" not in _methods(socket)
    bridge.close()


# -- eligibility -------------------------------------------------------------


def _mutated(mutate) -> dict:
    payload = copy.deepcopy(_captured_clarify("Which color?"))
    mutate(payload)
    return payload


INELIGIBLE = {
    "free_text_capture": _captured_clarify("What name?"),
    "multi_select_capture": _captured_clarify("Pick many"),
    "two_question_capture": _captured_clarify("Q one"),
    "empty_choices": _mutated(lambda p: p["questions"][0].update(choices=[])),
    "non_string_choice": _mutated(lambda p: p["questions"][0].update(choices=["a", 1])),
    "too_many_choices": _mutated(
        lambda p: p["questions"][0].update(choices=[f"c{i}" for i in range(33)])
    ),
    "duplicate_labels": _mutated(
        lambda p: p["questions"][0].update(choices=["a (Recommended)", "a"])
    ),
    "empty_after_strip": _mutated(
        lambda p: p["questions"][0].update(choices=["(Recommended)", "b"])
    ),
    "blank_choice": _mutated(lambda p: p["questions"][0].update(choices=["a", " "])),
    "long_label": _mutated(lambda p: p["questions"][0].update(choices=["x" * 257])),
    "oversized_wire_choice": _mutated(
        lambda p: p["questions"][0].update(choices=["A" + " " * 1024])
    ),
    "long_question": _mutated(lambda p: p["questions"][0].update(question="q" * 1025)),
    "blank_question": _mutated(lambda p: p["questions"][0].update(question=" ")),
    "long_request_id": _mutated(lambda p: p.update(request_id="r" * 65)),
    "long_qid": _mutated(lambda p: p["questions"][0].update(qid="q" * 65)),
    "sensitive": _mutated(lambda p: p.update(sensitive=True)),
    "malformed_sensitivity": _mutated(lambda p: p.update(sensitive="true")),
    "question_sensitive": _mutated(lambda p: p["questions"][0].update(sensitive=True)),
}


@pytest.mark.parametrize("name", sorted(INELIGIBLE))
def test_ineligible_clarify_shapes_stay_on_the_unchanged_clarify_path(name) -> None:
    payload = INELIGIBLE[name]
    socket = _socket(_clarify_events(payload))
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()

    event = bridge.next_event()

    assert event.type == "clarify.request"
    assert dict(event.payload) == payload
    assert event.choice_ttl_seconds is None
    assert event.choice_capabilities == frozenset()
    bridge.close()


def test_empty_clarify_batch_keeps_legacy_protocol_rejection() -> None:
    payload = _mutated(lambda value: value.update(questions=[]))
    socket = _socket(_clarify_events(payload))
    bridge = _bridge(socket)
    _opened(bridge)
    assert bridge.next_event().type == "message.start"

    with pytest.raises(
        BridgeProtocolError,
        match="batch clarify questions must be a non-empty list",
    ):
        bridge.next_event()

    bridge.close()


def test_a_choice_cap_overflow_falls_back_to_the_clarify_path() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(_clarify_events(payload))
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    with bridge._state_lock:
        bridge._active_turn.choice_revisions.update(
            (f"object-{index}", "fresh") for index in range(128)
        )

    event = bridge.next_event()

    assert event.type == "clarify.request"
    bridge.close()


def test_dropped_projected_clarifies_do_not_consume_turn_record_budget() -> None:
    legacy = _captured_clarify("Which color?")
    legacy["request_id"] = "legacy-pending"
    legacy["questions"][0]["multi_select"] = True
    eligible = _captured_clarify("Which color?")
    events = [
        {"type": "message.start"},
        {"type": "clarify.request", "payload": legacy},
    ]
    for index in range(160):
        payload = copy.deepcopy(eligible)
        payload["request_id"] = f"discarded-{index}"
        events.append({"type": "clarify.request", "payload": payload})
    events.append({"type": "status.update", "payload": {"text": "still pending"}})

    bridge = _bridge(_socket(events))
    _opened(bridge)
    bridge.next_event()
    assert bridge.next_event().type == "clarify.request"

    assert bridge.next_event().type == "status.update"
    with bridge._state_lock:
        assert bridge._active_turn.projected_clarifies == {}
        assert bridge._active_turn.choice_revisions == set()
    bridge.close()


def test_without_an_interactive_grant_nothing_is_advertised_or_projected() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(_clarify_events(payload))
    bridge = _bridge(socket, interactive=False)
    status = _opened(bridge)
    bridge.next_event()

    event = bridge.next_event()

    assert "prompt.choose" not in status.capabilities
    assert "prompt.explore" not in status.capabilities
    assert event.type == "clarify.request"
    assert dict(event.payload) == payload
    bridge.close()


def test_a_standard_that_advertises_typed_choice_keeps_the_native_path() -> None:
    payload = _captured_clarify("Which color?")
    for ready in (
        {"capabilities": {"prompt.choose": True, "prompt.explore": True}},
        {"capabilities": {"prompt.choose": False}},
    ):
        socket = _socket(_clarify_events(payload), ready=ready)
        bridge = _bridge(socket)
        status = _opened(bridge)
        bridge.next_event()

        event = bridge.next_event()

        assert event.type == "clarify.request"
        assert event.choice_ttl_seconds is None
        assert (
            status.capabilities["prompt.choose"]
            is ready["capabilities"]["prompt.choose"]
        )
        bridge.close()


def test_a_native_typed_choice_never_carries_the_clarify_ttl() -> None:
    native = {
        "request_id": "choice-request-1",
        "prompt_kind": "choice",
        "text": "Choose.",
        "choice": {
            "object_id": "source-object",
            "freshness": "source-freshness",
            "operations": ["choose"],
            "ttl": 3600,
            "timeout_s": 3600,
        },
        "options": [{"id": "inspect", "label": "Inspect"}],
        "ttl": 3600,
        "timeout_s": 3600,
        "choice_ttl_seconds": 3600,
    }
    socket = _socket(
        [{"type": "message.start"}, {"type": "prompt.request", "payload": native}],
        ready={"capabilities": {"prompt.choose": True}},
    )
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()

    event = bridge.next_event()

    assert event.type == "prompt.request"
    assert event.choice_ttl_seconds is None
    bridge.close()


def test_synthetic_choose_capability_does_not_authorize_native_choices() -> None:
    native = {
        "request_id": "choice-request-1",
        "prompt_kind": "choice",
        "text": "Choose.",
        "choice": {
            "object_id": "source-object",
            "freshness": "source-freshness",
            "operations": ["choose"],
        },
        "options": [{"id": "inspect", "label": "Inspect"}],
    }
    socket = _socket(
        [{"type": "message.start"}, {"type": "prompt.request", "payload": native}]
    )
    bridge = _bridge(socket)
    status = _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    assert status.capabilities["prompt.choose"] is True
    assert event.choice_capabilities == frozenset()
    with pytest.raises(BridgeCapabilityUnavailable):
        bridge.respond_prompt(event, _choose(event, "inspect"))
    assert "prompt.choose" not in _methods(socket)
    assert "clarify.respond" not in _methods(socket)
    bridge.close()


# -- expiry, replacement, resume, interrupt ----------------------------------


def test_upstream_clarify_expire_becomes_a_typed_choice_expiry() -> None:
    expired = _captured_events("clarify-expire", "clarify.request", "clarify.expire")
    socket = _socket([{"type": "message.start"}, *expired])
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    expiry = bridge.next_event()

    assert expiry.type == "prompt.expire"
    assert expiry.payload == {}
    assert expiry.correlation_id == event.correlation_id
    with pytest.raises(BridgeRequestRejected, match="no longer pending"):
        bridge.respond_prompt(event, _choose(event))
    assert "clarify.respond" not in _methods(socket)
    bridge.close()


def test_expiry_uses_projected_home_correlation_not_raw_expiry_correlation() -> None:
    payload = _captured_clarify("Which color?")
    correlation_id = "home-choice-correlation"
    socket = _socket(
        [
            {"type": "message.start"},
            {
                "type": "clarify.request",
                "payload": payload,
                "correlation_id": correlation_id,
            },
            {
                "type": "clarify.expire",
                "payload": {"request_id": payload["request_id"]},
                "correlation_id": "standard-expiry-correlation",
            },
        ]
    )
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()
    expiry = bridge.next_event()

    assert event.correlation_id == correlation_id
    assert expiry.type == "prompt.expire"
    assert expiry.correlation_id == correlation_id
    with pytest.raises(BridgeRequestRejected, match="no longer pending"):
        bridge.respond_prompt(event, _choose(event))
    assert "clarify.respond" not in _methods(socket)
    bridge.close()


def test_a_clarify_expire_for_another_request_leaves_the_choice_pending() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(
        [
            *_clarify_events(payload),
            {"type": "clarify.expire", "payload": {"request_id": "someone-else"}},
            {"type": "status.update", "payload": {"text": "still thinking"}},
        ],
        [{"status": "ok", "remaining": []}],
    )
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    # The unrelated expiry is dropped; the next event is the status update.
    assert bridge.next_event().type == "status.update"

    assert bridge.respond_prompt(event, _choose(event)) == {
        "status": "ok",
        "remaining": [],
    }
    bridge.close()


def test_a_re_emitted_request_id_is_never_offered_again() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(
        [
            *_clarify_events(payload),
            {"type": "clarify.request", "payload": payload},
            {
                "type": "message.complete",
                "payload": {"text": "ok", "status": "complete"},
            },
        ]
    )
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    assert bridge.next_event().type == "prompt.request"

    assert bridge.next_event().type == "message.complete"
    bridge.close()


def test_new_projected_clarify_revision_is_serialized_before_old_response() -> None:
    from threading import Condition, Event, Thread, current_thread

    first_payload = _captured_clarify("Which color?")
    revised_payload = copy.deepcopy(first_payload)
    revised_payload["request_id"] = "clarify-request-revised"
    socket = _socket(
        [
            {"type": "message.start"},
            {"type": "clarify.request", "payload": first_payload},
            {"type": "clarify.request", "payload": revised_payload},
        ]
    )
    bridge = _bridge(socket)

    class TicketLock:
        def __init__(self) -> None:
            self._condition = Condition()
            self._next_ticket = 0
            self._serving = 0
            self._locked = False
            self.revision_waiting = Event()
            self.response_waiting = Event()

        def acquire(self) -> bool:
            with self._condition:
                ticket = self._next_ticket
                self._next_ticket += 1
                if current_thread().name == "clarify-revision":
                    self.revision_waiting.set()
                elif current_thread().name == "clarify-response":
                    self.response_waiting.set()
                while ticket != self._serving or self._locked:
                    self._condition.wait()
                self._locked = True
                return True

        def release(self) -> None:
            with self._condition:
                self._locked = False
                self._serving += 1
                self._condition.notify_all()

        def __enter__(self) -> Self:
            self.acquire()
            return self

        def __exit__(self, *_args: object) -> None:
            self.release()

    try:
        _opened(bridge)
        bridge.next_event()
        first_event = bridge.next_event()
        response_lock = TicketLock()
        bridge._prompt_response_lock = response_lock
        response_lock.acquire()
        revision_events = []
        worker_errors = []

        def read_revision() -> None:
            try:
                revision_events.append(bridge.next_event())
            except Exception as error:  # noqa: BLE001 - report worker failures
                worker_errors.append(error)

        def respond_to_old_revision() -> None:
            try:
                bridge.respond_prompt(first_event, _choose(first_event))
            except Exception as error:  # noqa: BLE001 - report worker failures
                worker_errors.append(error)

        revision_thread = Thread(target=read_revision, name="clarify-revision")
        response_thread = Thread(
            target=respond_to_old_revision, name="clarify-response"
        )
        revision_thread.start()
        try:
            assert response_lock.revision_waiting.wait(timeout=5)
            response_thread.start()
            assert response_lock.response_waiting.wait(timeout=5)
        finally:
            response_lock.release()
            revision_thread.join(timeout=5)
            if response_thread.ident is not None:
                response_thread.join(timeout=5)

        assert not revision_thread.is_alive()
        assert not response_thread.is_alive()
        assert len(worker_errors) == 1
        assert isinstance(worker_errors[0], BridgeRequestRejected)
        assert len(revision_events) == 1
        assert revision_events[0].type == "prompt.request"
        assert revision_events[0].correlation_id == revised_payload["request_id"]
        assert "clarify.respond" not in _methods(socket)
        assert "prompt.choose" not in _methods(socket)
    finally:
        bridge.close()


def test_a_turn_terminal_event_revokes_a_pending_projected_choice() -> None:
    interrupted = _captured_events(
        "clarify-interrupt-pending", "clarify.request", "message.complete"
    )
    socket = _socket([{"type": "message.start"}, *interrupted])
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()
    assert event.type == "prompt.request"

    # Interrupting emits no clarify.expire; only the terminal message arrives.
    assert bridge.next_event().type == "message.complete"

    with pytest.raises(BridgeRequestRejected, match="no longer pending"):
        bridge.respond_prompt(event, _choose(event))
    assert "clarify.respond" not in _methods(socket)
    bridge.close()


def test_a_late_respond_after_standard_resolved_it_is_rejected_as_stale() -> None:
    payload = _captured_clarify("Which color?")
    late = _captured_result("clarify-expire", "clarify.respond", status="expired")
    assert late == {"status": "expired"}
    socket = _socket(_clarify_events(payload), [late])
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    with pytest.raises(BridgeRequestRejected):
        bridge.respond_prompt(event, _choose(event))

    # Standard answered `expired`: one write, never repeated by the bridge.
    assert _methods(socket).count("clarify.respond") == 1
    bridge.close()


def test_remaining_questions_after_a_single_answer_are_a_protocol_error() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(_clarify_events(payload), [{"status": "ok", "remaining": ["q1"]}])
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    with pytest.raises(BridgeProtocolError):
        bridge.respond_prompt(event, _choose(event))

    assert _methods(socket).count("clarify.respond") == 1
    bridge.close()


@pytest.mark.parametrize(
    "result", [{"status": "ok"}, {"status": "ok", "remaining": []}, {"accepted": True}]
)
def test_accepted_upstream_results_are_accepted(result) -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(_clarify_events(payload), [result])
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    assert bridge.respond_prompt(event, _choose(event)) == result
    bridge.close()


@pytest.mark.parametrize(
    "result", [{"status": "rejected"}, {"accepted": False}, {"status": "error"}]
)
def test_rejected_upstream_results_are_known_rejections(result) -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(_clarify_events(payload), [result])
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    with pytest.raises(BridgeRequestRejected):
        bridge.respond_prompt(event, _choose(event))
    bridge.close()


def test_losing_the_interactive_grant_after_projection_refuses_the_answer() -> None:
    payload = _captured_clarify("Which color?")
    state = {"interactive": True}
    socket = _socket(_clarify_events(payload))
    bridge = _bridge(socket, state=state)
    _opened(bridge)
    bridge.next_event()
    event = bridge.next_event()

    state["interactive"] = False

    with pytest.raises(BridgeCapabilityUnavailable):
        bridge.respond_prompt(event, _choose(event))
    assert "clarify.respond" not in _methods(socket)
    bridge.close()


def test_a_clarify_arriving_after_the_grant_was_lost_takes_the_clarify_path() -> None:
    payload = _captured_clarify("Which color?")
    state = {"interactive": True}
    socket = _socket(_clarify_events(payload))
    bridge = _bridge(socket, state=state)
    _opened(bridge)
    bridge.next_event()
    state["interactive"] = False
    # Production revalidates the claim before every event read (a conversation
    # closer is wired); the bare test bridge does it on demand.
    bridge._revalidate_ready_binding()

    event = bridge.next_event()

    assert event.type == "clarify.request"
    assert dict(event.payload) == payload
    bridge.close()


def test_the_projection_record_dies_with_the_turn() -> None:
    payload = _captured_clarify("Which color?")
    socket = _socket(
        [
            *_clarify_events(payload),
            {
                "type": "message.complete",
                "payload": {"text": "ok", "status": "complete"},
            },
        ]
    )
    bridge = _bridge(socket)
    _opened(bridge)
    bridge.next_event()
    bridge.next_event()
    with bridge._state_lock:
        assert list(bridge._active_turn.projected_clarifies) == [payload["request_id"]]

    bridge.next_event()

    with bridge._state_lock:
        assert bridge._active_turn is None
    bridge.close()


def test_a_reconnected_conversation_projects_clarify_again() -> None:
    payload = _captured_clarify("Which color?")

    def ready():
        return {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "gateway.ready", "payload": PINNED_READY},
        }

    first = FakeJsonSocket(
        [
            ready(),
            {
                "jsonrpc": "2.0",
                "id": "home-1",
                "result": {
                    "session_id": SESSION,
                    "stored_session_id": "stored-hermes-1",
                },
            },
        ]
    )
    resumed = FakeJsonSocket(
        [
            ready(),
            {"jsonrpc": "2.0", "id": "home-1", "result": {"session_id": "runtime-2"}},
            {"jsonrpc": "2.0", "id": "home-2", "result": {"status": "streaming"}},
            *[
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {"session_id": "runtime-2", **event},
                }
                for event in _clarify_events(payload)
            ],
        ]
    )
    bridge = _make_bridge(SequencedSocketFactory(first, resumed), resolver=_resolver())
    bridge.open(headers=HEADERS, conversation_handle=HANDLE)

    status = bridge.reconnect(headers=HEADERS)

    assert status.status == "ready"
    assert status.capabilities["prompt.choose"] is True
    assert "prompt.explore" not in status.capabilities
    bridge.submit_prompt("ask me")
    assert bridge.next_event().type == "message.start"
    event = bridge.next_event()
    assert event.type == "prompt.request"
    assert event.choice_ttl_seconds == CLARIFY_CHOICE_TTL_SECONDS
    bridge.close()


def test_resume_with_pending_clarify_does_not_reoffer_or_answer_it() -> None:
    fixture = "clarify-resume-pending"
    payload = _captured_clarify("Which color?", name=fixture)
    create_result = _captured_result(fixture, "session.create")
    resume_result = _captured_result(fixture, "session.resume")
    # The capture redacts `info`; drop the sentinel instead of inventing data.
    create_result.pop("info", None)
    resume_result.pop("info", None)
    assert resume_result["running"] is True
    assert resume_result["pending_clarify"] == payload
    assert not any(
        entry["dir"] == "in"
        and entry["conn"] == "resume_b"
        and (entry.get("frame") or {}).get("params", {}).get("type")
        == "clarify.request"
        for entry in _frames(fixture)
    )

    def ready() -> dict:
        return {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "gateway.ready", "payload": PINNED_READY},
        }

    session_id = create_result["session_id"]
    initial = IdleJsonSocket(
        [
            ready(),
            {"jsonrpc": "2.0", "id": "home-1", "result": create_result},
            {
                "jsonrpc": "2.0",
                "id": "home-2",
                "result": {"status": "streaming"},
            },
            *[
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {"session_id": session_id, **event},
                }
                for event in _clarify_events(payload)
            ],
        ]
    )
    resumed = IdleJsonSocket(
        [
            ready(),
            {"jsonrpc": "2.0", "id": "home-1", "result": resume_result},
        ]
    )
    bridge = _make_bridge(
        SequencedSocketFactory(initial, resumed), resolver=_resolver()
    )
    try:
        status = bridge.open(headers=HEADERS, conversation_handle=HANDLE)
        assert status.status == "ready"
        bridge.submit_prompt("ask me")
        assert bridge.next_event().type == "message.start"
        event = bridge.next_event()
        assert event.type == "prompt.request"

        status = bridge.reconnect(headers=HEADERS)

        assert status.status == "ready"
        with pytest.raises(BridgeRequestRejected, match="no longer pending"):
            bridge.respond_prompt(event, _choose(event))
        sent_methods = [frame.get("method") for frame in resumed.sent]
        assert sent_methods.count("session.resume") == 1
        assert "clarify.respond" not in sent_methods
        assert "prompt.submit" not in sent_methods
    finally:
        bridge.close()


# -- units -------------------------------------------------------------------


def test_clarify_projection_is_offered_only_to_interactive_grants_on_silent_standard() -> (
    None
):
    assert _clarify_projection_offered(PINNED_READY, interactive_choice=True) is True
    assert _clarify_projection_offered({}, interactive_choice=True) is True
    assert _clarify_projection_offered(PINNED_READY, interactive_choice=False) is False
    for capabilities in ({"prompt.choose": True}, {"prompt.choose": False}):
        ready = {"capabilities": capabilities}
        assert _clarify_projection_offered(ready, interactive_choice=True) is False
    flat = {"prompt.choose": True}
    assert _clarify_projection_offered(flat, interactive_choice=True) is False


def test_capabilities_offer_choose_only_for_the_projection() -> None:
    projected = _capabilities(
        PINNED_READY, interactive_choice=True, clarify_choice=True
    )
    assert projected["prompt.choose"] is True
    assert "prompt.explore" not in projected
    assert "prompt.choose" not in _capabilities(PINNED_READY, interactive_choice=True)
    assert "prompt.choose" not in _capabilities(
        PINNED_READY, interactive_choice=False, clarify_choice=True
    )
    native = _capabilities(
        {"capabilities": {"prompt.choose": False, "prompt.explore": True}},
        interactive_choice=True,
        clarify_choice=True,
    )
    assert native["prompt.choose"] is False and native["prompt.explore"] is True


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("red (Recommended)", "red"),
        ("red (recommended)", "red"),
        ("  red   (RECOMMENDED)  ", "red"),
        ("red", "red"),
        ("(Recommended) red", "(Recommended) red"),
        ("red (Recommended) extra", "red (Recommended) extra"),
        ("(Recommended)", ""),
    ],
)
def test_label_strip_matches_standards_rule(text: str, label: str) -> None:
    assert _strip_recommended(text) == label


def test_project_clarify_never_raises_on_odd_input() -> None:
    for payload in (
        {},
        {"request_id": "r", "questions": "no"},
        {"request_id": "r", "questions": [None]},
        {"request_id": "r", "questions": [{"qid": "q0"}]},
        {"request_id": ["r"], "questions": []},
    ):
        assert _project_clarify(payload) is None
