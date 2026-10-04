#!/usr/bin/env python3
# ruff: target-version=py311
"""Loopback WebSocket relay for the Standard-backed Home pilot.

Tailscale Serve preserves the public ``Host`` header when it proxies an HTTP
WebSocket upgrade.  Hermes Standard intentionally rejects that header while
bound to loopback.  This relay is the narrow boundary between those two
contracts: it authenticates the incoming pilot token, then opens a new
loopback WebSocket to Standard so Hermes sees its bound host.

The relay is deliberately limited to the two Standard WebSocket paths used by
Home.  It never binds a non-loopback address and never logs the token-bearing
request URI.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Headers, Request, Response
from websockets.sync.client import ClientConnection, connect
from websockets.sync.server import ServerConnection, serve

from hermes_home_diagnostics import (
    OperationalDiagnostics,
    SafeTransportLogHandler,
    close_fields,
    exception_fields,
    new_connection_id,
)

LOGGER = logging.getLogger("hermes.standard_home_pilot_proxy")

STANDARD_JSON_PATH = "/api/ws"
STANDARD_AUDIO_PATH = "/api/audio/speak-stream"
ALLOWED_PATHS = frozenset({STANDARD_JSON_PATH, STANDARD_AUDIO_PATH})
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 9121
DEFAULT_UPSTREAM_URI = "ws://127.0.0.1:9120"
# Session resume includes history; keep the same bounded budget as Home.
MAX_MESSAGE_SIZE = 16 * 1_048_576
_HOME_CONNECTION_HEADER = "x-hermes-diagnostic-connection"
_CONNECTION_ID = re.compile(r"conn-[0-9a-f]{32}\Z")



def _token_path() -> Path:
    configured = os.environ.get(
        "HERMES_STANDARD_PILOT_TOKEN_FILE",
        "~/.hermes/hermes-home-standard-pilot/standard-token",
    )
    return Path(configured).expanduser()


def _read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token or any(character in token for character in "\r\n"):
        raise ValueError("Standard pilot token is blank or malformed")
    return token


def _http_error(status: int, reason: str, message: str) -> Response:
    body = f"{message}\n".encode()
    headers = Headers(
        {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Length": str(len(body)),
            "Connection": "close",
        }
    )
    return Response(status, reason, headers, body)


def _validated_query(
    request_path: str, expected_token: str
) -> tuple[str, str] | Response:
    """Validate a Home request and return ``(path, query_without_token)``."""

    parts = urlsplit(request_path)
    if parts.scheme or parts.fragment or parts.path not in ALLOWED_PATHS:
        return _http_error(404, "Not Found", "Standard pilot path is unavailable")

    query = parse_qsl(parts.query, keep_blank_values=True)
    names = [name for name, _value in query]
    if any(name not in {"token", "profile"} for name in names):
        return _http_error(400, "Bad Request", "Standard pilot query is invalid")

    tokens = [value for name, value in query if name == "token"]
    if len(tokens) != 1 or not tokens[0]:
        return _http_error(401, "Unauthorized", "Standard pilot token is required")
    if not hmac.compare_digest(tokens[0].encode(), expected_token.encode()):
        return _http_error(403, "Forbidden", "Standard pilot token was rejected")

    profiles = [value for name, value in query if name == "profile"]
    if len(profiles) > 1 or (profiles and not profiles[0]):
        return _http_error(400, "Bad Request", "Standard pilot Profile is invalid")
    return parts.path, urlencode(
        [(name, value) for name, value in query if name == "profile"]
    )


def _process_request(
    _connection: ServerConnection,
    request: Request,
    *,
    token_path: Path,
) -> Response | None:
    """Enforce the pilot credential before accepting a WebSocket."""

    try:
        token = _read_token(token_path)
    except (OSError, ValueError):  # fmt: skip
        LOGGER.error("Standard pilot token file is unavailable")
        return _http_error(
            503,
            "Service Unavailable",
            "Standard pilot credential is unavailable",
        )
    result = _validated_query(request.path, token)
    return result if isinstance(result, Response) else None


def _upstream_uri(request_path: str, token_path: Path) -> str:
    """Build a loopback upstream URI without carrying the public Host header."""

    parts = urlsplit(request_path)
    token = _read_token(token_path)
    query = [
        (name, value) for name, value in parse_qsl(parts.query) if name == "profile"
    ]
    query.append(("token", token))
    return urlunsplit(
        (
            "ws",
            urlsplit(DEFAULT_UPSTREAM_URI).netloc,
            parts.path,
            urlencode(query),
            "",
        )
    )


def _emit_operational(
    diagnostics: OperationalDiagnostics | None,
    event: str,
    **fields: object,
) -> None:
    if diagnostics is None:
        return
    try:
        diagnostics.emit(event, **fields)
    except Exception:  # noqa: BLE001 - diagnostics never owns relay behavior
        return


def _home_connection_link(
    connection: ServerConnection,
    diagnostics: OperationalDiagnostics | None,
) -> tuple[str | None, str]:
    values = [
        value
        for name, value in connection.request.headers.raw_items()
        if name.casefold() == _HOME_CONNECTION_HEADER
    ]
    if not values:
        return None, "unavailable"
    if len(values) != 1:
        if diagnostics is not None:
            diagnostics.add_loss("correlation_conflicts")
        return None, "ambiguous"
    if _CONNECTION_ID.fullmatch(values[0]) is None:
        if diagnostics is not None:
            diagnostics.add_loss("correlation_conflicts")
        return None, "unavailable"
    return values[0], "linked"


def _connection_close_projection(
    diagnostics: OperationalDiagnostics | None,
    connection: ServerConnection | ClientConnection,
    *,
    connection_id: str,
    peer_connection_id: str | None,
    home_connection_id: str | None,
    correlation_state: str,
    leg: str,
    error: BaseException | None = None,
    closing_error: bool = False,
) -> None:
    facts = close_fields(connection, error)
    exception = exception_fields(error)
    category = exception["exception_category"]
    cause = exception["cause_category"]
    sent = facts["sent_close_code"]
    received = facts["received_close_code"]
    if closing_error and (category == "timeout" or cause == "timeout"):
        classification = "timeout_during_close"
    elif category == "connection_closed_ok" or (
        sent in {1000, 1001} and received in {1000, 1001}
    ):
        classification = "normal_shutdown"
    elif error is not None or facts["observed_status_code"] == 1006:
        classification = "transport_error"
    else:
        classification = "unknown"
    order = facts["close_order"]
    initiator = (
        "local"
        if order == "sent_first" or (sent is not None and received is None)
        else "peer"
        if order == "received_first" or (received is not None and sent is None)
        else "unknown"
    )
    _emit_operational(
        diagnostics,
        "connection_closed",
        leg=leg,
        phase="closed",
        classification=classification,
        connection_id=connection_id,
        peer_connection_id=peer_connection_id,
        home_connection_id=home_connection_id,
        correlation_state=correlation_state,
        initiator=initiator,
        close_trigger=(
            "counterpart_closed"
            if initiator == "peer"
            else "transport_error"
            if error is not None
            else "unknown"
        ),
        **facts,
    )


def _observe_transport(
    diagnostics: OperationalDiagnostics | None,
    connection: ServerConnection | ClientConnection | None,
    connection_id: str,
    peer_connection_id: str | None,
    home_connection_id: str | None,
    correlation_state: str,
    leg: str,
    error: BaseException,
) -> None:
    facts = close_fields(connection, error)
    exception = exception_fields(error)
    category = exception["exception_category"]
    cause = exception["cause_category"]
    sent = facts["sent_close_code"]
    received = facts["received_close_code"]
    if category == "connection_closed_ok" and cause == "timeout":
        classification = "timeout_during_close"
    elif category == "connection_closed_ok":
        classification = "normal_shutdown"
    else:
        classification = "transport_error"
    order = facts["close_order"]
    initiator = (
        "local"
        if order == "sent_first" or (sent is not None and received is None)
        else "peer"
        if order == "received_first" or (received is not None and sent is None)
        else "unknown"
    )
    _emit_operational(
        diagnostics,
        "transport_observed",
        leg=leg,
        phase="closing",
        classification=classification,
        connection_id=connection_id,
        peer_connection_id=peer_connection_id,
        home_connection_id=home_connection_id,
        correlation_state=correlation_state,
        initiator=initiator,
        close_trigger=(
            "counterpart_closed"
            if initiator == "peer"
            else "transport_error"
        ),
        **facts,
    )


def _close_quietly(
    connection: ServerConnection | ClientConnection | None,
) -> BaseException | None:
    if connection is None:
        return None
    try:
        connection.close()
    except Exception as error:  # noqa: BLE001 - relay cleanup stays best effort
        return error
    return None


def _pump(
    source: ServerConnection | ClientConnection,
    target: ServerConnection | ClientConnection,
    stopped: threading.Event,
    *,
    diagnostics: OperationalDiagnostics | None,
    source_connection_id: str,
    target_connection_id: str,
    home_connection_id: str | None,
    correlation_state: str,
    direction: str,
    target_leg: str,
) -> None:
    while not stopped.is_set():
        try:
            message = source.recv()
        except Exception as error:  # noqa: BLE001 - project typed transport facts
            _observe_transport(
                diagnostics,
                source,
                connection_id=source_connection_id,
                peer_connection_id=target_connection_id,
                home_connection_id=home_connection_id,
                correlation_state=correlation_state,
                leg=(
                    "home_proxy"
                    if direction == "home_to_standard"
                    else "proxy_standard"
                ),
                error=error,
            )
            break
        try:
            target.send(message)
        except Exception as error:  # noqa: BLE001 - project typed transport facts
            _observe_transport(
                diagnostics,
                target,
                connection_id=target_connection_id,
                peer_connection_id=source_connection_id,
                home_connection_id=home_connection_id,
                correlation_state=correlation_state,
                leg=target_leg,
                error=error,
            )
            break
        _emit_operational(
            diagnostics,
            "proxy_message_write_outcome",
            connection_id=target_connection_id,
            peer_connection_id=source_connection_id,
            home_connection_id=home_connection_id,
            correlation_state=correlation_state,
            leg=target_leg,
            phase="stream",
            direction=direction,
            frame_kind="text" if isinstance(message, str) else "binary",
            outcome="write_returned",
        )
    if not stopped.is_set():
        stopped.set()
        _close_quietly(target)


def _proxy_connection(
    connection: ServerConnection,
    *,
    token_path: Path,
    diagnostics: OperationalDiagnostics | None = None,
) -> None:
    proxy_connection_id = new_connection_id()
    home_connection_id, correlation_state = _home_connection_link(
        connection, diagnostics
    )
    _emit_operational(
        diagnostics,
        "connection_opened",
        leg="home_proxy",
        connection_id=proxy_connection_id,
        home_connection_id=home_connection_id,
        correlation_state=correlation_state,
        phase="open",
    )
    upstream: ClientConnection | None = None
    standard_connection_id = new_connection_id()
    try:
        upstream = connect(
            _upstream_uri(connection.request.path, token_path),
            open_timeout=10,
            proxy=None,
            max_size=MAX_MESSAGE_SIZE,
        )
        _emit_operational(
            diagnostics,
            "connection_opened",
            leg="proxy_standard",
            connection_id=standard_connection_id,
            peer_connection_id=proxy_connection_id,
            home_connection_id=home_connection_id,
            correlation_state=correlation_state,
            phase="open",
        )
        for leg, current_id, peer_id in (
            ("home_proxy", proxy_connection_id, standard_connection_id),
            ("proxy_standard", standard_connection_id, proxy_connection_id),
        ):
            _emit_operational(
                diagnostics,
                "connection_ready",
                leg=leg,
                connection_id=current_id,
                peer_connection_id=peer_id,
                home_connection_id=home_connection_id,
                correlation_state=correlation_state,
                ready_kind="open",
                phase="open",
            )
        stopped = threading.Event()
        threads = [
            threading.Thread(
                target=_pump,
                kwargs={
                    "diagnostics": diagnostics,
                    "source_connection_id": proxy_connection_id,
                    "target_connection_id": standard_connection_id,
                    "home_connection_id": home_connection_id,
                    "correlation_state": correlation_state,
                    "direction": "home_to_standard",
                    "target_leg": "proxy_standard",
                },
                args=(connection, upstream, stopped),
                name="standard-pilot-client-to-upstream",
                daemon=True,
            ),
            threading.Thread(
                target=_pump,
                kwargs={
                    "diagnostics": diagnostics,
                    "source_connection_id": standard_connection_id,
                    "target_connection_id": proxy_connection_id,
                    "home_connection_id": home_connection_id,
                    "correlation_state": correlation_state,
                    "direction": "standard_to_home",
                    "target_leg": "home_proxy",
                },
                args=(upstream, connection, stopped),
                name="standard-pilot-upstream-to-client",
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    except (
        ConnectionClosed,
        OSError,
        RuntimeError,
        TimeoutError,
        ValueError,
    ) as error:
        if upstream is None:
            _observe_transport(
                diagnostics,
                None,
                connection_id=standard_connection_id,
                peer_connection_id=proxy_connection_id,
                home_connection_id=home_connection_id,
                correlation_state=correlation_state,
                leg="proxy_standard",
                error=error,
            )
        _close_quietly(connection)
    finally:
        upstream_close_error = _close_quietly(upstream)
        home_close_error = _close_quietly(connection)
        _connection_close_projection(
            diagnostics,
            connection,
            connection_id=proxy_connection_id,
            peer_connection_id=standard_connection_id if upstream else None,
            home_connection_id=home_connection_id,
            correlation_state=correlation_state,
            leg="home_proxy",
            error=home_close_error,
            closing_error=home_close_error is not None,
        )
        if upstream is not None:
            _connection_close_projection(
                diagnostics,
                upstream,
                connection_id=standard_connection_id,
                peer_connection_id=proxy_connection_id,
                home_connection_id=home_connection_id,
                correlation_state=correlation_state,
                leg="proxy_standard",
                error=upstream_close_error,
                closing_error=upstream_close_error is not None,
            )


def main() -> None:
    token_path = _token_path()
    host = os.environ.get("HERMES_STANDARD_PILOT_PROXY_HOST", DEFAULT_PROXY_HOST)
    port = int(os.environ.get("HERMES_STANDARD_PILOT_PROXY_PORT", DEFAULT_PROXY_PORT))
    configured_log_dir = os.environ.get("HERMES_HOME_PROXY_LOG_DIR")
    log_dir = (
        Path(configured_log_dir).expanduser()
        if configured_log_dir
        else Path(__file__).parent / "logs"
    )
    diagnostics = OperationalDiagnostics(
        component="proxy",
        directory=log_dir,
        source_files=(
            Path(__file__),
            Path(__file__).with_name("hermes_home_diagnostics.py"),
        ),
        websocket_version=websockets.__version__,
    )
    transport_log_handler = SafeTransportLogHandler(
        diagnostics,
        leg="home_proxy",
    )
    transport_logger = logging.getLogger("websockets")
    transport_logger.addHandler(transport_log_handler)
    LOGGER.info("Starting Standard pilot relay on %s:%s", host, port)
    try:
        with serve(
            lambda connection: _proxy_connection(
                connection,
                token_path=token_path,
                diagnostics=diagnostics,
            ),
            host=host,
            port=port,
            process_request=lambda connection, request: _process_request(
                connection, request, token_path=token_path
            ),
            max_size=MAX_MESSAGE_SIZE,
            server_header="Hermes Standard Home pilot relay",
        ) as server:
            server.serve_forever()
    finally:
        transport_logger.removeHandler(transport_log_handler)
        transport_log_handler.close()
        diagnostics.close()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main()
