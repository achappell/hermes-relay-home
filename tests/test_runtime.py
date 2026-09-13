import http.client
import json
import threading

import pytest

from hermes_home.runtime import (
    RuntimeConfigurationError,
    create_runtime,
    load_settings,
)


def test_load_settings_reads_file_backed_credentials(tmp_path) -> None:
    data_dir = tmp_path / "data"
    admin_token_file = tmp_path / "admin-token"
    device_credentials_file = tmp_path / "device-credentials.json"
    admin_token_file.write_text("admin-secret\n", encoding="utf-8")
    device_credentials_file.write_text(
        json.dumps({"device-secret": "puck-kitchen"}),
        encoding="utf-8",
    )

    settings = load_settings(
        {
            "HERMES_HOME_DATA_DIR": str(data_dir),
            "HERMES_HOME_BIND_HOST": "192.168.0.4",
            "HERMES_HOME_PORT": "8780",
            "HERMES_HOME_ADMIN_TOKEN_FILE": str(admin_token_file),
            "HERMES_HOME_DEVICE_CREDENTIALS_FILE": str(device_credentials_file),
        }
    )

    assert settings.data_dir == data_dir
    assert settings.database_path == data_dir / "home.sqlite3"
    assert settings.bind_host == "192.168.0.4"
    assert settings.port == 8780
    assert settings.admin_token == "admin-secret"
    assert settings.device_credentials == {"device-secret": "puck-kitchen"}


def test_load_settings_rejects_a_missing_or_blank_admin_token(tmp_path) -> None:
    token_file = tmp_path / "admin-token"
    token_file.write_text("\n", encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError, match="admin token"):
        load_settings({"HERMES_HOME_ADMIN_TOKEN_FILE": str(token_file)})


def test_load_settings_rejects_invalid_runtime_values(tmp_path) -> None:
    token_file = tmp_path / "admin-token"
    token_file.write_text("admin-secret", encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError, match="port"):
        load_settings(
            {
                "HERMES_HOME_ADMIN_TOKEN_FILE": str(token_file),
                "HERMES_HOME_PORT": "not-a-port",
            }
        )


def test_create_runtime_serves_the_authenticated_metrics_endpoint(tmp_path) -> None:
    token_file = tmp_path / "admin-token"
    token_file.write_text("admin-secret", encoding="utf-8")
    settings = load_settings(
        {
            "HERMES_HOME_DATA_DIR": str(tmp_path / "data"),
            "HERMES_HOME_ADMIN_TOKEN_FILE": str(token_file),
            "HERMES_HOME_PORT": "0",
        }
    )
    runtime = create_runtime(settings)
    thread = threading.Thread(target=runtime.server.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = runtime.server.server_address
        connection = http.client.HTTPConnection(host, port)
        connection.request(
            "GET",
            "/metrics",
            headers={"Authorization": "Bearer admin-secret"},
        )
        response = connection.getresponse()

        assert response.status == 200
        assert b"hermes_home_http_requests_total" in response.read()
        connection.close()
    finally:
        runtime.server.shutdown()
        runtime.close()
        thread.join(timeout=2)
