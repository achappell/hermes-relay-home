"""Standard-library HTTP server for the local Home application."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hermes_home.api.application import HomeApplication


class _HomeRequestHandler(BaseHTTPRequestHandler):
    application: HomeApplication

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        path = self.path.split("?", 1)[0]
        response = self.application.handle(method, path, self.headers, body)
        if isinstance(response.body, str):
            payload = response.body.encode("utf-8")
        else:
            payload = json.dumps(response.body, separators=(",", ":")).encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class _ThreadingHomeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def create_server(
    application: HomeApplication,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create a loopback-by-default threaded server for the Home application."""
    handler: type[_HomeRequestHandler] = type(
        "HomeRequestHandler",
        (_HomeRequestHandler,),
        {"application": application},
    )
    return _ThreadingHomeHTTPServer((host, port), handler)
