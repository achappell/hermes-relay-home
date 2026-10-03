"""Sibling synchronous WebSocket listener for the Home bridge endpoint."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from websockets.http11 import Headers, Request, Response
from websockets.sync.server import Server, ServerConnection, serve

from hermes_home.bridge.endpoint import (
    BRIDGE_WS_PATH,
    HOME_BRIDGE_SCHEMA,
    MAX_BRIDGE_MESSAGE_BYTES,
    BridgeEndpoint,
    BridgeRoute,
)
from hermes_home.observability.diagnostics import DiagnosticsRecorder

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
                previous = None
                close_endpoint = True
            else:
                previous = self._entries.pop(handle, None)
                self._entries[handle] = _ParkedEndpoint(endpoint, deadline, timer)
                close_endpoint = False
        if close_endpoint:
            endpoint.close()
            return
        if previous is not None:
            previous.timer.cancel()
            previous.endpoint.close()
        self._notify("mark_detached", handle)
        timer.start()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            entries = list(self._entries.values())
            self._entries.clear()
        for parked in entries:
            parked.timer.cancel()
            parked.endpoint.close()

    def take(self, handle: str) -> _ParkedEndpoint | None:
        with self._lock:
            parked = self._entries.pop(handle, None)
        if parked is None:
            return None
        parked.timer.cancel()
        self._notify("clear_detached", handle)
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
            parked.endpoint.close()


def create_bridge_server(
    bridge_factory: Callable[[], object] | None = None,
    *,
    route: Mapping[str, object] | BridgeRoute | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
    websocket_serve: Callable[..., Server] | None = None,
    diagnostics: DiagnosticsRecorder | None = None,
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

    def handler(connection: ServerConnection) -> None:
        headers = dict(connection.request.headers.raw_items())
        try:
            first_message = connection.recv()
        except Exception:  # noqa: BLE001 - peer vanished before a request
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
            ).run(first_message=first_message)
            return
        method, handle, request_id = request
        if method == "conversation.open" and parking_lot.contains(handle):
            connection.send(
                json.dumps(
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
                    separators=(",", ":"),
                )
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
                endpoint.adopt(connection, headers=headers)
            except RuntimeError:
                recovery_timer.cancel()
                if endpoint.has_recoverable_state:
                    parking_lot.park(endpoint, expires_at=parked.expires_at)
                    connection.send(
                        json.dumps(
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
                            }
                        )
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
