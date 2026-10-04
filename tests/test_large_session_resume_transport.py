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


def test_proxy_logs_linked_directions_and_close_facts_without_transport_canaries(
    tmp_path,
    monkeypatch,
) -> None:
    from websockets.sync.client import connect

    from hermes_home_diagnostics import OperationalDiagnostics

    path = Path(__file__).parents[1] / "deploy/ops/hermes-standard-home-pilot-proxy.py"
    spec = importlib.util.spec_from_file_location("pilot_proxy_diagnostics", path)
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    token_path = tmp_path / "token"
    token_path.write_text("proxy-token-canary", encoding="utf-8")
    diagnostics = OperationalDiagnostics(component="proxy", directory=tmp_path)
    home_connection_id = "conn-" + "a" * 32
    home_text = "HOME-TEXT-PAYLOAD-CANARY"
    home_binary = b"HOME-BINARY-PAYLOAD-CANARY"
    standard_binary = b"STANDARD-BINARY-PAYLOAD-CANARY"
    standard_text = "STANDARD-TEXT-PAYLOAD-CANARY"
    close_reason = "CLOSE-REASON-CANARY"
    received: list[object] = []

    def upstream_handler(connection):
        try:
            received.append(connection.recv(timeout=5))
            connection.send(standard_binary)
            received.append(connection.recv(timeout=5))
            connection.send(standard_text)
            connection.recv(timeout=5)
        except Exception:  # noqa: BLE001 - test peer closes during relay teardown
            return

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
                connection,
                token_path=token_path,
                diagnostics=diagnostics,
            ),
            "127.0.0.1",
            0,
            process_request=lambda connection, request: proxy._process_request(
                connection,
                request,
                token_path=token_path,
            ),
            max_size=proxy.MAX_MESSAGE_SIZE,
        ) as relay:
            relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
            relay_thread.start()
            client = connect(
                (
                    f"ws://127.0.0.1:{relay.socket.getsockname()[1]}"
                    f"/api/ws?token=proxy-token-canary"
                ),
                additional_headers={
                    "X-Hermes-Diagnostic-Connection": home_connection_id
                },
            )
            try:
                client.send(home_text)
                assert client.recv(timeout=5) == standard_binary
                client.send(home_binary)
                assert client.recv(timeout=5) == standard_text
            finally:
                client.close(code=1001, reason=close_reason)
                relay.shutdown()
                relay_thread.join(timeout=5)
        upstream.shutdown()
        upstream_thread.join(timeout=5)
    diagnostics.close()

    raw = (tmp_path / "proxy.jsonl").read_text(encoding="utf-8")
    for canary in (
        "proxy-token-canary",
        home_text,
        home_binary.decode(),
        standard_binary.decode(),
        standard_text,
        close_reason,
    ):
        assert canary not in raw
    records = [json.loads(line) for line in raw.splitlines()]
    opened = [item for item in records if item["event"] == "connection_opened"]
    home_leg = next(item for item in opened if item["leg"] == "home_proxy")
    standard_leg = next(item for item in opened if item["leg"] == "proxy_standard")
    assert home_leg["home_connection_id"] == home_connection_id
    assert standard_leg["peer_connection_id"] == home_leg["connection_id"]
    assert standard_leg["home_connection_id"] == home_connection_id
    assert standard_leg["connection_id"] != home_leg["connection_id"]
    outcomes = [
        item for item in records if item["event"] == "proxy_message_write_outcome"
    ]
    assert {item["direction"] for item in outcomes} == {
        "home_to_standard",
        "standard_to_home",
    }
    assert {item["frame_kind"] for item in outcomes} == {"text", "binary"}
    observed = next(
        item
        for item in records
        if item["event"] == "transport_observed" and item["leg"] == "home_proxy"
    )
    assert observed["received_close_code"] == 1001
    closed = [item for item in records if item["event"] == "connection_closed"]
    home_close = next(item for item in closed if item["leg"] == "home_proxy")
    assert "close_order" in home_close
    assert received == [home_text, home_binary]
