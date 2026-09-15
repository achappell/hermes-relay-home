import http.client
import json
import threading

import pytest

from hermes_home.domain.credentials import CredentialService
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
    assert settings.auth_mode == "legacy"
    assert "admin-secret" not in repr(settings)
    assert "device-secret" not in repr(settings)


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


def test_load_settings_reads_a_32_byte_credential_root_in_paired_mode(tmp_path) -> None:
    token_file = tmp_path / "admin-token"
    root_file = tmp_path / "credential-root"
    token_file.write_text("admin-secret", encoding="utf-8")
    root_file.write_text("ab" * 32, encoding="utf-8")

    settings = load_settings(
        {
            "HERMES_HOME_ADMIN_TOKEN_FILE": str(token_file),
            "HERMES_HOME_CREDENTIAL_ROOT_SECRET_FILE": str(root_file),
        }
    )

    assert settings.credential_root_secret_file == root_file
    assert settings.credential_root_secret == bytes.fromhex("ab" * 32)
    assert settings.auth_mode == "paired"
    assert "ab" * 32 not in repr(settings)


def test_load_settings_rejects_static_and_paired_credential_sources_together(
    tmp_path,
) -> None:
    token_file = tmp_path / "admin-token"
    root_file = tmp_path / "credential-root"
    device_file = tmp_path / "device-credentials.json"
    token_file.write_text("admin-secret", encoding="utf-8")
    root_file.write_text("ab" * 32, encoding="utf-8")
    device_file.write_text('{"device-secret": "device-1"}', encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError, match="credential sources"):
        load_settings(
            {
                "HERMES_HOME_ADMIN_TOKEN_FILE": str(token_file),
                "HERMES_HOME_CREDENTIAL_ROOT_SECRET_FILE": str(root_file),
                "HERMES_HOME_DEVICE_CREDENTIALS_FILE": str(device_file),
            }
        )


def test_load_settings_rejects_a_malformed_credential_root_secret(tmp_path) -> None:
    token_file = tmp_path / "admin-token"
    root_file = tmp_path / "credential-root"
    token_file.write_text("admin-secret", encoding="utf-8")
    root_file.write_text("ab" * 31 + "zz", encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError, match="credential root"):
        load_settings(
            {
                "HERMES_HOME_ADMIN_TOKEN_FILE": str(token_file),
                "HERMES_HOME_CREDENTIAL_ROOT_SECRET_FILE": str(root_file),
            }
        )


def test_load_settings_reports_disabled_endpoint_auth_without_a_device_source(
    tmp_path,
) -> None:
    token_file = tmp_path / "admin-token"
    token_file.write_text("admin-secret", encoding="utf-8")

    settings = load_settings(
        {
            "HERMES_HOME_ADMIN_TOKEN_FILE": str(token_file),
        }
    )

    assert settings.auth_mode == "disabled"
    assert settings.device_credentials == {}
    assert settings.device_credentials_file is None


def test_create_runtime_wires_paired_enrollment_routes(tmp_path) -> None:
    token_file = tmp_path / "admin-token"
    root_file = tmp_path / "credential-root"
    token_file.write_text("admin-secret", encoding="utf-8")
    root_file.write_text("ab" * 32, encoding="utf-8")
    settings = load_settings(
        {
            "HERMES_HOME_DATA_DIR": str(tmp_path / "data"),
            "HERMES_HOME_ADMIN_TOKEN_FILE": str(token_file),
            "HERMES_HOME_CREDENTIAL_ROOT_SECRET_FILE": str(root_file),
            "HERMES_HOME_PORT": "0",
        }
    )
    runtime = create_runtime(settings)

    try:
        response = runtime.server.RequestHandlerClass.application.handle(
            "POST",
            "/api/v1/enrollment/offers",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )

        assert runtime.credential_store is not None
        assert response.status == 200
        assert response.body["enrollment_code"]
    finally:
        runtime.close()


def test_create_runtime_recovers_paired_credentials_after_restart(tmp_path) -> None:
    token_file = tmp_path / "admin-token"
    root_file = tmp_path / "credential-root"
    token_file.write_text("admin-secret", encoding="utf-8")
    root_file.write_text("ab" * 32, encoding="utf-8")
    settings = load_settings(
        {
            "HERMES_HOME_DATA_DIR": str(tmp_path / "data"),
            "HERMES_HOME_ADMIN_TOKEN_FILE": str(token_file),
            "HERMES_HOME_CREDENTIAL_ROOT_SECRET_FILE": str(root_file),
            "HERMES_HOME_PORT": "0",
        }
    )
    runtime = create_runtime(settings)
    application = runtime.server.RequestHandlerClass.application
    configuration = {
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "wake_mappings": [{"id": "hey-hermes", "name": "Hey Hermes"}],
        "devices": [],
    }

    try:
        published = application.handle(
            "PUT",
            "/api/v1/configuration",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            json.dumps(
                {"schema": 1, "expected_revision": 0, "snapshot": configuration}
            ).encode(),
        )
        offer = application.handle(
            "POST",
            "/api/v1/enrollment/offers",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )
        request = application.handle(
            "POST",
            "/api/v1/enrollment/requests",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": offer.body["enrollment_code"],
                    "endpoint_id": "endpoint-1",
                    "label": "Kitchen Puck",
                    "type": "puck",
                    "requested_rooms": ["kitchen"],
                    "requested_capabilities": ["wake_claim"],
                    "requested_profile_mappings": [],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )
        approved = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/approve",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1, "scope": {"rooms": ["kitchen"], "capabilities": ["wake_claim"]}}',
        )
        consumed = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/consume",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": offer.body["enrollment_code"],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )

        assert published.status == 200
        assert approved.status == 200
        assert consumed.status == 200
        credential = consumed.body["credential"]
        device_id = consumed.body["device_id"]
    finally:
        runtime.close()

    restarted = create_runtime(settings)
    try:
        assert restarted.credential_store is not None
        recovered = CredentialService(
            store=restarted.credential_store,
            root_secret=bytes.fromhex("ab" * 32),
        )
        authenticated = recovered.authenticate_device(credential)
        assert authenticated is not None
        assert authenticated.device_id == device_id
    finally:
        restarted.close()


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
