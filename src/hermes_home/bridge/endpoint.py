"""Framework-independent adapter for the versioned Home bridge route."""

from __future__ import annotations

import json
import logging
import math
import secrets
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from hermes_home.bridge.choice_authority import (
    CHOICE_EVENT_TYPE,
    CHOICE_EXPIRY_EVENT_TYPE,
    ChoiceAuthority,
    ChoiceProtocolError,
)
from hermes_home.bridge.routes import HOME_BRIDGE_PATH
from hermes_home.bridge.standard import (
    MAX_PROTECTED_INPUT_BYTES,
    PROTECTED_PROMPT_EXPIRY_TYPES,
    PROTECTED_PROMPT_TYPES,
    AudioFrame,
    BridgeAuthorizationError,
    BridgeCapabilityUnavailable,
    BridgeEvent,
    BridgeProtocolError,
    BridgeRequestRejected,
    BridgeStatus,
    BridgeTimeoutError,
    BridgeTransportError,
    BridgeTurn,
    GatewayRPCError,
    endpoint_safe_payload,
)
from hermes_home.observability.diagnostics import DiagnosticEvent, DiagnosticsRecorder

LOGGER = logging.getLogger(__name__)

BRIDGE_WS_PATH = HOME_BRIDGE_PATH
HOME_BRIDGE_SCHEMA = 1
MAX_BRIDGE_MESSAGE_BYTES = 1_048_576
MAX_MESSAGE_SIZE = MAX_BRIDGE_MESSAGE_BYTES
MAX_RETIRED_TURN_IDS = 1024
MAX_PARKED_EVENT_BYTES = 8 * 1_048_576

_METHODS = frozenset(
    {
        "conversation.open",
        "conversation.reconnect",
        "conversation.activity",
        "conversation.close",
        "prompt.submit",
        "prompt.respond",
        "session.interrupt",
        "command.dispatch",
        "bridge.ping",
    }
)
_STRUCTURED_PROMPT_FIELDS = {
    "approval.request": ("choice", frozenset({"choice", "all"})),
    "clarify.request": ("answer", frozenset({"answer", "question_id"})),
    "secret.request": ("value", frozenset({"value"})),
    "sudo.request": ("password", frozenset({"password"})),
    CHOICE_EVENT_TYPE: (
        "option_id",
        frozenset({"operation", "option_id", "object_id", "freshness"}),
    ),
}
_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "complete",
        "cancelled",
        "canceled",
        "interrupted",
        "failed",
        "error",
        "aborted",
        "stopped",
        "timeout",
        "timed_out",
        "timed-out",
    }
)
_TERMINAL_EVENT_TYPES = frozenset(
    {
        "turn_complete",
        "turn.complete",
        "turn.completed",
        "turn.end",
        "turn.ended",
        "turn_end",
        "response.complete",
        "response.completed",
    }
)
_INTERRUPTED_EVENT_TYPES = frozenset(
    {"turn_interrupted", "turn.interrupted", "turn.cancelled"}
)
_FAILED_EVENT_TYPES = frozenset({"error", "turn.error"})
_PROMPT_EXPIRY_TYPES = {
    "approval.expire": "approval.request",
    "clarify.expire": "clarify.request",
    "secret.expire": "secret.request",
    "sudo.expire": "sudo.request",
    CHOICE_EXPIRY_EVENT_TYPE: CHOICE_EVENT_TYPE,
}
# Standard events an endpoint may receive. Everything else Standard emits
# (session.info with the system prompt and tool inventory, sessions.changed,
# tool.*, session.usage, ...) stays server-side.
_ENDPOINT_EVENT_TYPES = frozenset(
    {
        "message.start",
        "message.delta",
        "message.interim",
        "message.complete",
        "text",
        "text_delta",
        "text_final",
        "thinking",
        "thinking.delta",
        "reasoning.available",
        "reasoning",
        "reasoning.delta",
        "status",
        "status.update",
        "turn_complete",
        "turn_interrupted",
        "turn.interrupted",
        "turn.cancelled",
        "turn.error",
        "audio_abort",
        "error",
        *_TERMINAL_EVENT_TYPES,
        *_STRUCTURED_PROMPT_FIELDS,
        *_PROMPT_EXPIRY_TYPES,
    }
)
_STABLE_CODES = frozenset(
    {
        "invalid_request",
        "authorization_unavailable",
        "unauthorized",
        "stale_conversation",
        "conversation_mismatch",
        "request_rejected",
        "transport_unavailable",
        "transport_timeout",
        "protocol_error",
        "capability_unavailable",
        "hermes_unavailable",
    }
)
_READINESS_ONLY_CODES = frozenset({"reconnect_required"})
_CODE_ALIASES = {
    "hermes_timeout": "transport_timeout",
    "timeout": "transport_timeout",
    "timed_out": "transport_timeout",
    "timed-out": "transport_timeout",
    "unavailable": "hermes_unavailable",
    "not_ready": "hermes_unavailable",
}
_HIDDEN_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "bearer_token",
        "credential",
        "credentials",
        "device_credential",
        "device_id",
        "hermes_bearer",
        "hermes_token",
        "password",
        "profile",
        "profile_id",
        "profile_name",
        "refresh_token",
        "runtime_session_id",
        "secret_token",
        "session_id",
        "session_key",
        "stored_session_id",
        "token",
    }
)
_HIDDEN_KEY_NAMES = frozenset(
    "".join(character for character in key.casefold() if character.isalnum())
    for key in _HIDDEN_KEYS
)


class WebSocketConnection(Protocol):
    """The small socket port required by :class:`BridgeEndpoint`."""

    def recv(self, timeout: float | None = None) -> object: ...

    def send(self, message: str | bytes) -> None: ...

    def close(self, *args: object, **kwargs: object) -> None: ...


BridgeFactory = Callable[[], object]


@dataclass(frozen=True, slots=True)
class BridgeRoute:
    """The allowlisted, endpoint-safe descriptor for the one local route."""

    route_class: str = "home"
    id: str = "local"

    def __post_init__(self) -> None:
        if type(self.route_class) is not str or self.route_class != "home":
            raise ValueError("Home bridge route class must be home")
        if type(self.id) is not str or not self.id.strip():
            raise ValueError("Home bridge route ID must be non-empty")
        if "\r" in self.id or "\n" in self.id:
            raise ValueError("Home bridge route ID must not contain line breaks")
        object.__setattr__(self, "id", self.id.strip())

    def to_endpoint(self) -> dict[str, str]:
        return {"class": "home", "id": self.id}


RouteDescriptor = BridgeRoute


class _RequestError(Exception):
    def __init__(
        self,
        code: str,
        *,
        delivery: str = "known",
        rpc_code: int = -32000,
    ) -> None:
        self.code = code
        self.delivery = delivery
        self.rpc_code = rpc_code
        super().__init__(code)


class BridgeEndpoint:
    """Translate one Home WebSocket connection into one injected bridge.

    The adapter intentionally owns no grants, Profile state, or Standard
    session identity.  It only keeps the opaque handle and the current prompt
    events needed to route a response back to the already-bound bridge.
    """

    def __init__(
        self,
        connection: WebSocketConnection,
        bridge: object | None,
        *,
        headers: Mapping[str, str] | None = None,
        route: Mapping[str, object] | BridgeRoute | None = None,
        max_message_size: int = MAX_BRIDGE_MESSAGE_BYTES,
        diagnostics: DiagnosticsRecorder | None = None,
        choice_clock: Callable[[], float] = time.monotonic,
        choice_token_factory: Callable[[], str] | None = None,
    ) -> None:
        if type(max_message_size) is not int or max_message_size <= 0:
            raise ValueError("Home bridge message size must be positive")
        self._connection: WebSocketConnection | None = connection
        self._bridge = bridge
        self._headers = dict(headers or {})
        self._route = _safe_route(route)
        self._max_message_size = max_message_size
        self._diagnostics = diagnostics
        self._choice_authority = ChoiceAuthority(
            clock=choice_clock,
            token_factory=choice_token_factory or (lambda: secrets.token_urlsafe(24)),
        )
        self._send_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._stop = threading.Event()
        self._readiness_changed = threading.Event()
        self._closed = False
        self._adopted_transport = False
        self._adoption_ack_pending = False
        self._adoption_acknowledged = False
        self._flush_after_response = False
        self._disconnect_after_response = False
        self._parked_events: deque[str] = deque()
        self._parked_event_bytes = 0
        self._bound_handle: str | None = None
        self._ready = False
        self._submitting_turn = False
        self._conversation_closing = False
        self._availability_reason = (
            "hermes_unavailable" if bridge is None else "stale_conversation"
        )
        self._active_turn_id: str | None = None
        self._known_turn_ids: set[str] = set()
        self._terminal_turn_ids: set[str] = set()
        self._interrupted_turn_ids: set[str] = set()
        self._retired_turn_ids: set[str] = set()
        self._retired_turn_order: deque[str] = deque()
        self._retired_turn_correlations: dict[str, str] = {}
        self._pending_prompts: dict[tuple[str, str, str, str], BridgeEvent] = {}
        self._event_thread: threading.Thread | None = None
        self._audio_thread: threading.Thread | None = None
        self._audio_turn_id: str | None = None
        self._turn_correlations: dict[str, str] = {}

    @property
    def conversation_handle(self) -> str | None:
        with self._state_lock:
            return self._bound_handle

    @property
    def ready(self) -> bool:
        with self._state_lock:
            return self._ready

    @property
    def has_active_turn(self) -> bool:
        with self._state_lock:
            return self._active_turn_id is not None or self._submitting_turn

    @property
    def has_recoverable_state(self) -> bool:
        with self._state_lock:
            return (
                self._active_turn_id is not None
                or self._submitting_turn
                or bool(self._parked_events)
            )

    @property
    def adoption_acknowledged(self) -> bool:
        with self._state_lock:
            return self._adoption_acknowledged

    def run(
        self,
        *,
        first_message: object | None = None,
        park_on_disconnect: bool = False,
    ) -> None:
        """Serve requests until the peer closes the WebSocket."""
        try:
            if first_message is not None:
                self.handle_message(first_message)
            while not self._stop.is_set():
                with self._state_lock:
                    connection = self._connection
                if connection is None:
                    break
                message = connection.recv()
                if message is None:
                    break
                self.handle_message(message)
        except Exception:  # noqa: BLE001 - peer close is transport cleanup
            # The WebSocket server owns the peer-facing close handshake.  A
            # receive failure is transport state, not a reason to expose a
            # Python exception or its message to the endpoint.
            return
        finally:
            if park_on_disconnect and self.has_recoverable_state:
                self.detach()
            else:
                self.close()

    serve = run

    def detach(self) -> None:
        """Detach a failed endpoint transport while the Hermes turn continues."""

        with self._send_lock:
            self._connection = None

    def adopt(
        self,
        connection: WebSocketConnection,
        *,
        headers: Mapping[str, str],
    ) -> None:
        """Attach a candidate reconnect; buffered events wait for reauthorization."""

        with self._send_lock:
            if self._closed or self._connection is not None:
                raise RuntimeError("Home bridge endpoint is not parked")
            self._connection = connection
            self._headers = dict(headers)
            self._adopted_transport = True
        with self._state_lock:
            self._adoption_ack_pending = True
            self._adoption_acknowledged = False

    def handle_message(self, message: object) -> dict[str, object] | None:
        """Validate and handle one inbound JSON-RPC message."""
        try:
            request_id, method, params = _parse_request(
                message, max_message_size=self._max_message_size
            )
        except _RequestError as error:
            response = _error_response(
                None,
                error.code,
                delivery=error.delivery,
                rpc_code=error.rpc_code,
            )
            self._send_json(response)
            if (
                error.rpc_code == -32600
                and _message_size(message) > self._max_message_size
            ):
                self._close_connection(code=1009, reason="message too large")
            return response

        try:
            result = self._dispatch(method, params, request_id)
            response = _success_response(request_id, result)
        except _RequestError as error:
            response = _error_response(
                request_id,
                error.code,
                delivery=error.delivery,
                rpc_code=error.rpc_code,
            )
        except Exception:  # noqa: BLE001 - never expose injected exception text
            # Keep an unexpected adapter defect typed and credential-free.  A
            # malformed bridge result must never become an endpoint payload.
            self._mark_unavailable("protocol_error")
            response = _error_response(
                request_id,
                "protocol_error",
                delivery="uncertain",
            )
        response_sent = self._send_json(response)
        with self._state_lock:
            if self._adoption_ack_pending:
                result = response.get("result")
                self._adoption_acknowledged = (
                    response_sent
                    and isinstance(result, Mapping)
                    and result.get("status") == "ready"
                )
                self._adoption_ack_pending = False
            flush_after_response = self._flush_after_response
            self._flush_after_response = False
        if flush_after_response:
            self._flush_parked_events()
        with self._state_lock:
            disconnect_after_response = self._disconnect_after_response
            self._disconnect_after_response = False
        if disconnect_after_response:
            self._close_connection()
        return response

    handle = handle_message

    def close(self) -> None:
        """Close the bridge and socket exactly once."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._ready = False
            self._choice_authority.revoke_all()
            self._stop.set()
            self._readiness_changed.set()
            bridge = self._bridge
            self._bridge = None
            event_thread = self._event_thread
            audio_thread = self._audio_thread
            self._turn_correlations.clear()
            self._parked_events.clear()
            self._parked_event_bytes = 0
        if bridge is not None:
            try:
                bridge.close()
            except Exception as error:  # noqa: BLE001 - cleanup must continue
                del error
        self._close_connection()
        current = threading.current_thread()
        for thread in (event_thread, audio_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=1)

    def _dispatch(
        self,
        method: str,
        params: dict[str, object],
        request_id: object,
    ) -> dict[str, object]:
        del request_id
        if method == "conversation.open":
            return self._conversation_open(params)
        if method == "conversation.reconnect":
            return self._conversation_reconnect(params)
        if method == "conversation.activity":
            return self._conversation_activity(params)
        if method == "conversation.close":
            return self._conversation_close(params)
        if method == "prompt.submit":
            return self._prompt_submit(params)
        if method == "prompt.respond":
            return self._prompt_respond(params)
        if method == "session.interrupt":
            return self._session_interrupt(params)
        if method == "command.dispatch":
            return self._command_dispatch(params)
        if method == "bridge.ping":
            return self._bridge_ping(params)
        raise _RequestError("invalid_request", rpc_code=-32601)

    def _conversation_open(self, params: dict[str, object]) -> dict[str, object]:
        _require_param_shape(params, required={"conversation_handle"})
        handle = _require_handle(params["conversation_handle"])
        with self._state_lock:
            if self._bound_handle is None:
                self._bound_handle = handle
            elif self._bound_handle != handle:
                return self._unavailable_result(handle, "conversation_mismatch")
            bridge = self._bridge
        if bridge is None:
            return self._apply_readiness(
                BridgeStatus("unavailable", handle, "hermes_unavailable"),
                handle=handle,
            )
        try:
            status = bridge.open(headers=self._headers, conversation_handle=handle)
        except Exception as error:  # noqa: BLE001 - injected bridge is untrusted
            status = _status_from_exception(handle, error)
        return self._apply_readiness(status, handle=handle)

    def _conversation_reconnect(self, params: dict[str, object]) -> dict[str, object]:
        _require_param_shape(params, optional={"conversation_handle"})
        with self._state_lock:
            existing_handle = self._bound_handle
        if "conversation_handle" in params:
            handle = _require_handle(params["conversation_handle"])
            if existing_handle is not None and handle != existing_handle:
                return self._unavailable_result(handle, "conversation_mismatch")
        elif existing_handle is None:
            raise _RequestError("invalid_request", rpc_code=-32602)
        else:
            handle = existing_handle

        with self._state_lock:
            if self._bound_handle is None:
                self._bound_handle = handle
            bridge = self._bridge
            adopted_transport = self._adopted_transport
        if bridge is None:
            return self._apply_readiness(
                BridgeStatus("unavailable", handle, "hermes_unavailable"),
                handle=handle,
                reconnect=True,
            )
        try:
            if adopted_transport:
                reauthorize = getattr(bridge, "reauthorize", None)
                if not callable(reauthorize):
                    status = BridgeStatus(
                        "unavailable", handle, "capability_unavailable"
                    )
                else:
                    status = reauthorize(headers=self._headers)
            # A newly created per-connection HomeBridge has no binding for
            # reconnect() to resume yet.  Its open() path resolves the same
            # opaque grant and uses the grant's durable Session when present.
            elif existing_handle is None:
                status = bridge.open(
                    headers=self._headers,
                    conversation_handle=handle,
                )
            else:
                status = bridge.reconnect(headers=self._headers)
        except Exception as error:  # noqa: BLE001 - injected bridge is untrusted
            status = _status_from_exception(handle, error)
        result = self._apply_readiness(
            status,
            handle=handle,
            reconnect=not adopted_transport,
        )
        if adopted_transport:
            with self._state_lock:
                self._adopted_transport = False
                self._flush_after_response = result.get("status") == "ready"
                self._disconnect_after_response = result.get("status") != "ready"
        reconnect_result = {
            "schema": HOME_BRIDGE_SCHEMA,
            "status": result["status"],
            "conversation_handle": handle,
        }
        # Keep the ready shape identical to conversation.open so clients can
        # revalidate the approved route and retain its capabilities when a
        # parked bridge is adopted. Reconnect adds the unresolved turn state;
        # it does not silently change the handshake contract.
        for key in ("reason", "route", "capabilities", "unresolved_turn"):
            if key in result:
                reconnect_result[key] = result[key]
        return reconnect_result

    def _conversation_activity(self, params: dict[str, object]) -> dict[str, object]:
        _require_param_shape(params, required={"conversation_handle", "state"})
        handle = self._require_bound_handle(params["conversation_handle"])
        state = params["state"]
        if type(state) is not str or state not in {
            "capture",
            "turn",
            "playback",
            "playback_complete",
        }:
            raise _RequestError("invalid_request", rpc_code=-32602)
        self._require_ready()
        with self._state_lock:
            bridge = self._bridge
        if bridge is None:  # pragma: no cover - _require_ready proves this
            raise _RequestError("hermes_unavailable")
        try:
            accepted = bridge.report_activity(state)
        except Exception as error:  # noqa: BLE001 - injected bridge is untrusted
            self._raise_bridge_error(error)
        if type(accepted) is not bool or not accepted:
            self._mark_unavailable("protocol_error")
            raise _RequestError("protocol_error", delivery="uncertain")
        return {
            "schema": HOME_BRIDGE_SCHEMA,
            "conversation_handle": handle,
            "status": "accepted",
        }

    def _conversation_close(self, params: dict[str, object]) -> dict[str, object]:
        _require_param_shape(params, required={"conversation_handle"})
        handle = self._require_bound_handle(params["conversation_handle"])
        with self._state_lock:
            bridge = self._bridge
        if bridge is None:  # pragma: no cover - _require_ready proves this
            raise _RequestError("hermes_unavailable")
        # Stop polling before closing the upstream socket. Its expected wakeup
        # must not close the downstream socket before this RPC is acknowledged.
        with self._state_lock:
            self._conversation_closing = True
            self._ready = False
        try:
            closed = bridge.close_conversation()
        except Exception as error:  # noqa: BLE001 - injected bridge is untrusted
            self._raise_bridge_error(error)
        if type(closed) is not bool or not closed:
            self._mark_unavailable("protocol_error")
            raise _RequestError("protocol_error", delivery="uncertain")
        with self._state_lock:
            self._ready = False
            self._availability_reason = "stale_conversation"
            self._choice_authority.revoke_all()
            self._readiness_changed.set()
        return {
            "schema": HOME_BRIDGE_SCHEMA,
            "conversation_handle": handle,
            "status": "closed",
        }

    def _prompt_submit(self, params: dict[str, object]) -> dict[str, object]:
        _require_param_shape(params, required={"conversation_handle", "text"})
        handle = self._require_bound_handle(params["conversation_handle"])
        text = params["text"]
        if type(text) is not str or not text.strip():
            raise _RequestError("invalid_request", rpc_code=-32602)
        correlation_id = (
            self._diagnostics.new_correlation_id()
            if self._diagnostics is not None
            else f"corr-{uuid.uuid4().hex}"
        )
        try:
            self._require_ready()
        except _RequestError as error:
            if correlation_id is not None:
                self._record_diagnostic(
                    correlation_id,
                    source="endpoint",
                    phase="turn",
                    outcome="unavailable",
                    failure_code=error.code,
                )
            raise
        self._wait_for_stale_audio()
        with self._state_lock:
            if self._active_turn_id is not None:
                raise _RequestError("request_rejected")
            self._submitting_turn = True
            bridge = self._bridge
        if bridge is None:  # pragma: no cover - _require_ready proves this
            with self._state_lock:
                self._submitting_turn = False
            raise _RequestError("hermes_unavailable")
        if correlation_id is not None:
            self._record_diagnostic(
                correlation_id,
                source="endpoint",
                phase="turn",
                outcome="started",
            )
        try:
            turn = bridge.submit_prompt(text)
        except Exception as error:  # noqa: BLE001 - injected bridge is untrusted
            if correlation_id is not None:
                self._record_diagnostic(
                    correlation_id,
                    source="home",
                    phase="turn",
                    outcome="failed",
                    failure_code=_normalize_code(_bridge_error(error)[0]),
                )
            self._raise_bridge_error(error)
        finally:
            with self._state_lock:
                self._submitting_turn = False
        try:
            payload = _turn_payload(turn, handle)
        except _RequestError as error:
            self._mark_unavailable(error.code)
            raise
        payload["correlation_id"] = correlation_id
        turn_id = payload["turn_id"]
        assert isinstance(turn_id, str)
        with self._state_lock:
            if turn_id in self._known_turn_ids or turn_id in self._retired_turn_ids:
                self._mark_unavailable("protocol_error")
                raise _RequestError("protocol_error", delivery="uncertain")
            terminal_seen = turn_id in self._terminal_turn_ids
            if correlation_id is not None:
                self._turn_correlations[turn_id] = correlation_id
            self._active_turn_id = turn_id
            self._known_turn_ids.add(turn_id)
            if not terminal_seen:
                self._audio_turn_id = turn_id
        if correlation_id is not None:
            self._record_diagnostic(
                correlation_id,
                source="home",
                phase="turn",
                outcome="accepted",
                turn_id=turn_id,
            )
        if terminal_seen:
            self._maybe_release_turn(turn_id)
        else:
            self._start_audio_pump(turn_id)
        return payload

    def _prompt_respond(self, params: dict[str, object]) -> dict[str, object]:
        _require_param_shape(
            params,
            required={
                "conversation_handle",
                "turn_id",
                "correlation_id",
                "event_type",
                "response",
            },
        )
        handle = _require_handle(params["conversation_handle"])
        turn_id = _require_string(params["turn_id"])
        correlation_id = _require_string(params["correlation_id"])
        event_type = _require_string(params["event_type"])
        response = params["response"]
        if not isinstance(response, Mapping):
            raise _RequestError("invalid_request", rpc_code=-32602)
        with self._state_lock:
            is_choice_correlation = self._choice_authority.knows_correlation(
                correlation_id
            )
        if event_type == CHOICE_EVENT_TYPE or is_choice_correlation:
            return self._choice_respond(
                handle=handle,
                turn_id=turn_id,
                correlation_id=correlation_id,
                event_type=event_type,
                response=response,
            )
        handle = self._require_bound_handle(handle)
        self._require_ready()
        with self._state_lock:
            active_turn_id = self._active_turn_id
            event = self._pending_prompts.get(
                (handle, turn_id, correlation_id, event_type)
            )
            bridge = self._bridge
        if active_turn_id != turn_id or event is None:
            raise _RequestError("request_rejected")
        _validate_prompt_response(event, response)
        if bridge is None:  # pragma: no cover - _require_ready proves this
            raise _RequestError("hermes_unavailable")
        protected_prompt = event.type in PROTECTED_PROMPT_TYPES
        prompt_key = (handle, turn_id, correlation_id, event_type)
        if protected_prompt:
            # A protected response is one-shot. Remove the pending entry before
            # crossing the transport boundary so neither known rejection nor
            # delivery uncertainty creates a secret retry path.
            with self._state_lock:
                self._pending_prompts.pop(prompt_key, None)
        try:
            result = bridge.respond_prompt(event, dict(response))
        except Exception as error:  # noqa: BLE001 - injected bridge is untrusted
            self._raise_bridge_error(error)
        if protected_prompt:
            return _protected_response_result_payload(
                result,
                handle=handle,
                turn_id=turn_id,
                event_type=event_type,
            )
        try:
            safe_result = _result_payload(
                result,
                conversation_handle=handle,
                turn_id=turn_id,
            )
        except _RequestError as error:
            self._mark_unavailable(error.code)
            raise
        if not _has_remaining_questions(safe_result):
            with self._state_lock:
                self._pending_prompts.pop(prompt_key, None)
        return safe_result

    def _choice_respond(
        self,
        *,
        handle: str,
        turn_id: str,
        correlation_id: str,
        event_type: str,
        response: Mapping[str, object],
    ) -> dict[str, object]:
        with self._state_lock:
            bound_handle = self._bound_handle
            active_turn_id = self._active_turn_id
            event = (
                self._pending_prompts.get(
                    (handle, turn_id, correlation_id, CHOICE_EVENT_TYPE)
                )
                if handle == bound_handle
                else None
            )
            bridge = self._bridge
            ready = self._ready
            if bound_handle is None or handle != bound_handle:
                reason = "revoked"
                decision = None
            elif not ready or bridge is None:
                reason = "revoked"
                decision = None
                self._choice_authority.revoke_all()
            else:
                decision = self._choice_authority.authorize(
                    handle=handle,
                    turn_id=turn_id,
                    correlation_id=correlation_id,
                    event_type=event_type,
                    response=response,
                    active_turn_id=active_turn_id,
                    ready=ready,
                    event=event,
                )
                reason = decision.reason
        if reason is not None:
            self._record_choice_diagnostic(turn_id, outcome="rejected")
            return _choice_unavailable_result(
                handle=handle,
                turn_id=turn_id,
                reason=reason,
            )

        assert decision is not None
        assert decision.event is not None
        assert decision.response is not None
        assert decision.operation is not None
        assert bridge is not None
        try:
            result = bridge.respond_prompt(decision.event, decision.response)
        except BridgeCapabilityUnavailable:
            with self._state_lock:
                self._choice_authority.revoke_all()
                self._pending_prompts.pop(
                    (handle, turn_id, correlation_id, CHOICE_EVENT_TYPE), None
                )
            self._record_choice_diagnostic(turn_id, outcome="rejected")
            return _choice_unavailable_result(
                handle=bound_handle,
                turn_id=turn_id,
                reason="unsupported",
            )
        except BridgeRequestRejected:
            with self._state_lock:
                self._choice_authority.revoke_turn(handle=handle, turn_id=turn_id)
                self._pending_prompts.pop(
                    (handle, turn_id, correlation_id, CHOICE_EVENT_TYPE), None
                )
            self._record_choice_diagnostic(turn_id, outcome="rejected")
            return _choice_unavailable_result(
                handle=bound_handle,
                turn_id=turn_id,
                reason="stale",
            )
        except BridgeAuthorizationError as error:
            with self._state_lock:
                self._choice_authority.revoke_all()
                self._pending_prompts.pop(
                    (handle, turn_id, correlation_id, CHOICE_EVENT_TYPE), None
                )
            self._mark_unavailable(error.code)
            self._record_choice_diagnostic(turn_id, outcome="rejected")
            return _choice_unavailable_result(
                handle=bound_handle,
                turn_id=turn_id,
                reason="revoked",
            )
        except Exception as error:  # noqa: BLE001 - never retry an action write
            self._record_choice_diagnostic(turn_id, outcome="failed")
            self._raise_bridge_error(error)
        try:
            safe_result = _choice_response_result_payload(
                result,
                handle=bound_handle,
                turn_id=turn_id,
                operation=decision.operation,
            )
        except _RequestError as error:
            if error.delivery == "uncertain":
                self._mark_unavailable(error.code)
            raise
        with self._state_lock:
            self._choice_authority.complete(decision)
            self._pending_prompts.pop(
                (handle, turn_id, correlation_id, CHOICE_EVENT_TYPE), None
            )
        self._record_choice_diagnostic(turn_id, outcome="accepted")
        return safe_result

    def _record_choice_diagnostic(self, turn_id: str, *, outcome: str) -> None:
        with self._state_lock:
            correlation_id = self._turn_correlations.get(turn_id)
            if correlation_id is None:
                correlation_id = self._retired_turn_correlations.get(turn_id)
        if correlation_id is None:
            return
        self._record_diagnostic(
            correlation_id,
            source="endpoint",
            phase="request",
            outcome=outcome,
            failure_code="request_rejected" if outcome == "rejected" else None,
            turn_id=turn_id,
        )

    def _session_interrupt(self, params: dict[str, object]) -> dict[str, object]:
        _require_param_shape(params, required={"conversation_handle", "turn_id"})
        handle = self._require_bound_handle(params["conversation_handle"])
        turn_id = _require_string(params["turn_id"])
        self._require_ready()
        with self._state_lock:
            if self._active_turn_id != turn_id:
                raise _RequestError("request_rejected")
            bridge = self._bridge
        if bridge is None:  # pragma: no cover - _require_ready proves this
            raise _RequestError("hermes_unavailable")
        try:
            accepted = bridge.interrupt()
        except Exception as error:  # noqa: BLE001 - injected bridge is untrusted
            self._raise_bridge_error(error)
        if type(accepted) is not bool:
            self._mark_unavailable("protocol_error")
            raise _RequestError("protocol_error", delivery="uncertain")
        if not accepted:
            raise _RequestError("request_rejected")
        with self._state_lock:
            self._interrupted_turn_ids.add(turn_id)
        return {
            "schema": HOME_BRIDGE_SCHEMA,
            "conversation_handle": handle,
            "turn_id": turn_id,
            "status": "accepted",
        }

    def _command_dispatch(self, params: dict[str, object]) -> dict[str, object]:
        _require_param_shape(
            params,
            required={"conversation_handle", "name"},
            optional={"arg"},
        )
        handle = self._require_bound_handle(params["conversation_handle"])
        name = _require_string(params["name"])
        if "arg" in params and params["arg"] is not None:
            _require_string(params["arg"])
        self._require_ready()
        with self._state_lock:
            bridge = self._bridge
        if bridge is None:  # pragma: no cover - _require_ready proves this
            raise _RequestError("hermes_unavailable")
        try:
            result = bridge.dispatch_command(name, params.get("arg"))
        except Exception as error:  # noqa: BLE001 - injected bridge is untrusted
            self._raise_bridge_error(error)
        try:
            return _result_payload(result, conversation_handle=handle)
        except _RequestError as error:
            self._mark_unavailable(error.code)
            raise

    def _bridge_ping(self, params: dict[str, object]) -> dict[str, object]:
        _require_param_shape(params, required={"conversation_handle"})
        handle = self._require_bound_handle(params["conversation_handle"])
        self._require_ready()
        with self._state_lock:
            bridge = self._bridge
        if bridge is None:  # pragma: no cover - _require_ready proves this
            raise _RequestError("hermes_unavailable")
        try:
            result = bridge.ping()
        except Exception as error:  # noqa: BLE001 - injected bridge is untrusted
            self._raise_bridge_error(error)
        try:
            return _result_payload(result, conversation_handle=handle)
        except _RequestError as error:
            self._mark_unavailable(error.code)
            raise

    def _require_bound_handle(self, value: object) -> str:
        handle = _require_handle(value)
        with self._state_lock:
            bound_handle = self._bound_handle
        if bound_handle is None:
            raise _RequestError("stale_conversation")
        if handle != bound_handle:
            raise _RequestError("conversation_mismatch")
        return handle

    def _require_ready(self) -> None:
        with self._state_lock:
            if self._ready and self._bridge is not None:
                return
            reason = self._availability_reason or "hermes_unavailable"
        raise _RequestError(_normalize_code(reason))

    def _apply_readiness(
        self,
        status: object,
        *,
        handle: str,
        reconnect: bool = False,
    ) -> dict[str, object]:
        audio_thread_to_join: threading.Thread | None = None
        try:
            payload = _readiness_payload(status, route=self._route)
        except _RequestError as error:
            payload = self._unavailable_result(handle, error.code)
        if payload.get("conversation_handle") != handle:
            payload = self._unavailable_result(handle, "conversation_mismatch")
        result_status = payload.get("status")
        if result_status == "ready":
            with self._state_lock:
                if self._bound_handle != handle:
                    self._ready = False
                    self._availability_reason = "conversation_mismatch"
                    return self._unavailable_result(
                        handle,
                        "conversation_mismatch",
                    )
                self._ready = True
                self._conversation_closing = False
                self._availability_reason = None
                if reconnect:
                    self._choice_authority.revoke_all()
                    audio_thread_to_join = self._audio_thread
                    self._active_turn_id = None
                    self._submitting_turn = False
                    self._audio_turn_id = None
                    self._known_turn_ids.clear()
                    self._terminal_turn_ids.clear()
                    self._retired_turn_ids.clear()
                    self._retired_turn_order.clear()
                    self._pending_prompts.clear()
                    self._turn_correlations.clear()
            self._readiness_changed.set()
            if (
                audio_thread_to_join is not None
                and audio_thread_to_join is not threading.current_thread()
            ):
                audio_thread_to_join.join(timeout=1)
            self._start_event_pump()
        else:
            with self._state_lock:
                self._choice_authority.revoke_all()
                self._ready = False
                reason = payload.get("reason")
                self._availability_reason = (
                    str(reason) if isinstance(reason, str) else "hermes_unavailable"
                )
            self._readiness_changed.set()
        return payload

    def _wait_for_stale_audio(self) -> None:
        with self._state_lock:
            thread = self._audio_thread
        if (
            thread is None
            or thread is threading.current_thread()
            or not thread.is_alive()
        ):
            return
        thread.join(timeout=1)
        if thread.is_alive():
            self._mark_unavailable("transport_unavailable")
            raise _RequestError("transport_unavailable")

    def _unavailable_result(self, handle: str, reason: str) -> dict[str, object]:
        normalized = _normalize_code(reason, readiness=True)
        result: dict[str, object] = {
            "schema": HOME_BRIDGE_SCHEMA,
            "status": "unavailable",
            "conversation_handle": handle,
            "reason": normalized,
        }
        return result

    def _raise_bridge_error(self, error: Exception) -> None:
        code, delivery = _bridge_error(error)
        if delivery == "uncertain" or code in {
            "authorization_unavailable",
            "unauthorized",
            "stale_conversation",
            "conversation_mismatch",
            "hermes_unavailable",
            "protocol_error",
        }:
            self._mark_unavailable(code)
        raise _RequestError(code, delivery=delivery)

    def _mark_unavailable(self, reason: str) -> None:
        with self._state_lock:
            self._choice_authority.revoke_all()
            self._ready = False
            self._availability_reason = _normalize_code(reason)
        self._readiness_changed.set()

    def _start_event_pump(self) -> None:
        with self._state_lock:
            thread = self._event_thread
            if self._closed or (thread is not None and thread.is_alive()):
                return
            thread = threading.Thread(
                target=self._event_loop,
                name="hermes-home-bridge-events",
                daemon=True,
            )
            self._event_thread = thread
            thread.start()

    def _event_loop(self) -> None:
        while not self._stop.is_set():
            with self._state_lock:
                bridge = self._bridge
                ready = self._ready
            if bridge is None or not ready:
                self._stop.wait(0.05)
                continue
            try:
                event = bridge.next_event()
            except BridgeProtocolError as error:
                with self._state_lock:
                    if self._conversation_closing:
                        continue
                # Messages are fixed, content-free validation text.
                LOGGER.warning(
                    "closing Home bridge connection: Standard event rejected: %s", error
                )
                self._mark_unavailable("protocol_error")
                self.close()
                return
            except BridgeTimeoutError, TimeoutError:
                with self._state_lock:
                    if self._conversation_closing:
                        continue
                # The Standard adapter consumes healthy idle polls itself.
                # A timeout escaping that adapter is an unavailable transport.
                self._mark_unavailable("transport_timeout")
                self._close_connection(code=1011, reason="upstream transport timeout")
                continue
            except BridgeTransportError, ConnectionError, EOFError, OSError:
                with self._state_lock:
                    if self._conversation_closing:
                        continue
                self._mark_unavailable("transport_unavailable")
                # Wake the endpoint's receive loop rather than leaving the UI
                # waiting forever. Keep the bridge's uncertain turn for the
                # authenticated reconnect response; close() would erase it.
                self._close_connection(
                    code=1011, reason="upstream transport unavailable"
                )
                continue
            except RuntimeError:
                with self._state_lock:
                    if self._conversation_closing:
                        continue
                self._mark_unavailable("hermes_unavailable")
                self._close_connection(code=1011, reason="upstream unavailable")
                continue
            except Exception as error:  # noqa: BLE001 - fail closed on bridge defects
                with self._state_lock:
                    if self._conversation_closing:
                        continue
                LOGGER.warning(
                    "closing Home bridge connection: bridge event failed: %s",
                    type(error).__name__,
                )
                self._mark_unavailable("protocol_error")
                self.close()
                return
            try:
                # Turn and prompt bookkeeping runs for every event; only the
                # allowlisted types are forwarded to the endpoint.
                params = self._event_payload(event)
                if event.type not in _ENDPOINT_EVENT_TYPES:
                    continue
                self._send_json(
                    {
                        "jsonrpc": "2.0",
                        "schema": HOME_BRIDGE_SCHEMA,
                        "method": "event",
                        "params": params,
                    }
                )
            except _RequestError as error:
                if error.code == "choice_revision_limit":
                    continue
                LOGGER.warning(
                    "closing Home bridge connection: %s event not forwardable: %s",
                    event.type if isinstance(event, BridgeEvent) else "unknown",
                    error.code,
                )
                self._mark_unavailable("protocol_error")
                self.close()
                return
            except Exception as error:  # noqa: BLE001 - transport send is best effort
                LOGGER.warning(
                    "closing Home bridge connection: event send failed: %s",
                    type(error).__name__,
                )
                self._mark_unavailable("transport_unavailable")
                self.close()
                return

    def _event_payload(self, event: object) -> dict[str, object]:
        if not isinstance(event, BridgeEvent):
            raise _RequestError("protocol_error", delivery="uncertain")
        with self._state_lock:
            handle = self._bound_handle
        if handle is None or event.conversation_handle != handle:
            raise _RequestError("conversation_mismatch", delivery="known")
        if type(event.type) is not str or not event.type:
            raise _RequestError("protocol_error", delivery="uncertain")
        if not isinstance(event.payload, Mapping):
            raise _RequestError("protocol_error", delivery="uncertain")
        if event.turn_id is not None:
            _require_string(event.turn_id)
        if event.correlation_id is not None:
            _require_string(event.correlation_id)
        with self._state_lock:
            turn_is_known = (
                event.turn_id is None or event.turn_id in self._known_turn_ids
            )
            submitting = self._active_turn_id is None and self._submitting_turn
        if not turn_is_known and not submitting:
            raise _RequestError("protocol_error", delivery="uncertain")
        if event.type == CHOICE_EVENT_TYPE:
            try:
                with self._state_lock:
                    safe_payload = self._choice_authority.project(event, handle=handle)
            except ChoiceProtocolError as error:
                if str(error) == "choice revision limit exceeded":
                    raise _RequestError(
                        "choice_revision_limit", delivery="known"
                    ) from error
                raise _RequestError("protocol_error", delivery="uncertain") from error
        elif event.type in PROTECTED_PROMPT_TYPES | PROTECTED_PROMPT_EXPIRY_TYPES:
            safe_payload = endpoint_safe_payload(event.type, event.payload)
        else:
            safe_payload = _safe_public_mapping(event.payload)
        if event.type in _STRUCTURED_PROMPT_FIELDS:
            if event.turn_id is None or event.correlation_id is None:
                raise _RequestError("protocol_error", delivery="uncertain")
            key = (
                handle,
                event.turn_id,
                event.correlation_id,
                event.type,
            )
            with self._state_lock:
                if event.type in PROTECTED_PROMPT_TYPES:
                    for pending_key in tuple(self._pending_prompts):
                        if (
                            pending_key[0] == handle
                            and pending_key[1] == event.turn_id
                            and pending_key[3] in PROTECTED_PROMPT_TYPES
                            and pending_key != key
                        ):
                            self._pending_prompts.pop(pending_key, None)
                self._pending_prompts[key] = event
        elif event.type in _PROMPT_EXPIRY_TYPES:
            if event.turn_id is not None and event.correlation_id is not None:
                if event.type == CHOICE_EXPIRY_EVENT_TYPE:
                    with self._state_lock:
                        self._choice_authority.expire_correlation(
                            handle=handle,
                            turn_id=event.turn_id,
                            correlation_id=event.correlation_id,
                        )
                with self._state_lock:
                    self._pending_prompts.pop(
                        (
                            handle,
                            event.turn_id,
                            event.correlation_id,
                            _PROMPT_EXPIRY_TYPES[event.type],
                        ),
                        None,
                    )
        terminal_event = event.turn_id is not None and _is_terminal_event(
            event.type, safe_payload
        )
        if terminal_event:
            assert event.turn_id is not None
            with self._state_lock:
                correlation_id = self._turn_correlations.get(event.turn_id)
                self._terminal_turn_ids.add(event.turn_id)
            if correlation_id is not None:
                self._record_diagnostic(
                    correlation_id,
                    source="hermes",
                    phase="turn",
                    outcome=_terminal_diagnostic_outcome(event.type, safe_payload),
                    turn_id=event.turn_id,
                )
            self._maybe_release_turn(event.turn_id)
        params: dict[str, object] = {
            "schema": HOME_BRIDGE_SCHEMA,
            "conversation_handle": handle,
            "event": {"type": event.type, "payload": safe_payload},
        }
        if event.turn_id is not None:
            params["turn_id"] = event.turn_id
        if event.correlation_id is not None:
            params["correlation_id"] = event.correlation_id
        return params

    def _start_audio_pump(self, turn_id: str) -> None:
        with self._state_lock:
            thread = self._audio_thread
            if self._closed or (thread is not None and thread.is_alive()):
                return
            thread = threading.Thread(
                target=self._audio_loop,
                args=(turn_id,),
                name="hermes-home-bridge-audio",
                daemon=True,
            )
            self._audio_thread = thread
            thread.start()

    def _audio_loop(self, turn_id: str) -> None:
        started = False
        ended = False
        try:
            while not self._stop.is_set():
                with self._state_lock:
                    bridge = self._bridge
                    ready = self._ready
                    active_turn_id = self._active_turn_id
                if bridge is None or not ready or active_turn_id != turn_id:
                    break
                try:
                    frame = bridge.next_audio()
                except BridgeTimeoutError:
                    self._record_audio_diagnostic(
                        turn_id,
                        outcome="unavailable",
                        failure_code="transport_timeout",
                    )
                    self._audio_stream_failed(
                        turn_id, "transport_timeout", started=started
                    )
                    break
                except TimeoutError:
                    self._record_audio_diagnostic(
                        turn_id,
                        outcome="unavailable",
                        failure_code="transport_timeout",
                    )
                    self._audio_stream_failed(
                        turn_id, "transport_timeout", started=started
                    )
                    break
                except BridgeTransportError, ConnectionError, EOFError, OSError:
                    self._record_audio_diagnostic(
                        turn_id,
                        outcome="unavailable",
                        failure_code="transport_unavailable",
                    )
                    self._audio_stream_failed(
                        turn_id, "transport_unavailable", started=started
                    )
                    break
                except Exception:  # noqa: BLE001 - audio sidecar failure is typed
                    self._record_audio_diagnostic(
                        turn_id,
                        outcome="unavailable",
                        failure_code="protocol_error",
                    )
                    self._audio_stream_failed(
                        turn_id, "protocol_error", started=started
                    )
                    break
                try:
                    kind, outgoing = self._audio_payload(
                        frame,
                        turn_id,
                        started=started,
                        ended=ended,
                    )
                except _RequestError as error:
                    self._record_audio_diagnostic(
                        turn_id,
                        outcome="unavailable",
                        failure_code=error.code,
                    )
                    self._audio_stream_failed(
                        turn_id, "protocol_error", started=started
                    )
                    break
                try:
                    if kind == "start":
                        started = True
                    elif kind in {"end", "fallback", "unavailable"}:
                        ended = True
                    if isinstance(outgoing, bytes):
                        self._send_binary(outgoing)
                    else:
                        self._send_json(outgoing)
                    if kind == "start":
                        self._record_audio_diagnostic(
                            turn_id,
                            outcome="started",
                        )
                    elif kind == "pcm":
                        self._record_audio_diagnostic(
                            turn_id,
                            outcome="accepted",
                            byte_count=len(outgoing),
                        )
                    elif kind == "end":
                        self._record_audio_diagnostic(
                            turn_id,
                            outcome="completed",
                        )
                    else:
                        self._record_audio_diagnostic(
                            turn_id,
                            outcome="unavailable",
                            failure_code=(
                                "audio_fallback"
                                if kind == "fallback"
                                else "audio_unavailable"
                            ),
                        )
                except Exception:  # noqa: BLE001 - close after a failed send
                    self._record_audio_diagnostic(
                        turn_id,
                        outcome="unavailable",
                        failure_code="transport_unavailable",
                    )
                    self._mark_unavailable("transport_unavailable")
                    self.close()
                    break
                if ended:
                    self._maybe_release_turn(turn_id)
                    break
        finally:
            with self._state_lock:
                if self._audio_thread is threading.current_thread():
                    self._audio_thread = None
                if self._audio_turn_id == turn_id:
                    self._audio_turn_id = None
            self._maybe_release_turn(turn_id)

    def _audio_payload(
        self,
        frame: object,
        turn_id: str,
        *,
        started: bool,
        ended: bool,
    ) -> tuple[str, dict[str, object] | bytes]:
        if not isinstance(frame, AudioFrame):
            raise _RequestError("protocol_error", delivery="uncertain")
        if frame.turn_id is not None:
            _require_string(frame.turn_id)
        frame_turn_id = frame.turn_id if frame.turn_id is not None else turn_id
        if frame_turn_id != turn_id:
            raise _RequestError("conversation_mismatch", delivery="known")
        if type(frame.kind) is not str or not frame.kind:
            raise _RequestError("protocol_error", delivery="uncertain")
        if not isinstance(frame.metadata, Mapping):
            raise _RequestError("protocol_error", delivery="uncertain")
        if frame.kind == "start":
            if started or ended:
                raise _RequestError("protocol_error", delivery="uncertain")
            sample_rate = frame.sample_rate
            channels = frame.channels
            sample_width = frame.sample_width
            byte_order = frame.byte_order
            if (
                type(sample_rate) is not int
                or not 0 < sample_rate <= 384_000
                or channels != 1
                or sample_width != 2
                or type(channels) is not int
                or type(sample_width) is not int
                or not isinstance(byte_order, str)
                or byte_order.lower() not in {"little", "le"}
            ):
                raise _RequestError("protocol_error", delivery="uncertain")
            return (
                "start",
                self._audio_notification(
                    turn_id,
                    {
                        "kind": "start",
                        "sample_rate": sample_rate,
                        "channels": channels,
                        "sample_width": 2,
                        "byte_order": "little",
                    },
                ),
            )
        if frame.kind == "pcm":
            if not started or ended or type(frame.data) is not bytes:
                raise _RequestError("protocol_error", delivery="uncertain")
            if len(frame.data) % 2:
                raise _RequestError("protocol_error", delivery="uncertain")
            return "pcm", frame.data
        if frame.kind in {"end", "fallback", "unavailable"}:
            if frame.kind == "end" and (not started or ended):
                raise _RequestError("protocol_error", delivery="uncertain")
            frame_data: dict[str, object] = {"kind": frame.kind}
            if frame.kind == "unavailable":
                reason = frame.metadata.get("reason")
                frame_data["reason"] = (
                    reason
                    if isinstance(reason, str) and reason
                    else "audio_unavailable"
                )
            return frame.kind, self._audio_notification(turn_id, frame_data)
        raise _RequestError("protocol_error", delivery="uncertain")

    def _audio_notification(
        self, turn_id: str, frame: dict[str, object]
    ) -> dict[str, object]:
        with self._state_lock:
            handle = self._bound_handle
        if handle is None:
            raise _RequestError("stale_conversation")
        return {
            "jsonrpc": "2.0",
            "schema": HOME_BRIDGE_SCHEMA,
            "method": "audio.frame",
            "params": {
                "schema": HOME_BRIDGE_SCHEMA,
                "conversation_handle": handle,
                "turn_id": turn_id,
                "frame": frame,
            },
        }

    def _audio_stream_failed(self, turn_id: str, reason: str, *, started: bool) -> None:
        """End a turn's audio after its sidecar stops.

        Interrupting a turn deliberately cuts its audio. That is not a
        failure: end a started stream normally (the interrupted terminal
        event stops playback) and send nothing if audio never started.
        """
        with self._state_lock:
            interrupted = turn_id in self._interrupted_turn_ids
        if not interrupted:
            self._send_audio_unavailable(turn_id, reason)
            return
        if not started:
            return
        try:
            self._send_json(self._audio_notification(turn_id, {"kind": "end"}))
        except Exception:  # noqa: BLE001 - client may close at any time
            self._mark_unavailable("transport_unavailable")
            self.close()

    def _send_audio_unavailable(self, turn_id: str, reason: str) -> None:
        try:
            self._send_json(
                self._audio_notification(
                    turn_id,
                    {"kind": "unavailable", "reason": _normalize_code(reason)},
                )
            )
        except Exception:  # noqa: BLE001 - client may close at any time
            self._mark_unavailable("transport_unavailable")
            self.close()

    def _maybe_release_turn(self, turn_id: str) -> None:
        with self._state_lock:
            audio_active = (
                self._audio_turn_id == turn_id and self._audio_thread is not None
            )
            released = False
            if (
                self._active_turn_id == turn_id
                and turn_id in self._terminal_turn_ids
                and not audio_active
            ):
                released = True
                self._active_turn_id = None
                if turn_id not in self._retired_turn_ids:
                    if len(self._retired_turn_order) >= MAX_RETIRED_TURN_IDS:
                        retired = self._retired_turn_order.popleft()
                        self._retired_turn_ids.discard(retired)
                        self._retired_turn_correlations.pop(retired, None)
                    self._retired_turn_order.append(turn_id)
                    self._retired_turn_ids.add(turn_id)
                self._known_turn_ids.discard(turn_id)
                self._terminal_turn_ids.discard(turn_id)
                correlation_id = self._turn_correlations.pop(turn_id, None)
                if correlation_id is not None:
                    self._retired_turn_correlations[turn_id] = correlation_id
                self._pending_prompts = {
                    key: event
                    for key, event in self._pending_prompts.items()
                    if key[1] != turn_id
                }
            handle = self._bound_handle
            if released and handle is not None:
                self._choice_authority.revoke_turn(handle=handle, turn_id=turn_id)

    def _record_audio_diagnostic(
        self,
        turn_id: str,
        *,
        outcome: str,
        failure_code: str | None = None,
        byte_count: int | None = None,
    ) -> None:
        with self._state_lock:
            correlation_id = self._turn_correlations.get(turn_id)
        if correlation_id is None:
            return
        self._record_diagnostic(
            correlation_id,
            source="hermes",
            phase="audio",
            outcome=outcome,
            failure_code=failure_code,
            turn_id=turn_id,
            byte_count=byte_count,
        )

    def _record_diagnostic(
        self,
        correlation_id: str,
        *,
        source: str,
        phase: str,
        outcome: str,
        failure_code: str | None = None,
        turn_id: str | None = None,
        byte_count: int | None = None,
    ) -> None:
        if self._diagnostics is None:
            return
        fields: dict[str, object] = {
            "route_class": "home",
            "route_id": self._diagnostics.route_identity(self._route.id),
        }
        if failure_code is not None:
            fields["failure_code"] = failure_code
        if turn_id is not None:
            fields["turn_fingerprint"] = self._diagnostics.fingerprint(turn_id)
        if byte_count is not None:
            fields["byte_count"] = byte_count
        try:
            self._diagnostics.record(
                DiagnosticEvent.create(
                    correlation_id=correlation_id,
                    source=source,
                    phase=phase,
                    outcome=outcome,
                    occurred_at=self._diagnostics.now(),
                    **fields,
                )
            )
        except Exception as error:  # noqa: BLE001 - telemetry cannot block bridge work
            del error

    def _prepare_choice_delivery(self, message: str) -> str:
        """Start the choice TTL when its event is about to reach a socket."""

        try:
            document = json.loads(message)
        except TypeError, ValueError, json.JSONDecodeError:
            return message
        if not isinstance(document, dict) or document.get("method") != "event":
            return message
        params = document.get("params")
        if not isinstance(params, dict):
            return message
        event = params.get("event")
        if not isinstance(event, dict) or event.get("type") != CHOICE_EVENT_TYPE:
            return message
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("prompt_kind") != "choice":
            return message
        handle = params.get("conversation_handle")
        turn_id = params.get("turn_id")
        correlation_id = params.get("correlation_id")
        if not all(
            type(value) is str and value for value in (handle, turn_id, correlation_id)
        ):
            return message
        with self._state_lock:
            timeout_s = self._choice_authority.mark_delivered(
                handle=handle,
                turn_id=turn_id,
                correlation_id=correlation_id,
            )
        if timeout_s is None:
            return message
        payload["timeout_s"] = timeout_s
        return json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

    def _send_json(self, payload: dict[str, object]) -> bool:
        message = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(message.encode("utf-8")) > self._max_message_size:
            self._mark_unavailable("protocol_error")
            self._stop.set()
            self._close_connection(code=1009, reason="message too large")
            raise _RequestError("protocol_error", delivery="uncertain")
        overflow = False
        failed_connection: WebSocketConnection | None = None
        sent_or_queued = False
        with self._send_lock:
            if self._closed:
                return False
            connection = self._connection
            if connection is None:
                if payload.get("method") == "event":
                    message_bytes = len(message.encode("utf-8"))
                    if (
                        self._parked_event_bytes + message_bytes
                        > MAX_PARKED_EVENT_BYTES
                    ):
                        overflow = True
                    else:
                        self._parked_events.append(message)
                        self._parked_event_bytes += message_bytes
                        sent_or_queued = True
            else:
                if payload.get("method") == "event":
                    message = self._prepare_choice_delivery(message)
                try:
                    connection.send(message)
                    sent_or_queued = True
                except Exception:  # noqa: BLE001 - detach a failed endpoint socket
                    self._connection = None
                    failed_connection = connection
                    if payload.get("method") == "event":
                        message_bytes = len(message.encode("utf-8"))
                        if (
                            self._parked_event_bytes + message_bytes
                            > MAX_PARKED_EVENT_BYTES
                        ):
                            overflow = True
                        else:
                            self._parked_events.append(message)
                            self._parked_event_bytes += message_bytes
                            sent_or_queued = True
        if failed_connection is not None:
            _close_connection_quietly(failed_connection)
        if overflow:
            self._mark_unavailable("transport_unavailable")
            self.close()
            return False
        return sent_or_queued

    def _send_binary(self, data: bytes) -> None:
        chunk_size = self._max_message_size - self._max_message_size % 2
        if chunk_size < 2 and data:
            raise _RequestError("protocol_error", delivery="uncertain")
        failed_connection: WebSocketConnection | None = None
        with self._send_lock:
            if self._closed:
                return
            connection = self._connection
            if connection is None:
                return
            try:
                for offset in range(0, len(data), chunk_size):
                    connection.send(data[offset : offset + chunk_size])
            except Exception:  # noqa: BLE001 - audio is not replayed after a drop
                self._connection = None
                failed_connection = connection
        if failed_connection is not None:
            _close_connection_quietly(failed_connection)

    def _flush_parked_events(self) -> None:
        failed_connection: WebSocketConnection | None = None
        with self._send_lock:
            connection = self._connection
            if connection is None or self._closed:
                return
            while self._parked_events:
                message = self._prepare_choice_delivery(self._parked_events[0])
                self._parked_events[0] = message
                try:
                    connection.send(message)
                except Exception:  # noqa: BLE001 - retain the unsent event
                    self._connection = None
                    failed_connection = connection
                    break
                self._parked_events.popleft()
            self._parked_event_bytes = sum(
                len(message.encode("utf-8")) for message in self._parked_events
            )
        if failed_connection is not None:
            _close_connection_quietly(failed_connection)

    def _close_connection(self, *, code: int = 1000, reason: str = "") -> None:
        with self._send_lock:
            connection = self._connection
            self._connection = None
        if connection is None:
            return
        try:
            connection.close(code=code, reason=reason)
        except TypeError:
            try:
                connection.close()
            except Exception as error:  # noqa: BLE001 - close is best effort
                del error
        except Exception as error:  # noqa: BLE001 - close is best effort
            del error


def _close_connection_quietly(connection: WebSocketConnection) -> None:
    try:
        connection.close()
    except Exception as error:  # noqa: BLE001 - failed transport is already detached
        del error


HomeBridgeEndpoint = BridgeEndpoint


def _parse_request(
    message: object, *, max_message_size: int
) -> tuple[object, str, dict[str, object]]:
    size = _message_size(message)
    if size > max_message_size:
        raise _RequestError("invalid_request", rpc_code=-32600)
    if not isinstance(message, str):
        raise _RequestError("invalid_request", rpc_code=-32600)
    try:
        document = json.loads(message)
    except TypeError, ValueError, json.JSONDecodeError, RecursionError:
        raise _RequestError("invalid_request", rpc_code=-32700) from None
    if not isinstance(document, dict):
        raise _RequestError("invalid_request", rpc_code=-32600)
    request_id = document.get("id")
    if not _valid_request_id(request_id) or "id" not in document:
        raise _RequestError("invalid_request", rpc_code=-32600)
    if (
        document.get("jsonrpc") != "2.0"
        or type(document.get("schema")) is not int
        or document["schema"] != HOME_BRIDGE_SCHEMA
    ):
        raise _RequestError("invalid_request", rpc_code=-32600)
    method = document.get("method")
    if type(method) is not str or not method.strip() or method not in _METHODS:
        raise _RequestError("invalid_request", rpc_code=-32601)
    params = document.get("params", {})
    if not isinstance(params, dict) or any(type(key) is not str for key in params):
        raise _RequestError("invalid_request", rpc_code=-32602)
    return request_id, method, dict(params)


def _valid_request_id(value: object) -> bool:
    if type(value) is str:
        return bool(value)
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    return False


def _message_size(message: object) -> int:
    if isinstance(message, bytes):
        return len(message)
    if isinstance(message, str):
        return len(message.encode("utf-8"))
    return MAX_BRIDGE_MESSAGE_BYTES + 1


def _require_param_shape(
    params: Mapping[str, object],
    *,
    required: set[str] | frozenset[str] = frozenset(),
    optional: set[str] | frozenset[str] = frozenset(),
) -> None:
    keys = set(params)
    if keys - required - optional or required - keys:
        raise _RequestError("invalid_request", rpc_code=-32602)


def _require_string(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise _RequestError("invalid_request", rpc_code=-32602)
    return value


def _require_handle(value: object) -> str:
    return _require_string(value)


def _safe_route(
    route: Mapping[str, object] | BridgeRoute | None,
) -> BridgeRoute:
    if route is None:
        return BridgeRoute()
    if isinstance(route, BridgeRoute):
        return route
    if not isinstance(route, Mapping):
        raise TypeError("Home bridge route must be an object")
    route_class = route.get("class")
    route_id = route.get("id")
    if type(route_class) is not str or route_class != "home":
        raise ValueError("Home bridge route class must be home")
    if type(route_id) is not str:
        raise ValueError("Home bridge route ID must be a string")
    return BridgeRoute(route_class, route_id)


def _normalize_code(code: object, *, readiness: bool = False) -> str:
    value = str(code or "hermes_unavailable").strip().lower()
    value = _CODE_ALIASES.get(value, value)
    if value in _STABLE_CODES or (readiness and value in _READINESS_ONLY_CODES):
        return value
    return "protocol_error" if not readiness else "hermes_unavailable"


def _error_response(
    request_id: object,
    code: str,
    *,
    delivery: str = "known",
    rpc_code: int = -32000,
) -> dict[str, object]:
    normalized = _normalize_code(code)
    data: dict[str, object] = {
        "schema": HOME_BRIDGE_SCHEMA,
        "code": normalized,
    }
    if delivery in {"known", "uncertain"}:
        data["delivery"] = delivery
    return {
        "jsonrpc": "2.0",
        "schema": HOME_BRIDGE_SCHEMA,
        "id": request_id,
        "error": {
            "code": rpc_code,
            "message": _error_message(normalized),
            "data": data,
        },
    }


def _success_response(
    request_id: object, result: dict[str, object]
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "schema": HOME_BRIDGE_SCHEMA,
        "id": request_id,
        "result": result,
    }


def _error_message(code: str) -> str:
    return {
        "invalid_request": "invalid Home bridge request",
        "authorization_unavailable": "Home authorization is unavailable",
        "unauthorized": "Home authorization was rejected",
        "stale_conversation": "conversation is no longer active",
        "conversation_mismatch": "conversation binding does not match",
        "request_rejected": "Home bridge request was rejected",
        "transport_unavailable": "Home bridge transport is unavailable",
        "transport_timeout": "Home bridge transport timed out",
        "protocol_error": "Home bridge protocol error",
        "capability_unavailable": "Home bridge capability is unavailable",
        "hermes_unavailable": "Hermes is unavailable",
    }.get(code, "Home bridge request failed")


def _status_from_exception(handle: str, error: Exception) -> BridgeStatus:
    code, _delivery = _bridge_error(error)
    return BridgeStatus("unavailable", handle, code)


def _bridge_error(error: Exception) -> tuple[str, str]:
    if isinstance(error, BridgeTimeoutError):
        return "transport_timeout", "uncertain"
    if isinstance(error, BridgeProtocolError):
        return "protocol_error", "uncertain"
    if isinstance(error, BridgeTransportError):
        return _normalize_code(error.code), "uncertain"
    if isinstance(error, BridgeRequestRejected):
        return "request_rejected", "known"
    if isinstance(error, BridgeCapabilityUnavailable):
        return "capability_unavailable", "known"
    if isinstance(error, BridgeAuthorizationError):
        return _normalize_code(error.code), "known"
    if isinstance(error, GatewayRPCError):
        return "request_rejected", "known"
    if isinstance(error, TimeoutError):
        return "transport_timeout", "uncertain"
    if isinstance(error, (ConnectionError, EOFError, OSError)):
        return "transport_unavailable", "uncertain"
    if isinstance(error, ValueError):
        return "invalid_request", "known"
    if isinstance(error, RuntimeError):
        return "hermes_unavailable", "known"
    return "protocol_error", "uncertain"


def _readiness_payload(status: object, *, route: BridgeRoute) -> dict[str, object]:
    if isinstance(status, BridgeStatus):
        raw = status.to_endpoint()
    elif isinstance(status, Mapping):
        raw = dict(status)
    else:
        raise _RequestError("protocol_error")
    status_value = raw.get("status")
    handle = raw.get("conversation_handle")
    if type(handle) is not str or not handle.strip():
        raise _RequestError("protocol_error")
    if status_value not in {"ready", "unavailable"}:
        raise _RequestError("protocol_error")
    result: dict[str, object] = {
        "schema": HOME_BRIDGE_SCHEMA,
        "status": status_value,
        "conversation_handle": handle,
    }
    if status_value == "ready":
        result["route"] = route.to_endpoint()
        result["capabilities"] = _safe_capabilities(raw.get("capabilities"))
    else:
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason:
            reason = "hermes_unavailable"
        result["reason"] = _normalize_code(reason, readiness=True)
    unresolved = raw.get("unresolved_turn")
    if unresolved is None and status_value == "ready":
        result["unresolved_turn"] = False
    elif unresolved is not None:
        result["unresolved_turn"] = _safe_unresolved_turn(unresolved, handle)
    return result


def _safe_capabilities(value: object) -> dict[str, object]:
    if value is None:
        return {"commands": [], "timing": "absent"}
    if not isinstance(value, Mapping):
        raise _RequestError("protocol_error")
    commands = value.get("commands", [])
    if not isinstance(commands, list) or any(
        type(command) is not str or not command for command in commands
    ):
        raise _RequestError("protocol_error")
    result: dict[str, object] = {"commands": list(dict.fromkeys(commands))}
    heartbeat = value.get("heartbeat")
    if heartbeat is not None:
        if type(heartbeat) is not bool:
            raise _RequestError("protocol_error")
        result["heartbeat"] = heartbeat
    for key in ("interrupt", "audio"):
        capability = value.get(key)
        if capability is not None:
            if type(capability) is not bool:
                raise _RequestError("protocol_error")
            result[key] = capability
    for key in ("prompt.choose", "prompt.explore"):
        capability = value.get(key)
        if capability is not None:
            if type(capability) is not bool:
                raise _RequestError("protocol_error")
            result[key] = capability
    timing = value.get("timing", "absent")
    if type(timing) is not str or not timing:
        raise _RequestError("protocol_error")
    # The pinned Standard baseline has no verified speech-timing contract.
    # Never promote an injected or future label into endpoint evidence here.
    result["timing"] = "absent"
    return result


def _safe_unresolved_turn(value: object, handle: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _RequestError("protocol_error")
    turn_handle = value.get("conversation_handle")
    turn_id = value.get("turn_id")
    status = value.get("status")
    if turn_handle != handle or type(turn_id) is not str or not turn_id:
        raise _RequestError("protocol_error")
    if type(status) is not str or not status:
        raise _RequestError("protocol_error")
    return {
        "schema": HOME_BRIDGE_SCHEMA,
        "conversation_handle": handle,
        "turn_id": turn_id,
        "status": status,
    }


def _turn_payload(value: object, handle: str) -> dict[str, object]:
    if isinstance(value, BridgeTurn):
        raw = value.to_endpoint()
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        raise _RequestError("protocol_error", delivery="uncertain")
    turn_handle = raw.get("conversation_handle")
    turn_id = raw.get("turn_id")
    status = raw.get("status")
    if turn_handle != handle or type(turn_id) is not str or not turn_id:
        raise _RequestError("protocol_error", delivery="uncertain")
    if status not in {"submitted", "accepted"}:
        raise _RequestError("protocol_error", delivery="uncertain")
    return {
        "schema": HOME_BRIDGE_SCHEMA,
        "conversation_handle": handle,
        "turn_id": turn_id,
        "status": status,
    }


def _result_payload(
    value: object,
    *,
    conversation_handle: str | None = None,
    turn_id: str | None = None,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _RequestError("protocol_error", delivery="uncertain")
    result = _safe_public_mapping(value)
    result["schema"] = HOME_BRIDGE_SCHEMA
    if conversation_handle is not None:
        result["conversation_handle"] = conversation_handle
    if turn_id is not None:
        result["turn_id"] = turn_id
    return result


def _protected_response_result_payload(
    value: object,
    *,
    handle: str,
    turn_id: str,
    event_type: str,
) -> dict[str, object]:
    operations = {
        "approval.request": "approval.respond",
        "secret.request": "secret.respond",
        "sudo.request": "sudo.respond",
    }
    operation = operations.get(event_type)
    if operation is None:
        raise _RequestError("protocol_error", delivery="uncertain")
    if not isinstance(value, Mapping):
        raise _RequestError("protocol_error", delivery="uncertain")
    for key in ("accepted", "resolved"):
        if key in value and type(value[key]) is not bool:
            raise _RequestError("protocol_error", delivery="uncertain")
    status = value.get("status")
    if status is not None and type(status) is not str:
        raise _RequestError("protocol_error", delivery="uncertain")
    if value.get("accepted") is False or value.get("resolved") is False:
        raise _RequestError("request_rejected")
    if isinstance(status, str) and status.casefold() in {
        "rejected",
        "denied",
        "expired",
        "failed",
        "error",
    }:
        raise _RequestError("request_rejected")
    if not (
        value.get("accepted") is True
        or value.get("resolved") is True
        or (
            isinstance(status, str)
            and status.casefold()
            in {
                "ok",
                "accepted",
                "resolved",
                "complete",
                "completed",
            }
        )
    ):
        raise _RequestError("protocol_error", delivery="uncertain")
    return {
        "schema": HOME_BRIDGE_SCHEMA,
        "conversation_handle": handle,
        "turn_id": turn_id,
        "status": "accepted",
        "operation": operation,
    }


def _choice_unavailable_result(
    *,
    handle: str,
    turn_id: str,
    reason: str,
) -> dict[str, object]:
    if reason not in {
        "stale",
        "expired",
        "replaced",
        "revoked",
        "duplicate",
        "unknown",
        "unsupported",
    }:
        raise _RequestError("protocol_error")
    return {
        "schema": HOME_BRIDGE_SCHEMA,
        "conversation_handle": handle,
        "turn_id": turn_id,
        "status": "unavailable",
        "reason": reason,
    }


def _choice_response_result_payload(
    value: object,
    *,
    handle: str,
    turn_id: str,
    operation: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _RequestError("protocol_error", delivery="uncertain")
    for key in ("accepted", "resolved"):
        if key in value and type(value[key]) is not bool:
            raise _RequestError("protocol_error", delivery="uncertain")
    status = value.get("status")
    if status is not None and type(status) is not str:
        raise _RequestError("protocol_error", delivery="uncertain")
    if value.get("accepted") is False or value.get("resolved") is False:
        raise _RequestError("request_rejected")
    if isinstance(status, str) and status.casefold() in {
        "rejected",
        "denied",
        "expired",
        "failed",
        "error",
    }:
        raise _RequestError("request_rejected")
    accepted_statuses = {"ok", "accepted", "resolved", "complete", "completed"}
    if not (
        value.get("accepted") is True
        or value.get("resolved") is True
        or (isinstance(status, str) and status.casefold() in accepted_statuses)
    ):
        raise _RequestError("protocol_error", delivery="uncertain")
    return {
        "schema": HOME_BRIDGE_SCHEMA,
        "conversation_handle": handle,
        "turn_id": turn_id,
        "status": "accepted",
        "operation": operation,
    }


def _validate_prompt_response(
    event: BridgeEvent, response: Mapping[str, object]
) -> None:
    event_type = event.type
    prompt_info = _STRUCTURED_PROMPT_FIELDS.get(event_type)
    if prompt_info is None:
        raise _RequestError("capability_unavailable")
    primary_key, allowed_keys = prompt_info
    if set(response) - allowed_keys:
        raise _RequestError("invalid_request", rpc_code=-32602)
    if primary_key not in response:
        raise _RequestError("invalid_request", rpc_code=-32602)
    value = response.get(primary_key)
    if event_type == "approval.request":
        if type(value) is not str or not value:
            raise _RequestError("invalid_request", rpc_code=-32602)
        all_value = response.get("all", False)
        if type(all_value) is not bool:
            raise _RequestError("invalid_request", rpc_code=-32602)
    elif event_type == "clarify.request":
        if not (
            type(value) is str
            and bool(value)
            or isinstance(value, list)
            and bool(value)
            and all(type(item) is str and bool(item) for item in value)
        ):
            raise _RequestError("invalid_request", rpc_code=-32602)
        if "question_id" in response and (
            type(response["question_id"]) is not str or not response["question_id"]
        ):
            raise _RequestError("invalid_request", rpc_code=-32602)
        if "question_id" in response:
            questions = event.payload.get("questions")
            if not isinstance(questions, list) or not questions:
                raise _RequestError("invalid_request", rpc_code=-32602)
            question_id = response["question_id"]
            question_ids = {
                question.get("qid")
                for question in questions
                if isinstance(question, Mapping)
            }
            if question_id not in question_ids:
                raise _RequestError("invalid_request", rpc_code=-32602)
    elif type(value) is not str or not value:
        raise _RequestError("invalid_request", rpc_code=-32602)
    elif event_type in {"secret.request", "sudo.request"}:
        try:
            byte_count = len(value.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise _RequestError("invalid_request", rpc_code=-32602) from error
        if byte_count > MAX_PROTECTED_INPUT_BYTES:
            raise _RequestError("invalid_request", rpc_code=-32602)


def _has_remaining_questions(result: Mapping[str, object]) -> bool:
    remaining = result.get("remaining")
    return isinstance(remaining, list) and bool(remaining)


def _is_terminal_event(event_type: str, payload: Mapping[str, object]) -> bool:
    if event_type in _INTERRUPTED_EVENT_TYPES or event_type in _FAILED_EVENT_TYPES:
        return True
    if event_type in _TERMINAL_EVENT_TYPES:
        return True
    if event_type != "message.complete":
        return False
    status = payload.get("status")
    return status is None or (
        type(status) is str and status.lower() in _TERMINAL_STATUSES
    )


def _terminal_diagnostic_outcome(
    event_type: str,
    payload: Mapping[str, object],
) -> str:
    if event_type in _INTERRUPTED_EVENT_TYPES:
        return "interrupted"
    if event_type in _FAILED_EVENT_TYPES:
        return "failed"
    status = payload.get("status")
    if isinstance(status, str):
        normalized = status.casefold()
        if normalized in {"interrupted", "cancelled", "canceled", "aborted", "stopped"}:
            return "interrupted"
        if normalized in {"failed", "error", "timeout", "timed_out", "timed-out"}:
            return "failed"
    return "completed"


def _safe_public_mapping(value: Mapping[object, object]) -> dict[str, object]:
    result = _safe_public_value(value)
    if not isinstance(result, dict):  # pragma: no cover - mapping always yields dict
        raise _RequestError("protocol_error", delivery="uncertain")
    return result


def _safe_public_value(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, nested in value.items():
            if type(key) is not str:
                raise _RequestError("protocol_error", delivery="uncertain")
            normalized_key = "".join(
                character for character in key.casefold() if character.isalnum()
            )
            if normalized_key in _HIDDEN_KEY_NAMES:
                continue
            result[key] = _safe_public_value(nested)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_public_value(item) for item in value]
    if type(value) is float and not math.isfinite(value):
        raise _RequestError("protocol_error", delivery="uncertain")
    if value is None or type(value) in {str, int, float, bool}:
        return value
    raise _RequestError("protocol_error", delivery="uncertain")
