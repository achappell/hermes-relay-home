"""Contract tests for the Home WebSocket endpoint adapter."""

from __future__ import annotations

import json
import threading
import time
from collections import deque

import pytest

from hermes_home.bridge import (
    AudioFrame,
    BridgeAuthorizationError,
    BridgeCapabilityUnavailable,
    BridgeEndpoint,
    BridgeEvent,
    BridgeRequestRejected,
    BridgeStatus,
    BridgeTimeoutError,
    BridgeTransportError,
    BridgeTurn,
)
from hermes_home.bridge.choice_authority import (
    MAX_CHOICE_CORRELATIONS,
    MAX_CHOICE_OBJECTS_PER_TURN,
)
from hermes_home.bridge.endpoint import _RequestError
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
        self.activity_calls: list[str] = []
        self.close_conversation_calls = 0
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

    def report_activity(self, state: str) -> bool:
        self.activity_calls.append(state)
        return True

    def close_conversation(self) -> bool:
        self.close_conversation_calls += 1
        return True

    def close(self) -> None:
        self.close_calls += 1
        self.event_ready.set()
        self.audio_ready.set()


class FailingPromptBridge(FakeBridge):
    def submit_prompt(self, text: str) -> BridgeTurn:
        self.prompt_calls.append(text)
        raise BridgeTimeoutError("timed out")


class ChoiceResponseFailureBridge(FakeBridge):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.response_error = error

    def respond_prompt(
        self, event: BridgeEvent, response: dict[str, object]
    ) -> dict[str, object]:
        self.respond_calls.append((event, dict(response)))
        raise self.response_error


class ResolvedTrueBridge(FakeBridge):
    def respond_prompt(
        self, event: BridgeEvent, response: dict[str, object]
    ) -> dict[str, object]:
        self.respond_calls.append((event, dict(response)))
        return {"resolved": True}


class RejectedProtectedBridge(FakeBridge):
    def respond_prompt(
        self, event: BridgeEvent, response: dict[str, object]
    ) -> dict[str, object]:
        self.respond_calls.append((event, dict(response)))
        return {"status": "rejected", "detail": "secret-value-must-not-leak"}


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


def _choice_event(
    correlation_id: str,
    *,
    object_id: str = "source-choice-1",
    freshness: str = "source-freshness-1",
    operations: tuple[str, ...] = ("choose", "explore"),
    options: list[dict[str, object]] | None = None,
    text: str = "Pick a safe option.",
) -> BridgeEvent:
    return BridgeEvent(
        HANDLE,
        "prompt.request",
        {
            "prompt_kind": "choice",
            "text": text,
            "choice": {
                "object_id": object_id,
                "freshness": freshness,
                "operations": list(operations),
                "private_note": "must not leak",
            },
            "options": options
            if options is not None
            else [
                {"id": "inspect", "label": "Inspect", "value": "raw-private"},
                {"id": "skip", "label": "Skip", "value": "raw-private-2"},
            ],
            "session_id": "hidden-standard-session",
            "profile_id": "hidden-profile",
            "credential": "hidden-credential",
            "unknown_private_field": "must not leak either",
        },
        turn_id="home-turn-1",
        correlation_id=correlation_id,
        standard_session_id="hidden-standard-session",
        configuration_revision=7,
        choice_capabilities=frozenset({"prompt.choose", "prompt.explore"}),
    )


def _deliver_choice_event(
    endpoint: BridgeEndpoint, event: BridgeEvent
) -> dict[str, object]:
    params = endpoint._event_payload(event)
    assert endpoint._send_json(
        {"jsonrpc": "2.0", "schema": 1, "method": "event", "params": params}
    )
    return params


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


def test_open_forwards_authorized_typed_choice_capabilities() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.status = BridgeStatus(
        "ready",
        HANDLE,
        capabilities={
            "commands": [],
            "prompt.choose": True,
            "prompt.explore": False,
        },
    )
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        result = _open(endpoint)["result"]

        assert result["capabilities"]["prompt.choose"] is True
        assert result["capabilities"]["prompt.explore"] is False
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


def test_conversation_activity_and_close_are_bound_and_content_free() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        for state in ("capture", "turn", "playback", "playback_complete"):
            result = _send(
                endpoint,
                jsonrpc="2.0",
                schema=1,
                id=f"activity-{state}",
                method="conversation.activity",
                params={"conversation_handle": HANDLE, "state": state},
            )
            assert result["result"] == {
                "schema": 1,
                "conversation_handle": HANDLE,
                "status": "accepted",
            }

        closed = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="close-1",
            method="conversation.close",
            params={"conversation_handle": HANDLE},
        )

        assert closed["result"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "status": "closed",
        }
        assert bridge.activity_calls == [
            "capture",
            "turn",
            "playback",
            "playback_complete",
        ]
        assert bridge.close_conversation_calls == 1
        assert "profile" not in json.dumps(connection.sent).casefold()
        assert "session_id" not in json.dumps(connection.sent).casefold()
    finally:
        endpoint.close()


def test_conversation_close_is_allowed_when_standard_is_unavailable() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    bridge.status = BridgeStatus("unavailable", HANDLE, "hermes_unavailable")
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        opened = _open(endpoint)
        assert opened["result"]["status"] == "unavailable"

        closed = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="close-unavailable-1",
            method="conversation.close",
            params={"conversation_handle": HANDLE},
        )

        assert closed["result"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "status": "closed",
        }
        assert bridge.close_conversation_calls == 1
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
                    "value": "must-not-leak",
                    "password": "must-not-leak",
                    "unknown_private_field": "must-not-leak",
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
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
            "status": "accepted",
            "operation": "approval.respond",
        }
        assert bridge.prompt_calls == ["run it"]
        assert bridge.respond_calls[0][1] == {"choice": "allow", "all": True}
    finally:
        endpoint.close()


def test_endpoint_rejects_an_oversized_sensitive_entry_before_bridge_delivery() -> None:
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
            params={"conversation_handle": HANDLE, "text": "enter it"},
        )
        endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "secret.request",
                {"request_id": "secret-1", "value": "must-not-leak"},
                turn_id="home-turn-1",
                correlation_id="secret-1",
            )
        )

        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "secret-1",
                "event_type": "secret.request",
                "response": {"value": "🙂" * 1025},
            },
        )

        assert response["error"]["code"] == -32602
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    ("event_type", "response_key", "operation"),
    [
        ("secret.request", "value", "secret.respond"),
        ("sudo.request", "password", "sudo.respond"),
    ],
)
def test_endpoint_projects_and_resolves_each_protected_prompt_type(
    event_type: str,
    response_key: str,
    operation: str,
) -> None:
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
            params={"conversation_handle": HANDLE, "text": "enter it"},
        )
        projected = endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                event_type,
                {
                    "request_id": "protected-1",
                    "question": "Enter it",
                    "sensitivity": "high",
                    "value": "raw-secret",
                    "password": "raw-password",
                },
                turn_id="home-turn-1",
                correlation_id="protected-1",
            )
        )
        assert "raw-secret" not in json.dumps(projected)
        assert "raw-password" not in json.dumps(projected)

        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "protected-1",
                "event_type": event_type,
                "response": {response_key: "current-secret"},
            },
        )

        assert response["result"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
            "status": "accepted",
            "operation": operation,
        }
        assert bridge.respond_calls[-1][1] == {response_key: "current-secret"}
    finally:
        endpoint.close()


def test_endpoint_redacts_protected_expiry_payloads() -> None:
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
            params={"conversation_handle": HANDLE, "text": "enter it"},
        )
        projected = endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "secret.expire",
                {
                    "request_id": "protected-1",
                    "value": "expired-secret",
                    "password": "expired-password",
                },
                turn_id="home-turn-1",
                correlation_id="protected-1",
            )
        )

        assert "expired-secret" not in json.dumps(projected)
        assert "expired-password" not in json.dumps(projected)
    finally:
        endpoint.close()


def test_endpoint_does_not_claim_a_rejected_protected_result_was_accepted() -> None:
    connection = FakeConnection()
    bridge = RejectedProtectedBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "enter it"},
        )
        endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "secret.request",
                {"request_id": "protected-1"},
                turn_id="home-turn-1",
                correlation_id="protected-1",
            )
        )

        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "protected-1",
                "event_type": "secret.request",
                "response": {"value": "current-secret"},
            },
        )

        assert response["error"]["data"]["code"] == "request_rejected"
        assert "secret-value-must-not-leak" not in json.dumps(response)
    finally:
        endpoint.close()


def test_endpoint_replaces_an_older_protected_prompt_before_response() -> None:
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
            params={"conversation_handle": HANDLE, "text": "enter it"},
        )
        endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "secret.request",
                {"request_id": "protected-old"},
                turn_id="home-turn-1",
                correlation_id="protected-old",
            )
        )
        endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "secret.request",
                {"request_id": "protected-new"},
                turn_id="home-turn-1",
                correlation_id="protected-new",
            )
        )

        replaced = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="respond-old",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "protected-old",
                "event_type": "secret.request",
                "response": {"value": "old-secret"},
            },
        )

        assert replaced["error"]["data"]["code"] == "request_rejected"
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


def test_typed_choice_projection_hides_source_values_and_consumes_once() -> None:
    class FixedRecorder(DiagnosticsRecorder):
        def new_correlation_id(self) -> str:
            return "corr-" + "a" * 32

    connection = FakeConnection()
    bridge = FakeBridge()
    tokens = iter(["home-choice-1", "home-freshness-1"])
    recorder = FixedRecorder(store=InMemoryDiagnosticsStore(), clock=lambda: 100.0)
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        diagnostics=recorder,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        source_event = _choice_event("choice-correlation-1")
        projected = _deliver_choice_event(endpoint, source_event)["event"]["payload"]

        assert projected == {
            "prompt_id": "choice-correlation-1",
            "prompt_kind": "choice",
            "text": "Pick a safe option.",
            "timeout_s": 300,
            "options": [
                {"id": "inspect", "label": "Inspect"},
                {"id": "skip", "label": "Skip"},
            ],
            "choice": {
                "object_id": "home-choice-1",
                "freshness": "home-freshness-1",
                "operations": ["choose", "explore"],
            },
        }
        public_payload = json.dumps(projected)
        for private_value in (
            "source-choice-1",
            "source-freshness-1",
            "raw-private",
            "hidden-standard-session",
            "hidden-profile",
            "hidden-credential",
            "must not leak",
        ):
            assert private_value not in public_payload

        response = {
            "operation": "choose",
            "option_id": "inspect",
            "object_id": "home-choice-1",
            "freshness": "home-freshness-1",
        }
        accepted = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": response,
            },
        )
        duplicate = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-2",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": response,
            },
        )

        assert accepted["result"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
            "status": "accepted",
            "operation": "choose",
        }
        assert duplicate["result"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
            "status": "unavailable",
            "reason": "duplicate",
        }
        assert bridge.respond_calls == [
            (
                source_event,
                {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": "source-choice-1",
                    "freshness": "source-freshness-1",
                },
            )
        ]
        assert bridge.prompt_calls == ["choose"]
        timeline = recorder.timeline("corr-" + "a" * 32)
        serialized_timeline = json.dumps([event.to_dict() for event in timeline])
        assert [(event.phase, event.outcome) for event in timeline[-2:]] == [
            ("request", "accepted"),
            ("request", "rejected"),
        ]
        for private_value in (
            "Pick a safe option.",
            "Inspect",
            "source-choice-1",
            "home-choice-1",
        ):
            assert private_value not in serialized_timeline
    finally:
        endpoint.close()


def test_resolved_true_is_a_successful_endpoint_choice_result() -> None:
    connection = FakeConnection()
    bridge = ResolvedTrueBridge()
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        _deliver_choice_event(endpoint, _choice_event("choice-correlation-1"))

        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": "home-choice-1",
                    "freshness": "home-freshness-1",
                },
            },
        )

        assert result["result"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
            "status": "accepted",
            "operation": "choose",
        }
        assert len(bridge.respond_calls) == 1
    finally:
        endpoint.close()


def test_explore_keeps_object_and_does_not_extend_monotonic_expiry() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    now = [100.0]
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_clock=lambda: now[0],
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        first = _deliver_choice_event(endpoint, _choice_event("choice-correlation-1"))
        first_choice = first["event"]["payload"]["choice"]
        now[0] = 175.0
        explored = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-explore-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": {
                    "operation": "explore",
                    "option_id": "inspect",
                    "object_id": first_choice["object_id"],
                    "freshness": first_choice["freshness"],
                },
            },
        )
        second = _deliver_choice_event(
            endpoint, _choice_event("choice-correlation-2", text="More detail.")
        )
        second_payload = second["event"]["payload"]

        assert explored["result"]["status"] == "accepted"
        assert second_payload["choice"] == first_choice
        assert second_payload["timeout_s"] == 225
        chosen = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-choose-2",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-2",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "skip",
                    "object_id": first_choice["object_id"],
                    "freshness": first_choice["freshness"],
                },
            },
        )

        assert chosen["result"]["status"] == "accepted"
        assert [response[1]["operation"] for response in bridge.respond_calls] == [
            "explore",
            "choose",
        ]
    finally:
        endpoint.close()


def test_upstream_choice_expiry_returns_expired_without_a_bridge_action() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        event = _choice_event("choice-correlation-1")
        _deliver_choice_event(endpoint, event)
        expiry = BridgeEvent(
            HANDLE,
            "prompt.expire",
            {"credential": "must not leak"},
            turn_id="home-turn-1",
            correlation_id="choice-correlation-1",
        )
        endpoint._event_payload(expiry)

        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": "home-choice-1",
                    "freshness": "home-freshness-1",
                },
            },
        )

        assert result["result"]["status"] == "unavailable"
        assert result["result"]["reason"] == "expired"
        assert bridge.respond_calls == []
        assert "must not leak" not in json.dumps(endpoint._event_payload(expiry))
    finally:
        endpoint.close()


def test_typed_choice_expires_on_direct_response_after_silent_timeout() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    now = [10.0]
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_clock=lambda: now[0],
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        event = _choice_event("choice-correlation-1")
        choice = _deliver_choice_event(endpoint, event)["event"]["payload"]["choice"]
        now[0] += 300.1

        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": choice["object_id"],
                    "freshness": choice["freshness"],
                },
            },
        )

        assert result["result"]["status"] == "unavailable"
        assert result["result"]["reason"] == "expired"
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


def test_parked_typed_choice_gets_full_window_when_first_delivered() -> None:
    first_connection = FakeConnection()
    bridge = FakeBridge()
    now = [10.0]
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        first_connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_clock=lambda: now[0],
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        endpoint.detach()
        params = endpoint._event_payload(_choice_event("choice-correlation-1"))
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

        delivered = _message(second_connection, 1)
        choice_payload = delivered["params"]["event"]["payload"]
        assert reconnect["result"]["status"] == "ready"
        assert choice_payload["timeout_s"] == 300
        now[0] = 1_299.0
        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": choice_payload["choice"]["object_id"],
                    "freshness": choice_payload["choice"]["freshness"],
                },
            },
        )

        assert result["result"]["status"] == "accepted"
        assert len(bridge.respond_calls) == 1
    finally:
        endpoint.close()


def test_typed_choice_timeout_stays_positive_during_final_fractional_second() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    now = [10.0]
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_clock=lambda: now[0],
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        _deliver_choice_event(endpoint, _choice_event("choice-correlation-1"))
        now[0] = 309.1

        reprompt = _deliver_choice_event(
            endpoint, _choice_event("choice-correlation-2")
        )
        payload = reprompt["event"]["payload"]

        assert payload["timeout_s"] == 1
        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-final-second",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-2",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": payload["choice"]["object_id"],
                    "freshness": payload["choice"]["freshness"],
                },
            },
        )

        assert result["result"]["status"] == "accepted"
    finally:
        endpoint.close()


def test_same_freshness_action_change_revokes_existing_endpoint_authority() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        first = _deliver_choice_event(endpoint, _choice_event("choice-correlation-1"))[
            "event"
        ]["payload"]
        changed = _choice_event(
            "choice-correlation-2",
            options=[{"id": "different", "label": "Different"}],
        )

        with pytest.raises(_RequestError) as error:
            endpoint._event_payload(changed)

        assert error.value.code == "protocol_error"
        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-revoked",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": first["choice"]["object_id"],
                    "freshness": first["choice"]["freshness"],
                },
            },
        )

        assert result["result"]["reason"] == "replaced"
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [("object_id", "foreign-choice"), ("freshness", "foreign-freshness")],
)
def test_typed_choice_mismatched_public_identity_is_stale(
    field: str, wrong_value: str
) -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        _deliver_choice_event(endpoint, _choice_event("choice-correlation-1"))
        response = {
            "operation": "choose",
            "option_id": "inspect",
            "object_id": "home-choice-1",
            "freshness": "home-freshness-1",
        }
        response[field] = wrong_value

        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": response,
            },
        )

        assert result["result"]["status"] == "unavailable"
        assert result["result"]["reason"] == "stale"
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


def test_choice_revision_limit_preserves_the_last_usable_choice() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    tokens = iter(
        token
        for revision in range(MAX_CHOICE_OBJECTS_PER_TURN)
        for token in (f"home-choice-{revision}", f"home-freshness-{revision}")
    )
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        latest_correlation = ""
        latest_choice: dict[str, object] = {}
        for revision in range(MAX_CHOICE_OBJECTS_PER_TURN):
            latest_correlation = f"choice-correlation-{revision}"
            payload = _deliver_choice_event(
                endpoint,
                _choice_event(
                    latest_correlation,
                    freshness=f"source-freshness-{revision}",
                ),
            )["event"]["payload"]
            latest_choice = payload["choice"]

        with pytest.raises(_RequestError) as error:
            endpoint._event_payload(
                _choice_event(
                    "choice-correlation-overflow",
                    freshness="source-freshness-overflow",
                )
            )
        assert error.value.code == "choice_revision_limit"

        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-last-valid",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": latest_correlation,
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": latest_choice["object_id"],
                    "freshness": latest_choice["freshness"],
                },
            },
        )

        assert result["result"]["status"] == "accepted"
        assert len(bridge.respond_calls) == 1
    finally:
        endpoint.close()


def test_event_pump_drops_revision_overflow_and_keeps_last_choice_usable() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    tokens = iter(
        token
        for revision in range(MAX_CHOICE_OBJECTS_PER_TURN)
        for token in (f"home-choice-{revision}", f"home-freshness-{revision}")
    )
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        bridge.events.extend(
            [
                *(
                    _choice_event(
                        f"choice-correlation-{revision}",
                        freshness=f"source-freshness-{revision}",
                    )
                    for revision in range(MAX_CHOICE_OBJECTS_PER_TURN)
                ),
                _choice_event(
                    "choice-correlation-overflow",
                    freshness="source-freshness-overflow",
                ),
                BridgeEvent(
                    HANDLE,
                    "message.delta",
                    {"text": "event pump continued"},
                    turn_id="home-turn-1",
                ),
            ]
        )
        bridge.event_ready.set()
        _wait_for(
            lambda: any(
                isinstance(raw, str) and "event pump continued" in raw
                for raw in connection.sent
            )
        )

        sent_events = [
            json.loads(raw)
            for raw in connection.sent
            if isinstance(raw, str) and json.loads(raw).get("method") == "event"
        ]
        choices = [
            item["params"]["event"]["payload"]
            for item in sent_events
            if item["params"]["event"]["type"] == "prompt.request"
        ]
        latest = choices[-1]
        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-last-valid",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": (
                    f"choice-correlation-{MAX_CHOICE_OBJECTS_PER_TURN - 1}"
                ),
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": latest["choice"]["object_id"],
                    "freshness": latest["choice"]["freshness"],
                },
            },
        )

        assert endpoint.ready is True
        assert connection.closed is False
        assert len(choices) == MAX_CHOICE_OBJECTS_PER_TURN
        assert result["result"]["status"] == "accepted"
        assert len(bridge.respond_calls) == 1
    finally:
        endpoint.close()


def test_event_pump_delivers_valid_choice_and_closes_on_malformed_choice() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )
    valid = _choice_event("choice-correlation-valid")
    malformed_payload = {**valid.payload, "sensitive": True}
    malformed = BridgeEvent(
        valid.conversation_handle,
        valid.type,
        malformed_payload,
        turn_id=valid.turn_id,
        correlation_id="choice-correlation-malformed",
        standard_session_id=valid.standard_session_id,
        configuration_revision=valid.configuration_revision,
        choice_capabilities=valid.choice_capabilities,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        bridge.events.extend([valid, malformed])
        bridge.event_ready.set()
        _wait_for(
            lambda: connection.closed, message="malformed choice was not rejected"
        )

        event_messages = [
            json.loads(raw)
            for raw in connection.sent
            if isinstance(raw, str) and json.loads(raw).get("method") == "event"
        ]
        delivered = [
            message["params"]["event"]["payload"] for message in event_messages
        ]

        assert len(delivered) == 1
        assert delivered[0]["prompt_kind"] == "choice"
        assert delivered[0]["choice"]["object_id"] == "home-choice-1"
        assert "unknown_private_field" not in json.dumps(delivered[0])
        assert endpoint.ready is False
    finally:
        endpoint.close()


def test_live_choice_correlations_are_not_evicted_into_replay_authority() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    now = [10.0]
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_clock=lambda: now[0],
        choice_token_factory=tokens.__next__,
    )

    def respond_to_explore(correlation_id: str, choice: dict[str, object]):
        return _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id=f"respond-{correlation_id}",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": correlation_id,
                "event_type": "prompt.request",
                "response": {
                    "operation": "explore",
                    "option_id": "inspect",
                    "object_id": choice["object_id"],
                    "freshness": choice["freshness"],
                },
            },
        )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        latest_choice: dict[str, object] = {}
        for index in range(MAX_CHOICE_CORRELATIONS + 1):
            correlation_id = f"choice-correlation-{index}"
            try:
                payload = _deliver_choice_event(
                    endpoint,
                    _choice_event(
                        correlation_id,
                        operations=("choose", "explore"),
                    ),
                )["event"]["payload"]
            except _RequestError as error:
                assert error.code == "protocol_error"
                break
            latest_choice = payload["choice"]
            explored = respond_to_explore(correlation_id, latest_choice)
            assert explored["result"]["status"] == "accepted"

        bridge_calls_before_replay = len(bridge.respond_calls)
        try:
            replay_payload = _deliver_choice_event(
                endpoint,
                _choice_event(
                    "choice-correlation-0",
                    operations=("choose", "explore"),
                ),
            )["event"]["payload"]
        except _RequestError as error:
            assert error.code == "protocol_error"
        else:
            replayed = respond_to_explore(
                "choice-correlation-0", replay_payload["choice"]
            )
            assert replayed["result"]["status"] == "unavailable"
        assert len(bridge.respond_calls) == bridge_calls_before_replay
        assert latest_choice["object_id"] == "home-choice-1"
    finally:
        endpoint.close()


def test_replaced_expired_and_unsupported_choices_have_typed_no_effect_results() -> (
    None
):
    connection = FakeConnection()
    bridge = FakeBridge()
    now = [10.0]
    tokens = iter(
        [
            "home-choice-1",
            "home-freshness-1",
            "home-choice-2",
            "home-freshness-2",
            "home-choice-3",
            "home-freshness-3",
        ]
    )
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_clock=lambda: now[0],
        choice_token_factory=tokens.__next__,
    )

    def respond(correlation_id: str, choice: dict[str, object], operation: str):
        return _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id=f"respond-{correlation_id}",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": correlation_id,
                "event_type": "prompt.request",
                "response": {
                    "operation": operation,
                    "option_id": "inspect",
                    "object_id": choice["object_id"],
                    "freshness": choice["freshness"],
                },
            },
        )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        old_payload = _deliver_choice_event(
            endpoint, _choice_event("choice-correlation-old")
        )["event"]["payload"]
        _deliver_choice_event(
            endpoint,
            _choice_event(
                "choice-correlation-new",
                freshness="source-freshness-2",
            ),
        )
        replaced = respond("choice-correlation-old", old_payload["choice"], "choose")
        assert replaced["result"]["reason"] == "replaced"
        assert bridge.respond_calls == []

        unsupported_event = _choice_event(
            "choice-correlation-unsupported",
            freshness="source-freshness-3",
        )
        unsupported_event = BridgeEvent(
            unsupported_event.conversation_handle,
            unsupported_event.type,
            unsupported_event.payload,
            turn_id=unsupported_event.turn_id,
            correlation_id=unsupported_event.correlation_id,
            standard_session_id=unsupported_event.standard_session_id,
            configuration_revision=unsupported_event.configuration_revision,
            choice_capabilities=frozenset({"prompt.choose"}),
        )
        unsupported_payload = _deliver_choice_event(endpoint, unsupported_event)[
            "event"
        ]["payload"]
        unsupported = respond(
            "choice-correlation-unsupported",
            unsupported_payload["choice"],
            "explore",
        )
        assert unsupported_payload["choice"]["operations"] == ["choose"]
        assert unsupported["result"]["reason"] == "unsupported"
        assert bridge.respond_calls == []

        _deliver_choice_event(endpoint, _choice_event("choice-correlation-expired"))
        now[0] = 310.0
        expired_payload = _deliver_choice_event(
            endpoint,
            _choice_event(
                "choice-correlation-expired-reprompt",
            ),
        )["event"]["payload"]
        assert expired_payload["timeout_s"] == 0
        expired = respond(
            "choice-correlation-expired",
            expired_payload["choice"],
            "choose",
        )
        assert expired["result"]["reason"] == "expired"
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"sensitive": True}),
        lambda payload: payload.update({"sensitive": "yes"}),
        lambda payload: payload["choice"].update({"sensitive": True}),
        lambda payload: payload["choice"].update({"sensitive": "yes"}),
        lambda payload: payload["options"].append(
            {"id": "inspect", "label": "Duplicate"}
        ),
        lambda payload: payload["options"].extend(
            {"id": f"option-{index}", "label": "Option"} for index in range(31)
        ),
        lambda payload: payload["options"][0].update({"sensitive": True}),
        lambda payload: payload["options"][0].update({"sensitive": "yes"}),
        lambda payload: payload["options"].clear(),
        lambda payload: payload["choice"].update({"operations": ["choose", "run"]}),
    ],
)
def test_malformed_typed_choice_is_rejected_before_projection(mutate) -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)
    source_event = _choice_event("invalid-choice-1")
    unsafe_payload = {
        **source_event.payload,
        "choice": dict(source_event.payload["choice"]),
        "options": [dict(option) for option in source_event.payload["options"]],
    }
    mutate(unsafe_payload)
    invalid_event = BridgeEvent(
        source_event.conversation_handle,
        source_event.type,
        unsafe_payload,
        turn_id=source_event.turn_id,
        correlation_id=source_event.correlation_id,
        standard_session_id=source_event.standard_session_id,
        configuration_revision=source_event.configuration_revision,
        choice_capabilities=source_event.choice_capabilities,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        with pytest.raises(_RequestError) as error:
            endpoint._event_payload(invalid_event)
        assert error.value.code == "protocol_error"
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


def test_choice_event_helper_preserves_an_explicit_empty_options_list() -> None:
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
            params={"conversation_handle": HANDLE, "text": "choose"},
        )

        with pytest.raises(_RequestError) as error:
            endpoint._event_payload(_choice_event("empty-options", options=[]))

        assert error.value.code == "protocol_error"
    finally:
        endpoint.close()


def test_typed_choice_accepts_published_text_and_identifier_limits() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    event = _choice_event(
        "choice-correlation-limits",
        object_id="o" * 64,
        freshness="f" * 64,
        text="T" * 1024,
        options=[{"id": "i" * 64, "label": "L" * 256}],
    )
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )

        projected = _deliver_choice_event(endpoint, event)["event"]["payload"]

        assert len(projected["text"]) == 1024
        assert len(projected["options"][0]["id"]) == 64
        assert len(projected["options"][0]["label"]) == 256
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    ("field", "length"),
    [
        ("object_id", 65),
        ("freshness", 65),
        ("option_id", 65),
        ("text", 1025),
        ("label", 257),
    ],
)
def test_typed_choice_rejects_text_and_identifiers_over_published_limits(
    field: str, length: int
) -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    kwargs: dict[str, object] = {}
    options: list[dict[str, object]] = [{"id": "option", "label": "Option"}]
    if field in {"object_id", "freshness", "text"}:
        kwargs[field] = "x" * length
    elif field == "option_id":
        options[0]["id"] = "x" * length
    else:
        options[0]["label"] = "x" * length
    if field in {"option_id", "label"}:
        kwargs["options"] = options
    event = _choice_event("choice-correlation-over-limit", **kwargs)
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )

        with pytest.raises(_RequestError) as error:
            endpoint._event_payload(event)

        assert error.value.code == "protocol_error"
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    ("response_change", "expected_reason"),
    [
        ({"option_id": "not-listed"}, "unknown"),
        ({"unexpected": "field"}, "unsupported"),
    ],
)
def test_typed_choice_rejects_unknown_options_and_response_fields(
    response_change: dict[str, object], expected_reason: str
) -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        _deliver_choice_event(endpoint, _choice_event("choice-correlation-1"))
        response: dict[str, object] = {
            "operation": "choose",
            "option_id": "inspect",
            "object_id": "home-choice-1",
            "freshness": "home-freshness-1",
            **response_change,
        }

        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-invalid",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": response,
            },
        )

        assert result["result"]["status"] == "unavailable"
        assert result["result"]["reason"] == expected_reason
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


def test_typed_choice_wrong_context_and_revocation_return_unavailable() -> None:
    connection = FakeConnection()
    bridge = FakeBridge()
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )
    response = {
        "operation": "choose",
        "option_id": "inspect",
        "object_id": "home-choice-1",
        "freshness": "home-freshness-1",
    }
    event = _choice_event("choice-correlation-1")

    def respond(*, handle: str = HANDLE, turn_id: str = "home-turn-1", request_id: str):
        return _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id=request_id,
            method="prompt.respond",
            params={
                "conversation_handle": handle,
                "turn_id": turn_id,
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": response,
            },
        )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        _deliver_choice_event(endpoint, event)
        wrong_endpoint = respond(handle="other-handle", request_id="wrong-endpoint")
        wrong_turn = respond(turn_id="other-turn", request_id="wrong-turn")
        closed = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="close-1",
            method="conversation.close",
            params={"conversation_handle": HANDLE},
        )
        revoked = respond(request_id="revoked")

        assert wrong_endpoint["result"]["reason"] == "revoked"
        assert wrong_endpoint["result"]["conversation_handle"] == "other-handle"
        assert wrong_turn["result"]["reason"] == "stale"
        assert closed["result"]["status"] == "closed"
        assert revoked["result"]["reason"] == "revoked"
        assert bridge.respond_calls == []
    finally:
        endpoint.close()


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (BridgeRequestRejected("choice is no longer pending"), "stale"),
        (BridgeAuthorizationError("stale_conversation"), "revoked"),
    ],
)
def test_choice_revalidation_failures_return_typed_unavailable(
    failure: Exception, reason: str
) -> None:
    connection = FakeConnection()
    bridge = ChoiceResponseFailureBridge(failure)
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        event = _choice_event("choice-correlation-1")
        _deliver_choice_event(endpoint, event)

        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-1",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": "home-choice-1",
                    "freshness": "home-freshness-1",
                },
            },
        )

        assert result["result"] == {
            "schema": 1,
            "conversation_handle": HANDLE,
            "turn_id": "home-turn-1",
            "status": "unavailable",
            "reason": reason,
        }
        assert len(bridge.respond_calls) == 1
    finally:
        endpoint.close()


def test_capability_loss_after_choice_authorization_cannot_be_retried() -> None:
    connection = FakeConnection()
    bridge = ChoiceResponseFailureBridge(
        BridgeCapabilityUnavailable("prompt.choose is unavailable")
    )
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        _deliver_choice_event(endpoint, _choice_event("choice-correlation-1"))
        request = {
            "jsonrpc": "2.0",
            "schema": 1,
            "method": "prompt.respond",
            "params": {
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": "home-choice-1",
                    "freshness": "home-freshness-1",
                },
            },
        }

        failed = endpoint.handle_message(json.dumps({**request, "id": "choice-1"}))
        retried = endpoint.handle_message(json.dumps({**request, "id": "choice-2"}))

        assert failed["result"]["status"] == "unavailable"
        assert failed["result"]["reason"] == "unsupported"
        assert retried["result"]["status"] == "unavailable"
        assert len(bridge.respond_calls) == 1
    finally:
        endpoint.close()


def test_uncertain_choice_timeout_marks_endpoint_unavailable_and_blocks_retry() -> None:
    connection = FakeConnection()
    bridge = ChoiceResponseFailureBridge(BridgeTimeoutError("response timed out"))
    tokens = iter(["home-choice-1", "home-freshness-1"])
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        choice_token_factory=tokens.__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        _deliver_choice_event(endpoint, _choice_event("choice-correlation-1"))
        request = {
            "jsonrpc": "2.0",
            "schema": 1,
            "method": "prompt.respond",
            "params": {
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-1",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": "home-choice-1",
                    "freshness": "home-freshness-1",
                },
            },
        }

        failed = endpoint.handle_message(json.dumps({**request, "id": "choice-1"}))
        retried = endpoint.handle_message(json.dumps({**request, "id": "choice-2"}))

        assert failed["error"]["data"]["code"] == "transport_timeout"
        assert failed["error"]["data"]["delivery"] == "uncertain"
        assert retried["result"]["status"] == "unavailable"
        assert len(bridge.respond_calls) == 1
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


def test_late_choice_rejection_after_turn_release_keeps_safe_diagnostic_correlation() -> (
    None
):
    class FixedRecorder(DiagnosticsRecorder):
        def new_correlation_id(self) -> str:
            return "corr-" + "f" * 32

    connection = FakeConnection()
    bridge = FakeBridge()
    recorder = FixedRecorder(store=InMemoryDiagnosticsStore(), clock=lambda: 100.0)
    endpoint = BridgeEndpoint(
        connection,
        bridge,
        headers=HEADERS,
        route=ROUTE,
        diagnostics=recorder,
        choice_token_factory=iter(["home-choice-1", "home-freshness-1"]).__next__,
    )

    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "choose"},
        )
        projected = _deliver_choice_event(
            endpoint, _choice_event("choice-correlation-late")
        )["event"]["payload"]
        bridge.audio.append(AudioFrame("fallback", turn_id="home-turn-1"))
        bridge.audio_ready.set()
        _wait_for(lambda: endpoint._audio_thread is None)
        endpoint._event_payload(
            BridgeEvent(
                HANDLE,
                "message.complete",
                {"status": "completed"},
                turn_id="home-turn-1",
            )
        )
        assert endpoint._active_turn_id is None

        result = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="choice-respond-late",
            method="prompt.respond",
            params={
                "conversation_handle": HANDLE,
                "turn_id": "home-turn-1",
                "correlation_id": "choice-correlation-late",
                "event_type": "prompt.request",
                "response": {
                    "operation": "choose",
                    "option_id": "inspect",
                    "object_id": projected["choice"]["object_id"],
                    "freshness": projected["choice"]["freshness"],
                },
            },
        )

        timeline = recorder.timeline("corr-" + "f" * 32)
        assert result["result"]["status"] == "unavailable"
        assert result["result"]["reason"] == "revoked"
        assert [(event.phase, event.outcome) for event in timeline[-2:]] == [
            ("turn", "completed"),
            ("request", "rejected"),
        ]
        assert len(bridge.respond_calls) == 0
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


@pytest.mark.parametrize(
    "failure",
    [
        BridgeTransportError("upstream dropped"),
        BridgeTimeoutError("upstream timed out"),
        TimeoutError("upstream timed out"),
        RuntimeError("upstream unavailable"),
    ],
)
def test_upstream_event_loss_closes_peer_but_preserves_uncertain_turn(failure):
    class UncertainBridge(FakeBridge):
        def reauthorize(self, *, headers):
            self.reauthorize_calls.append(dict(headers))
            return BridgeStatus(
                "unavailable",
                HANDLE,
                "stale_conversation",
                unresolved_turn=BridgeTurn("home-turn-1", HANDLE, "uncertain"),
            )

    connection = FakeConnection()
    bridge = UncertainBridge()
    endpoint = BridgeEndpoint(connection, bridge, headers=HEADERS, route=ROUTE)
    try:
        _open(endpoint)
        _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="prompt-1",
            method="prompt.submit",
            params={"conversation_handle": HANDLE, "text": "Once only"},
        )
        bridge.events.append(failure)
        bridge.event_ready.set()
        _wait_for(lambda: connection.closed)
        assert bridge.close_calls == 0
        assert endpoint.has_recoverable_state
        endpoint.adopt(FakeConnection(), headers=HEADERS)
        response = _send(
            endpoint,
            jsonrpc="2.0",
            schema=1,
            id="reconnect-1",
            method="conversation.reconnect",
            params={"conversation_handle": HANDLE},
        )
        assert response["result"]["unresolved_turn"]["status"] == "uncertain"
        assert bridge.prompt_calls == ["Once only"]
    finally:
        endpoint.close()
