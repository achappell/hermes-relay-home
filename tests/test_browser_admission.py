import copy
import http.client
import json
import threading

import pytest

from hermes_home.api.server import create_server
from hermes_home.domain.credentials import (
    CredentialStateError,
    CredentialValidationError,
)
from tests.test_client_claims_api import ADMIN, CONFIGURATION, Home
from tests.test_client_grants import PROFILES, _pair, _service


@pytest.mark.parametrize("kind", ["tui", "ios", "macos", "android", "browser"])
def test_storage_attestation_is_endpoint_specific(kind):
    service = _service()
    offer = service.create_offer()
    wrong = "platform_secure_store" if kind == "browser" else "service_private_file"
    with pytest.raises(CredentialValidationError):
        service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id=kind,
            label=kind,
            endpoint_type=kind,
            requested_rooms=[],
            requested_capabilities=["client_claim"],
            secure_storage=wrong,
        )


@pytest.mark.parametrize(
    "capability",
    [
        "sensitive_entry",
        "consequence_confirm",
        "health_view",
        "wake_claim",
        "touch_claim",
    ],
)
def test_browser_cannot_request_protected_capability(capability):
    service = _service()
    offer = service.create_offer()
    with pytest.raises(CredentialValidationError):
        service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="browser",
            label="Browser",
            endpoint_type="browser",
            requested_rooms=[],
            requested_capabilities=["client_claim", capability],
            secure_storage="service_private_file",
        )


@pytest.mark.parametrize("approve", [True, False])
def test_browser_cannot_decide_at_domain_boundary(approve):
    service = _service()
    browser = _pair(
        service, endpoint_id="browser", endpoint_type="browser", profiles=["amanda"]
    )
    other = _pair(service, endpoint_id="phone", profiles=["amanda"])
    with pytest.raises(CredentialStateError, match="forbidden"):
        service.decide_owner_grant(
            browser.device_id, other.client_grants[0].grant_id, approve=approve
        )
    assert service.client_grants(other.device_id)[0].status == "pending_owner"


def test_append_authorization_idempotency_and_identity():
    service = _service()
    owner = _pair(service, endpoint_id="owner", profiles=["amanda"])
    browser = _pair(
        service,
        endpoint_id="browser",
        endpoint_type="browser",
        profiles=["spark"],
        shared=("spark",),
    )
    kwargs = {
        "configured_profiles": PROFILES,
        "profile_labels": {profile: profile.title() for profile in PROFILES},
        "shared_profiles": ("spark",),
    }
    pending = service.add_client_grant(
        browser.device_id,
        "amanda",
        idempotency_key="owned",
        authorize_bootstrap=True,
        **kwargs,
    )
    assert pending.status == "pending_owner"
    assert (
        service.add_client_grant(
            browser.device_id,
            "amanda",
            idempotency_key="owned",
            authorize_bootstrap=True,
            **kwargs,
        )
        == pending
    )
    with pytest.raises(CredentialStateError, match="conflict"):
        service.add_client_grant(
            browser.device_id,
            "jensen",
            idempotency_key="owned",
            authorize_bootstrap=True,
            **kwargs,
        )
    active = service.decide_owner_grant(owner.device_id, pending.grant_id, approve=True)
    assert active.grant_id == pending.grant_id
    assert len(service.client_grants(browser.device_id)) == 2
    service.revoke_client_grant(active.grant_id)
    replay = service.add_client_grant(
        browser.device_id,
        "amanda",
        idempotency_key="owned",
        authorize_bootstrap=True,
        **kwargs,
    )
    assert replay.status == "revoked"
    replacement = service.add_client_grant(
        browser.device_id, "amanda", idempotency_key="owned-again", **kwargs
    )
    assert replacement.grant_id != active.grant_id
    assert service.active_client_grant(browser.device_id, active.grant_id) is None
    first_holder = service.add_client_grant(
        browser.device_id, "jensen", idempotency_key="first", **kwargs
    )
    assert first_holder.status == "pending_owner"
    service.revoke_client_grant(first_holder.grant_id)
    bootstrap = service.add_client_grant(
        browser.device_id,
        "jensen",
        idempotency_key="bootstrap",
        authorize_bootstrap=True,
        **kwargs,
    )
    assert bootstrap.status == "active" and bootstrap.bootstrap
    with pytest.raises(CredentialValidationError):
        service.add_client_grant(
            browser.device_id, "*", idempotency_key="wildcard", **kwargs
        )


def test_browser_http_permissions_append_discovery_rename_and_revoke(tmp_path):
    home = Home(tmp_path)
    browser = home.pair("browser", ["amanda"], "browser")
    other = home.pair("other", ["amanda"])
    credential = browser["credential"]
    device_id = browser["device_id"]
    grant_id = other["client_grants"][0]["grant_id"]
    for action in ("approve", "reject"):
        result = home.call(
            "POST",
            f"/api/v1/profile-grants/{grant_id}/{action}",
            {"schema": 1},
            credential=credential,
        )
        assert result.status == 403, result.body
    path = f"/api/v1/devices/{device_id}/profile-grants"
    body = {"schema": 1, "profile_id": "spark", "idempotency_key": "later"}
    assert home.call("POST", path, body, credential=credential).status == 401
    proxied = dict(ADMIN, **{"X-Forwarded-For": "127.0.0.1"})
    assert home.app.handle("POST", path, proxied, json.dumps(body)).status == 403
    config_path = f"/api/v1/devices/{device_id}/configuration"
    before = home.call("GET", config_path, credential=credential).body["snapshot"]
    added = home.call("POST", path, body, admin=True)
    assert added.status == 200, added.body
    assert home.call("POST", path, body, admin=True).body == added.body
    after = home.call("GET", config_path, credential=credential).body["snapshot"]
    assert len(after["client_grants"]) == 2
    assert after["revision"] > before["revision"]
    candidate = copy.deepcopy(CONFIGURATION)
    candidate["profiles"][2]["name"] = "Amanda"
    collision = home.call(
        "PUT",
        "/api/v1/configuration",
        {"schema": 1, "expected_revision": 1, "snapshot": candidate},
        admin=True,
    )
    assert collision.status == 400, collision.body
    candidate["profiles"][2]["name"] = "Shared renamed"
    renamed = home.call(
        "PUT",
        "/api/v1/configuration",
        {"schema": 1, "expected_revision": 1, "snapshot": candidate},
        admin=True,
    )
    assert renamed.status == 200, renamed.body
    final = home.call("GET", config_path, credential=credential).body["snapshot"]
    assert final["revision"] > after["revision"]
    assert (
        next(g for g in final["client_grants"] if g["label"] == "Shared renamed")[
            "grant_id"
        ]
        == added.body["grant"]["grant_id"]
    )


def test_real_loopback_browser_contract_smoke(tmp_path):
    home = Home(tmp_path)
    home.app._session_directory = None
    # Real HTTP transport and SQLite-backed Home policy; no remote host or grants.
    server = create_server(home.app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(method, path, body=None, *, credential=None, admin=False):
        headers = dict(ADMIN) if admin else {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Device {credential}"
        connection = http.client.HTTPConnection(*server.server_address)
        connection.request(
            method,
            path,
            body=None if body is None else json.dumps(body),
            headers=headers,
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        status = response.status
        connection.close()
        from hermes_home.api.responses import HTTPResponse

        return HTTPResponse(status, payload)

    home.call = call
    try:
        browser = home.pair("smoke-browser", ["spark"], "browser")
        credential, device = browser["credential"], browser["device_id"]
        config_path = f"/api/v1/devices/{device}/configuration"
        before = call("GET", config_path, credential=credential).body["snapshot"]
        assert (
            call(
                "GET", f"/api/v1/devices/{device}/health", credential=credential
            ).status
            == 403
        )
        add_path = f"/api/v1/devices/{device}/profile-grants"
        add_body = {
            "schema": 1,
            "profile_id": "amanda",
            "idempotency_key": "http-later",
            "authorize_bootstrap": True,
        }
        assert call("POST", add_path, add_body, credential=credential).status == 401
        added = call("POST", add_path, add_body, admin=True)
        assert added.status == 200, added.body
        assert call("POST", add_path, add_body, admin=True).body == added.body
        after = call("GET", config_path, credential=credential).body["snapshot"]
        assert (
            after["revision"] > before["revision"] and len(after["client_grants"]) == 2
        )
        grant = added.body["grant"]["grant_id"]
        pending_device = home.pair("smoke-pending", ["amanda"])
        pending_grant = pending_device["client_grants"][0]["grant_id"]
        for action in ("approve", "reject"):
            assert (
                call(
                    "POST",
                    f"/api/v1/profile-grants/{pending_grant}/{action}",
                    {"schema": 1},
                    credential=credential,
                ).status
                == 403
            )
        claim = call(
            "POST",
            "/api/v1/client-claims",
            {
                "schema": 1,
                "claim_id": "smoke-claim",
                "device_id": device,
                "configuration_revision": after["revision"],
                "grant_id": grant,
            },
            credential=credential,
        )
        assert claim.status == 200, claim.body
        handle = claim.body["conversation_handle"]
        assert home.claims.resolve(handle, device) is not None
        assert (
            call(
                "POST",
                f"/api/v1/profile-grants/{grant}/revoke",
                {"schema": 1},
                credential=credential,
            ).status
            == 200
        )
        assert home.claims.resolve(handle, device) is None
        final = call("GET", config_path, credential=credential).body["snapshot"]
        assert grant not in {g["grant_id"] for g in final["client_grants"]}
        assert (
            call(
                "POST", f"/api/v1/devices/{device}/revoke", {"schema": 1}, admin=True
            ).status
            == 200
        )
        assert call("GET", config_path, credential=credential).status == 401
        print(
            "LOCAL HOME HTTP SMOKE: browser enrollment/consume=200; health=403; browser grant-add=401; approve/reject=403; admin later grant=200; idempotent replay=same grant; discovery revision advanced; claim=200; grant revoke=200 and claim closed; device revoke=200; revoked credential=401"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "substitution",
    [
        {"secure_storage": "platform_secure_store"},
        {"type": "tui"},
        {"type": "tui", "secure_storage": "platform_secure_store"},
    ],
)
def test_browser_consume_rejects_attestation_or_type_substitution(
    tmp_path, substitution
):
    home = Home(tmp_path)
    offer = home.call(
        "POST", "/api/v1/enrollment/offers", {"schema": 1}, admin=True
    ).body
    request = home.call(
        "POST",
        "/api/v1/enrollment/requests",
        {
            "schema": 1,
            "enrollment_code": offer["enrollment_code"],
            "endpoint_id": "appliance",
            "label": "Browser",
            "type": "browser",
            "requested_rooms": [],
            "requested_capabilities": ["client_claim"],
            "secure_storage": "service_private_file",
        },
    ).body
    request_id = request["request_id"]
    approved = home.call(
        "POST",
        f"/api/v1/enrollment/requests/{request_id}/approve",
        {
            "schema": 1,
            "scope": {
                "rooms": [],
                "capabilities": ["client_claim"],
                "wake_mapping_grant": {"mode": "selected", "ids": []},
                "client_grants": [{"profile_id": "spark"}],
            },
        },
        admin=True,
    )
    assert approved.status == 200
    original = {
        "schema": 1,
        "enrollment_code": offer["enrollment_code"],
        "secure_storage": "service_private_file",
    }
    denied = home.call(
        "POST",
        f"/api/v1/enrollment/requests/{request_id}/consume",
        original | substitution,
    )
    assert denied.status == 400, denied.body
    consumed = home.call(
        "POST", f"/api/v1/enrollment/requests/{request_id}/consume", original
    )
    assert consumed.status == 200
    assert consumed.body["scope"]["capabilities"] == ["client_claim"]


def test_add_collision_is_rejected_transactionally_and_retry_survives_unavailability():
    service = _service()
    browser = _pair(
        service,
        endpoint_id="browser",
        endpoint_type="browser",
        profiles=["spark"],
        shared=("spark",),
    )
    original = service.client_grants(browser.device_id)
    with pytest.raises(CredentialValidationError, match="unique"):
        service.add_client_grant(
            browser.device_id,
            "amanda",
            idempotency_key="collision",
            configured_profiles=PROFILES,
            profile_labels={"spark": "Same", "amanda": "Same", "jensen": "Other"},
        )
    assert service.client_grants(browser.device_id) == original
    added = service.add_client_grant(
        browser.device_id,
        "amanda",
        idempotency_key="later",
        configured_profiles=PROFILES,
        profile_labels={profile: profile for profile in PROFILES},
        authorize_bootstrap=True,
    )
    replay = service.add_client_grant(
        browser.device_id,
        "amanda",
        idempotency_key="later",
        configured_profiles=[],
        profile_labels={},
        authorize_bootstrap=True,
    )
    assert replay == added


def test_pairing_admin_tool_admits_browser_and_adds_exact_profile(tmp_path):
    from tests.test_pairing_page import Page

    page = Page(tmp_path)
    page.sign_in()
    offer = page.call("POST", "/pair/api/offers", {}).body
    request = page.call(
        "POST",
        "/api/v1/enrollment/requests",
        {
            "schema": 1,
            "enrollment_code": offer["code"],
            "endpoint_id": "browser",
            "label": "Shared browser",
            "type": "browser",
            "requested_rooms": [],
            "requested_capabilities": ["client_claim"],
            "secure_storage": "service_private_file",
        },
        cookie=False,
    )
    assert request.status == 200, request.body
    request_id = request.body["request_id"]
    assert (
        page.call(
            "POST", f"/pair/api/requests/{request_id}/approve", {"profiles": ["spark"]}
        ).status
        == 200
    )
    material = page.call(
        "POST",
        f"/api/v1/enrollment/requests/{request_id}/consume",
        {
            "schema": 1,
            "enrollment_code": offer["code"],
            "secure_storage": "service_private_file",
        },
        cookie=False,
    ).body
    path = f"/pair/api/devices/{material['device_id']}/profile-grants"
    body = {
        "profile_id": "amanda",
        "idempotency_key": "page-add",
        "authorize_bootstrap": True,
    }
    assert (
        page.call(
            "POST", path, body, cookie=False, device=material["credential"]
        ).status
        == 401
    )
    assert (
        page.call("POST", path, body, origin="https://attacker.invalid").status == 403
    )
    added = page.call("POST", path, body)
    assert added.status == 200, added.body
    assert page.call("POST", path, body).body == added.body
    assert len(page.service.client_grants(material["device_id"])) == 2
