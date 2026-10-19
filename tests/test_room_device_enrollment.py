import http.client
import json
import threading
from copy import deepcopy
from pathlib import Path

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.api.responses import HTTPResponse
from hermes_home.api.server import create_server
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.domain.credentials import (
    CredentialScope,
    CredentialService,
    CredentialValidationError,
    TouchBinding,
)
from hermes_home.storage.credentials import SQLiteCredentialStore
from hermes_home.storage.sqlite import SQLiteConfigurationStore
from tests.test_credentials_api import CONFIGURATION


@pytest.fixture
def room_home(tmp_path):
    configuration = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    credentials = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    now = [1_000.0]
    service = CredentialService(
        store=credentials, root_secret=b"r" * 32, clock=lambda: now[0]
    )
    app = HomeApplication(
        configuration_store=configuration,
        arbitration_engine=ArbitrationEngine(configuration=configuration.read),
        admin_token="local-test-admin",
        device_credentials={},
        credential_service=service,
    )
    snapshot = deepcopy(CONFIGURATION)
    snapshot["devices"] = []
    configuration.replace(expected_revision=0, candidate=snapshot)

    def call(method, path, body=None, *, admin=False, credential=None):
        headers = {"Content-Type": "application/json"}
        if admin:
            headers["Authorization"] = "Bearer local-test-admin"
        elif credential is not None:
            headers["Authorization"] = f"Device {credential}"
        return app.handle(method, path, headers, json.dumps(body) if body else b"")

    yield app, service, configuration, credentials, now, call
    credentials.close()
    configuration.close()


def _request(call, **changes):
    offer = call("POST", "/api/v1/enrollment/offers", {"schema": 1}, admin=True)
    assert offer.status == 200
    body = {
        "schema": 1,
        "enrollment_code": offer.body["enrollment_code"],
        "endpoint_id": "persistent-puck-identity",
        "label": "Kitchen Puck service",
        "type": "puck",
        "requested_rooms": ["kitchen"],
        "requested_capabilities": ["wake_claim"],
        "secure_storage": "service_private_file",
    }
    body.update(changes)
    return body, call("POST", "/api/v1/enrollment/requests", body)


def _scope(**changes):
    scope = {
        "rooms": ["kitchen"],
        "capabilities": ["wake_claim"],
        "wake_mapping_grant": {"mode": "selected", "ids": ["hey-hermes"]},
    }
    scope.update(changes)
    return {"schema": 1, "scope": scope}


@pytest.mark.parametrize(
    "changes",
    [
        {"requested_rooms": []},
        {"requested_rooms": ["kitchen", "hall"]},
        {"requested_capabilities": []},
        {"requested_capabilities": ["client_claim"]},
        {"requested_capabilities": ["wake_claim", "touch_claim"]},
        {"requested_capabilities": ["wake_claim", "sensitive_entry"]},
        {"type": "touch"},
        {"type": "tui"},
        {"type": "ios"},
        {"type": "macos"},
        {"type": "android"},
        {"type": "browser"},
        {"type": "puck_bridge"},
        {"secure_storage": "plaintext_file"},
    ],
)
def test_private_puck_invalid_request_creates_no_pending_state(room_home, changes):
    _, _, _, credentials, _, call = room_home
    _, response = _request(call, **changes)
    assert response.status == 400
    assert credentials.read_state()["requests"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"rooms": []},
        {"rooms": ["hall"]},
        {"rooms": ["kitchen", "hall"]},
        {"capabilities": []},
        {"capabilities": ["client_claim"]},
        {"capabilities": ["wake_claim", "health_view"]},
        {"wake_mapping_grant": {"mode": "selected", "ids": []}},
        {"wake_mapping_grant": {"mode": "selected", "ids": ["missing"]}},
        {"touch_binding": {"room_id": "kitchen", "profile_id": "family"}},
        {"client_grants": [{"profile_id": "family"}]},
    ],
)
def test_private_puck_invalid_approval_stays_pending(room_home, changes):
    _, _, _, credentials, _, call = room_home
    _, request = _request(call)
    path = f"/api/v1/enrollment/requests/{request.body['request_id']}/approve"
    response = call("POST", path, _scope(**changes), admin=True)
    assert response.status == 400
    assert credentials.read_state()["requests"][0]["status"] == "pending"
    assert credentials.read_state()["credentials"] == []


@pytest.mark.parametrize("unavailable", ["room", "inactive", "profile"])
def test_private_puck_approval_requires_current_configuration(room_home, unavailable):
    _, _, configuration, credentials, _, call = room_home
    _, request = _request(call)
    snapshot = deepcopy(CONFIGURATION)
    snapshot["devices"] = []
    if unavailable == "room":
        snapshot["rooms"] = []
    elif unavailable == "inactive":
        snapshot["wake_mappings"][0]["active"] = False
    else:
        snapshot["profiles"][0]["available"] = False
    configuration.replace(expected_revision=1, candidate=snapshot)
    response = call(
        "POST",
        f"/api/v1/enrollment/requests/{request.body['request_id']}/approve",
        _scope(),
        admin=True,
    )
    assert response.status == 400
    assert credentials.read_state()["requests"][0]["status"] == "pending"


def test_private_puck_scope_is_enforced_at_domain_approval(room_home):
    _, service, _, _, _, call = room_home
    _, request = _request(call)
    for scope in (
        CredentialScope.from_values(rooms=["kitchen"], capabilities=["wake_claim"]),
        CredentialScope.from_values(
            rooms=["kitchen"],
            capabilities=["wake_claim"],
            wake_mappings=["hey-hermes"],
            touch_binding=TouchBinding("kitchen", "family"),
        ),
    ):
        with pytest.raises(CredentialValidationError):
            service.approve_request(
                request.body["request_id"],
                scope,
                configured_rooms=["kitchen"],
                configured_wake_mappings=["hey-hermes"],
                configured_profiles=["family"],
            )


def test_private_puck_unreadable_configuration_cannot_approve(room_home, monkeypatch):
    _, _, configuration, credentials, _, call = room_home
    _, request = _request(call)

    def unavailable():
        raise OSError("configuration unavailable")

    monkeypatch.setattr(configuration, "read", unavailable)
    response = call(
        "POST",
        f"/api/v1/enrollment/requests/{request.body['request_id']}/approve",
        _scope(),
        admin=True,
    )
    assert response.status == 503
    assert credentials.read_state()["requests"][0]["status"] == "pending"


@pytest.mark.parametrize("empty", [False, True])
def test_private_puck_all_current_mapping_grant_is_a_nonempty_snapshot(
    room_home, empty
):
    _, _, configuration, _, _, call = room_home
    if empty:
        candidate = deepcopy(CONFIGURATION)
        candidate["devices"] = []
        candidate["wake_mappings"][0]["active"] = False
        configuration.replace(expected_revision=1, candidate=candidate)
    submitted, request = _request(call)
    path = f"/api/v1/enrollment/requests/{request.body['request_id']}"
    approved = call(
        "POST",
        path + "/approve",
        _scope(wake_mapping_grant={"mode": "all_current_profiles"}),
        admin=True,
    )
    if empty:
        assert approved.status == 400
        assert (
            call("GET", "/api/v1/enrollment/requests", admin=True).body["requests"][0][
                "status"
            ]
            == "pending"
        )
        return
    assert approved.status == 200
    candidate = deepcopy(CONFIGURATION)
    candidate["devices"] = []
    candidate["wake_mappings"].append(
        {
            "id": "later",
            "phrase": "Later phrase",
            "profile_id": "family",
            "active": True,
        }
    )
    configuration.replace(expected_revision=1, candidate=candidate)
    consumed = call(
        "POST",
        path + "/consume",
        {
            "schema": 1,
            "enrollment_code": submitted["enrollment_code"],
            "secure_storage": "service_private_file",
        },
    )
    assert consumed.status == 200
    assert consumed.body["scope"]["wake_mappings"] == ["hey-hermes"]
    discovered = call(
        "GET",
        f"/api/v1/devices/{consumed.body['device_id']}/configuration",
        credential=consumed.body["credential"],
    )
    assert discovered.body["snapshot"]["wake_mappings"] == [
        {"id": "hey-hermes", "phrase": "Hey Hermes"},
    ]


def test_platform_store_puck_retains_multi_room_enrollment(room_home):
    _, _, configuration, _, _, call = room_home
    candidate = deepcopy(CONFIGURATION)
    candidate["devices"] = []
    candidate["rooms"].append({"id": "hall", "name": "Hall"})
    configuration.replace(expected_revision=1, candidate=candidate)
    submitted, request = _request(
        call,
        requested_rooms=["kitchen", "hall"],
        secure_storage="platform_secure_store",
    )
    assert request.status == 200
    path = f"/api/v1/enrollment/requests/{request.body['request_id']}"
    assert (
        call(
            "POST", path + "/approve", _scope(rooms=["kitchen", "hall"]), admin=True
        ).status
        == 200
    )
    consumed = call(
        "POST",
        path + "/consume",
        {
            "schema": 1,
            "enrollment_code": submitted["enrollment_code"],
            "secure_storage": "platform_secure_store",
        },
    )
    assert consumed.status == 200
    assert consumed.body["scope"]["rooms"] == ["kitchen", "hall"]


def test_approved_private_puck_scope_and_attestation_survive_reopen(
    room_home, tmp_path
):
    _, _, _, credentials, now, call = room_home
    submitted, request = _request(call)
    path = f"/api/v1/enrollment/requests/{request.body['request_id']}"
    assert call("POST", path + "/approve", _scope(), admin=True).status == 200
    credentials.close()
    reopened = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    try:
        recovered = CredentialService(
            store=reopened,
            root_secret=b"r" * 32,
            clock=lambda: now[0],
        )
        expected = CredentialScope.from_values(
            rooms=["kitchen"],
            capabilities=["wake_claim"],
            wake_mappings=["hey-hermes"],
        )
        restored = recovered.list_requests()[0]
        assert restored.endpoint_type == "puck"
        assert restored.approved_scope == expected
        with pytest.raises(CredentialValidationError, match="attestation changed"):
            recovered.consume_request(
                request.body["request_id"],
                enrollment_code=submitted["enrollment_code"],
                secure_storage="platform_secure_store",
            )
        material = recovered.consume_request(
            request.body["request_id"],
            enrollment_code=submitted["enrollment_code"],
            secure_storage="service_private_file",
        )
        assert material.scope == expected
        assert recovered.authenticate_device(material.credential).scope == expected
    finally:
        reopened.close()


def test_real_loopback_private_puck_enrollment_lifecycle(room_home, caplog):
    app, service, configuration, credentials, now, _ = room_home
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(method, path, body=None, *, admin=False, credential=None):
        headers = {"Content-Type": "application/json"}
        if admin:
            headers["Authorization"] = "Bearer local-test-admin"
        elif credential is not None:
            headers["Authorization"] = f"Device {credential}"
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request(
                method, path, json.dumps(body) if body else None, headers
            )
            response = connection.getresponse()
            return HTTPResponse(response.status, json.loads(response.read()))
        finally:
            connection.close()

    try:
        submitted, request = _request(call)
        assert request.status == 200
        request_path = f"/api/v1/enrollment/requests/{request.body['request_id']}"
        consume = {
            "schema": 1,
            "enrollment_code": submitted["enrollment_code"],
            "secure_storage": "service_private_file",
        }
        assert call("POST", request_path + "/consume", consume).status == 409
        assert call("POST", request_path + "/approve", _scope()).status == 401
        listed = call("GET", "/api/v1/enrollment/requests", admin=True)
        assert (
            listed.body["requests"][0]["confirmation_code"]
            == request.body["confirmation_code"]
        )
        assert submitted["enrollment_code"] not in json.dumps(listed.body)
        assert (
            call("POST", request_path + "/approve", _scope(), admin=True).status == 200
        )
        for change, status in (
            ({"secure_storage": "platform_secure_store"}, 400),
            ({"secure_storage": "plaintext_file"}, 400),
            ({"type": "browser"}, 400),
            ({"enrollment_code": "wrong-code"}, 401),
        ):
            assert (
                call("POST", request_path + "/consume", consume | change).status
                == status
            )
        material = call("POST", request_path + "/consume", consume)
        assert material.status == 200
        device = material.body["device_id"]
        token = material.body["credential"]
        assert material.body["scope"] == {
            "rooms": ["kitchen"],
            "capabilities": ["wake_claim"],
            "wake_mappings": ["hey-hermes"],
        }
        assert not material.body.get("client_grants")
        assert call("POST", request_path + "/consume", consume).status == 410
        assert (
            call("POST", request_path + "/approve", _scope(), credential=token).status
            == 401
        )
        # Reuse the published synthetic contract fixture solely to prove denial;
        # this is not calibrated acoustic evidence or a live wake exercise.
        claim = json.loads(
            (Path(__file__).parent / "fixtures/wake-claim.json").read_text()
        )
        claim.update(device_id=device, configuration_revision=1)
        assert (
            call("POST", "/api/v1/wake-claims", claim, credential=token).status == 404
        )
        assert call("PUT", "/api/v1/configuration", {}, credential=token).status == 401
        candidate = deepcopy(CONFIGURATION)
        candidate["devices"][0]["id"] = device
        publish = {"schema": 1, "expected_revision": 1, "snapshot": candidate}
        assert call("PUT", "/api/v1/configuration", publish, admin=True).status == 200
        assert call("PUT", "/api/v1/configuration", publish, admin=True).status == 409
        config_path = f"/api/v1/devices/{device}/configuration"
        discovered = call("GET", config_path, credential=token)
        assert discovered.body["snapshot"]["wake_mappings"] == [
            {"id": "hey-hermes", "phrase": "Hey Hermes"}
        ]
        claim["configuration_revision"] = 2
        assert (
            call(
                "POST",
                "/api/v1/wake-claims",
                claim | {"wake_mapping_id": "unapproved"},
                credential=token,
            ).status
            == 403
        )
        missing_evidence = {
            key: value for key, value in claim.items() if key != "acoustic_evidence"
        }
        assert (
            call(
                "POST", "/api/v1/wake-claims", missing_evidence, credential=token
            ).status
            == 400
        )
        candidate["rooms"].append({"id": "hall", "name": "Hall"})
        candidate["devices"][0]["room_id"] = "hall"
        assert (
            call(
                "PUT",
                "/api/v1/configuration",
                {"schema": 1, "expected_revision": 2, "snapshot": candidate},
                admin=True,
            ).status
            == 200
        )
        claim["configuration_revision"] = 3
        assert (
            call("POST", "/api/v1/wake-claims", claim, credential=token).status == 403
        )
        candidate["devices"][0]["room_id"] = "kitchen"
        assert (
            call(
                "PUT",
                "/api/v1/configuration",
                {"schema": 1, "expected_revision": 3, "snapshot": candidate},
                admin=True,
            ).status
            == 200
        )
        browser_offer, browser_request = _request(
            call,
            endpoint_id="separate-browser",
            type="browser",
            requested_rooms=[],
            requested_capabilities=["client_claim"],
        )
        browser_path = (
            f"/api/v1/enrollment/requests/{browser_request.body['request_id']}"
        )
        browser_scope = _scope(
            rooms=[],
            capabilities=["client_claim"],
            wake_mapping_grant={"mode": "selected", "ids": []},
            client_grants=[{"profile_id": "family"}],
        )
        assert (
            call("POST", browser_path + "/approve", browser_scope, admin=True).status
            == 200
        )
        browser = call(
            "POST",
            browser_path + "/consume",
            consume
            | {
                "enrollment_code": browser_offer["enrollment_code"],
            },
        )
        assert browser.status == 200
        assert (
            call("GET", config_path, credential=browser.body["credential"]).status
            == 401
        )
        renew_path = f"/api/v1/devices/{device}/credentials/renew"
        renewal = {"schema": 1, "request_id": "renew-private", "generation": 1}
        assert call("POST", renew_path, renewal, credential=token).status == 409
        now[0] = material.body["expires_at"] - 60
        renewed = call("POST", renew_path, renewal, credential=token)
        assert renewed.status == 200
        assert renewed.body["scope"] == material.body["scope"]
        assert call("POST", renew_path, renewal, credential=token).body == renewed.body
        assert call("GET", config_path, credential=token).status == 401
        replacement = renewed.body["credential"]
        rotated = call(
            "POST",
            f"/api/v1/devices/{device}/credentials/rotate",
            {"schema": 1, "request_id": "rotate-private", "generation": 2},
            admin=True,
        )
        assert rotated.status == 200
        assert call("GET", config_path, credential=replacement).status == 401
        active = rotated.body["credential"]
        assert call("GET", config_path, credential=active).status == 200
        assert (
            call(
                "POST", f"/api/v1/devices/{device}/revoke", {"schema": 1}, admin=True
            ).status
            == 200
        )
        for old in (token, replacement, active):
            assert call("GET", config_path, credential=old).status == 401
        assert call("POST", renew_path, renewal, credential=token).status == 401
        again, new_request = _request(call)
        again_path = f"/api/v1/enrollment/requests/{new_request.body['request_id']}"
        assert call("POST", again_path + "/approve", _scope(), admin=True).status == 200
        reenrolled = call(
            "POST",
            again_path + "/consume",
            consume
            | {
                "enrollment_code": again["enrollment_code"],
            },
        )
        assert reenrolled.status == 200
        assert reenrolled.body["device_id"] == device
        assert reenrolled.body["generation"] == 4
        assert call("GET", config_path, credential=active).status == 401
        assert (
            call("GET", config_path, credential=reenrolled.body["credential"]).status
            == 200
        )
        redacted = json.dumps(
            call("GET", "/api/v1/enrollment/requests", admin=True).body
        )
        redacted += json.dumps(configuration.read()) + json.dumps(
            credentials.read_state()
        )
        redacted += caplog.text
        for secret in (
            submitted["enrollment_code"],
            token,
            replacement,
            active,
            again["enrollment_code"],
            reenrolled.body["credential"],
            browser_offer["enrollment_code"],
            browser.body["credential"],
        ):
            assert secret not in redacted
        assert service.authenticate_device(reenrolled.body["credential"]) is not None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
