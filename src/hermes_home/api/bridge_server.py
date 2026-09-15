"""Sibling synchronous WebSocket listener for the Home bridge endpoint."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

from websockets.http11 import Headers, Request, Response
from websockets.sync.server import Server, ServerConnection, serve

from hermes_home.bridge.endpoint import (
    BRIDGE_WS_PATH,
    MAX_BRIDGE_MESSAGE_BYTES,
    BridgeEndpoint,
    BridgeRoute,
)

__all__ = [
    "BRIDGE_WS_PATH",
    "MAX_BRIDGE_MESSAGE_BYTES",
    "create_bridge_server",
]


def create_bridge_server(
    bridge_factory: Callable[[], object] | None = None,
    *,
    route: Mapping[str, object] | BridgeRoute | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
    websocket_serve: Callable[..., Server] | None = None,
) -> Server:
    """Create the local Home WebSocket server.

    ``websockets`` owns the RFC 6455 handshake, framing, size enforcement, and
    close protocol.  This function adds only the Home path and Device-header
    boundary before handing an upgraded connection to ``BridgeEndpoint``.
    """
    safe_route = _safe_route(route)

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
        bridge = None
        if bridge_factory is not None:
            try:
                bridge = bridge_factory()
            except Exception:  # noqa: BLE001 - an injected factory must fail closed
                # A factory is an injected operational dependency.  A broken
                # or absent one must remain an unavailable Home bridge, never
                # an implicit grant or Standard Session.
                bridge = None
        endpoint = BridgeEndpoint(
            connection,
            bridge,
            headers=headers,
            route=safe_route,
            max_message_size=MAX_BRIDGE_MESSAGE_BYTES,
        )
        endpoint.run()

    server_factory = websocket_serve or serve
    return server_factory(
        handler,
        host,
        port,
        process_request=process_request,
        max_size=MAX_BRIDGE_MESSAGE_BYTES,
    )


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
