"""Framework-independent bridge for the pinned Standard Hermes boundary."""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from threading import Condition, Event, Lock, RLock, Thread, current_thread
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

STANDARD_GATEWAY_PATH = "/api/ws"
STANDARD_AUDIO_PATH = "/api/audio/speak-stream"
MAX_TYPED_CHOICE_REVISIONS_PER_TURN = 128
MAX_PROTECTED_INPUT_BYTES = 4096
PROTECTED_CAPABILITIES = frozenset({"sensitive_entry", "consequence_confirm"})
PROTECTED_PROMPT_TYPES = frozenset(
    {"approval.request", "secret.request", "sudo.request"}
)
PROTECTED_PROMPT_EXPIRY_TYPES = frozenset(
    {"approval.expire", "secret.expire", "sudo.expire"}
)
PROTECTED_PROMPT_CAPABILITIES = {
    "approval.request": "consequence_confirm",
    "secret.request": "sensitive_entry",
    "sudo.request": "sensitive_entry",
}
_STRUCTURED_PROMPT_OPERATIONS = {
    "approval.request": ("approval.respond", "choice", frozenset({"choice", "all"})),
    "clarify.request": ("clarify.respond", "answer", frozenset({"answer"})),
    "secret.request": ("secret.respond", "value", frozenset({"value"})),
    "sudo.request": ("sudo.respond", "password", frozenset({"password"})),
    "prompt.request": (
        "prompt.choose",
        "option_id",
        frozenset({"operation", "option_id", "object_id", "freshness"}),
    ),
}
_TERMINAL_MESSAGE_STATUSES = frozenset(
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
_NONTERMINAL_MESSAGE_STATUSES = frozenset(
    {
        "accepted",
        "pending",
        "queued",
        "running",
        "streaming",
        "started",
        "in_progress",
        "in-progress",
    }
)
_AUDIO_FRAME_TYPES = frozenset({"start", "end", "fallback"})
_PROMPT_EXPIRY_TYPES = {
    "approval.expire": "approval.request",
    "clarify.expire": "clarify.request",
    "secret.expire": "secret.request",
    "sudo.expire": "sudo.request",
    "prompt.expire": "prompt.request",
}
LOGGER = logging.getLogger(__name__)


class GatewayRPCError(RuntimeError):
    """A Standard gateway explicitly rejected one JSON-RPC request."""

    def __init__(self, code: object, message: object) -> None:
        self.code = str(code) if code is not None else "request_rejected"
        self.message = str(message) if message is not None else "request rejected"
        super().__init__(self.message)


class BridgeRequestRejected(RuntimeError):
    """A request was rejected with known non-delivery semantics."""

    code = "request_rejected"


class BridgeCapabilityUnavailable(ValueError):
    """A requested optional Standard capability was not advertised."""

    code = "capability_unavailable"


class BridgeTransportError(RuntimeError):
    """The transport ended before delivery could be known."""

    code = "transport_unavailable"


class BridgeTimeoutError(BridgeTransportError):
    """The peer did not answer within the adapter's bounded deadline."""

    code = "transport_timeout"


class BridgeAuthorizationError(RuntimeError):
    """Home no longer authorizes the bound endpoint or conversation."""

    def __init__(self, reason: str) -> None:
        self.code = reason
        super().__init__(f"home bridge authorization failed: {reason}")


class BridgeProtocolError(BridgeTransportError):
    """A peer violated the pinned Standard wire shape."""

    code = "protocol_error"


class JsonSocket(Protocol):
    """The narrow JSON-only port used by the Standard gateway adapter."""

    def send_json(self, frame: Mapping[str, object]) -> None: ...

    def receive_json(self, timeout: float | None = None) -> Mapping[str, object]: ...

    def close(self) -> None: ...


class JsonSocketFactory(Protocol):
    def open(self, url: str) -> JsonSocket: ...


class AudioSocket(Protocol):
    """The mixed JSON/binary port for Standard response speech."""

    def send_json(self, frame: Mapping[str, object]) -> None: ...

    def receive(self, timeout: float | None = None) -> object: ...

    def close(self) -> None: ...


class AudioSocketFactory(Protocol):
    def open(self, url: str) -> AudioSocket: ...


@dataclass(frozen=True, slots=True)
class ConversationGrant:
    """Home-owned context resolved from an endpoint's opaque handle."""

    handle: str
    device_id: str
    profile_id: str
    session_id: str | None = None
    status: str = "active"
    credential_generation: int | None = None
    configuration_revision: int | None = None
    interactive_choice: bool = False
    protected_capabilities: frozenset[str] = field(
        default_factory=frozenset, repr=False, compare=False
    )
    capability_revision: int | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class BridgeStatus:
    """The only handshake result exposed at the endpoint boundary."""

    status: str
    conversation_handle: str
    reason: str | None = None
    capabilities: Mapping[str, object] = field(default_factory=dict)
    unresolved_turn: BridgeTurn | None = None

    @property
    def unresolved_turn_id(self) -> str | None:
        return (
            self.unresolved_turn.turn_id if self.unresolved_turn is not None else None
        )

    def to_endpoint(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": 1,
            "status": self.status,
            "conversation_handle": self.conversation_handle,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.capabilities:
            payload["capabilities"] = dict(self.capabilities)
        if self.unresolved_turn is not None:
            payload["unresolved_turn"] = self.unresolved_turn.to_endpoint()
        return payload


@dataclass(frozen=True, slots=True)
class BridgeTurn:
    """An accepted prompt identified only by a Home-owned opaque turn ID."""

    turn_id: str
    conversation_handle: str
    status: str = "submitted"

    def to_endpoint(self) -> dict[str, object]:
        return {
            "schema": 1,
            "conversation_handle": self.conversation_handle,
            "turn_id": self.turn_id,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class BridgeEvent:
    """A Standard event forwarded without changing its event name or payload."""

    conversation_handle: str
    type: str
    payload: Mapping[str, object] = field(repr=False)
    turn_id: str | None = None
    correlation_id: str | None = None
    standard_session_id: str | None = field(default=None, repr=False, compare=False)
    configuration_revision: int | None = field(default=None, repr=False, compare=False)
    choice_capabilities: frozenset[str] = field(
        default_factory=frozenset, repr=False, compare=False
    )
    capability_revision: int | None = field(default=None, repr=False, compare=False)

    def to_endpoint(self) -> dict[str, object]:
        if self.type == "prompt.request":
            raise BridgeProtocolError(
                "typed choice must be projected by Home authority"
            )
        event: dict[str, object] = {
            "type": self.type,
            "payload": endpoint_safe_payload(self.type, self.payload),
        }
        result: dict[str, object] = {
            "schema": 1,
            "conversation_handle": self.conversation_handle,
            "event": event,
        }
        if self.turn_id is not None:
            result["turn_id"] = self.turn_id
        if self.correlation_id is not None:
            result["correlation_id"] = self.correlation_id
        return result


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """One typed frame from the separate Standard response-audio socket."""

    kind: str
    turn_id: str | None = None
    data: bytes = b""
    sample_rate: int | None = None
    channels: int | None = None
    sample_width: int | None = None
    byte_order: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class _ActiveTurn:
    turn_id: str
    generation: int = 0
    terminal: bool = False
    uncertain: bool = False
    interrupt_requested: bool = False
    rendered_preview: str = ""
    audio_text_sent: str = ""
    pending_prompt_type: str | None = None
    pending_prompt_id: str | None = None
    pending_choice_revision: tuple[str, str] | None = field(default=None, repr=False)
    pending_choice_authority: tuple[object, ...] | None = field(
        default=None, repr=False
    )
    choice_revisions: set[tuple[str, str]] = field(default_factory=set, repr=False)
    event_started: bool = False
    event_terminal_seen: bool = False
    event_start_seq: int | None = None
    last_event_seq: int | None = None
    standard_turn_id: str | None = None


class StandardGatewayClient:
    """Single-reader JSON-RPC client for Standard Hermes ``/api/ws``."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        socket_factory: JsonSocketFactory,
        request_id_factory: Callable[[int], str] | None = None,
        connect_timeout: float | None = 10.0,
        request_timeout: float | None = 30.0,
        event_timeout: float | None = 30.0,
    ) -> None:
        self._url = url
        self._token = token
        self._socket_factory = socket_factory
        self._request_id_factory = request_id_factory or (lambda index: f"home-{index}")
        self._connect_timeout = _validate_timeout(connect_timeout, "connect timeout")
        self._request_timeout = _validate_timeout(request_timeout, "request timeout")
        self._event_timeout = _validate_timeout(event_timeout, "event timeout")
        self._connection_lock = RLock()
        self._condition = Condition()
        self._send_lock = Lock()
        self._socket: JsonSocket | None = None
        self._reader_thread: Thread | None = None
        self._closed = True
        self._reader_error: Exception | None = None
        self._next_request_id = 0
        self._inflight_request_ids: set[str | int] = set()
        self._queued_events: deque[dict[str, object]] = deque()
        self._responses: dict[str | int, deque[dict[str, object]]] = {}
        self.ready_payload: dict[str, object] = {}

    @property
    def is_open(self) -> bool:
        with self._condition:
            return (
                self._socket is not None
                and not self._closed
                and self._reader_error is None
            )

    def connect(self) -> dict[str, object]:
        with self._connection_lock:
            deadline = (
                None
                if self._connect_timeout is None
                else time.monotonic() + self._connect_timeout
            )
            with self._condition:
                if self._socket is not None and not self._closed:
                    if self._reader_error is None:
                        return dict(self.ready_payload)
                    old_socket = self._socket
                else:
                    old_socket = None
            if old_socket is not None:
                self.close()

            try:
                socket = _run_bounded(
                    lambda: self._socket_factory.open(
                        _authenticated_url(
                            self._url,
                            self._token,
                            required_path=STANDARD_GATEWAY_PATH,
                        )
                    ),
                    None
                    if deadline is None
                    else _remaining(deadline, "gateway socket setup"),
                    operation_name="gateway socket setup",
                    close_late_result=True,
                )
            except TimeoutError as error:
                raise BridgeTimeoutError(str(error)) from error
            with self._condition:
                self._socket = socket
                self._closed = False
                self._reader_error = None
                self._queued_events.clear()
                self._responses.clear()
                self.ready_payload = {}
            try:
                while True:
                    timeout = (
                        None
                        if deadline is None
                        else _remaining(deadline, "gateway.ready")
                    )
                    frame = self._receive_frame(socket, timeout=timeout)
                    if frame.get("method") == "event":
                        event = _event_from_frame(frame)
                        if event is None:
                            continue
                        if event["type"] == "gateway.ready":
                            payload = event["payload"]
                            if not isinstance(payload, Mapping):
                                raise BridgeProtocolError(
                                    "standard gateway.ready payload is not an object"
                                )
                            self.ready_payload = dict(payload)
                            break
                        with self._condition:
                            self._queued_events.append(event)
                            self._condition.notify_all()
                    else:
                        self._store_response(frame)
            except TimeoutError as error:
                self.close()
                raise BridgeTimeoutError("standard gateway.ready timed out") from error
            except Exception:
                self.close()
                raise

            reader = Thread(
                target=self._read_loop,
                args=(socket,),
                name="hermes-standard-reader",
                daemon=True,
            )
            with self._condition:
                self._reader_thread = reader
            reader.start()
            return dict(self.ready_payload)

    def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        timeout: float | None = None,
    ) -> dict[str, object]:
        socket = self._require_socket()
        request_timeout = (
            self._request_timeout
            if timeout is None
            else _validate_timeout(timeout, "request timeout")
        )
        deadline = (
            None if request_timeout is None else time.monotonic() + request_timeout
        )
        with self._condition:
            self._next_request_id += 1
            request_id = self._request_id_factory(self._next_request_id)
            if type(request_id) is not int and not (
                isinstance(request_id, str) and request_id
            ):
                raise BridgeProtocolError(
                    "standard request ID must be a non-empty string or integer"
                )
            if request_id in self._inflight_request_ids:
                raise BridgeProtocolError(
                    f"duplicate in-flight standard request ID: {request_id}"
                )
            self._inflight_request_ids.add(request_id)
        frame = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": dict(params),
        }
        try:
            with self._send_lock:
                _run_bounded(
                    lambda: socket.send_json(frame),
                    None
                    if deadline is None
                    else _remaining(deadline, "gateway request send"),
                    operation_name="gateway request send",
                )
        except TimeoutError as error:
            typed_error = BridgeTimeoutError("standard gateway request send timed out")
            self._set_reader_error(typed_error, socket)
            self.close()
            with self._condition:
                self._inflight_request_ids.discard(request_id)
            raise typed_error from error
        except Exception as error:
            transport_error = (
                error
                if isinstance(error, BridgeTransportError)
                else BridgeTransportError("standard gateway request send failed")
            )
            if transport_error is not error:
                transport_error.__cause__ = error
            self._set_reader_error(transport_error, socket)
            with self._condition:
                self._inflight_request_ids.discard(request_id)
            raise transport_error from error

        try:
            with self._condition:
                while True:
                    responses = self._responses.get(request_id)
                    if responses:
                        response = responses.popleft()
                        if not responses:
                            self._responses.pop(request_id, None)
                        self._inflight_request_ids.discard(request_id)
                        break
                    if self._socket is not socket or self._closed:
                        raise RuntimeError("standard gateway is not connected")
                    if self._reader_error is not None:
                        raise self._reader_error
                    if deadline is None:
                        self._condition.wait()
                    else:
                        try:
                            self._condition.wait(
                                _remaining(deadline, "gateway response")
                            )
                        except TimeoutError as error:
                            raise BridgeTimeoutError(
                                "standard gateway response timed out"
                            ) from error
        except Exception:
            with self._condition:
                self._inflight_request_ids.discard(request_id)
            raise
        return _result_from_response(response)

    def ping(self, *, timeout: float | None = None) -> dict[str, object]:
        """Exercise the Standard gateway heartbeat operation."""

        return self.request("gateway.ping", {}, timeout=timeout)

    def probe_readiness(self, *, timeout: float | None = None) -> dict[str, object]:
        """Run a fresh gateway readiness probe without creating a Session."""
        readiness_timeout = (
            self._request_timeout
            if timeout is None
            else _validate_timeout(timeout, "readiness timeout")
        )
        deadline = (
            None if readiness_timeout is None else time.monotonic() + readiness_timeout
        )
        ready = self.connect()
        ping_timeout = (
            None if deadline is None else _remaining(deadline, "gateway.ping")
        )
        return {"ready": ready, "ping": self.ping(timeout=ping_timeout)}

    def next_event(self, *, timeout: float | None = None) -> dict[str, object]:
        event_timeout = (
            self._event_timeout
            if timeout is None
            else _validate_timeout(timeout, "event timeout")
        )
        deadline = None if event_timeout is None else time.monotonic() + event_timeout
        with self._condition:
            while True:
                if self._queued_events:
                    return self._queued_events.popleft()
                if self._reader_error is not None:
                    raise self._reader_error
                if self._socket is None or self._closed:
                    raise RuntimeError("standard gateway is not connected")
                if deadline is None:
                    self._condition.wait()
                else:
                    try:
                        self._condition.wait(_remaining(deadline, "gateway event"))
                    except TimeoutError as error:
                        raise BridgeTimeoutError(
                            "standard gateway event timed out"
                        ) from error

    def close(self) -> None:
        with self._connection_lock:
            with self._condition:
                socket = self._socket
                reader = self._reader_thread
                self._socket = None
                self._reader_thread = None
                self._closed = True
                self._reader_error = None
                self._queued_events.clear()
                self._responses.clear()
                self._inflight_request_ids.clear()
                self.ready_payload = {}
                self._condition.notify_all()
            if socket is not None:
                _close_quietly(socket)
            if reader is not None and reader is not current_thread():
                reader.join(timeout=0.5)

    def _read_loop(self, socket: JsonSocket) -> None:
        while True:
            with self._condition:
                if self._closed or self._socket is not socket:
                    return
            try:
                frame = self._receive_frame(socket, timeout=0.1)
                self._queue_frame(frame, socket=socket)
            except TimeoutError:
                continue
            except Exception as error:  # noqa: BLE001 - reader must retain unexpected failures
                if isinstance(error, BridgeTransportError):
                    reader_error = error
                else:
                    reader_error = BridgeTransportError(
                        "standard gateway reader failed"
                    )
                    reader_error.__cause__ = error
                self._set_reader_error(reader_error, socket)
                return

    def _queue_frame(
        self, frame: Mapping[str, object], *, socket: JsonSocket | None = None
    ) -> None:
        if frame.get("method") == "event":
            event = _event_from_frame(frame)
            if event is None:
                return
            if event["type"] == "gateway.ready":
                raise BridgeProtocolError(
                    "standard gateway sent duplicate gateway.ready"
                )
            with self._condition:
                if socket is not None and (self._closed or self._socket is not socket):
                    return
                self._queued_events.append(event)
                self._condition.notify_all()
            return
        self._store_response(frame, socket=socket)

    def _store_response(
        self, frame: Mapping[str, object], *, socket: JsonSocket | None = None
    ) -> None:
        response_id = frame.get("id")
        if type(response_id) not in {str, int}:
            raise BridgeProtocolError("standard gateway response has no valid ID")
        with self._condition:
            if socket is not None and (self._closed or self._socket is not socket):
                return
            self._responses.setdefault(response_id, deque()).append(dict(frame))
            self._condition.notify_all()

    def _set_reader_error(self, error: Exception, socket: JsonSocket) -> None:
        with self._condition:
            if self._closed or self._socket is not socket:
                return
            self._reader_error = error
            self._condition.notify_all()

    def _receive_frame(
        self, socket: JsonSocket, *, timeout: float | None = None
    ) -> dict[str, object]:
        raw = socket.receive_json(timeout=timeout)
        if not isinstance(raw, Mapping):
            raise BridgeProtocolError(
                "standard gateway JSON-RPC frame is not an object"
            )
        frame = dict(raw)
        if frame.get("jsonrpc") != "2.0":
            raise BridgeProtocolError("standard gateway frame is not JSON-RPC 2.0")
        return frame

    def _require_socket(self) -> JsonSocket:
        with self._condition:
            if self._socket is None or self._closed:
                raise RuntimeError("standard gateway is not connected")
            return self._socket


def _validate_timeout(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be positive or None")
    try:
        numeric_value = float(value)
    except OverflowError, ValueError:
        raise ValueError(f"{name} must be positive or None") from None
    if numeric_value <= 0 or not math.isfinite(numeric_value):
        raise ValueError(f"{name} must be positive or None")
    return numeric_value


def _run_bounded(
    operation: Callable[[], object],
    timeout: float | None,
    *,
    operation_name: str,
    close_late_result: bool = False,
) -> object:
    """Run a blocking injected port operation without losing the caller's deadline."""

    if timeout is None:
        return operation()

    done = Event()
    state_lock = Lock()
    state: dict[str, object] = {"finished": False, "timed_out": False}

    def run() -> None:
        value: object | None = None
        error: Exception | None = None
        try:
            value = operation()
        except Exception as caught:  # noqa: BLE001 - injected ports must not kill the reader
            error = caught
        with state_lock:
            state["finished"] = True
            if state["timed_out"]:
                if close_late_result and value is not None:
                    _close_quietly(value)
            else:
                state["value"] = value
                state["error"] = error
            done.set()

    Thread(target=run, name=f"hermes-standard-{operation_name}", daemon=True).start()
    if not done.wait(timeout):
        with state_lock:
            if state["finished"]:
                pass
            else:
                state["timed_out"] = True
                raise TimeoutError(f"{operation_name} timed out")

    with state_lock:
        error = state.get("error")
        value = state.get("value")
    if error is not None:
        raise error
    return value


def _close_quietly(resource: object) -> None:
    try:
        close = resource.close  # type: ignore[attr-defined]
    except AttributeError:
        return
    try:
        close()
    except Exception:  # noqa: BLE001 - cleanup is best effort
        return


def _remaining(deadline: float, operation: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f"{operation} timed out")
    return remaining


def _result_from_response(frame: Mapping[str, object]) -> dict[str, object]:
    if "error" in frame and "result" in frame:
        raise BridgeProtocolError(
            "standard gateway response contains both result and error"
        )
    if "error" in frame:
        error = frame.get("error")
        if isinstance(error, Mapping):
            raise GatewayRPCError(error.get("code"), error.get("message"))
        raise GatewayRPCError(None, "standard gateway request failed")
    if "result" not in frame:
        raise BridgeProtocolError(
            "standard gateway response has neither result nor error"
        )
    result = frame["result"]
    if not isinstance(result, Mapping):
        raise BridgeProtocolError("standard gateway result is not an object")
    return dict(result)


class HomeBridge:
    """Authorize an endpoint and bind its opaque handle to one Standard Session."""

    def __init__(
        self,
        *,
        gateway_url: str,
        hermes_token: str,
        device_authenticator,
        conversation_resolver: Callable[[str, str], ConversationGrant | None],
        gateway_socket_factory: JsonSocketFactory,
        audio_socket_factory: AudioSocketFactory | None = None,
        session_persistor: Callable[[ConversationGrant, str], None] | None = None,
        conversation_closer: Callable[..., object] | None = None,
        activity_recorder: Callable[[str, str, str], None] | None = None,
        conversation_opener: Callable[[str, str], None] | None = None,
        conversation_disconnector: Callable[[str, str], None] | None = None,
        revocation_registrar: Callable[[str, Callable[[str], None]], bool]
        | None = None,
        revocation_unregistrar: Callable[[str, Callable[[str], None]], None]
        | None = None,
        request_id_factory: Callable[[int], str] | None = None,
        turn_id_factory: Callable[[int], str] | None = None,
        audio_timeout: float | None = 30.0,
    ) -> None:
        self._gateway_url = gateway_url
        self._hermes_token = hermes_token
        self._device_authenticator = device_authenticator
        self._conversation_resolver = conversation_resolver
        self._gateway_socket_factory = gateway_socket_factory
        self._audio_socket_factory = audio_socket_factory
        self._session_persistor = session_persistor
        self._conversation_closer = conversation_closer
        self._activity_recorder = activity_recorder
        self._conversation_opener = conversation_opener
        self._conversation_disconnector = conversation_disconnector
        self._revocation_registrar = revocation_registrar
        self._revocation_unregistrar = revocation_unregistrar
        self._revocation_handler = self._on_claim_revoked
        self._registered_handle: str | None = None
        self._request_id_factory = request_id_factory
        self._turn_id_factory = turn_id_factory or (lambda index: f"home-turn-{index}")
        self._audio_timeout = _validate_timeout(audio_timeout, "audio timeout")
        self._lifecycle_lock = RLock()
        self._state_lock = RLock()
        self._event_processing_lock = Lock()
        self._prompt_response_lock = Lock()
        self._audio_send_lock = Lock()
        self._audio_read_lock = Lock()
        self._gateway: StandardGatewayClient | None = None
        self._audio_socket: AudioSocket | None = None
        self._audio_owner: _ActiveTurn | None = None
        self._audio_started = False
        self._audio_pcm_remainder = b""
        self._grant: ConversationGrant | None = None
        self._conversation_handle: str | None = None
        self._endpoint_headers: dict[str, str] = {}
        self._device_id: str | None = None
        self._runtime_session_id: str | None = None
        self._resume_session_id: str | None = None
        # A new Session's durable ID is written to the grant only after Standard
        # accepts a prompt: Hermes does not store an empty Session, and resuming
        # a reaped, never-stored ID would reject every later open.
        self._unpersisted_session_id: str | None = None
        self._advertised_commands: frozenset[str] = frozenset()
        self._endpoint_capabilities: dict[str, object] = {}
        self._active_turn: _ActiveTurn | None = None
        self._unresolved_turn: BridgeTurn | None = None
        self._last_terminal_event_seq: int | None = None
        self._last_binding_failure_reason: str | None = None
        self._turn_count = 0
        self._state = "disconnected"

    @property
    def active_turn_id(self) -> str | None:
        with self._state_lock:
            return self._active_turn.turn_id if self._active_turn is not None else None

    @property
    def unresolved_turn(self) -> BridgeTurn | None:
        with self._state_lock:
            return self._unresolved_turn

    @property
    def unresolved_turn_id(self) -> str | None:
        with self._state_lock:
            return (
                self._unresolved_turn.turn_id
                if self._unresolved_turn is not None
                else None
            )

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    def open(
        self,
        *,
        headers: Mapping[str, str],
        conversation_handle: str,
    ) -> BridgeStatus:
        with self._lifecycle_lock:
            return self._open(headers=headers, conversation_handle=conversation_handle)

    def _open(
        self,
        *,
        headers: Mapping[str, str],
        conversation_handle: str,
    ) -> BridgeStatus:
        if not isinstance(conversation_handle, str) or not conversation_handle.strip():
            with self._state_lock:
                has_binding = self._grant is not None or self._gateway is not None
                if not has_binding:
                    self._state = "unavailable"
            return BridgeStatus(
                "unavailable", str(conversation_handle), "invalid_request"
            )

        try:
            device_id, credential_generation = self._authenticate_device(headers)
        except OSError, RuntimeError, TypeError, ValueError:
            return self._open_failure(conversation_handle, "authorization_unavailable")
        if device_id is None:
            return self._open_failure(conversation_handle, "unauthorized")

        with self._state_lock:
            if self._active_turn is not None:
                return BridgeStatus(
                    "unavailable",
                    conversation_handle,
                    "turn_active",
                    unresolved_turn=self._unresolved_turn,
                )
            if self._state == "turn_uncertain":
                return BridgeStatus(
                    "unavailable",
                    conversation_handle,
                    "reconnect_required",
                    unresolved_turn=self._unresolved_turn,
                )
            reconnect_existing = (
                self._conversation_handle == conversation_handle
                and self._grant is not None
            )
            if not reconnect_existing and self._gateway is None and self._grant is None:
                self._state = "connecting"
        if reconnect_existing:
            return self.reconnect(headers=headers)

        try:
            grant = self._conversation_resolver(conversation_handle, device_id)
        except OSError, RuntimeError, TypeError, ValueError:
            return self._open_failure(conversation_handle, "authorization_unavailable")
        if grant is None or not isinstance(grant, ConversationGrant):
            return self._open_failure(conversation_handle, "stale_conversation")
        if grant.status != "active":
            return self._open_failure(conversation_handle, "stale_conversation")
        if grant.device_id != device_id or grant.handle != conversation_handle:
            return self._open_failure(conversation_handle, "unauthorized")
        if grant.credential_generation != credential_generation:
            self._close_claim(grant.handle, grant.device_id, reason="endpoint_revoked")
            return self._open_failure(conversation_handle, "stale_conversation")
        if not self._register_revocation_handler(grant.handle):
            return self._failed_open(grant, conversation_handle, "stale_conversation")

        gateway = StandardGatewayClient(
            url=self._gateway_url,
            token=self._hermes_token,
            socket_factory=self._gateway_socket_factory,
            request_id_factory=self._request_id_factory,
        )
        try:
            ready_payload = gateway.connect()
            advertised_commands = self._discover_commands(gateway)
            operation = "session.resume" if grant.session_id else "session.create"
            params: dict[str, object] = {
                "source": "home",
                "profile": grant.profile_id,
            }
            if grant.session_id:
                params["session_id"] = grant.session_id
            session = gateway.request(operation, params)
            runtime_session_id, durable_session_id = _session_identity(session)
            if not _validate_profile_binding(session, grant.profile_id):
                gateway.close()
                return self._open_failure(conversation_handle, "conversation_mismatch")
        except BridgeTimeoutError:
            gateway.close()
            return self._failed_open(grant, conversation_handle, "hermes_timeout")
        except GatewayRPCError:
            gateway.close()
            return self._failed_open(grant, conversation_handle, "request_rejected")
        except BridgeProtocolError:
            gateway.close()
            return self._failed_open(grant, conversation_handle, "protocol_error")
        except ConnectionError, OSError, RuntimeError, TypeError, ValueError:
            gateway.close()
            return self._failed_open(grant, conversation_handle, "hermes_unavailable")
        if grant.session_id and durable_session_id not in (None, grant.session_id):
            gateway.close()
            return self._failed_open(
                grant, conversation_handle, "conversation_mismatch"
            )
        unpersisted_session_id = None
        try:
            refreshed_device_id, refreshed_generation = self._authenticate_device(
                headers
            )
        except OSError, RuntimeError, TypeError, ValueError:
            gateway.close()
            return self._failed_open(
                grant, conversation_handle, "authorization_unavailable"
            )
        if (
            refreshed_device_id != device_id
            or refreshed_generation != credential_generation
        ):
            gateway.close()
            self._close_claim(grant.handle, grant.device_id, reason="endpoint_revoked")
            return self._open_failure(conversation_handle, "stale_conversation")
        if durable_session_id is not None and not grant.session_id:
            unpersisted_session_id = durable_session_id
        elif durable_session_id is not None:
            try:
                if self._session_persistor is not None:
                    self._session_persistor(grant, durable_session_id)
                grant = replace(grant, session_id=durable_session_id)
            except OSError, RuntimeError, TypeError, ValueError:
                self._interrupt_created_session(gateway, runtime_session_id)
                return self._failed_open(
                    grant, conversation_handle, "authorization_unavailable"
                )
        if self._conversation_opener is not None:
            try:
                self._conversation_opener(grant.handle, grant.device_id)
            except OSError, RuntimeError, TypeError, ValueError:
                self._interrupt_created_session(gateway, runtime_session_id)
                return self._failed_open(
                    grant, conversation_handle, "authorization_unavailable"
                )

        endpoint_capabilities = _capabilities(
            ready_payload,
            commands=advertised_commands,
            audio=self._audio_socket_factory is not None,
            interactive_choice=grant.interactive_choice,
        )
        with self._state_lock:
            if self._active_turn is not None:
                self._mark_disconnected(grant.handle, grant.device_id)
                gateway.close()
                return BridgeStatus(
                    "unavailable",
                    conversation_handle,
                    "turn_active",
                    unresolved_turn=self._unresolved_turn,
                )
            if self._state == "turn_uncertain":
                self._mark_disconnected(grant.handle, grant.device_id)
                gateway.close()
                return BridgeStatus(
                    "unavailable",
                    conversation_handle,
                    "reconnect_required",
                    unresolved_turn=self._unresolved_turn,
                )
            old_gateway = self._gateway
            old_audio = self._audio_socket
            old_handle = self._conversation_handle
            old_device_id = self._device_id
            self._gateway = gateway
            self._grant = grant
            self._conversation_handle = conversation_handle
            self._endpoint_headers = dict(headers)
            self._device_id = device_id
            self._runtime_session_id = runtime_session_id
            self._resume_session_id = durable_session_id or grant.session_id
            self._unpersisted_session_id = unpersisted_session_id
            self._advertised_commands = frozenset(advertised_commands)
            self._endpoint_capabilities = dict(endpoint_capabilities)
            self._audio_socket = None
            self._audio_owner = None
            self._audio_started = False
            self._audio_pcm_remainder = b""
            self._unresolved_turn = None
            self._last_terminal_event_seq = None
            self._state = "ready"
        if (old_handle, old_device_id) != (conversation_handle, device_id):
            self._mark_disconnected(old_handle, old_device_id)
        if old_audio is not None:
            _close_quietly(old_audio)
        if old_gateway is not None and old_gateway is not gateway:
            old_gateway.close()
        return BridgeStatus(
            "ready",
            conversation_handle,
            capabilities=endpoint_capabilities,
        )

    def _open_failure(self, conversation_handle: str, reason: str) -> BridgeStatus:
        """Return an open failure without destroying an already-bound conversation."""

        self._unregister_revocation_handler(conversation_handle)
        with self._state_lock:
            has_binding = self._grant is not None or self._gateway is not None
            if not has_binding:
                self._state = "unavailable"
            unresolved_turn = (
                self._unresolved_turn
                if self._conversation_handle == conversation_handle
                else None
            )
        return BridgeStatus(
            "unavailable",
            conversation_handle,
            reason,
            unresolved_turn=unresolved_turn,
        )

    def reconnect(self, *, headers: Mapping[str, str]) -> BridgeStatus:
        with self._lifecycle_lock:
            return self._reconnect(headers=headers)

    def reauthorize(self, *, headers: Mapping[str, str]) -> BridgeStatus:
        """Reauthorize a replacement endpoint without touching the live Hermes turn."""

        with self._lifecycle_lock:
            with self._state_lock:
                handle = self._conversation_handle
                grant = self._grant
                active = self._active_turn
                ready = (
                    self._state == "ready"
                    and self._gateway is not None
                    and self._runtime_session_id is not None
                )
            if handle is None or grant is None or not ready:
                return BridgeStatus(
                    "unavailable",
                    handle or "",
                    "stale_conversation",
                    unresolved_turn=self._unresolved_turn,
                )
            try:
                device_id = self._device_authenticator.authenticate_device(headers)
                refreshed = (
                    None
                    if device_id is None
                    else self._conversation_resolver(handle, device_id)
                )
            except OSError, RuntimeError, TypeError, ValueError:
                return BridgeStatus("unavailable", handle, "authorization_unavailable")
            if device_id is None:
                return BridgeStatus("unavailable", handle, "unauthorized")
            if refreshed is None or not isinstance(refreshed, ConversationGrant):
                return BridgeStatus("unavailable", handle, "stale_conversation")
            if refreshed.status != "active":
                return BridgeStatus("unavailable", handle, "stale_conversation")
            if (
                refreshed.device_id != device_id
                or refreshed.handle != handle
                or refreshed.profile_id != grant.profile_id
                or refreshed.session_id not in (None, "", self._resume_session_id)
            ):
                return BridgeStatus("unavailable", handle, "conversation_mismatch")
            with self._state_lock:
                if self._active_turn is not active or self._state != "ready":
                    return BridgeStatus("unavailable", handle, "stale_conversation")
                self._grant = refreshed
                self._endpoint_headers = dict(headers)
                self._device_id = device_id
                unresolved = (
                    BridgeTurn(active.turn_id, handle, "streaming")
                    if active is not None and not active.terminal
                    else self._unresolved_turn
                )
                capabilities = dict(self._endpoint_capabilities)
            return BridgeStatus(
                "ready",
                handle,
                capabilities=capabilities,
                unresolved_turn=unresolved,
            )

    def _reconnect(self, *, headers: Mapping[str, str]) -> BridgeStatus:
        """Resume the bound Standard Session without resubmitting any turn."""

        with self._state_lock:
            handle = self._conversation_handle
            grant = self._grant
            resume_session_id = self._resume_session_id
            if handle is None or grant is None:
                self._state = "unavailable"
                return BridgeStatus(
                    "unavailable",
                    handle or "",
                    "stale_conversation",
                    unresolved_turn=self._unresolved_turn,
                )
            active = self._active_turn
            if active is not None:
                if not active.terminal:
                    self._remember_uncertain_turn_locked(active)
                self._active_turn = None
            self._state = "reconnecting"

        try:
            device_id, credential_generation = self._authenticate_device(headers)
        except OSError, RuntimeError, TypeError, ValueError:
            return self._reconnect_failure(handle, "authorization_unavailable")
        if device_id is None:
            self._close_claim(handle, grant.device_id, reason="endpoint_revoked")
            return self._reconnect_failure(handle, "unauthorized")
        if device_id != grant.device_id:
            self._close_claim(handle, grant.device_id, reason="endpoint_revoked")
            return self._reconnect_failure(handle, "unauthorized")
        try:
            refreshed = self._conversation_resolver(handle, device_id)
        except OSError, RuntimeError, TypeError, ValueError:
            return self._reconnect_failure(handle, "authorization_unavailable")
        if refreshed is None or not isinstance(refreshed, ConversationGrant):
            self._close_claim(handle, grant.device_id, reason="authorization_revoked")
            return self._reconnect_failure(handle, "stale_conversation")
        if refreshed.status != "active":
            self._close_claim(handle, grant.device_id, reason="authorization_revoked")
            return self._reconnect_failure(handle, "stale_conversation")
        if refreshed.device_id != device_id or refreshed.handle != handle:
            self._close_claim(handle, grant.device_id, reason="endpoint_revoked")
            return self._reconnect_failure(handle, "unauthorized")
        if (
            refreshed.credential_generation != credential_generation
            or refreshed.credential_generation != grant.credential_generation
        ):
            self._close_claim(handle, grant.device_id, reason="endpoint_revoked")
            return self._reconnect_failure(handle, "stale_conversation")
        if refreshed.profile_id != grant.profile_id:
            self._close_claim(handle, grant.device_id, reason="authorization_revoked")
            return self._reconnect_failure(handle, "conversation_mismatch")
        if (
            refreshed.session_id not in (None, "")
            and refreshed.session_id != resume_session_id
        ):
            self._close_claim(handle, grant.device_id, reason="authorization_revoked")
            return self._reconnect_failure(handle, "conversation_mismatch")
        if resume_session_id is None:
            self._close_claim(handle, grant.device_id, reason="session_startup_failed")
            return self._reconnect_failure(handle, "stale_conversation")

        self._drop_transport()

        gateway = StandardGatewayClient(
            url=self._gateway_url,
            token=self._hermes_token,
            socket_factory=self._gateway_socket_factory,
            request_id_factory=self._request_id_factory,
        )
        try:
            ready_payload = gateway.connect()
            advertised_commands = self._discover_commands(gateway)
            session = gateway.request(
                "session.resume",
                {
                    "session_id": resume_session_id,
                    "source": "home",
                    "profile": refreshed.profile_id,
                },
            )
            runtime_session_id, durable_session_id = _session_identity(session)
            if not _validate_profile_binding(session, refreshed.profile_id):
                gateway.close()
                return self._reconnect_failure(handle, "conversation_mismatch")
        except BridgeTimeoutError:
            gateway.close()
            return self._reconnect_failure(handle, "hermes_timeout")
        except GatewayRPCError:
            gateway.close()
            return self._reconnect_failure(handle, "request_rejected")
        except BridgeProtocolError:
            gateway.close()
            return self._reconnect_failure(handle, "protocol_error")
        except ConnectionError, OSError, RuntimeError, TypeError, ValueError:
            gateway.close()
            return self._reconnect_failure(handle, "hermes_unavailable")
        if durable_session_id not in (None, resume_session_id):
            gateway.close()
            return self._reconnect_failure(handle, "conversation_mismatch")
        try:
            refreshed_device_id, refreshed_generation = self._authenticate_device(
                headers
            )
        except OSError, RuntimeError, TypeError, ValueError:
            gateway.close()
            return self._reconnect_failure(handle, "authorization_unavailable")
        if (
            refreshed_device_id != device_id
            or refreshed_generation != credential_generation
        ):
            gateway.close()
            self._close_claim(handle, grant.device_id, reason="endpoint_revoked")
            return self._reconnect_failure(handle, "stale_conversation")
        if durable_session_id is not None and refreshed.session_id:
            try:
                if self._session_persistor is not None:
                    self._session_persistor(refreshed, durable_session_id)
                refreshed = replace(refreshed, session_id=durable_session_id)
            except OSError, RuntimeError, TypeError, ValueError:
                gateway.close()
                return self._reconnect_failure(handle, "authorization_unavailable")
        if self._conversation_opener is not None:
            try:
                self._conversation_opener(handle, device_id)
            except OSError, RuntimeError, TypeError, ValueError:
                self._interrupt_created_session(gateway, runtime_session_id)
                self._close_claim(handle, device_id, reason="authorization_revoked")
                return self._reconnect_failure(handle, "authorization_unavailable")

        endpoint_capabilities = _capabilities(
            ready_payload,
            commands=advertised_commands,
            audio=self._audio_socket_factory is not None,
            interactive_choice=refreshed.interactive_choice,
        )
        with self._state_lock:
            self._gateway = gateway
            self._grant = refreshed
            self._endpoint_headers = dict(headers)
            self._device_id = device_id
            self._runtime_session_id = runtime_session_id
            self._resume_session_id = (
                str(durable_session_id) if durable_session_id else resume_session_id
            )
            if refreshed.session_id:
                self._unpersisted_session_id = None
            elif self._unpersisted_session_id is None:
                self._unpersisted_session_id = self._resume_session_id
            self._advertised_commands = frozenset(advertised_commands)
            self._endpoint_capabilities = dict(endpoint_capabilities)
            self._state = "ready"
            unresolved_turn = self._unresolved_turn
        return BridgeStatus(
            "ready",
            handle,
            capabilities=endpoint_capabilities,
            unresolved_turn=unresolved_turn,
        )

    def _reconnect_failure(self, handle: str, reason: str) -> BridgeStatus:
        self._drop_transport()
        with self._state_lock:
            self._state = "unavailable"
            unresolved_turn = self._unresolved_turn
        return BridgeStatus(
            "unavailable",
            handle,
            reason,
            unresolved_turn=unresolved_turn,
        )

    @staticmethod
    def _discover_commands(gateway: StandardGatewayClient) -> list[str]:
        """Read the pinned Standard command catalog without guessing commands."""

        try:
            catalog = gateway.request("commands.catalog", {})
            return _catalog_commands(catalog)
        except BridgeProtocolError, BridgeTimeoutError, GatewayRPCError:
            return []

    def _revalidate_ready_binding(self) -> tuple[StandardGatewayClient, str]:
        """Recheck the Home claim before an operation uses a ready transport."""

        with self._state_lock:
            if (
                self._state != "ready"
                or self._gateway is None
                or self._runtime_session_id is None
            ):
                raise RuntimeError("home bridge is not ready")
            gateway = self._gateway
            runtime_session_id = self._runtime_session_id
            handle = self._conversation_handle
            grant = self._grant
            endpoint_headers = dict(self._endpoint_headers)
            expected_device_id = self._device_id
            expected_resume_id = self._resume_session_id

        if handle is None or grant is None or expected_device_id is None:
            self._invalidate_binding("unauthorized", gateway=gateway)
            raise BridgeAuthorizationError("unauthorized")
        try:
            device_id, credential_generation = self._authenticate_device(
                endpoint_headers
            )
        except Exception as error:
            self._invalidate_binding("authorization_unavailable", gateway=gateway)
            raise BridgeAuthorizationError("authorization_unavailable") from error
        if device_id is None or device_id != expected_device_id:
            self._invalidate_binding("unauthorized", gateway=gateway)
            raise BridgeAuthorizationError("unauthorized")
        try:
            refreshed = self._conversation_resolver(handle, device_id)
        except Exception as error:
            self._invalidate_binding("authorization_unavailable", gateway=gateway)
            raise BridgeAuthorizationError("authorization_unavailable") from error
        if refreshed is None or not isinstance(refreshed, ConversationGrant):
            self._invalidate_binding("stale_conversation", gateway=gateway)
            raise BridgeAuthorizationError("stale_conversation")
        if refreshed.status != "active":
            self._invalidate_binding("stale_conversation", gateway=gateway)
            raise BridgeAuthorizationError("stale_conversation")
        if refreshed.device_id != device_id or refreshed.handle != handle:
            self._invalidate_binding("unauthorized", gateway=gateway)
            raise BridgeAuthorizationError("unauthorized")
        if (
            refreshed.credential_generation != credential_generation
            or refreshed.credential_generation != grant.credential_generation
        ):
            self._invalidate_binding("stale_conversation", gateway=gateway)
            raise BridgeAuthorizationError("stale_conversation")
        if refreshed.configuration_revision != grant.configuration_revision:
            self._invalidate_binding("stale_conversation", gateway=gateway)
            raise BridgeAuthorizationError("stale_conversation")
        if refreshed.profile_id != grant.profile_id:
            self._invalidate_binding("conversation_mismatch", gateway=gateway)
            raise BridgeAuthorizationError("conversation_mismatch")
        if (
            refreshed.session_id not in (None, "")
            and refreshed.session_id != expected_resume_id
        ):
            self._invalidate_binding("conversation_mismatch", gateway=gateway)
            raise BridgeAuthorizationError("conversation_mismatch")

        with self._state_lock:
            if (
                self._gateway is not gateway
                or self._runtime_session_id != runtime_session_id
                or self._grant is not grant
                or self._state != "ready"
            ):
                raise BridgeTransportError("bridge changed during Home authorization")
            self._grant = refreshed
        return gateway, runtime_session_id

    def _binding_is_current(
        self,
        *,
        gateway: StandardGatewayClient,
        runtime_session_id: str,
        active: _ActiveTurn | None = None,
    ) -> bool:
        with self._state_lock:
            return (
                self._state == "ready"
                and self._gateway is gateway
                and self._runtime_session_id == runtime_session_id
                and (active is None or self._active_turn is active)
            )

    def _invalidate_binding(
        self, reason: str, *, gateway: StandardGatewayClient | None = None
    ) -> None:
        with self._state_lock:
            if gateway is not None and self._gateway is not gateway:
                return
            self._last_binding_failure_reason = reason
            current_active = self._active_turn
            if current_active is not None and not current_active.terminal:
                self._remember_uncertain_turn_locked(current_active)
            self._active_turn = None
            gateway_to_close = self._gateway
            runtime_session_id = self._runtime_session_id
            grant = self._grant
            device_id = self._device_id
            self._gateway = None
            self._runtime_session_id = None
            self._resume_session_id = None
            self._unpersisted_session_id = None
            self._advertised_commands = frozenset()
            self._endpoint_headers = {}
            self._state = "unavailable"
        self._close_audio()
        self._mark_disconnected(
            grant.handle if grant is not None else None,
            device_id,
        )
        if (
            reason in {"unauthorized", "stale_conversation", "conversation_mismatch"}
            and grant is not None
            and device_id is not None
        ):
            self._close_claim(grant.handle, device_id, reason="authorization_revoked")
            with self._state_lock:
                if self._grant is grant:
                    self._grant = None
                    self._resume_session_id = None
            self._unregister_revocation_handler(grant.handle)
        if gateway_to_close is not None and runtime_session_id is not None:
            try:
                gateway_to_close.request(
                    "session.interrupt",
                    {"session_id": runtime_session_id},
                    timeout=1.0,
                )
            except Exception as error:  # noqa: BLE001 - revocation must still close locally
                del error
        if gateway_to_close is not None:
            gateway_to_close.close()

    def _authenticate_device(
        self, headers: Mapping[str, str]
    ) -> tuple[str | None, int | None]:
        authenticate_context = getattr(
            self._device_authenticator, "authenticate_device_context", None
        )
        if callable(authenticate_context):
            context = authenticate_context(headers)
            if context is None:
                return None, None
            device_id = getattr(context, "device_id", None)
            generation = getattr(context, "generation", None)
            if (
                type(device_id) is not str
                or not device_id
                or type(generation) is not int
                or generation < 1
            ):
                raise ValueError("authenticated device context is invalid")
            return device_id, generation
        return self._device_authenticator.authenticate_device(headers), None

    def _close_claim(self, handle: str, device_id: str, *, reason: str) -> bool:
        closer = self._conversation_closer
        if closer is None:
            return True
        try:
            result = closer(handle, device_id, reason=reason)
        except Exception:  # noqa: BLE001 - cleanup must not expose claim internals
            return False
        return result is not False

    def _register_revocation_handler(self, handle: str) -> bool:
        registrar = self._revocation_registrar
        if registrar is None:
            return True
        try:
            registered = registrar(handle, self._revocation_handler)
        except Exception:  # noqa: BLE001 - fail closed before opening Standard
            return False
        if not registered:
            return False
        self._registered_handle = handle
        return True

    def _unregister_revocation_handler(self, handle: str) -> None:
        if self._registered_handle != handle:
            return
        self._registered_handle = None
        unregistrar = self._revocation_unregistrar
        if unregistrar is None:
            return
        try:
            unregistrar(handle, self._revocation_handler)
        except Exception:
            LOGGER.warning(
                "could not unregister conversation revocation handler", exc_info=True
            )

    @staticmethod
    def _interrupt_created_session(
        gateway: StandardGatewayClient,
        runtime_session_id: str,
    ) -> None:
        try:
            gateway.request(
                "session.interrupt",
                {"session_id": runtime_session_id},
                timeout=1.0,
            )
        except Exception:
            LOGGER.warning(
                "could not interrupt unbound Standard session", exc_info=True
            )
        gateway.close()

    def _on_claim_revoked(self, reason: str) -> None:
        """Stop a live Standard Session after its durable claim is revoked."""

        del reason
        with self._lifecycle_lock:
            with self._state_lock:
                handle = self._conversation_handle
                gateway = self._gateway
                runtime_session_id = self._runtime_session_id
            if gateway is not None and runtime_session_id is not None:
                try:
                    gateway.request(
                        "session.interrupt",
                        {"session_id": runtime_session_id},
                        timeout=1.0,
                    )
                except Exception:
                    LOGGER.warning(
                        "could not interrupt Standard session after claim revocation",
                        exc_info=True,
                    )
            self._close_audio()
            if gateway is not None:
                gateway.close()
            with self._state_lock:
                self._gateway = None
                self._runtime_session_id = None
                self._resume_session_id = None
                self._endpoint_headers = {}
                self._advertised_commands = frozenset()
                self._active_turn = None
                self._unresolved_turn = None
                self._grant = None
                self._last_binding_failure_reason = "stale_conversation"
                self._state = "unavailable"
            if handle is not None:
                self._unregister_revocation_handler(handle)

    def _failed_open(
        self, grant: ConversationGrant, handle: str, reason: str
    ) -> BridgeStatus:
        closed = self._close_claim(
            grant.handle, grant.device_id, reason="session_startup_failed"
        )
        return self._open_failure(
            handle, reason if closed else "authorization_unavailable"
        )

    def _record_activity(self, state: str) -> None:
        recorder = self._activity_recorder
        if recorder is None:
            return
        with self._state_lock:
            handle = self._conversation_handle
            device_id = self._device_id
        if handle is None or device_id is None:
            raise BridgeAuthorizationError("stale_conversation")
        try:
            recorder(handle, device_id, state)
        except ValueError as error:
            raise BridgeRequestRejected("conversation activity was rejected") from error
        except (OSError, RuntimeError, TypeError) as error:
            raise BridgeAuthorizationError("authorization_unavailable") from error

    def _set_idle_after_known_failure(self) -> None:
        try:
            self._record_activity("idle")
        except BridgeAuthorizationError:
            self._invalidate_binding("authorization_unavailable")

    def report_activity(self, state: str) -> bool:
        """Record content-free endpoint activity for this bound conversation."""
        if state not in {"capture", "turn", "playback", "playback_complete"}:
            raise ValueError("endpoint activity state is invalid")
        self._revalidate_ready_binding()
        self._record_activity(state)
        return True

    def close_conversation(self) -> bool:
        """Close the logical claim while retaining route transport semantics."""
        with self._lifecycle_lock:
            with self._state_lock:
                handle = self._conversation_handle
                device_id = self._device_id
                gateway = self._gateway
                runtime_session_id = self._runtime_session_id
                active = self._active_turn
            if handle is None or device_id is None:
                raise BridgeAuthorizationError("stale_conversation")
            if (
                active is not None
                and not active.terminal
                and gateway is not None
                and runtime_session_id is not None
            ):
                try:
                    gateway.request(
                        "session.interrupt",
                        {"session_id": runtime_session_id},
                        timeout=1.0,
                    )
                except Exception as error:  # noqa: BLE001 - close still proceeds
                    del error
            if not self._close_claim(handle, device_id, reason="stopped"):
                self._drop_transport()
                with self._state_lock:
                    self._state = "unavailable"
                raise BridgeAuthorizationError("authorization_unavailable")
            self._drop_transport()
            self._unregister_revocation_handler(handle)
            with self._state_lock:
                self._grant = None
                self._conversation_handle = None
                self._endpoint_headers = {}
                self._device_id = None
                self._resume_session_id = None
                self._active_turn = None
                self._unresolved_turn = None
                self._runtime_session_id = None
                self._advertised_commands = frozenset()
                self._state = "disconnected"
            return True

    def dispatch_command(self, name: str, arg: str | None = None) -> dict[str, object]:
        """Forward only an explicitly advertised Standard command."""

        gateway, runtime_session_id = self._revalidate_ready_binding()
        with self._state_lock:
            if (
                self._state != "ready"
                or self._gateway is None
                or self._runtime_session_id != runtime_session_id
            ):
                raise RuntimeError("home bridge is not ready")
            if not isinstance(name, str) or not name:
                raise ValueError("command name must be a non-empty string")
            if name not in self._advertised_commands:
                raise BridgeCapabilityUnavailable(
                    "command is not advertised by Standard Hermes"
                )
            params: dict[str, object] = {
                "session_id": runtime_session_id,
                "name": name,
            }
        if arg is not None:
            params["arg"] = arg
        try:
            result = gateway.request("command.dispatch", params)
        except GatewayRPCError as error:
            if not self._binding_is_current(
                gateway=gateway, runtime_session_id=runtime_session_id
            ):
                raise BridgeTransportError(
                    "bridge changed during command delivery"
                ) from error
            raise BridgeRequestRejected(
                f"standard gateway rejected command: {error.message}"
            ) from error
        except BridgeProtocolError:
            self._mark_transport_loss(gateway=gateway)
            raise
        except BridgeTimeoutError as error:
            self._mark_transport_loss(gateway=gateway)
            raise BridgeTimeoutError("command delivery timed out") from error
        except (ConnectionError, OSError, RuntimeError, TypeError, ValueError) as error:
            self._mark_transport_loss(gateway=gateway)
            raise BridgeTransportError("command delivery is unavailable") from error
        if not self._binding_is_current(
            gateway=gateway, runtime_session_id=runtime_session_id
        ):
            raise BridgeTransportError("bridge changed during command delivery")
        if _is_rejected_result(result):
            raise BridgeRequestRejected("standard gateway rejected command")
        return result

    def ping(self) -> dict[str, object]:
        """Check that the ready Standard gateway is still live."""

        gateway, runtime_session_id = self._revalidate_ready_binding()
        with self._state_lock:
            if (
                self._state != "ready"
                or self._gateway is None
                or self._runtime_session_id != runtime_session_id
            ):
                raise RuntimeError("home bridge is not ready")
        try:
            result = gateway.ping()
        except GatewayRPCError as error:
            if not self._binding_is_current(
                gateway=gateway, runtime_session_id=runtime_session_id
            ):
                raise BridgeTransportError(
                    "bridge changed during liveness check"
                ) from error
            raise BridgeRequestRejected(
                f"standard gateway rejected ping: {error.message}"
            ) from error
        except BridgeProtocolError:
            self._mark_transport_loss(gateway=gateway)
            raise
        except BridgeTimeoutError as error:
            self._mark_transport_loss(gateway=gateway)
            raise BridgeTimeoutError("gateway liveness check timed out") from error
        except (ConnectionError, OSError, RuntimeError, TypeError, ValueError) as error:
            self._mark_transport_loss(gateway=gateway)
            raise BridgeTransportError("gateway liveness check failed") from error
        if not self._binding_is_current(
            gateway=gateway, runtime_session_id=runtime_session_id
        ):
            raise BridgeTransportError("bridge changed during liveness check")
        if _is_rejected_result(result):
            raise BridgeRequestRejected("standard gateway rejected ping")
        return result

    def respond_prompt(
        self, event: BridgeEvent, response: Mapping[str, object]
    ) -> dict[str, object]:
        with self._prompt_response_lock:
            return self._respond_prompt(event, response)

    def _respond_prompt(
        self, event: BridgeEvent, response: Mapping[str, object]
    ) -> dict[str, object]:
        """Resolve one correlated Standard structured prompt."""

        gateway, runtime_session_id = self._revalidate_ready_binding()
        operation_info = _STRUCTURED_PROMPT_OPERATIONS.get(event.type)
        if operation_info is None:
            raise BridgeCapabilityUnavailable(
                "structured prompt type is not supported by Standard Hermes"
            )
        if not event.correlation_id:
            raise BridgeProtocolError("structured prompt has no correlation ID")
        if not isinstance(response, Mapping):
            raise BridgeProtocolError("structured prompt response is not an object")
        operation, _, _ = operation_info
        protected_prompt = event.type in PROTECTED_PROMPT_TYPES
        if protected_prompt:
            self._authorize_protected_prompt(event)
        if event.type == "prompt.request":
            choice_operation = response.get("operation")
            if type(choice_operation) is not str or choice_operation not in {
                "choose",
                "explore",
            }:
                raise BridgeProtocolError("typed choice operation is invalid")
            operation = f"prompt.{choice_operation}"
            if self._endpoint_capabilities.get(operation) is not True:
                raise BridgeCapabilityUnavailable(
                    "typed choice operation is not supported by Standard Hermes"
                )
            with self._state_lock:
                grant = self._grant
            if (
                grant is None
                or not grant.interactive_choice
                or grant.configuration_revision != event.configuration_revision
                or operation not in event.choice_capabilities
            ):
                raise BridgeCapabilityUnavailable(
                    "typed choice is not authorized for this Home Device"
                )
        validated_response = _validate_structured_response(event, response)
        with self._state_lock:
            active = self._active_turn
            if (
                active is None
                or event.conversation_handle != self._conversation_handle
                or event.turn_id != active.turn_id
                or active.pending_prompt_type != event.type
                or active.pending_prompt_id != event.correlation_id
            ):
                raise BridgeRequestRejected("structured prompt is no longer pending")
            if (
                self._state != "ready"
                or self._gateway is None
                or self._runtime_session_id is None
            ):
                raise RuntimeError("home bridge is not ready")
            params: dict[str, object] = {
                "session_id": runtime_session_id,
                "request_id": event.correlation_id,
                **validated_response,
            }
            if protected_prompt:
                active.pending_prompt_type = None
                active.pending_prompt_id = None
                active.pending_choice_revision = None
                active.pending_choice_authority = None
        try:
            result = gateway.request(operation, params)
        except GatewayRPCError as error:
            if not self._binding_is_current(
                gateway=gateway,
                runtime_session_id=runtime_session_id,
                active=active,
            ):
                raise BridgeTransportError(
                    "bridge changed during structured prompt delivery"
                ) from error
            if protected_prompt:
                raise BridgeRequestRejected(
                    f"protected {operation} request was rejected"
                ) from error
            raise BridgeRequestRejected(
                f"standard gateway rejected {operation}: {error.message}"
            ) from error
        except BridgeProtocolError:
            self._mark_transport_loss(gateway=gateway, active=active)
            raise
        except BridgeTimeoutError as error:
            self._mark_transport_loss(gateway=gateway, active=active)
            raise BridgeTimeoutError("structured prompt delivery timed out") from error
        except (ConnectionError, OSError, RuntimeError, TypeError, ValueError) as error:
            self._mark_transport_loss(gateway=gateway, active=active)
            raise BridgeTransportError(
                "structured prompt delivery is uncertain"
            ) from error
        if not self._binding_is_current(
            gateway=gateway,
            runtime_session_id=runtime_session_id,
            active=active,
        ):
            raise BridgeTransportError(
                "bridge changed during structured prompt delivery"
            )
        _require_prompt_resolution_result(result, event)
        if protected_prompt:
            return result
        with self._state_lock:
            if (
                active.pending_prompt_type != event.type
                or active.pending_prompt_id != event.correlation_id
            ):
                raise BridgeTransportError("structured prompt is no longer pending")
            if not _has_remaining_prompt_questions(event, result):
                active.pending_prompt_type = None
                active.pending_prompt_id = None
                active.pending_choice_revision = None
                active.pending_choice_authority = None
        return result

    def _authorize_protected_prompt(self, event: BridgeEvent) -> None:
        capability = PROTECTED_PROMPT_CAPABILITIES.get(event.type)
        if capability is None:
            return
        with self._state_lock:
            grant = self._grant
        if (
            grant is None
            or capability not in grant.protected_capabilities
            or event.capability_revision is None
            or grant.capability_revision != event.capability_revision
        ):
            raise BridgeCapabilityUnavailable(
                "protected prompt is not authorized for this Home Device"
            )

    def submit_prompt(self, text: str) -> BridgeTurn:
        """Submit one fresh prompt; an uncertain submission is never retried."""

        gateway, runtime_session_id = self._revalidate_ready_binding()
        with self._state_lock:
            if (
                self._state != "ready"
                or self._gateway is None
                or self._runtime_session_id != runtime_session_id
            ):
                raise RuntimeError("home bridge is not ready")
            if self._active_turn is not None:
                raise RuntimeError("home bridge already has an active turn")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("prompt text must be non-empty")
            handle = self._require_handle()
            self._turn_count += 1
            turn_id = self._turn_id_factory(self._turn_count)
            active = _ActiveTurn(turn_id, generation=self._turn_count)
            self._active_turn = active
        try:
            self._record_activity("turn")
        except BridgeAuthorizationError:
            with self._state_lock:
                if self._active_turn is active:
                    self._active_turn = None
                    self._state = "ready"
            self._invalidate_binding("authorization_unavailable", gateway=gateway)
            raise
        try:
            result = gateway.request(
                "prompt.submit",
                {"session_id": runtime_session_id, "text": text},
            )
        except GatewayRPCError as error:
            with self._state_lock:
                if self._gateway is gateway and self._active_turn is active:
                    self._active_turn = None
                    self._state = "ready"
            self._close_audio(owner=active)
            self._set_idle_after_known_failure()
            raise BridgeRequestRejected(
                f"standard gateway rejected prompt: {error.message}"
            ) from error
        except BridgeProtocolError:
            self._mark_transport_loss(gateway=gateway, active=active)
            raise
        except BridgeTimeoutError as error:
            self._mark_transport_loss(gateway=gateway, active=active)
            raise BridgeTimeoutError("prompt delivery timed out") from error
        except (ConnectionError, OSError, RuntimeError, TypeError, ValueError) as error:
            self._mark_transport_loss(gateway=gateway, active=active)
            raise BridgeTransportError("prompt delivery is uncertain") from error
        try:
            _require_accepted_result(
                result,
                operation="prompt.submit",
                accepted_statuses={"accepted", "queued", "streaming", "submitted"},
            )
        except BridgeRequestRejected:
            with self._state_lock:
                if self._gateway is gateway and self._active_turn is active:
                    self._active_turn = None
                    self._state = "ready"
            self._close_audio(owner=active)
            self._set_idle_after_known_failure()
            raise BridgeRequestRejected("standard gateway rejected prompt")
        except BridgeProtocolError:
            with self._state_lock:
                if self._gateway is gateway and self._active_turn is active:
                    active.uncertain = True
                    self._state = "turn_uncertain"
                    self._active_turn = None
            self._close_audio(owner=active)
            raise
        if not self._binding_is_current(
            gateway=gateway,
            runtime_session_id=runtime_session_id,
            active=active,
        ):
            raise BridgeTransportError("bridge changed during prompt delivery")
        self._persist_session_after_accepted_turn()
        # Response audio is optional. Its setup happens only after Standard has
        # accepted the text, so a wedged sidecar cannot prevent the text turn.
        self._open_audio(owner=active)
        return BridgeTurn(turn_id=turn_id, conversation_handle=handle)

    def _persist_session_after_accepted_turn(self) -> None:
        """Bind a new Session to the grant once Hermes has a message to store."""

        with self._state_lock:
            session_id = self._unpersisted_session_id
            grant = self._grant
        if session_id is None or grant is None:
            return
        try:
            if self._session_persistor is not None:
                self._session_persistor(grant, session_id)
        except OSError, RuntimeError, TypeError, ValueError:
            # The accepted turn stands. Keep the ID pending so the next accepted
            # turn retries; until then a reopen starts a fresh Session.
            return
        with self._state_lock:
            if self._unpersisted_session_id == session_id and self._grant is not None:
                self._grant = replace(self._grant, session_id=session_id)
                self._unpersisted_session_id = None

    def next_event(self) -> BridgeEvent:
        with self._event_processing_lock:
            return self._next_event()

    def _next_event(self) -> BridgeEvent:
        """Read the next session event through the gateway's sole reader."""

        revalidate_claim = self._conversation_closer is not None
        with self._state_lock:
            if (
                self._state != "ready"
                or self._gateway is None
                or self._runtime_session_id is None
            ):
                raise RuntimeError("home bridge is not ready")
            gateway = self._gateway
            runtime_session_id = self._runtime_session_id
        while True:
            if revalidate_claim:
                self._revalidate_ready_binding()
            try:
                raw = gateway.next_event(timeout=0.25 if revalidate_claim else None)
            except BridgeProtocolError as error:
                self._mark_transport_loss(gateway=gateway)
                raise BridgeProtocolError(
                    f"home bridge received an invalid event: {error}"
                ) from error
            except BridgeTimeoutError:
                # next_event() has a bounded queue wait. A quiet but still-open
                # websocket is an idle poll, not evidence that the transport died.
                if revalidate_claim or gateway.is_open:
                    continue
                self._mark_transport_loss(gateway=gateway)
                raise
            except (
                ConnectionError,
                EOFError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as error:
                self._mark_transport_loss(gateway=gateway)
                raise BridgeTransportError(
                    "home bridge transport is disconnected"
                ) from error

            audio_text = ""
            audio_owner: _ActiveTurn | None = None
            terminal_audio_action: str | None = None
            event_active: _ActiveTurn | None = None
            lock_choice_revision = (
                isinstance(raw, Mapping) and raw.get("type") == "prompt.request"
            )
            if lock_choice_revision:
                self._prompt_response_lock.acquire()
            try:
                with self._state_lock:
                    if (
                        self._gateway is not gateway
                        or self._runtime_session_id != runtime_session_id
                    ):
                        raise BridgeTransportError(
                            "bridge changed while reading an event"
                        )
                    if not isinstance(raw, Mapping):
                        raise BridgeProtocolError("standard event is not an object")
                    event_type = raw.get("type")
                    if not isinstance(event_type, str) or not event_type:
                        raise BridgeProtocolError("standard event has no type")
                    session_id = raw.get("session_id")
                    if session_id not in (
                        None,
                        "",
                        runtime_session_id,
                    ) and not isinstance(session_id, str):
                        raise BridgeProtocolError(
                            "standard event session identity is not a string"
                        )
                    if session_id not in (None, "", runtime_session_id):
                        continue
                    if session_id in (None, "") and _is_session_bound_event(event_type):
                        raise BridgeProtocolError(
                            "standard session event has no session identity"
                        )
                    payload = raw.get("payload")
                    if not isinstance(payload, Mapping):
                        raise BridgeProtocolError(
                            "standard event payload is not an object"
                        )
                    event_payload = dict(payload)
                    session_bound = session_id == runtime_session_id
                    active = self._active_turn
                    event_active = active
                    seq = raw.get("seq")
                    if seq is not None and (type(seq) is not int or seq < 1):
                        raise BridgeProtocolError(
                            "standard event sequence must be a positive integer"
                        )
                    turn_id: str | None = None
                    if session_bound and _is_turn_owned_event(event_type):
                        if active is None or active.terminal:
                            continue
                        standard_turn_id = _event_turn_id(event_payload)
                        if seq is not None:
                            if (
                                active.last_event_seq is not None
                                and seq <= active.last_event_seq
                            ):
                                continue
                            if not active.event_started:
                                if event_type != "message.start":
                                    continue
                                if (
                                    self._last_terminal_event_seq is not None
                                    and seq <= self._last_terminal_event_seq
                                ):
                                    continue
                        elif event_type == "message.start" and active.event_started:
                            continue

                        if event_type == "message.start":
                            if active.event_started:
                                continue
                            active.event_started = True
                            active.event_start_seq = seq
                            active.standard_turn_id = standard_turn_id
                        elif standard_turn_id is not None:
                            if (
                                active.standard_turn_id is not None
                                and standard_turn_id != active.standard_turn_id
                            ):
                                continue
                            active.standard_turn_id = standard_turn_id
                        if seq is not None:
                            active.last_event_seq = seq
                        turn_id = active.turn_id

                        if event_type in _STRUCTURED_PROMPT_OPERATIONS:
                            _validate_structured_event_payload(
                                event_type, event_payload
                            )
                            correlation_id = raw.get("correlation_id")
                            if (
                                not isinstance(correlation_id, str)
                                or not correlation_id
                            ):
                                correlation_id = _correlation_id(event_payload)
                            if correlation_id is None:
                                raise BridgeProtocolError(
                                    "structured prompt has no correlation ID"
                                )
                            choice_revision = (
                                _typed_choice_revision(event_payload)
                                if event_type == "prompt.request"
                                else None
                            )
                            choice_authority = (
                                _typed_choice_authority(event_payload)
                                if event_type == "prompt.request"
                                else None
                            )
                            if (
                                choice_revision is not None
                                and choice_revision not in active.choice_revisions
                                and len(active.choice_revisions)
                                >= MAX_TYPED_CHOICE_REVISIONS_PER_TURN
                            ):
                                continue
                            pending_mismatch = (
                                active.pending_prompt_type is not None
                                and (
                                    active.pending_prompt_type != event_type
                                    or active.pending_prompt_id != correlation_id
                                )
                            )
                            is_choice_revision = (
                                pending_mismatch
                                and active.pending_prompt_type == "prompt.request"
                                and event_type == "prompt.request"
                                and active.pending_prompt_id != correlation_id
                                and active.pending_choice_revision is not None
                                and choice_revision is not None
                                and (
                                    active.pending_choice_revision != choice_revision
                                    or active.pending_choice_authority
                                    != choice_authority
                                )
                            )
                            if pending_mismatch and not is_choice_revision:
                                if active.pending_prompt_type in PROTECTED_PROMPT_TYPES:
                                    active.pending_prompt_type = None
                                    active.pending_prompt_id = None
                                    active.pending_choice_revision = None
                                    active.pending_choice_authority = None
                                continue
                            active.pending_prompt_type = event_type
                            active.pending_prompt_id = correlation_id
                            active.pending_choice_revision = choice_revision
                            active.pending_choice_authority = choice_authority
                            if choice_revision is not None:
                                active.choice_revisions.add(choice_revision)
                        elif event_type in _PROMPT_EXPIRY_TYPES:
                            correlation_id = raw.get("correlation_id")
                            if (
                                not isinstance(correlation_id, str)
                                or not correlation_id
                            ):
                                correlation_id = _correlation_id(event_payload)
                            if (
                                correlation_id is None
                                or active.pending_prompt_type
                                != _PROMPT_EXPIRY_TYPES[event_type]
                                or active.pending_prompt_id != correlation_id
                            ):
                                continue
                            active.pending_prompt_type = None
                            active.pending_prompt_id = None
                            active.pending_choice_revision = None
                            active.pending_choice_authority = None
                        else:
                            correlation_id = raw.get("correlation_id")
                            if (
                                not isinstance(correlation_id, str)
                                or not correlation_id
                            ):
                                correlation_id = _correlation_id(event_payload)

                        if event_type in {
                            "message.delta",
                            "text_delta",
                            "message.interim",
                        }:
                            audio_text = _audio_text_fragment(
                                event_type, event_payload, active
                            )
                            audio_owner = active

                        if event_type == "message.complete":
                            active.terminal = _is_terminal_message(event_payload)
                            active.event_terminal_seen = active.terminal
                            if active.terminal:
                                if seq is not None:
                                    self._last_terminal_event_seq = seq
                                active.pending_prompt_type = None
                                active.pending_prompt_id = None
                                active.pending_choice_revision = None
                                active.pending_choice_authority = None
                                if _is_interrupted_message(event_payload):
                                    if not active.interrupt_requested:
                                        terminal_audio_action = "stop"
                                else:
                                    terminal_audio_action = "finish"
                                self._state = "ready"
                                if self._audio_socket is None:
                                    self._active_turn = None
                    else:
                        correlation_id = raw.get("correlation_id")
                        if not isinstance(correlation_id, str) or not correlation_id:
                            correlation_id = _correlation_id(event_payload)
                        # Standard emits activity and errors for the running turn
                        # without turn identity; endpoints discard turnless events
                        # mid-turn, so bind them to the active turn.
                        if (
                            session_bound
                            and event_type in _TURN_ACTIVITY_EVENT_TYPES
                            and active is not None
                            and active.event_started
                            and not active.terminal
                        ):
                            turn_id = active.turn_id
                    conversation_handle = self._require_handle()
                    choice_context: dict[str, object] = {}
                    if event_type in _STRUCTURED_PROMPT_OPERATIONS:
                        with self._state_lock:
                            grant = self._grant
                            choice_context["capability_revision"] = (
                                grant.capability_revision if grant else None
                            )
                            if event_type == "prompt.request":
                                choice_context.update(
                                    {
                                        "standard_session_id": runtime_session_id,
                                        "configuration_revision": (
                                            grant.configuration_revision
                                            if grant
                                            else None
                                        ),
                                        "choice_capabilities": frozenset(
                                            operation
                                            for operation in (
                                                "prompt.choose",
                                                "prompt.explore",
                                            )
                                            if grant is not None
                                            and grant.interactive_choice
                                            and self._endpoint_capabilities.get(
                                                operation
                                            )
                                            is True
                                        ),
                                    }
                                )
                    event = BridgeEvent(
                        conversation_handle=conversation_handle,
                        type=event_type,
                        payload=event_payload,
                        turn_id=turn_id,
                        correlation_id=correlation_id,
                        **choice_context,
                    )
            except BridgeProtocolError:
                self._mark_transport_loss(gateway=gateway)
                raise
            finally:
                if lock_choice_revision:
                    self._prompt_response_lock.release()

            if (
                audio_text
                and audio_owner is not None
                and self._append_audio(audio_text, owner=audio_owner)
            ):
                with self._state_lock:
                    if (
                        self._gateway is gateway
                        and self._runtime_session_id == runtime_session_id
                        and self._active_turn is audio_owner
                    ):
                        audio_owner.audio_text_sent += audio_text
            if terminal_audio_action == "stop":
                self._stop_audio(owner=event_active)
            elif terminal_audio_action == "finish":
                self._finish_audio(owner=event_active)
            if terminal_audio_action is not None:
                try:
                    self._record_activity("response_ready")
                except BridgeAuthorizationError:
                    self._invalidate_binding(
                        "authorization_unavailable", gateway=gateway
                    )
                    raise
            with self._state_lock:
                if (
                    self._gateway is not gateway
                    or self._runtime_session_id != runtime_session_id
                ):
                    raise BridgeTransportError(
                        "bridge changed while processing an event"
                    )
            return event

    def interrupt(self) -> bool:
        """Request interruption without claiming it until Standard confirms it."""

        gateway, runtime_session_id = self._revalidate_ready_binding()
        with self._state_lock:
            active = self._active_turn
            if (
                active is None
                or self._gateway is not gateway
                or self._runtime_session_id != runtime_session_id
                or active.terminal
            ):
                return False
            if active.interrupt_requested:
                return True
        try:
            result = gateway.request(
                "session.interrupt",
                {"session_id": runtime_session_id},
            )
        except GatewayRPCError as error:
            raise BridgeRequestRejected(
                f"standard gateway rejected interrupt: {error.message}"
            ) from error
        except BridgeProtocolError:
            self._mark_transport_loss(gateway=gateway, active=active)
            raise
        except BridgeTimeoutError as error:
            self._mark_transport_loss(gateway=gateway, active=active)
            raise BridgeTimeoutError("interrupt delivery timed out") from error
        except (ConnectionError, OSError, RuntimeError, TypeError, ValueError) as error:
            self._mark_transport_loss(gateway=gateway, active=active)
            raise BridgeTransportError("interrupt delivery is uncertain") from error
        try:
            _require_accepted_result(
                result,
                operation="session.interrupt",
                accepted_statuses={
                    "accepted",
                    "interrupted",
                    "cancelled",
                    "canceled",
                },
            )
        except BridgeRequestRejected:
            raise
        except BridgeProtocolError:
            self._mark_transport_loss(gateway=gateway, active=active)
            raise
        if not self._binding_is_current(
            gateway=gateway,
            runtime_session_id=runtime_session_id,
            active=active,
        ):
            raise BridgeTransportError("bridge changed during interruption")
        self._stop_audio(owner=active)
        with self._state_lock:
            if (
                self._gateway is not gateway
                or self._runtime_session_id != runtime_session_id
                or self._active_turn is not active
            ):
                raise BridgeTransportError("bridge changed during interruption")
            active.interrupt_requested = True
        return True

    def next_audio(self, *, timeout: float | None = None) -> AudioFrame:
        with self._audio_read_lock:
            return self._next_audio(timeout=timeout)

    def _next_audio(self, *, timeout: float | None = None) -> AudioFrame:
        """Read one frame from the separate Standard response-audio socket."""

        audio_timeout = (
            self._audio_timeout
            if timeout is None
            else _validate_timeout(timeout, "audio timeout")
        )
        deadline = None if audio_timeout is None else time.monotonic() + audio_timeout

        with self._state_lock:
            active = self._active_turn
            turn_id = active.turn_id if active is not None else None
            socket = self._audio_socket
            owner = self._audio_owner
        if socket is None:
            return AudioFrame(
                kind="unavailable",
                turn_id=turn_id,
                metadata={"reason": "audio_unavailable"},
            )
        while True:
            try:
                receive_timeout = (
                    None if deadline is None else _remaining(deadline, "response audio")
                )
                raw = socket.receive(timeout=receive_timeout)
            except TimeoutError as error:
                self._close_audio(owner=owner)
                raise BridgeTimeoutError("standard response audio timed out") from error
            except (
                ConnectionError,
                EOFError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as error:
                self._close_audio(owner=owner)
                raise BridgeTransportError(
                    "home bridge audio is unavailable"
                ) from error
            with self._state_lock:
                if self._audio_socket is not socket:
                    return AudioFrame(
                        kind="unavailable",
                        turn_id=turn_id,
                        metadata={"reason": "audio_changed"},
                    )
                active = self._active_turn
                current_turn_id = active.turn_id if active is not None else turn_id
                audio_started = self._audio_started
                pcm_remainder = self._audio_pcm_remainder
            if type(raw) is bytes:
                if not audio_started:
                    self._close_audio(owner=owner)
                    return AudioFrame(
                        kind="unavailable",
                        turn_id=current_turn_id,
                        metadata={"reason": "pcm_before_start"},
                    )
                pcm = pcm_remainder + raw
                complete_bytes = len(pcm) - len(pcm) % 2
                with self._state_lock:
                    if self._audio_socket is not socket:
                        return AudioFrame(
                            kind="unavailable",
                            turn_id=current_turn_id,
                            metadata={"reason": "audio_changed"},
                        )
                    self._audio_pcm_remainder = pcm[complete_bytes:]
                if complete_bytes:
                    return AudioFrame(
                        kind="pcm", turn_id=current_turn_id, data=pcm[:complete_bytes]
                    )
                continue
            if not isinstance(raw, Mapping):
                self._close_audio(owner=owner)
                return AudioFrame(
                    kind="unavailable",
                    turn_id=current_turn_id,
                    metadata={"reason": "invalid_audio_frame"},
                )
            metadata = dict(raw)
            frame_type = metadata.get("type")
            if not isinstance(frame_type, str) or frame_type not in _AUDIO_FRAME_TYPES:
                self._close_audio(owner=owner)
                return AudioFrame(
                    kind="unavailable",
                    turn_id=current_turn_id,
                    metadata={"reason": "invalid_audio_frame"},
                )
            if frame_type == "start":
                if audio_started:
                    self._close_audio(owner=owner)
                    return AudioFrame(
                        kind="unavailable",
                        turn_id=current_turn_id,
                        metadata={"reason": "duplicate_audio_start"},
                    )
                sample_rate = metadata.get("sample_rate")
                channels = metadata.get("channels")
                sample_width = metadata.get("sample_width", 2)
                byte_order = metadata.get("byte_order", "little")
                if (
                    type(sample_rate) is not int
                    or not 0 < sample_rate <= 384_000
                    or type(channels) is not int
                    or channels != 1
                    or type(sample_width) is not int
                    or sample_width != 2
                    or not isinstance(byte_order, str)
                    or byte_order.lower() not in {"little", "le"}
                ):
                    self._close_audio(owner=owner)
                    return AudioFrame(
                        kind="unavailable",
                        turn_id=current_turn_id,
                        metadata={"reason": "invalid_audio_metadata"},
                    )
                with self._state_lock:
                    if self._audio_socket is not socket:
                        return AudioFrame(
                            kind="unavailable",
                            turn_id=current_turn_id,
                            metadata={"reason": "audio_changed"},
                        )
                    self._audio_started = True
                return AudioFrame(
                    kind="start",
                    turn_id=current_turn_id,
                    sample_rate=sample_rate,
                    channels=channels,
                    sample_width=2,
                    byte_order="little",
                    metadata=metadata,
                )
            if frame_type in {"end", "fallback"}:
                if pcm_remainder:
                    self._close_audio(owner=owner)
                    return AudioFrame(
                        kind="unavailable",
                        turn_id=current_turn_id,
                        metadata={"reason": "incomplete_pcm_sample"},
                    )
                frame = AudioFrame(
                    kind=str(frame_type), turn_id=current_turn_id, metadata=metadata
                )
                self._close_audio(owner=owner)
                return frame
            self._close_audio(owner=owner)
            return AudioFrame(
                kind="unavailable",
                turn_id=current_turn_id,
                metadata={"reason": "invalid_audio_frame"},
            )

    def _open_audio(self, *, owner: _ActiveTurn | None = None) -> None:
        with self._state_lock:
            factory = self._audio_socket_factory
            grant = self._grant
            previous_socket = self._audio_socket
            self._audio_started = False
            self._audio_pcm_remainder = b""
            self._audio_owner = owner
            self._audio_socket = None
        if previous_socket is not None:
            _close_quietly(previous_socket)
        if factory is None:
            return
        try:
            socket = _run_bounded(
                lambda: factory.open(
                    _authenticated_url(
                        _audio_url(self._gateway_url),
                        self._hermes_token,
                        profile=grant.profile_id if grant is not None else None,
                        required_path=STANDARD_AUDIO_PATH,
                    )
                ),
                self._audio_timeout,
                operation_name="audio socket setup",
                close_late_result=True,
            )
        except (
            TimeoutError,
            ConnectionError,
            EOFError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            with self._state_lock:
                if self._audio_owner is owner:
                    self._audio_socket = None
                    self._audio_owner = None
            return
        with self._state_lock:
            if owner is not None and self._active_turn is not owner:
                stale = True
            else:
                self._audio_socket = socket
                stale = False
        if stale:
            _close_quietly(socket)

    def _append_audio(self, text: str, *, owner: _ActiveTurn | None = None) -> bool:
        with self._state_lock:
            socket = self._audio_socket
            if owner is not None and self._audio_owner is not owner:
                return False
        if socket is None:
            return False
        try:
            with self._audio_send_lock:
                _run_bounded(
                    lambda: socket.send_json({"text": text}),
                    self._audio_timeout,
                    operation_name="audio text send",
                )
            return True
        except (
            TimeoutError,
            ConnectionError,
            EOFError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            self._close_audio(owner=owner)
            return False

    def _finish_audio(self, *, owner: _ActiveTurn | None = None) -> None:
        with self._state_lock:
            socket = self._audio_socket
            if owner is not None and self._audio_owner is not owner:
                return
        if socket is not None:
            try:
                with self._audio_send_lock:
                    _run_bounded(
                        lambda: socket.send_json({"done": True}),
                        self._audio_timeout,
                        operation_name="audio finish send",
                    )
            except (
                TimeoutError,
                ConnectionError,
                EOFError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                self._close_audio(owner=owner)

    def _stop_audio(self, *, owner: _ActiveTurn | None = None) -> None:
        with self._state_lock:
            socket = self._audio_socket
            if owner is not None and self._audio_owner is not owner:
                return
        if socket is not None:
            try:
                with self._audio_send_lock:
                    _run_bounded(
                        lambda: socket.send_json({"stop": True}),
                        self._audio_timeout,
                        operation_name="audio stop send",
                    )
            except (
                TimeoutError,
                ConnectionError,
                EOFError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                self._close_audio(owner=owner)

    def _close_audio(self, *, owner: _ActiveTurn | None = None) -> None:
        with self._state_lock:
            if owner is not None and self._audio_owner is not owner:
                return
            socket = self._audio_socket
            self._audio_socket = None
            self._audio_owner = None
            self._audio_started = False
            self._audio_pcm_remainder = b""
            active = self._active_turn
            if active is not None and active.terminal:
                self._active_turn = None
                self._state = "ready"
        if socket is not None:
            _close_quietly(socket)

    def _drop_transport(self) -> None:
        """Close live sockets while retaining the opaque session for resume."""

        with self._state_lock:
            handle = self._conversation_handle
            device_id = self._device_id
            self._close_audio()
            gateway = self._gateway
            self._gateway = None
            self._runtime_session_id = None
            self._advertised_commands = frozenset()
        self._mark_disconnected(handle, device_id)
        if gateway is not None:
            gateway.close()

    def _mark_disconnected(
        self,
        handle: str | None,
        device_id: str | None,
    ) -> None:
        callback = self._conversation_disconnector
        if callback is None or handle is None or device_id is None:
            return
        try:
            callback(handle, device_id)
        except OSError, RuntimeError, TypeError, ValueError:
            LOGGER.debug("could not clear Home Watch connection marker", exc_info=True)

    def _mark_transport_loss(
        self,
        *,
        gateway: StandardGatewayClient | None = None,
        active: _ActiveTurn | None = None,
    ) -> None:
        with self._state_lock:
            if gateway is not None and gateway is not self._gateway:
                return
            current_active = self._active_turn or active
            if current_active is not None and not current_active.terminal:
                self._remember_uncertain_turn_locked(current_active)
                self._active_turn = None
                self._state = "turn_uncertain"
            else:
                self._active_turn = None
                self._state = "disconnected"
            self._runtime_session_id = None
            self._advertised_commands = frozenset()
            gateway_to_close = self._gateway
            self._gateway = None
            handle = self._conversation_handle
            device_id = self._device_id
        self._close_audio()
        self._mark_disconnected(handle, device_id)
        if gateway_to_close is not None:
            gateway_to_close.close()

    def _remember_uncertain_turn_locked(self, active: _ActiveTurn) -> None:
        active.uncertain = True
        if self._conversation_handle is not None:
            self._unresolved_turn = BridgeTurn(
                turn_id=active.turn_id,
                conversation_handle=self._conversation_handle,
                status="uncertain",
            )

    def _require_handle(self) -> str:
        if self._conversation_handle is None:
            raise RuntimeError("home bridge has no conversation handle")
        return self._conversation_handle

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                grant = self._grant
                handle = self._conversation_handle
                device_id = self._device_id
                can_resume = self._resume_session_id is not None
            if grant is not None and device_id is not None and not can_resume:
                self._close_claim(grant.handle, device_id, reason="session_unavailable")
            self._drop_transport()
            if handle is not None:
                self._unregister_revocation_handler(handle)
            with self._state_lock:
                self._grant = None
                self._conversation_handle = None
                self._endpoint_headers = {}
                self._device_id = None
                self._runtime_session_id = None
                self._resume_session_id = None
                self._unpersisted_session_id = None
                self._advertised_commands = frozenset()
                self._endpoint_capabilities = {}
                self._active_turn = None
                self._unresolved_turn = None
                self._last_terminal_event_seq = None
                self._last_binding_failure_reason = None
                self._state = "disconnected"


def _authenticated_url(
    url: str,
    token: str,
    *,
    profile: str | None = None,
    required_path: str | None = None,
) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    if required_path is not None:
        if path != required_path:
            raise BridgeProtocolError(
                f"standard transport must use {required_path}, got {parts.path or '/'}"
            )
        path = required_path
    selected_profile = str(profile or "").strip()
    query = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name.lower() != "token"
        and (not selected_profile or name.lower() != "profile")
    ]
    query.append(("token", token))
    if selected_profile:
        query.append(("profile", selected_profile))
    return urlunsplit(
        (parts.scheme, parts.netloc, path, urlencode(query), parts.fragment)
    )


def _audio_url(gateway_url: str) -> str:
    parts = urlsplit(gateway_url)
    path = parts.path.rstrip("/") or "/"
    if path != STANDARD_GATEWAY_PATH:
        raise BridgeProtocolError(
            f"standard audio requires gateway path {STANDARD_GATEWAY_PATH}, got {parts.path or '/'}"
        )
    return urlunsplit(
        (parts.scheme, parts.netloc, STANDARD_AUDIO_PATH, parts.query, parts.fragment)
    )


_ENDPOINT_HIDDEN_SESSION_KEYS = frozenset(
    {"session_id", "runtime_session_id", "stored_session_id", "session_key"}
)
_PROTECTED_PROMPT_METADATA_KEYS = frozenset(
    {
        "choices",
        "description",
        "hint",
        "kind",
        "label",
        "options",
        "placeholder",
        "prompt",
        "question",
        "request_id",
        "sensitivity",
        "timeout_s",
        "title",
    }
)
_PROTECTED_PROMPT_OPTION_KEYS = frozenset({"description", "id", "label"})
_PROTECTED_PROMPT_STRING_KEYS = frozenset(
    {
        "description",
        "hint",
        "kind",
        "label",
        "placeholder",
        "prompt",
        "question",
        "request_id",
        "sensitivity",
        "title",
    }
)
_PROTECTED_PROMPT_NUMERIC_KEYS = frozenset({"timeout_s"})
_PROTECTED_PROMPT_LIST_KEYS = frozenset({"choices", "options"})
_PROTECTED_METADATA_DROP = object()


def endpoint_safe_payload(
    event_type: str, payload: Mapping[str, object]
) -> dict[str, object]:
    """Project one Standard event into the endpoint-safe public payload."""
    if event_type in PROTECTED_PROMPT_TYPES | PROTECTED_PROMPT_EXPIRY_TYPES:
        return _protected_prompt_safe_payload(payload)
    return _endpoint_safe_payload(payload)


def _protected_prompt_safe_payload(
    payload: Mapping[str, object],
) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in payload.items():
        if key not in _PROTECTED_PROMPT_METADATA_KEYS:
            continue
        if key in _PROTECTED_PROMPT_STRING_KEYS and type(value) is not str:
            continue
        if key in _PROTECTED_PROMPT_NUMERIC_KEYS and type(value) not in {
            int,
            float,
        }:
            continue
        if key in _PROTECTED_PROMPT_LIST_KEYS and not isinstance(value, (list, tuple)):
            continue
        normalized = _protected_prompt_metadata_value(
            value, option_list=key in _PROTECTED_PROMPT_LIST_KEYS
        )
        if normalized is not _PROTECTED_METADATA_DROP:
            safe[key] = normalized
    return safe


def _protected_prompt_metadata_value(
    value: object,
    *,
    option_list: bool = False,
    option: bool = False,
) -> object:
    if type(value) is str:
        if len(value) > 1024 or any(ord(character) < 32 for character in value):
            return _PROTECTED_METADATA_DROP
        return value
    if type(value) in {bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > 32:
            return _PROTECTED_METADATA_DROP
        normalized_items = []
        for item in value:
            normalized = _protected_prompt_metadata_value(item, option=option_list)
            if normalized is _PROTECTED_METADATA_DROP:
                return _PROTECTED_METADATA_DROP
            normalized_items.append(normalized)
        return normalized_items
    if isinstance(value, Mapping) and option:
        result: dict[str, object] = {}
        for key, nested in value.items():
            if key not in _PROTECTED_PROMPT_OPTION_KEYS:
                continue
            normalized = _protected_prompt_metadata_value(nested)
            if normalized is not _PROTECTED_METADATA_DROP:
                result[key] = normalized
        return result
    return _PROTECTED_METADATA_DROP


def _endpoint_safe_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Copy endpoint data while keeping all Standard session identities internal."""

    def scrub(value: object) -> object:
        if isinstance(value, Mapping):
            return {
                key: scrub(nested)
                for key, nested in value.items()
                if key not in _ENDPOINT_HIDDEN_SESSION_KEYS
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, tuple):
            return [scrub(item) for item in value]
        return value

    safe = scrub(payload)
    if not isinstance(safe, dict):  # pragma: no cover - scrub(mapping) is always dict
        raise TypeError("endpoint payload must remain an object")
    return safe


def _event_from_frame(frame: Mapping[str, object]) -> dict[str, object] | None:
    if frame.get("method") != "event":
        return None
    params = frame.get("params")
    if not isinstance(params, Mapping):
        raise BridgeProtocolError("standard event params are not an object")
    event_type = params.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise BridgeProtocolError("standard event has no type")
    if "payload" not in params:
        event_payload: dict[str, object] = {}
    else:
        payload = params.get("payload")
        if not isinstance(payload, Mapping):
            raise BridgeProtocolError("standard event payload is not an object")
        event_payload = dict(payload)

    envelope_session_id = params.get("session_id")
    payload_session_id = event_payload.get("session_id")
    if envelope_session_id not in (None, "") and not isinstance(
        envelope_session_id, str
    ):
        raise BridgeProtocolError("standard event session identity is not a string")
    if payload_session_id not in (None, "") and not isinstance(payload_session_id, str):
        raise BridgeProtocolError("standard event session identity is not a string")
    if (
        isinstance(envelope_session_id, str)
        and envelope_session_id
        and isinstance(payload_session_id, str)
        and payload_session_id
        and envelope_session_id != payload_session_id
    ):
        raise BridgeProtocolError("standard event has conflicting session identity")

    correlation_values = [
        params.get("correlation_id"),
        params.get("request_id"),
    ]
    correlation_id: str | None = None
    for value in correlation_values:
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not value:
            raise BridgeProtocolError("standard event correlation ID is not a string")
        if correlation_id is not None and value != correlation_id:
            raise BridgeProtocolError("standard event has conflicting correlation ID")
        correlation_id = value

    seq: int | None = None
    if "seq" in params:
        value = params.get("seq")
        if type(value) is not int or value < 1:
            raise BridgeProtocolError(
                "standard event sequence must be a positive integer"
            )
        seq = value

    return {
        "type": event_type,
        "payload": event_payload,
        "session_id": envelope_session_id or payload_session_id,
        "correlation_id": correlation_id,
        "seq": seq,
    }


def _session_identity(payload: Mapping[str, object]) -> tuple[str, str | None]:
    runtime_values: list[str] = []
    for key in ("session_id", "runtime_session_id"):
        value = payload.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not value:
            raise BridgeProtocolError(
                f"standard session {key} must be a non-empty string"
            )
        runtime_values.append(value)
    if not runtime_values:
        raise BridgeProtocolError("standard session response has no runtime session ID")
    if len(set(runtime_values)) > 1:
        raise BridgeProtocolError("standard session has conflicting runtime IDs")
    return runtime_values[0], _durable_session_id(payload)


def _validate_profile_binding(
    payload: Mapping[str, object], expected_profile: str
) -> bool:
    """Validate an optional Standard profile echo without requiring it in fixtures."""

    values: list[str] = []
    sources: list[tuple[str, object]] = []
    for key in ("profile_id", "profile", "profile_name"):
        if key in payload:
            sources.append((key, payload.get(key)))
    info = payload.get("info")
    if info is not None:
        if not isinstance(info, Mapping):
            raise BridgeProtocolError("standard session info is not an object")
        for key in ("profile_id", "profile", "profile_name"):
            if key in info:
                sources.append((f"info.{key}", info.get(key)))
    for key, value in sources:
        if not isinstance(value, str) or not value.strip():
            raise BridgeProtocolError(f"standard session {key} is not a profile name")
        values.append(value.strip())
    if len(set(values)) > 1:
        raise BridgeProtocolError("standard session has conflicting profile bindings")
    return not values or values[0] == expected_profile


def _durable_session_id(payload: Mapping[str, object]) -> str | None:
    values: list[str] = []
    for key in ("stored_session_id", "session_key"):
        value = payload.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not value:
            raise BridgeProtocolError(
                f"standard session {key} must be a non-empty string"
            )
        values.append(value)
    if len(set(values)) > 1:
        raise BridgeProtocolError("standard session has conflicting durable IDs")
    return values[0] if values else None


def _capabilities(
    payload: Mapping[str, object],
    *,
    commands: list[str] | None = None,
    audio: bool = False,
    interactive_choice: bool = False,
) -> dict[str, object]:
    capabilities = payload.get("capabilities")
    source = capabilities if isinstance(capabilities, Mapping) else payload
    result: dict[str, object] = {
        "commands": list(commands) if commands is not None else [],
        "timing": "absent",
        "interrupt": True,
        "audio": audio,
    }
    heartbeat = source.get("heartbeat")
    if not isinstance(heartbeat, bool):
        heartbeat = payload.get("heartbeat")
    if isinstance(heartbeat, bool):
        result["heartbeat"] = heartbeat
    for operation in ("prompt.choose", "prompt.explore"):
        if operation in source:
            capability = source[operation]
            if type(capability) is not bool:
                raise BridgeProtocolError(
                    f"standard {operation} capability is not a boolean"
                )
            if interactive_choice:
                result[operation] = capability
    return result


def _catalog_commands(payload: Mapping[str, object]) -> list[str]:
    """Normalize the pinned ``commands.catalog`` pairs for Home callers."""

    pairs = payload.get("pairs")
    if pairs is None:
        return []
    if not isinstance(pairs, list):
        raise BridgeProtocolError("standard command catalog pairs are not a list")

    commands: list[str] = []
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) != 2:
            raise BridgeProtocolError(
                "standard command catalog entry is not a name/description pair"
            )
        name, description = pair
        if not isinstance(name, str) or not name.startswith("/"):
            raise BridgeProtocolError(
                "standard command catalog name must start with a slash"
            )
        if not isinstance(description, str):
            raise BridgeProtocolError(
                "standard command catalog description is not a string"
            )
        command = name[1:]
        if not command or command != command.strip():
            raise BridgeProtocolError(
                "standard command catalog name must be a non-empty command"
            )
        if command not in commands:
            commands.append(command)
    return commands


def _audio_text_fragment(
    event_type: str, payload: Mapping[str, object], active: _ActiveTurn
) -> str:
    """Convert Standard text updates into text the audio sidecar has not heard."""

    previous_preview = active.rendered_preview
    if "rendered" in payload:
        rendered = payload.get("rendered")
        if not isinstance(rendered, str):
            raise BridgeProtocolError("standard rendered text is not a string")
        active.rendered_preview = rendered
    else:
        rendered = None

    if event_type == "message.interim" and "already_streamed" in payload:
        already_streamed = payload.get("already_streamed")
        if type(already_streamed) is not bool:
            raise BridgeProtocolError(
                "standard interim already_streamed flag is not a boolean"
            )
        if already_streamed:
            return ""

    text_key = "text"
    if event_type == "text_delta" and "text" not in payload and "delta" in payload:
        text_key = "delta"
    if text_key in payload:
        text = payload.get(text_key)
        if not isinstance(text, str):
            raise BridgeProtocolError("standard text update is not a string")
        if text == active.audio_text_sent:
            return ""
        if active.audio_text_sent and text.startswith(active.audio_text_sent):
            return text[len(active.audio_text_sent) :]
        return text

    if rendered is not None and event_type in {"message.delta", "text_delta"}:
        if rendered.startswith(active.audio_text_sent):
            return rendered[len(active.audio_text_sent) :]
        if rendered.startswith(previous_preview):
            return rendered[len(previous_preview) :]
        # A replacement cannot retract PCM already sent to the sidecar.
        return ""
    return ""


def _is_interrupted_message(payload: Mapping[str, object]) -> bool:
    status = _message_status(payload)
    return status in {
        "cancelled",
        "canceled",
        "interrupted",
        "aborted",
        "stopped",
    }


def _is_terminal_message(payload: Mapping[str, object]) -> bool:
    status = _message_status(payload)
    return status is None or status in _TERMINAL_MESSAGE_STATUSES


def _message_status(payload: Mapping[str, object]) -> str | None:
    if "status" not in payload:
        return None
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        raise BridgeProtocolError("standard message.complete status is not a string")
    normalized = status.lower()
    if normalized not in _TERMINAL_MESSAGE_STATUSES | _NONTERMINAL_MESSAGE_STATUSES:
        raise BridgeProtocolError(
            f"standard message.complete has unknown status: {status}"
        )
    return normalized


def _correlation_id(payload: Mapping[str, object]) -> str | None:
    for key in ("correlation_id", "request_id", "prompt_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _event_turn_id(payload: Mapping[str, object]) -> str | None:
    values: list[str] = []
    for key in ("turn_id", "turn_uuid"):
        if key not in payload:
            continue
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise BridgeProtocolError(f"standard event {key} is not a string")
        values.append(value)
    if len(set(values)) > 1:
        raise BridgeProtocolError("standard event has conflicting turn identity")
    return values[0] if values else None


_TURN_ACTIVITY_EVENT_TYPES = frozenset(
    {
        "thinking.delta",
        "reasoning.delta",
        "reasoning.available",
        "status.update",
        "error",
    }
)


def _is_session_bound_event(event_type: str) -> bool:
    return event_type.startswith(
        (
            "message.",
            "tool.",
            "approval.",
            "clarify.",
            "secret.",
            "sudo.",
            "prompt.",
        )
    ) or event_type in {"text_delta"}


def _is_turn_owned_event(event_type: str) -> bool:
    return _is_session_bound_event(event_type)


def _typed_choice_revision(payload: Mapping[str, object]) -> tuple[str, str]:
    choice = payload.get("choice")
    if not isinstance(choice, Mapping):
        raise BridgeProtocolError("typed choice event has no choice identity")
    object_id = choice.get("object_id")
    freshness = choice.get("freshness")
    if type(object_id) is not str or type(freshness) is not str:
        raise BridgeProtocolError("typed choice identity is invalid")
    return object_id, freshness


def _typed_choice_authority(payload: Mapping[str, object]) -> tuple[object, ...]:
    """Capture only the fields that determine a choice's public authority."""

    choice = payload["choice"]
    options = payload["options"]
    assert isinstance(choice, Mapping)
    assert isinstance(options, list)
    return (
        payload.get("sensitive"),
        choice.get("sensitive"),
        tuple(choice["operations"]),
        tuple(
            (option.get("id"), option.get("label"), option.get("sensitive"))
            for option in options
            if isinstance(option, Mapping)
        ),
    )


def _validate_structured_event_payload(
    event_type: str, payload: Mapping[str, object]
) -> None:
    if event_type == "prompt.request":
        _validate_typed_choice_event_payload(payload)
        return
    if event_type == "clarify.request" and "questions" in payload:
        questions = payload.get("questions")
        if not isinstance(questions, list) or not questions:
            raise BridgeProtocolError(
                "batch clarify questions must be a non-empty list"
            )
        for question in questions:
            if not isinstance(question, Mapping):
                raise BridgeProtocolError("batch clarify question is not an object")
            qid = question.get("qid")
            if not isinstance(qid, str) or not qid:
                raise BridgeProtocolError("batch clarify question has no qid")
            prompt = question.get("question")
            if not isinstance(prompt, str):
                raise BridgeProtocolError("batch clarify question is not text")
            # Standard sends `choices: null` for a free-text question.
            choices = question.get("choices")
            if choices is not None and not isinstance(choices, list):
                raise BridgeProtocolError("batch clarify choices are not a list")
            multi_select = question.get("multi_select")
            if type(multi_select) is not bool:
                raise BridgeProtocolError("batch clarify multi_select is not a boolean")


def _validate_typed_choice_event_payload(payload: Mapping[str, object]) -> None:
    """Reject malformed choice authority before it enters a live turn."""

    if payload.get("prompt_kind") != "choice":
        raise BridgeProtocolError("typed choice event has an invalid prompt kind")
    text = payload.get("text")
    if type(text) is not str or not text.strip() or len(text) > 1024:
        raise BridgeProtocolError("typed choice event has invalid explanation text")
    if "sensitive" in payload and type(payload["sensitive"]) is not bool:
        raise BridgeProtocolError("typed choice privacy flag is not a boolean")
    if payload.get("sensitive") is True:
        raise BridgeProtocolError("sensitive prompt cannot be a typed choice")
    choice = payload.get("choice")
    if not isinstance(choice, Mapping):
        raise BridgeProtocolError("typed choice event has no choice identity")
    object_id = choice.get("object_id")
    freshness = choice.get("freshness")
    if not _valid_choice_identifier(object_id) or not _valid_choice_identifier(
        freshness
    ):
        raise BridgeProtocolError("typed choice identity is invalid")
    operations = choice.get("operations")
    if (
        not isinstance(operations, list)
        or not operations
        or any(
            type(value) is not str or value not in {"choose", "explore"}
            for value in operations
        )
        or len(set(operations)) != len(operations)
    ):
        raise BridgeProtocolError("typed choice operations are invalid")
    options = payload.get("options")
    if not isinstance(options, list) or not options or len(options) > 32:
        raise BridgeProtocolError("typed choice options are invalid")
    option_ids: set[str] = set()
    for option in options:
        if not isinstance(option, Mapping):
            raise BridgeProtocolError("typed choice option is not an object")
        option_id = option.get("id")
        label = option.get("label")
        if not _valid_choice_identifier(option_id):
            raise BridgeProtocolError("typed choice option ID is invalid")
        if type(label) is not str or not label.strip() or len(label) > 256:
            raise BridgeProtocolError("typed choice option label is invalid")
        if option_id in option_ids:
            raise BridgeProtocolError("typed choice option IDs are duplicated")
        option_ids.add(option_id)


def _validate_typed_choice_response(
    event: BridgeEvent,
    response: Mapping[str, object],
) -> dict[str, object]:
    if set(response) != {"operation", "option_id", "object_id", "freshness"}:
        raise BridgeProtocolError("typed choice response has unsupported fields")
    operation = response.get("operation")
    option_id = response.get("option_id")
    object_id = response.get("object_id")
    freshness = response.get("freshness")
    if type(operation) is not str or operation not in {"choose", "explore"}:
        raise BridgeProtocolError("typed choice response operation is invalid")
    choice = event.payload.get("choice")
    if not isinstance(choice, Mapping):
        raise BridgeProtocolError("typed choice response has no active choice")
    operations = choice.get("operations")
    if not isinstance(operations, list) or operation not in operations:
        raise BridgeCapabilityUnavailable(
            "typed choice operation was not advertised by the prompt"
        )
    if f"prompt.{operation}" not in event.choice_capabilities:
        raise BridgeCapabilityUnavailable(
            "typed choice operation was not advertised for this Session"
        )
    if (
        not _valid_choice_identifier(option_id)
        or not _valid_choice_identifier(object_id)
        or not _valid_choice_identifier(freshness)
    ):
        raise BridgeProtocolError("typed choice response identity is invalid")
    if object_id != choice.get("object_id") or freshness != choice.get("freshness"):
        raise BridgeRequestRejected("typed choice response is stale")
    options = event.payload.get("options")
    option_ids = (
        {option.get("id") for option in options if isinstance(option, Mapping)}
        if isinstance(options, list)
        else set()
    )
    if option_id not in option_ids:
        raise BridgeRequestRejected("typed choice response option is unknown")
    return {
        "option_id": option_id,
        "object_id": object_id,
        "freshness": freshness,
    }


def _valid_choice_identifier(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= 64
        and not any(ord(character) < 32 for character in value)
    )


def _is_batch_clarify_event(event: BridgeEvent) -> bool:
    return event.type == "clarify.request" and isinstance(
        event.payload.get("questions"), list
    )


def _validate_structured_response(
    event: BridgeEvent, response: Mapping[str, object]
) -> dict[str, object]:
    operation_info = _STRUCTURED_PROMPT_OPERATIONS.get(event.type)
    if operation_info is None:
        raise BridgeCapabilityUnavailable(
            "structured prompt type is not supported by Standard Hermes"
        )
    _, primary_key, allowed_keys = operation_info
    if any(not isinstance(key, str) for key in response):
        raise BridgeProtocolError("structured prompt response has a non-string key")
    unexpected = set(response) - allowed_keys - {"question_id"}
    if unexpected:
        raise BridgeProtocolError(
            f"structured prompt response has unsupported fields: {sorted(unexpected)}"
        )
    if event.type != "clarify.request" and "question_id" in response:
        raise BridgeProtocolError("question_id is only valid for clarify requests")

    if event.type == "prompt.request":
        return _validate_typed_choice_response(event, response)

    if event.type == "approval.request":
        all_value = response.get("all", False)
        if type(all_value) is not bool:
            raise BridgeProtocolError("approval response all flag is not a boolean")
        choice = response.get("choice", "deny")
        if not isinstance(choice, str) or not choice:
            raise BridgeProtocolError("approval response choice is not a string")
        normalized: dict[str, object] = {"choice": choice}
        if "all" in response:
            normalized["all"] = all_value
        return normalized

    if primary_key not in response:
        raise BridgeProtocolError(f"structured prompt response has no {primary_key}")
    value = response.get(primary_key)
    if event.type == "clarify.request":
        if not isinstance(value, str) and not (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and item for item in value)
        ):
            raise BridgeProtocolError("clarify response answer has an invalid type")
        normalized = {primary_key: value}
        if _is_batch_clarify_event(event) and "question_id" not in response:
            # Standard records a batch answer without a qid as an empty response.
            raise BridgeProtocolError("batch clarify response requires question_id")
        if "question_id" in response:
            if not _is_batch_clarify_event(event):
                raise BridgeProtocolError(
                    "question_id is only valid for batch clarify requests"
                )
            question_id = response.get("question_id")
            if not isinstance(question_id, str) or not question_id:
                raise BridgeProtocolError("clarify response question_id is invalid")
            qids = {
                question.get("qid")
                for question in event.payload["questions"]
                if isinstance(question, Mapping)
            }
            if question_id not in qids:
                raise BridgeProtocolError("clarify response question_id is unknown")
            normalized["question_id"] = question_id
        return normalized

    if not isinstance(value, str):
        raise BridgeProtocolError(
            f"{event.type} response {primary_key} has an invalid type"
        )
    if event.type in {"secret.request", "sudo.request"}:
        if not value:
            raise BridgeProtocolError(f"{event.type} response {primary_key} is empty")
        try:
            byte_count = len(value.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise BridgeProtocolError(
                f"{event.type} response {primary_key} is not valid UTF-8"
            ) from error
        if byte_count > MAX_PROTECTED_INPUT_BYTES:
            raise BridgeProtocolError(
                f"{event.type} response exceeds the protected input limit"
            )
    return {primary_key: value}


def _require_prompt_resolution_result(
    result: Mapping[str, object], event: BridgeEvent
) -> None:
    if _is_rejected_result(result):
        raise BridgeRequestRejected(
            f"standard gateway rejected {event.type} resolution"
        )
    for key in ("accepted", "resolved"):
        if key in result and type(result.get(key)) is not bool:
            raise BridgeProtocolError(
                f"standard gateway prompt result has invalid {key} flag"
            )
    status = result.get("status")
    if status is not None and not isinstance(status, str):
        raise BridgeProtocolError("standard gateway prompt result has invalid status")
    if "remaining" in result:
        if not _is_batch_clarify_event(event):
            raise BridgeProtocolError(
                "only batch clarify results may include remaining questions"
            )
        remaining = result.get("remaining")
        if not isinstance(remaining, list) or any(
            not isinstance(item, str) or not item for item in remaining
        ):
            raise BridgeProtocolError("batch clarify remaining is not a list of IDs")
    if result.get("accepted") is False or result.get("resolved") is False:
        raise BridgeRequestRejected(
            f"standard gateway rejected {event.type} resolution"
        )
    accepted_statuses = {"ok", "accepted", "resolved", "complete", "completed"}
    if result.get("accepted") is True or result.get("resolved") is True:
        return
    if isinstance(status, str) and status.lower() in accepted_statuses:
        return
    if isinstance(status, str) and status.lower() in {
        "rejected",
        "denied",
        "expired",
        "failed",
        "error",
    }:
        raise BridgeRequestRejected(
            f"standard gateway rejected {event.type} resolution"
        )
    raise BridgeProtocolError(
        "standard gateway prompt result has no accepted terminal status"
    )


def _has_remaining_prompt_questions(
    event: BridgeEvent, result: Mapping[str, object]
) -> bool:
    if not _is_batch_clarify_event(event):
        return False
    remaining = result.get("remaining")
    return bool(remaining) if isinstance(remaining, list) else False


def _is_rejected_result(result: Mapping[str, object]) -> bool:
    status = result.get("status")
    if isinstance(status, str) and status.lower() in {
        "rejected",
        "denied",
        "expired",
        "unsupported",
        "failed",
        "error",
    }:
        return True
    return result.get("accepted") is False or result.get("resolved") is False


def _require_accepted_result(
    result: Mapping[str, object],
    *,
    operation: str,
    accepted_statuses: set[str],
) -> None:
    accepted = result.get("accepted")
    if accepted is not None and type(accepted) is not bool:
        raise BridgeProtocolError(
            f"standard gateway {operation} response has invalid acceptance"
        )
    resolved = result.get("resolved")
    if resolved is not None and type(resolved) is not bool:
        raise BridgeProtocolError(
            f"standard gateway {operation} response has invalid resolution"
        )
    status = result.get("status")
    if status is not None and not isinstance(status, str):
        raise BridgeProtocolError(
            f"standard gateway {operation} response has invalid status"
        )
    if _is_rejected_result(result):
        raise BridgeRequestRejected(f"standard gateway rejected {operation}")
    if (
        accepted is True
        or resolved is True
        or (
            accepted is None
            and resolved is None
            and isinstance(status, str)
            and status.lower() in accepted_statuses
        )
    ):
        return
    raise BridgeProtocolError(
        f"standard gateway {operation} response has no acceptance proof"
    )
