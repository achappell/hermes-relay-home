"""Contract tests for the Home WebSocket endpoint adapter."""

from __future__ import annotations

import json
import threading
import time
from collections import deque

import pytest

from hermes_home.bridge import (
    AudioFrame,
    BridgeEndpoint,
    BridgeEvent,
    BridgeStatus,
    BridgeTimeoutError,
    BridgeTransportError,
    BridgeTurn,
)
from hermes_home.observability.diagnostics import (
    DiagnosticsRecorder,
    InMemoryDiagnosticsStore,
)

HANDLE = "opaque-home-handle"
ROUTE = {"class": "home", "id": "approved-route-label"}
HEADERS = {"Authorization": "Device endpoint-secret"}


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False
        self._lock = threading.Lock()

    def send(self, message: str | bytes, **_kwargs: object) -> None:
        with self._lock:
            self.sent.append(message)

    def close(self, **_kwargs: object) -> None:
        self.closed = True


class FakeBridge:
    def __init__(self) -> None:
        self.open_calls: list[tuple[dict[str, str], str]] = []
        self.reconnect_calls: list[dict[str, str]] = []
        self.reauthorize_calls: list[dict[str, str]] = []
        self.prompt_calls: list[str] = []
        self.respond_calls: list[tuple[BridgeEvent, dict[str, object]]] = []
        self.interrupt_calls = 0
        self.command_calls: list[tuple[str, str | None]] = []
        self.ping_calls = 0
        self.close_calls = 0
        self.events: deque[BridgeEvent | BaseException] = deque()
        self.audio: deque[AudioFrame | BaseException] = deque()
        self.event_ready = threading.Event()
        self.audio_ready = threading.Event()
        self.prompt_result = BridgeTurn("home-turn-1", HANDLE)
        self.status = BridgeStatus(
            "ready",
            HANDLE,
            capabilities={
                "commands": ["status"],
                "heartbeat": True,
                "timing": "absent",
                "profile_id": "must-not-leak",
            },
        )

    def open(
        self,
        *,
        headers: dict[str, str],
        conversation_handle: str,
    ) -> BridgeStatus:
        self.open_calls.append((dict(headers), conversation_handle))
        return self.status

    def reconnect(self, *, headers: dict[str, str]) -> BridgeStatus:
        self.reconnect_calls.append(dict(headers))
        return self.status

    def reauthorize(self, *, headers: dict[str, str]) -> BridgeStatus:
        self.reauthorize_calls.append(dict(headers))
        return BridgeStatus(
            "ready",
            HANDLE,
            capabilities=self.status.capabilities,
            unresolved_turn=BridgeTurn("home-turn-1", HANDLE, "streaming"),
        )

    def submit_prompt(self, text: str) -> BridgeTurn:
        self.prompt_calls.append(text)
        return self.prompt_result

    def next_event(self) -> BridgeEvent:
        while not self.events:
            self.event_ready.wait(0.01)
            if self.close_calls:
                raise BridgeTransportError("closed")
        event = self.events.popleft()
        if isinstance(event, BaseException):
            raise event
        return event

    def next_audio(self, *, timeout: float | None = None) -> AudioFrame:
        del timeout
        while not self.audio:
            self.audio_ready.wait(0.01)
            if self.close_calls:
                raise BridgeTransportError("closed")
        frame = self.audio.popleft()
        if isinstance(frame, BaseException):
            raise frame
        return frame

    def respond_prompt(
        self, event: BridgeEvent, response: dict[str, object]
    ) -> dict[str, object]:
        self.respond_calls.append((event, dict(response)))
        return {"accepted": True}

    def interrupt(self) -> bool:
        self.interrupt_calls += 1
        return True

    def dispatch_command(self, name: str, arg: str | None = None) -> dict[str, object]:
        self.command_calls.append((name, arg))
        return {"accepted": True, "name": name, "arg": arg}

    def ping(self) -> dict[str, object]:
        self.ping_calls += 1
        return {"ok": True, "session_id": "hidden"}

    def close(self) -> None:
        self.close_calls += 1
        self.event_ready.set()
        self.audio_ready.set()


class FailingPromptBridge(FakeBridge):
    def submit_prompt(self, text: str) -> BridgeTurn:
        self.prompt_calls.append(text)
        raise BridgeTimeoutError("timed out")


def _message(connection: FakeConnection, index: int = -1) -> dict[str, object]:
    raw = connection.sent[index]
    assert isinstance(raw, str)
    return json.loads(raw)


def _send(endpoint: BridgeEndpoint, **payload: object) -> dict[str, object]:
    response = endpoint.handle_message(json.dumps(payload))
    assert isinstance(response, dict)
    return response


def _open(endpoint: BridgeEndpoint) -> dict[str, object]:
    return _send(
        endpoint,
        jsonrpc="2.0",
        schema=1,
        id="open-1",
        method="conversation.open",
        params={"conversation_handle": HANDLE},
    )


def _wait_for(
    predicate, *, timeout: float = 1.0, message: str = "condition not met"
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), message


def test_open_wraps_safe_ready_status_with_the_injected_home_route() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        response = _open(endpoint)
        assert response == {
            "jsonrpc": "2.0",
            "schema": 1,
            "id": "open-1",
            "result": {
                "schema": 1,
                "status": "ready",
                "conversation_handle": HANDLE,
                "route": ROUTE,
                "capabilities": {
                    "commands": ["status"],
                    "heartbeat": True,
                    "timing": "absent",
                },
                "unresolved_turn": False,
            },
        }
        assert bridge.open_calls == [(HEADERS, HANDLE)]
        assert "endpoint-secret" not in json.dumps(response)
        assert "must-not-leak" not in json.dumps(response)
    finally:
        endpoint.close()


def test_endpoint_dispatches_prompt_controls_and_ping_without_exposing_identity() -> (
    None
):
    connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        prompt = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "hello"},
        )
        correlation_id = prompt["result"].get("correlation_id")
        assert isinstance(correlation_id, str) and correlation_id.startswith("corr-")
        assert prompt["result"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
            "status": "submitted",
            "correlation_id": correlation_id,
        }
        assert _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="interrupt-1",
            method="session.interrupt",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
            },
        )["result"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
            "status": "accepted",
        }
        assert _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="command-1",
            method="command.dispatch",
            params={
                "conversation_handle": HANDLE,
                "name": "status",
                "arg": "brief",
            },
        )["result"] == {
            "schema": 1,
            "accepted": True,
            "name": "status",
            "arg": "brief",
            "conversation_handle": HANDLE,
        }
        assert _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="ping-1",
            method="bridge.ping",
            params={"conversation_handle": HANDLE},
        )["result"] == {
            "schema": 1,
            "ok": True,
            "conversation_handle": HANDLE,
        }
        assert bridge.prompt_calls == ["hello"]
        assert bridge.interrupt_calls == 1
        assert bridge.command_calls == [("status", "brief")]
        assert bridge.ping_calls == 1
        assert "hidden" not in json.dumps(connection.sent)
    finally:
        endpoint.close()


def test_endpoint_forwards_only_allowlisted_standard_events() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    def forwarded_types() -> list[str]:
        return [
            json.loads(item)["params"]["event"]["type"]
            for item in list(connection.sent)
            if isinstance(item, str) and json.loads(item).get("method") == "event"
        ]

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "hello"},
        )
        bridge.events.extend(
            [
                BridgeEvent(
                    HANDLE,
                    "session.info",
                    {"system_prompt": "private", "tools": {"web": ["search"]}},
                ),
                BridgeEvent(HANDLE, "sessions.changed", {}),
                BridgeEvent(HANDLE, "reasoning.delta", {"text": "Considering"}),
                BridgeEvent(
                    HANDLE, "tool.start", {"name": "search"}, turn_id="home-turn-1"
                ),
                BridgeEvent(
                    HANDLE, "message.delta", {"text": "OK"}, turn_id="home-turn-1"
                ),
                BridgeEvent(
                    HANDLE,
                    "message.complete",
                    {"text": "OK", "status": "complete"},
                    turn_id="home-turn-1",
                ),
            ]
        )
        bridge.event_ready.set()
        _wait_for(lambda: "message.complete" in forwarded_types())
        assert forwarded_types() == [
            "reasoning.delta",
            "message.delta",
            "message.complete",
        ]
        assert "private" not in repr(connection.sent)
    finally:
        endpoint.close()


def test_structured_prompt_event_is_retained_and_response_is_not_submitted_as_text() -> (
    None
):
    connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "run it"},
        )
        # Queue after submit: the event loop drains events as soon as it is ready.
        bridge.events.append(
            BridgeEvent(
                HANDLE,
                "approval.request",
                {
                    "prompt": "Allow it?",
                    "session_id": "hidden",
                    "profile_id": "hidden",
                },
                turn_id="home-turn-1",
                correlation_id="approval-1",
            )
        )
        bridge.event_ready.set()
        _wait_for(
            lambda: any(
                isinstance(item, str) and json.loads(item).get("method") == "event"
                for item in connection.sent
            )
        )
        event = next(
            json.loads(item)
            for item in connection.sent
            if isinstance(item, str) and json.loads(item).get("method") == "event"
        )
        assert event["params"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
            "correlation_id": "approval-1",
            "event": {"type": "approval.request", "payload": {"prompt": "Allow it?"}},
        }
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "approval-1",
                "event_type": "approval.request",
                "response": {"choice": "allow", "all": True},
            },
        )
        assert response["result"] == {
            "schema": 1,
            "accepted": True,
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
        }
        assert bridge.prompt_calls == ["run it"]
        assert bridge.respond_calls[0][1] == {"choice": "allow", "all": True}
    finally:
        endpoint.close()


def test_transport_timeout_is_uncertain_and_is_never_retried() -> None:
    connection = FakeConnection()
    bridge = FailingPromptBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "once"},
        )
        assert response["error"]["data"] == {
            "schema": 1,
            "code": "transport_timeout",
            "delivery": "uncertain",
        }
        assert bridge.prompt_calls == ["once"]
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            {"jsonrpc": "2.0", "schema": 1, "id": "bad", "method": "bridge.ping"},
            "invalid_request",
        ),
        (
            {
                "jsonrpc": "2.0",
                "schema": 2,
                "id": "bad-schema",
                "method": "bridge.ping",
                "params": {"conversation_handle": HANDLE},
            },
            "invalid_request",
        ),
        (
            {
                "jsonrpc": "2.0",
                "schema": 1,
                "id": "bad-method",
                "method": "not-home.method",
                "params": {},
            },
            "invalid_request",
        ),
    ],
)
def test_malformed_home_requests_are_rejected_before_bridge_calls(
    payload: dict[str, object], code: str
) -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        response = endpoint.handle_message(json.dumps(payload))
        assert response is not None
        assert response["error"]["data"]["code"] == code
        assert bridge.open_calls == []
        assert bridge.prompt_calls == []
    finally:
        endpoint.close()


def test_missing_bridge_factory_result_is_a_safe_unavailable_readiness_response() -> (
    None
):
    connection = FakeConnection()
    endpoint = BridgeEndpoint(connection, None, headers=HEADERS, route=ROUTE)

    try:
        response = _open(endpoint)
        assert response["result"] == {
            "schema": 1,
            "status": "unavailable",
            "conversation_handle": HANDLE,
            "reason": "hermes_unavailable",
        }
    finally:
        endpoint.close()


def test_reconnect_keeps_unresolved_turn_separate_from_readiness() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.status = BridgeStatus(
        "ready",
        HANDLE,
        capabilities={"commands": [], "heartbeat": True, "timing": "absent"},
        unresolved_turn=BridgeTurn("home-turn-uncertain", HANDLE, "uncertain"),
    )
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="reconnect-1",
            method="conversation.reconnect",
            params={"conversation_handle": HANDLE},
        )
        assert response["result"]["status"] == "ready"
        assert set(response["result"]) == {
            "schema",
            "status",
            "conversation_handle",
            "unresolved_turn",
            "route",
            "capabilities",
        }
        assert response["result"]["route"] == ROUTE
        assert response["result"]["capabilities"] == {
            "commands": [],
            "heartbeat": True,
            "timing": "absent",
        }
        assert response["result"]["unresolved_turn"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-uncertain",
            "status": "uncertain",
        }
        assert bridge.reconnect_calls == [HEADERS]
        assert bridge.prompt_calls == []
    finally:
        endpoint.close()


def test_audio_notifications_preserve_start_metadata_and_binary_pcm() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.audio.extend(
        [
            AudioFrame(
                "start",
                turn_id="home-turn-1",
                sample_rate=24000,
                channels=1,
                sample_width=2,
                byte_order="little",
            ),
            AudioFrame("pcm", turn_id="home-turn-1", data=b"\x01\x00\x02\x00"),
            AudioFrame("end", turn_id="home-turn-1"),
        ]
    )
    bridge.audio_ready.set()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "speak"},
        )
        _wait_for(lambda: any(item == b"\x01\x00\x02\x00" for item in connection.sent))
        frames = [
            json.loads(item)
            for item in connection.sent
            if isinstance(item, str) and json.loads(item).get("method") == "audio.frame"
        ]
        assert [frame["params"]["frame"]["kind"] for frame in frames] == [
            "start",
            "end",
        ]
        assert frames[0]["params"]["frame"] == {
            "kind": "start",
            "sample_rate": 24000,
            "channels": 1,
            "sample_width": 2,
            "byte_order": "little",
        }
    finally:
        endpoint.close()


def test_bridge_records_audio_facts_without_retaining_pcm() -> None:
    class FixedRecorder(DiagnosticsRecorder):
        def new_correlation_id(self) -> str:
            return "corr-" + "a" * 32

    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.audio.extend(
        [
            AudioFrame(
                "start",
                turn_id="home-turn-1",
                sample_rate=24000,
                channels=1,
                sample_width=2,
                byte_order="little",
            ),
            AudioFrame("pcm", turn_id="home-turn-1", data=b"\x01\x00\x02\x00"),
            AudioFrame("end", turn_id="home-turn-1"),
        ]
    )
    bridge.audio_ready.set()
    recorder = FixedRecorder(store=InMemoryDiagnosticsStore(), clock=lambda: 100.0)
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        diagnostics=recorder,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "speak"},
        )
        _wait_for(lambda: endpoint._audio_thread is None)

        timeline = recorder.timeline("corr-" + "a" * 32)

        assert [(event.phase, event.outcome) for event in timeline] == [
            ("turn", "started"),
            ("turn", "accepted"),
            ("audio", "started"),
            ("audio", "accepted"),
            ("audio", "completed"),
        ]
        assert timeline[3].byte_count == 4
        serialized = json.dumps([event.to_dict() for event in timeline])
        assert "private prompt" not in serialized
        assert "\\x01" not in serialized
        assert "\\x02" not in serialized
    finally:
        endpoint.close()


def test_bridge_records_audio_unavailability_without_retrying_the_turn() -> None:
    class FixedRecorder(DiagnosticsRecorder):
        def new_correlation_id(self) -> str:
            return "corr-" + "b" * 32

    class TimeoutAudioBridge(FakeBridge):
        def next_audio(self, *, timeout: float | None = None) -> AudioFrame:
            del timeout
            raise BridgeTimeoutError("audio timed out")

    connection = FakeConnection()
    bridge = TimeoutAudioBridge()
    recorder = FixedRecorder(store=InMemoryDiagnosticsStore(), clock=lambda: 100.0)
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        diagnostics=recorder,
    )

    try:
        _open(endpoint)
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "once"},
        )
        assert response["result"]["status"] == "submitted"
        _wait_for(lambda: endpoint._audio_thread is None)

        timeline = recorder.timeline("corr-" + "b" * 32)

        assert timeline[-1].phase == "audio"
        assert timeline[-1].outcome == "unavailable"
        assert timeline[-1].failure_code == "transport_timeout"
        assert bridge.prompt_calls == ["once"]
    finally:
        endpoint.close()


def test_schema_and_deep_json_validation_fail_closed() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        boolean_schema = _send(
            endpoint,
            jsonrpc="2.0",
            schema=True,
            id="bad-schema-type",
            method="bridge.ping",
            params={"conversation_handle": HANDLE},
        )
        assert boolean_schema["error"]["data"]["code"] == "invalid_request"
        nested = "[" * 2000 + "0" + "]" * 2000
        deep = endpoint.handle_message(
            '{"jsonrpc":"2.0","schema":1,"id":"deep",'
            f'"method":"bridge.ping","params":{{"conversation_handle":"{HANDLE}",'
            f'"deep":{nested}}}}}'
        )
        assert deep is not None
        assert deep["error"]["data"]["code"] == "invalid_request"
        assert bridge.ping_calls == 0
    finally:
        endpoint.close()


def test_bound_conversation_rejects_a_different_handle() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="wrong-handle",
            method="prompt.submit",
            params={"conversation_handle": "another-handle", "text": "no"},
        )
        assert response["error"]["data"]["code"] == "conversation_mismatch"
        assert bridge.prompt_calls == []
    finally:
        endpoint.close()


class SequentialBridge(FakeBridge):
    def submit_prompt(self, text: str) -> BridgeTurn:
        self.prompt_calls.append(text)
        return BridgeTurn(f"home-turn-{len(self.prompt_calls)}", HANDLE)


def test_terminal_event_releases_the_connection_for_a_new_prompt() -> None:
    connection = FakeConnection()
    bridge = SequentialBridge()
    bridge.audio.extend(
        [
            AudioFrame("unavailable", turn_id="home-turn-1"),
            AudioFrame("unavailable", turn_id="home-turn-2"),
        ]
    )
    bridge.audio_ready.set()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "first"},
        )
        endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "message.complete",
                {"status": "completed"},
                turn_id="home-turn-1",
            )
        )
        _wait_for(lambda: endpoint._active_turn_id is None)
        second = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-2",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "second"},
        )
        assert second["result"]["turn_id"] == "home-turn-2"
        assert bridge.prompt_calls == ["first", "second"]
    finally:
        endpoint.close()


def test_reusing_a_retired_turn_id_fails_closed_after_bridge_acceptance() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.audio.append(AudioFrame("unavailable", turn_id="home-turn-1"))
    bridge.audio_ready.set()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "first"},
        )
        _wait_for(lambda: endpoint._audio_thread is None)
        endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "message.complete",
                {"status": "completed"},
                turn_id="home-turn-1",
            )
        )
        _wait_for(lambda: endpoint._active_turn_id is None)

        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-2",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "duplicate"},
        )
        assert response["error"]["data"] == {
            "schema": 1,
            "code": "protocol_error",
            "delivery": "uncertain",
        }
        assert bridge.prompt_calls == ["first", "duplicate"]
        assert endpoint.ready is False
    finally:
        endpoint.close()


def test_reconnect_clears_the_previous_endpoint_turn_without_resubmitting_it() -> None:
    connection = FakeConnection()
    bridge = SequentialBridge()
    bridge.status = BridgeStatus(
        "ready",
        HANDLE,
        capabilities={"commands": [], "heartbeat": True, "timing": "absent"},
        unresolved_turn=BridgeTurn("home-turn-1", HANDLE, "uncertain"),
    )
    bridge.audio.extend(
        [
            AudioFrame("unavailable", turn_id="home-turn-1"),
            AudioFrame("unavailable", turn_id="home-turn-2"),
        ]
    )
    bridge.audio_ready.set()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "uncertain"},
        )
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="reconnect-1",
            method="conversation.reconnect",
            params={"conversation_handle": HANDLE},
        )
        second = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-2",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "fresh"},
        )
        assert second["result"]["turn_id"] == "home-turn-2"
        assert bridge.prompt_calls == ["uncertain", "fresh"]
    finally:
        endpoint.close()


def test_parked_endpoint_adopts_matching_reconnect_and_streams_buffered_events() -> (
    None
):
    first_connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(first_connection, bridge, headers=HEADERS, route=ROUTE)

    _open(endpoint)
    _send(
        endpoint,
        jsonrpc="2.0",
        schema=1,
        id="prompt-1",
        method="prompt.submit",
        params={"conversation_handle": HANDLE, "text": "keep going"},
    )
    endpoint.detach()
    buffered = endpoint._event_payload(
        BridgeEvent(
            HANDLE,
            "message.delta",
            {"text": "still running"},
            turn_id="home-turn-1",
        )
    )
    endpoint._send_json(
        {"jsonrpc": "2.0", "schema": 1, "method": "event", "params": buffered}
    )

    second_connection = FakeConnection()
    endpoint.adopt(second_connection, headers=HEADERS)
    response = _send(
        endpoint,
        jsonrpc="2.0",
        schema=1,
        id="reconnect-1",
        method="conversation.reconnect",
        params={"conversation_handle": HANDLE},
    )

    assert set(response["result"]) == {
        "schema",
        "status",
        "conversation_handle",
        "route",
        "capabilities",
        "unresolved_turn",
    }
    assert response["result"]["route"] == ROUTE
    assert isinstance(response["result"]["capabilities"], dict)
    assert response["result"]["unresolved_turn"] == {
        "schema": 1,
        "conversation_handle": HANDLE,
        "turn_id": "home-turn-1",
        "status": "streaming",
    }
    assert bridge.reauthorize_calls == [HEADERS]
    assert bridge.reconnect_calls == []
    assert bridge.prompt_calls == ["keep going"]
    sent = [
        json.loads(item) for item in second_connection.sent if isinstance(item, str)
    ]
    assert [item.get("id", item.get("method")) for item in sent] == [
        "reconnect-1",
        "event",
    ]
    assert sent[1]["params"]["event"]["payload"] == {"text": "still running"}
    endpoint.close()


def test_event_send_failure_detaches_socket_without_closing_the_live_bridge() -> None:
    class FailingSendConnection(FakeConnection):
        def send(self, message: str | bytes, **_kwargs: object) -> None:
            raise ConnectionError("peer vanished")

    first_connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(first_connection, bridge, headers=HEADERS, route=ROUTE)
    _open(endpoint)
    _send(
        endpoint,
        jsonrpc="2.0",
        schema=1,
        id="prompt-1",
        method="prompt.submit",
        params={"conversation_handle": HANDLE, "text": "keep going"},
    )
    endpoint.detach()
    endpoint.adopt(FailingSendConnection(), headers=HEADERS)

    endpoint._send_json(
        {
            "jsonrpc": "2.0",
            "schema": 1,
            "method": "event",
            "params": endpoint._event_payload(
                BridgeEvent(
                    HANDLE,
                    "message.delta",
                    {"text": "survived"},
                    turn_id="home-turn-1",
                )
            ),
        }
    )

    assert endpoint.has_recoverable_state is True
    assert bridge.close_calls == 0
    assert len(endpoint._parked_events) == 1
    endpoint.close()


def test_audio_can_end_before_the_terminal_text_event() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.audio.extend(
        [
            AudioFrame(
                "start",
                turn_id="home-turn-1",
                sample_rate=24_000,
                channels=1,
                sample_width=2,
                byte_order="little",
            ),
            AudioFrame("end", turn_id="home-turn-1"),
        ]
    )
    bridge.audio_ready.set()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "speak then finish text"},
        )
        _wait_for(lambda: endpoint._audio_thread is None)
        assert endpoint._active_turn_id == "home-turn-1"

        payload = endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "message.delta",
                {"text": "text after audio"},
                turn_id="home-turn-1",
            )
        )
        assert payload["event"]["payload"] == {"text": "text after audio"}
        endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "message.complete",
                {"status": "completed"},
                turn_id="home-turn-1",
            )
        )
        assert endpoint._active_turn_id is None
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    "event_type",
    [
        "turn_complete",
        "turn.complete",
        "turn.completed",
        "turn.end",
        "turn.ended",
        "turn_end",
        "response.complete",
        "response.completed",
        "turn_interrupted",
        "turn.interrupted",
        "turn.cancelled",
        "turn.error",
        "error",
    ],
)
def test_endpoint_releases_turn_for_every_supported_terminal_event(
    event_type: str,
) -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.audio.append(AudioFrame("unavailable", turn_id="home-turn-1"))
    bridge.audio_ready.set()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "finish this turn"},
        )
        _wait_for(lambda: endpoint._audio_thread is None)
        payload = (
            {"status": "completed"}
            if event_type
            in {
                "turn_complete",
                "turn.complete",
                "turn.completed",
                "turn.end",
                "turn.ended",
                "turn_end",
                "response.complete",
                "response.completed",
            }
            else {}
        )
        endpoint._event_payload(
            BridgeEvent(HANDLE, event_type, payload, turn_id="home-turn-1")
        )
        assert endpoint._active_turn_id is None
    finally:
        endpoint.close()


def test_bridge_records_one_safe_correlation_timeline_without_endpoint_content() -> (
    None
):
    class FixedRecorder(DiagnosticsRecorder):
        def new_correlation_id(self) -> str:
            return "corr-" + "c" * 32

    connection = FakeConnection()
    bridge = FakeBridge()
    recorder = FixedRecorder(
        store=InMemoryDiagnosticsStore(),
        clock=lambda: 100.0,
    )
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        diagnostics=recorder,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "private prompt"},
        )

        timeline = recorder.timeline("corr-" + "c" * 32)

        assert [event.outcome for event in timeline] == ["started", "accepted"]
        assert {event.source for event in timeline} == {"endpoint", "home"}
        serialized = json.dumps([event.to_dict() for event in timeline])
        assert "private prompt" not in serialized
        assert "endpoint-secret" not in serialized
        assert "profile_id" not in serialized
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    ("status", "expected_outcome"),
    [("completed", "completed"), ("interrupted", "interrupted")],
)
def test_bridge_records_terminal_hermes_outcome_on_the_same_correlation(
    status: str,
    expected_outcome: str,
) -> None:
    class FixedRecorder(DiagnosticsRecorder):
        def new_correlation_id(self) -> str:
            return "corr-" + "d" * 32

    connection = FakeConnection()
    bridge = FakeBridge()
    recorder = FixedRecorder(store=InMemoryDiagnosticsStore(), clock=lambda: 100.0)
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        diagnostics=recorder,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "private prompt"},
        )
        endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "message.complete",
                {"status": status, "profile_id": "hidden"},
                turn_id="home-turn-1",
            )
        )

        timeline = recorder.timeline("corr-" + "d" * 32)

        assert [event.outcome for event in timeline] == [
            "started",
            "accepted",
            expected_outcome,
        ]
        assert timeline[-1].source == "hermes"
        assert timeline[-1].turn_fingerprint is not None
        assert "home-turn-1" not in json.dumps([event.to_dict() for event in timeline])
    finally:
        endpoint.close()


def test_bridge_records_a_timed_out_prompt_without_retrying_it() -> None:
    class FixedRecorder(DiagnosticsRecorder):
        def new_correlation_id(self) -> str:
            return "corr-" + "e" * 32

    connection = FakeConnection()
    bridge = FailingPromptBridge()
    recorder = FixedRecorder(store=InMemoryDiagnosticsStore(), clock=lambda: 100.0)
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        diagnostics=recorder,
    )

    try:
        _open(endpoint)
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "once"},
        )

        assert response["error"]["data"]["code"] == "transport_timeout"
        timeline = recorder.timeline("corr-" + "e" * 32)
        assert [event.outcome for event in timeline] == ["started", "failed"]
        assert timeline[-1].failure_code == "transport_timeout"
        assert bridge.prompt_calls == ["once"]
    finally:
        endpoint.close()


def test_diagnostics_rejection_cannot_change_bridge_delivery() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    recorder = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(),
        clock=lambda: 100.0,
    )
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route={"class": "home", "id": "unsafe route label"},
        diagnostics=recorder,
    )

    try:
        _open(endpoint)
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "once"},
        )

        assert response["result"]["status"] == "submitted"
        assert bridge.prompt_calls == ["once"]
    finally:
        endpoint.close()


def test_bridge_records_unavailable_turns_without_submitting_them() -> None:
    class FixedRecorder(DiagnosticsRecorder):
        def new_correlation_id(self) -> str:
            return "corr-" + "f" * 32

    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.status = BridgeStatus("unavailable", HANDLE, "hermes_unavailable")
    recorder = FixedRecorder(store=InMemoryDiagnosticsStore(), clock=lambda: 100.0)
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        diagnostics=recorder,
    )

    try:
        _open(endpoint)
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "once"},
        )

        assert response["error"]["data"]["code"] == "hermes_unavailable"
        timeline = recorder.timeline("corr-" + "f" * 32)
        assert [
            (event.phase, event.outcome, event.failure_code) for event in timeline
        ] == [("turn", "unavailable", "hermes_unavailable")]
        assert bridge.prompt_calls == []
    finally:
        endpoint.close()


def test_nested_server_only_fields_are_removed_from_results() -> None:
    class NestedBridge(FakeBridge):
        def ping(self) -> dict[str, object]:
            return {
                "ok": True,
                "nested": {
                    "profileId": "hidden-profile",
                    "runtimeSessionId": "hidden-session",
                    "safe": "kept",
                },
                "items": [{"device_credential": "hidden-device", "ok": True}],
            }

    connection = FakeConnection()
    endpoint = BridgeEndpoint(connection, NestedBridge(), headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="ping-1",
            method="bridge.ping",
            params={"conversation_handle": HANDLE},
        )
        serialized = json.dumps(response)
        assert "hidden-profile" not in serialized
        assert "hidden-session" not in serialized
        assert "hidden-device" not in serialized
        assert response["result"]["nested"] == {"safe": "kept"}
    finally:
        endpoint.close()


def test_builtin_timeout_on_a_control_is_uncertain() -> None:
    class TimeoutBridge(FakeBridge):
        def interrupt(self) -> bool:
            self.interrupt_calls += 1
            raise TimeoutError("control timed out")

    connection = FakeConnection()
    endpoint = BridgeEndpoint(connection, TimeoutBridge(), headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "interrupt"},
        )
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="interrupt-1",
            method="session.interrupt",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
            },
        )
        assert response["error"]["data"] == {
            "schema": 1,
            "code": "transport_timeout",
            "delivery": "uncertain",
        }
        assert endpoint.ready is False
    finally:
        endpoint.close()


def test_audio_timeout_is_reported_without_killing_text_readiness() -> None:
    class TimeoutAudioBridge(FakeBridge):
        def next_audio(self, *, timeout: float | None = None) -> AudioFrame:
            del timeout
            raise TimeoutError("audio timed out")

    connection = FakeConnection()
    endpoint = BridgeEndpoint(
        connection, TimeoutAudioBridge(), headers=HEADERS, route=ROUTE
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "audio"},
        )
        _wait_for(
            lambda: any(
                isinstance(item, str)
                and json.loads(item).get("method") == "audio.frame"
                for item in connection.sent
            )
        )
        audio = next(
            json.loads(item)
            for item in connection.sent
            if isinstance(item, str) and json.loads(item).get("method") == "audio.frame"
        )
        assert audio["params"]["frame"] == {
            "kind": "unavailable",
            "reason": "transport_timeout",
        }
        assert endpoint.ready is True
    finally:
        endpoint.close()


def test_unavailable_readiness_normalizes_internal_timeout() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.status = BridgeStatus("unavailable", HANDLE, "hermes_timeout")
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        response = _open(endpoint)
        assert response["result"] == {
            "schema": 1,
            "status": "unavailable",
            "conversation_handle": HANDLE,
            "reason": "transport_timeout",
        }
    finally:
        endpoint.close()


def test_unverified_timing_capability_is_reported_as_absent() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.status = BridgeStatus(
        "ready",
        HANDLE,
        capabilities={"commands": [], "heartbeat": True, "timing": "arrival"},
    )
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        response = _open(endpoint)
        assert response["result"]["capabilities"]["timing"] == "absent"
    finally:
        endpoint.close()


def test_malformed_audio_frame_is_reported_and_worker_cleans_up() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.audio.append(AudioFrame([]))  # type: ignore[arg-type]
    bridge.audio_ready.set()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "bad audio"},
        )
        _wait_for(
            lambda: any(
                isinstance(item, str)
                and json.loads(item).get("method") == "audio.frame"
                for item in connection.sent
            )
        )
        audio = next(
            json.loads(item)
            for item in connection.sent
            if isinstance(item, str) and json.loads(item).get("method") == "audio.frame"
        )
        assert audio["params"]["frame"] == {
            "kind": "unavailable",
            "reason": "protocol_error",
        }
        _wait_for(lambda: endpoint._audio_thread is None)
    finally:
        endpoint.close()


def test_interrupted_turn_ends_its_audio_instead_of_reporting_a_failure() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.audio.append(
        AudioFrame(
            "start",
            turn_id="home-turn-1",
            sample_rate=24_000,
            channels=1,
            sample_width=2,
            byte_order="little",
        )
    )
    bridge.audio_ready.set()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    def audio_kinds() -> list[str]:
        return [
            json.loads(item)["params"]["frame"]["kind"]
            for item in list(connection.sent)
            if isinstance(item, str) and json.loads(item).get("method") == "audio.frame"
        ]

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "count"},
        )
        _wait_for(lambda: audio_kinds() == ["start"])
        interrupted = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="interrupt-1",
            method="session.interrupt",
            params={"conversation_handle": HANDLE, "turn_id": "home-turn-1"},
        )
        assert interrupted["result"]["status"] == "accepted"
        # The sidecar stops once Standard cuts the interrupted turn's speech.
        bridge.audio.append(RuntimeError("audio stopped"))
        _wait_for(lambda: len(audio_kinds()) == 2)
        assert audio_kinds() == ["start", "end"]
    finally:
        endpoint.close()
