"""Sibling synchronous WebSocket listener for the Home bridge endpoint."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from websockets.http11 import Headers, Request, Response
from websockets.sync.server import Server, ServerConnection, serve

from hermes_home.bridge.endpoint import (
    BRIDGE_WS_PATH,
    CLIENT_FEATURES_HEADER,
    HOME_BRIDGE_SCHEMA,
    MAX_BRIDGE_MESSAGE_BYTES,
    BridgeEndpoint,
    BridgeRoute,
    client_turn_keepalive,
)
from hermes_home.observability.diagnostics import DiagnosticsRecorder
from hermes_home_diagnostics import (
    close_fields,
    exception_fields,
    new_connection_id,
)

__all__ = [
    "BRIDGE_WS_PATH",
    "MAX_BRIDGE_MESSAGE_BYTES",
    "create_bridge_server",
]

LOGGER = logging.getLogger(__name__)
DEFAULT_RECONNECT_GRACE_SECONDS = 90.0


@dataclass(slots=True)
class _ParkedEndpoint:
    endpoint: BridgeEndpoint
    expires_at: float
    timer: threading.Timer


class ClaimParkingObserver(Protocol):
    """Content-free claim-store hooks for parked endpoints (HOME-NW-18)."""

    def mark_detached(self, handle: str) -> None: ...

    def clear_detached(self, handle: str) -> None: ...

    def add_close_listener(self, listener: Callable[[str], None]) -> None: ...


class _EndpointParkingLot:
    """Hold one detached endpoint per opaque conversation during brief drops."""

    def __init__(
        self,
        grace_seconds: float,
        observer: ClaimParkingObserver | None = None,
    ) -> None:
        self._grace_seconds = grace_seconds
        self._observer = observer
        self._lock = threading.Lock()
        self._entries: dict[str, _ParkedEndpoint] = {}
        self._closed = False

    def _notify(self, method: str, handle: str) -> None:
        observer = self._observer
        if observer is None:
            return
        try:
            getattr(observer, method)(handle)
        except Exception:  # noqa: BLE001 - list state must not break parking
            LOGGER.warning("Home claim parking marker could not be updated")

    def park(
        self, endpoint: BridgeEndpoint, *, expires_at: float | None = None
    ) -> None:
        handle = endpoint.conversation_handle
        if handle is None or not endpoint.has_recoverable_state:
            endpoint.close()
            return
        deadline = (
            time.monotonic() + self._grace_seconds if expires_at is None else expires_at
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            endpoint.close()
            return
        timer = threading.Timer(remaining, self._expire, args=(handle,))
        timer.daemon = True
        with self._lock:
            if self._closed:
                close_endpoint = True
                previous = None
            else:
                # Serialize the store marker with publish/take/expiry. Without
                # this, an adoption can clear the marker before this late set.
                self._notify("mark_detached", handle)
                previous = self._entries.pop(handle, None)
                self._entries[handle] = _ParkedEndpoint(endpoint, deadline, timer)
                close_endpoint = False
        if close_endpoint:
            endpoint.close()
            return
        if previous is not None:
            previous.timer.cancel()
            previous.endpoint.close()
        timer.start()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            entries = list(self._entries.items())
            self._entries.clear()
            for handle, _parked in entries:
                self._notify("clear_detached", handle)
        for _handle, parked in entries:
            parked.timer.cancel()
            parked.endpoint.close()

    def take(self, handle: str) -> _ParkedEndpoint | None:
        with self._lock:
            parked = self._entries.pop(handle, None)
            if parked is not None:
                self._notify("clear_detached", handle)
        if parked is None:
            return None
        parked.timer.cancel()
        return parked

    def evict(self, handle: str) -> None:
        """Drop a parked endpoint whose claim Home has closed."""
        with self._lock:
            parked = self._entries.pop(handle, None)
        if parked is None:
            return
        parked.timer.cancel()
        parked.endpoint.close()

    def contains(self, handle: str) -> bool:
        with self._lock:
            return handle in self._entries

    def _expire(self, handle: str) -> None:
        with self._lock:
            parked = self._entries.pop(handle, None)
            if parked is not None:
                self._notify("clear_detached", handle)
        if parked is not None:
            parked.endpoint.close()


def create_bridge_server(
    bridge_factory: Callable[[], object] | None = None,
    *,
    route: Mapping[str, object] | BridgeRoute | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
    websocket_serve: Callable[..., Server] | None = None,
    diagnostics: DiagnosticsRecorder | None = None,
    operational_diagnostics: object | None = None,
    client_reports: object | None = None,
    reconnect_grace_seconds: float = DEFAULT_RECONNECT_GRACE_SECONDS,
    claim_store: ClaimParkingObserver | None = None,
) -> Server:
    """Create the local Home WebSocket server.

    ``websockets`` owns the RFC 6455 handshake, framing, size enforcement, and
    close protocol.  This function adds only the Home path and Device-header
    boundary before handing an upgraded connection to ``BridgeEndpoint``.
    ``claim_store`` (optional) learns which claims are parked, and evicts a
    parked endpoint when Home closes its claim.
    """
    safe_route = _safe_route(route)
    if reconnect_grace_seconds <= 0:
        raise ValueError("reconnect grace period must be positive")
    parking_lot = _EndpointParkingLot(reconnect_grace_seconds, claim_store)
    if claim_store is not None:
        claim_store.add_close_listener(parking_lot.evict)

    def process_request(
        connection: ServerConnection,
        request: Request,
    ) -> Response | None:
        del connection
        parsed = urlsplit(request.path)
        if parsed.path != BRIDGE_WS_PATH or parsed.query or parsed.fragment:
            return _rejection(404, "Not Found")
        if not _valid_device_authorization(request.headers):
            return _rejection(401, "Unauthorized")
        return None

    def early_rejection(
        connection: ServerConnection,
        connection_id: str,
        response: dict[str, object],
        *,
        failure_code: str,
        compact: bool,
    ) -> None:
        correlation_id = f"corr-{uuid.uuid4().hex}"
        _emit_operational(
            operational_diagnostics,
            "rejection_generated",
            correlation_id=correlation_id,
            connection_id=connection_id,
            phase="response",
            failure_code=failure_code,
            origin="readiness_gate",
            unavailable_latch=False,
        )
        started = time.monotonic()
        try:
            if compact:
                message = json.dumps(response, separators=(",", ":"))
            else:
                message = json.dumps(response)
            _emit_operational(
                operational_diagnostics,
                "response_write_started",
                correlation_id=correlation_id,
                connection_id=connection_id,
                phase="response",
                response_kind="rejection",
            )
            started = time.monotonic()
            connection.send(message)
        except Exception as error:  # preserve transport behavior
            _emit_operational(
                operational_diagnostics,
                "response_write_outcome",
                correlation_id=correlation_id,
                connection_id=connection_id,
                phase="response",
                response_kind="rejection",
                outcome="failed",
                failure_code="transport_unavailable",
                exception_category=exception_fields(error)["exception_category"],
                duration_ms=min(
                    2**63 - 1,
                    max(0, int((time.monotonic() - started) * 1000)),
                ),
            )
            raise
        _emit_operational(
            operational_diagnostics,
            "response_write_outcome",
            correlation_id=correlation_id,
            connection_id=connection_id,
            phase="response",
            response_kind="rejection",
            outcome="write_returned",
            duration_ms=min(
                2**63 - 1,
                max(0, int((time.monotonic() - started) * 1000)),
            ),
        )

    def handler(connection: ServerConnection) -> None:
        raw_header_items = list(connection.request.headers.raw_items())
        headers = dict(raw_header_items)
        turn_keepalive = client_turn_keepalive(
            connection.request.headers.get_all(CLIENT_FEATURES_HEADER)
        )
        connection_id = new_connection_id()
        diagnostics_opted_in = _diagnostics_opted_in(raw_header_items)
        _emit_operational(
            operational_diagnostics,
            "connection_opened",
            leg="client_home",
            connection_id=connection_id,
            correlation_state="local_only",
            phase="open",
        )
        transport_error: BaseException | None = None
        try:
            _handle_connection(
                connection,
                headers,
                connection_id,
                diagnostics_opted_in,
                turn_keepalive,
            )
        except Exception as error:
            transport_error = error
            raise
        finally:
            facts = close_fields(connection, transport_error)
            received = facts["received_close_code"]
            sent = facts["sent_close_code"]
            initiator = (
                "peer"
                if received is not None
                else "local"
                if sent is not None
                else "unknown"
            )
            close_trigger = (
                "counterpart_closed"
                if initiator == "peer"
                else "transport_error"
                if transport_error is not None
                else "unknown"
            )
            category = facts["exception_category"]
            if transport_error is not None and category == "timeout":
                classification = "transport_error"
            elif category == "connection_closed_ok" or (
                sent in {1000, 1001} and received in {1000, 1001}
            ):
                classification = "normal_shutdown"
            elif transport_error is not None or facts["observed_status_code"] == 1006:
                classification = "transport_error"
            else:
                classification = "unknown"
            _emit_operational(
                operational_diagnostics,
                "connection_closed",
                leg="client_home",
                connection_id=connection_id,
                correlation_state="local_only",
                phase="closed",
                initiator=initiator,
                close_trigger=close_trigger,
                classification=classification,
                **facts,
            )

    def _handle_connection(
        connection: ServerConnection,
        headers: Mapping[str, str],
        connection_id: str,
        diagnostics_opted_in: bool,
        turn_keepalive: bool,
    ) -> None:
        try:
            first_message = connection.recv()
        except Exception as error:  # noqa: BLE001 - peer vanished before a request
            facts = close_fields(connection, error)
            received = facts["received_close_code"]
            classification = (
                "normal_shutdown"
                if facts["exception_category"] == "connection_closed_ok"
                else "transport_error"
            )
            _emit_operational(
                operational_diagnostics,
                "transport_observed",
                leg="client_home",
                phase="closing",
                classification=classification,
                connection_id=connection_id,
                correlation_state="local_only",
                initiator="peer" if received is not None else "unknown",
                close_trigger="counterpart_closed"
                if received is not None
                else "transport_error",
                **facts,
            )
            return

        request = _initial_request(first_message)
        if request is None:
            bridge = _new_bridge(bridge_factory)
            BridgeEndpoint(
                connection,
                bridge,
                headers=headers,
                route=safe_route,
                max_message_size=MAX_BRIDGE_MESSAGE_BYTES,
                diagnostics=diagnostics,
                operational_diagnostics=operational_diagnostics,
                client_reports=client_reports,
                diagnostics_connection_id=connection_id,
                diagnostics_opted_in=diagnostics_opted_in,
                turn_keepalive=turn_keepalive,
            ).run(first_message=first_message)
            return
        method, handle, request_id = request
        if method == "conversation.open" and parking_lot.contains(handle):
            early_rejection(
                connection,
                connection_id,
                {
                    "jsonrpc": "2.0",
                    "schema": 1,
                    "id": request_id,
                    "result": {
                        "schema": 1,
                        "status": "unavailable",
                        "conversation_handle": handle,
                        "reason": "reconnect_required",
                    },
                },
                failure_code="reconnect_required",
                compact=True,
            )
            connection.close()
            return
        parked = (
            parking_lot.take(handle) if method == "conversation.reconnect" else None
        )
        endpoint = None if parked is None else parked.endpoint
        recovery_timer = None
        if endpoint is not None:
            recovery_timer = threading.Timer(
                max(0.0, parked.expires_at - time.monotonic()), endpoint.close
            )
            recovery_timer.daemon = True
            recovery_timer.start()
            try:
                endpoint.adopt(
                    connection,
                    headers=headers,
                    diagnostics_connection_id=connection_id,
                    diagnostics_opted_in=diagnostics_opted_in,
                    turn_keepalive=turn_keepalive,
                )
            except RuntimeError:
                recovery_timer.cancel()
                if endpoint.has_recoverable_state:
                    parking_lot.park(endpoint, expires_at=parked.expires_at)
                    early_rejection(
                        connection,
                        connection_id,
                        {
                            "jsonrpc": "2.0",
                            "schema": 1,
                            "id": request_id,
                            "result": {
                                "schema": 1,
                                "status": "unavailable",
                                "conversation_handle": handle,
                                "reason": "transport_unavailable",
                            },
                        },
                        failure_code="transport_unavailable",
                        compact=False,
                    )
                    connection.close()
                    return
                endpoint.close()
                endpoint = None
                parked = None
        if endpoint is None:
            bridge = _new_bridge(bridge_factory)
            endpoint = BridgeEndpoint(
                connection,
                bridge,
                headers=headers,
                route=safe_route,
                max_message_size=MAX_BRIDGE_MESSAGE_BYTES,
                diagnostics=diagnostics,
                operational_diagnostics=operational_diagnostics,
                client_reports=client_reports,
                diagnostics_connection_id=connection_id,
                diagnostics_opted_in=diagnostics_opted_in,
                turn_keepalive=turn_keepalive,
            )
        if parked is not None:
            try:
                endpoint.handle_message(first_message)
            finally:
                recovery_timer.cancel()
            endpoint.run(park_on_disconnect=True)
        else:
            endpoint.run(first_message=first_message, park_on_disconnect=True)
        if endpoint.has_recoverable_state:
            expires_at = (
                parked.expires_at
                if parked is not None and not endpoint.adoption_acknowledged
                else None
            )
            parking_lot.park(endpoint, expires_at=expires_at)

    server_factory = websocket_serve or serve
    server = server_factory(
        handler,
        host,
        port,
        process_request=process_request,
        max_size=MAX_BRIDGE_MESSAGE_BYTES,
    )
    shutdown = getattr(server, "shutdown", None)
    if callable(shutdown):

        def shutdown_with_cleanup() -> None:
            parking_lot.close()
            shutdown()

        server.shutdown = shutdown_with_cleanup
    return server


def _emit_operational(
    diagnostics: object | None,
    event: str,
    **fields: object,
) -> None:
    emit = getattr(diagnostics, "emit", None)
    if callable(emit):
        try:
            emit(event, **fields)
        except Exception:  # noqa: BLE001 - diagnostics never owns transport behavior
            return


def _diagnostics_opted_in(raw_header_items: list[tuple[str, str]]) -> bool:
    versions = [
        value
        for name, value in raw_header_items
        if name.casefold() == "x-hermes-diagnostics-version"
    ]
    return len(versions) == 1 and versions[0] == "1"


def _new_bridge(bridge_factory: Callable[[], object] | None) -> object | None:
    bridge = None
    if bridge_factory is not None:
        try:
            bridge = bridge_factory()
        except Exception:  # noqa: BLE001 - an injected factory must fail closed
            # A factory is an injected operational dependency.  A broken
            # or absent one must remain an unavailable Home bridge, never
            # an implicit grant or Standard Session.
            bridge = None
    return bridge


def _initial_request(message: object) -> tuple[str, str, object] | None:
    if not isinstance(message, str):
        return None
    try:
        document = json.loads(message)
    except TypeError, ValueError, json.JSONDecodeError:
        return None
    if not isinstance(document, dict):
        return None
    if (
        document.get("jsonrpc") != "2.0"
        or document.get("schema") != HOME_BRIDGE_SCHEMA
        or "id" not in document
        or not _valid_request_id(document.get("id"))
    ):
        return None
    method = document.get("method")
    params = document.get("params")
    if method not in {"conversation.open", "conversation.reconnect"}:
        return None
    if not isinstance(params, dict):
        return None
    if set(params) != {"conversation_handle"}:
        return None
    handle = params.get("conversation_handle")
    if not isinstance(handle, str) or not handle.strip():
        return None
    return method, handle.strip(), document.get("id")


def _valid_request_id(value: object) -> bool:
    if type(value) is str:
        return bool(value)
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    return False


def _safe_route(
    route: Mapping[str, object] | BridgeRoute | None,
) -> BridgeRoute:
    if route is None or isinstance(route, BridgeRoute):
        return route or BridgeRoute()
    if not isinstance(route, Mapping):
        raise TypeError("Home bridge route must be an object")
    return BridgeRoute(route.get("class"), route.get("id"))  # type: ignore[arg-type]


def _valid_device_authorization(headers: Mapping[str, str]) -> bool:
    values: list[str]
    try:
        values = list(headers.get_all("Authorization"))  # type: ignore[attr-defined]
    except AttributeError:
        values = [
            value for name, value in headers.items() if name.lower() == "authorization"
        ]
    return (
        len(values) == 1
        and values[0].startswith("Device ")
        and bool(values[0][len("Device ") :].strip())
    )


def _rejection(status: int, reason: str) -> Response:
    return Response(
        status,
        reason,
        Headers(
            [
                ("Content-Length", "0"),
                ("Cache-Control", "no-store"),
            ]
        ),
    )
