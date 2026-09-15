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
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from websockets.exceptions import ConnectionClosed
from websockets.http11 import Headers, Request, Response
from websockets.sync.client import ClientConnection, connect
from websockets.sync.server import ServerConnection, serve

LOGGER = logging.getLogger("hermes.standard_home_pilot_proxy")

STANDARD_JSON_PATH = "/api/ws"
STANDARD_AUDIO_PATH = "/api/audio/speak-stream"
ALLOWED_PATHS = frozenset({STANDARD_JSON_PATH, STANDARD_AUDIO_PATH})
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 9121
DEFAULT_UPSTREAM_URI = "ws://127.0.0.1:9120"
MAX_MESSAGE_SIZE = 4 * 1_048_576


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


def _close_quietly(connection: ServerConnection | ClientConnection | None) -> None:
    if connection is None:
        return
    try:
        connection.close()
    except (ConnectionClosed, OSError, RuntimeError):  # fmt: skip
        pass


def _pump(
    source: ServerConnection | ClientConnection,
    target: ServerConnection | ClientConnection,
    stopped: threading.Event,
) -> None:
    try:
        while not stopped.is_set():
            target.send(source.recv())
    except (ConnectionClosed, EOFError, OSError, RuntimeError, TimeoutError):  # fmt: skip
        pass
    except Exception:  # pragma: no cover - defensive logging for a live relay
        LOGGER.exception("Standard pilot WebSocket relay failed")
    finally:
        if not stopped.is_set():
            stopped.set()
            _close_quietly(target)


def _proxy_connection(connection: ServerConnection, *, token_path: Path) -> None:
    upstream: ClientConnection | None = None
    try:
        upstream = connect(
            _upstream_uri(connection.request.path, token_path),
            open_timeout=10,
            proxy=None,
            max_size=MAX_MESSAGE_SIZE,
        )
        stopped = threading.Event()
        threads = [
            threading.Thread(
                target=_pump,
                args=(connection, upstream, stopped),
                name="standard-pilot-client-to-upstream",
                daemon=True,
            ),
            threading.Thread(
                target=_pump,
                args=(upstream, connection, stopped),
                name="standard-pilot-upstream-to-client",
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    except (ConnectionClosed, OSError, RuntimeError, TimeoutError, ValueError) as error:
        LOGGER.warning("Standard pilot upstream unavailable: %s", type(error).__name__)
        _close_quietly(connection)
    finally:
        _close_quietly(upstream)
        _close_quietly(connection)


def main() -> None:
    token_path = _token_path()
    host = os.environ.get("HERMES_STANDARD_PILOT_PROXY_HOST", DEFAULT_PROXY_HOST)
    port = int(os.environ.get("HERMES_STANDARD_PILOT_PROXY_PORT", DEFAULT_PROXY_PORT))
    LOGGER.info("Starting Standard pilot relay on %s:%s", host, port)
    with serve(
        lambda connection: _proxy_connection(connection, token_path=token_path),
        host=host,
        port=port,
        process_request=lambda connection, request: _process_request(
            connection, request, token_path=token_path
        ),
        max_size=MAX_MESSAGE_SIZE,
        server_header="Hermes Standard Home pilot relay",
    ) as server:
        server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main()
