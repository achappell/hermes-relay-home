"""Large synthetic resume replies must survive both Home transport hops."""

import importlib.util
import json
import threading
from pathlib import Path

from websockets.sync.server import serve

from hermes_home.bridge.production import WebsocketsJsonSocketFactory


def test_large_resume_reply_survives_pilot_proxy_and_home(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / "deploy/ops/hermes-standard-home-pilot-proxy.py"
    spec = importlib.util.spec_from_file_location("pilot_proxy", path)
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    token_path = tmp_path / "token"
    token_path.write_text("synthetic-test-token")
    reply = {
        "jsonrpc": "2.0",
        "id": "resume",
        "result": {"history": "x" * (9 * 1_048_576)},
    }

    def upstream_handler(connection):
        connection.recv(timeout=10)
        connection.send(json.dumps(reply))
        connection.recv(timeout=10)

    with serve(upstream_handler, "127.0.0.1", 0) as upstream:
        monkeypatch.setattr(
            proxy,
            "DEFAULT_UPSTREAM_URI",
            f"ws://127.0.0.1:{upstream.socket.getsockname()[1]}",
        )
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        with serve(
            lambda connection: proxy._proxy_connection(
                connection, token_path=token_path
            ),
            "127.0.0.1",
            0,
            max_size=proxy.MAX_MESSAGE_SIZE,
        ) as relay:
            relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
            relay_thread.start()
            client = WebsocketsJsonSocketFactory().open(
                f"ws://127.0.0.1:{relay.socket.getsockname()[1]}/api/ws?token=synthetic-test-token"
            )
            try:
                client.send_json({"method": "session.resume"})
                assert client.receive_json(timeout=10) == reply
                client.send_json({"probe_complete": True})
            finally:
                client.close()
                relay.shutdown()
                relay_thread.join(timeout=5)
        upstream.shutdown()
        upstream_thread.join(timeout=5)
