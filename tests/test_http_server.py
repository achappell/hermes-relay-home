import http.client
import json
import threading

from hermes_home.api.application import MAX_REQUEST_BODY_BYTES, HomeApplication
from hermes_home.api.server import create_server
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.storage.sqlite import SQLiteConfigurationStore


def test_loopback_server_exposes_the_configuration_contract(tmp_path) -> None:
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    engine = ArbitrationEngine(configuration=store.read)
    application = HomeApplication(
        configuration_store=store,
        arbitration_engine=engine,
        admin_token="admin-secret",
        device_credentials={},
    )
    server = create_server(application, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = server.server_address
        connection = http.client.HTTPConnection(host, port)
        connection.request(
            "GET",
            "/api/v1/configuration",
            headers={"Authorization": "Bearer admin-secret"},
        )
        response = connection.getresponse()

        assert response.status == 200
        assert json.loads(response.read()) == {
            "schema": 1,
            "snapshot": {
                "revision": 0,
                "rooms": [],
                "profiles": [],
                "wake_mappings": [],
                "devices": [],
            },
        }
        connection.close()

        connection = http.client.HTTPConnection(host, port)
        connection.request(
            "GET",
            "/metrics",
            headers={"Authorization": "Bearer admin-secret"},
        )
        response = connection.getresponse()

        assert response.status == 200
        assert response.getheader("Content-Type") == (
            "text/plain; version=0.0.4; charset=utf-8"
        )
        assert b"hermes_home_http_requests_total" in response.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()


def test_loopback_server_rejects_an_oversized_request_before_reading_it(
    tmp_path,
) -> None:
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    engine = ArbitrationEngine(configuration=store.read)
    application = HomeApplication(
        configuration_store=store,
        arbitration_engine=engine,
        admin_token="admin-secret",
        device_credentials={},
    )
    server = create_server(application, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = server.server_address
        connection = http.client.HTTPConnection(host, port)
        connection.putrequest("POST", "/api/v1/enrollment/offers")
        connection.putheader("Authorization", "Bearer admin-secret")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(MAX_REQUEST_BODY_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()

        assert response.status == 400
        assert json.loads(response.read()) == {
            "schema": 1,
            "error": {"code": "invalid_request"},
        }
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()


def test_real_server_delivers_pairing_cookie_and_security_headers(tmp_path) -> None:
    from hermes_home.domain.credentials import CredentialService
    from hermes_home.storage.credentials import SQLiteCredentialStore

    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    application = HomeApplication(
        configuration_store=store,
        arbitration_engine=ArbitrationEngine(configuration=store.read),
        admin_token="admin-secret",
        device_credentials={},
        credential_service=CredentialService(
            store=SQLiteCredentialStore(tmp_path / "home.sqlite3"),
            root_secret=b"r" * 32,
        ),
    )
    server = create_server(application, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        authority = f"{host}:{port}"
        connection = http.client.HTTPConnection(host, port)
        connection.request("GET", "/pair")
        page = connection.getresponse()
        page.read()
        assert page.status == 200
        assert "default-src 'none'" in page.getheader("Content-Security-Policy")
        connection.close()

        connection = http.client.HTTPConnection(host, port)
        connection.request(
            "POST",
            "/pair/api/session",
            body=json.dumps({"admin_token": "admin-secret"}),
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://{authority}",
                "Host": authority,
            },
        )
        signed_in = connection.getresponse()
        signed_in.read()
        assert signed_in.status == 200
        cookie = signed_in.getheader("Set-Cookie")
        assert cookie.startswith("hermes_home_pair=")
        assert "HttpOnly" in cookie
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
