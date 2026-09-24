import itertools
import json

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.bridge.production import ConversationGrantStore
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.domain.credentials import CredentialService
from hermes_home.storage.credentials import SQLiteCredentialStore
from hermes_home.storage.sqlite import SQLiteConfigurationStore

HOST = "home.example.ts.net"
ORIGIN = f"https://{HOST}"
CONFIGURATION = {
    "rooms": [{"id": "kitchen", "name": "Kitchen"}],
    "profiles": [
        {"id": "amanda", "name": "Amanda", "available": True},
        {"id": "spark", "name": "Spark", "available": True, "shared": True},
    ],
    "wake_mappings": [],
    "devices": [],
}


class Page:
    def __init__(self, tmp_path) -> None:
        ids = itertools.count(1)
        tokens = itertools.count(1)
        self.configuration = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
        self.configuration.replace(expected_revision=0, candidate=CONFIGURATION)
        self.claims = ConversationGrantStore(
            tmp_path / "home.sqlite3", configuration=self.configuration.read
        )
        self.service = CredentialService(
            store=SQLiteCredentialStore(tmp_path / "home.sqlite3"),
            root_secret=b"r" * 32,
            id_factory=lambda: f"id-{next(ids)}",
            token_factory=lambda: f"token-{next(tokens)}",
            short_code_factory=lambda: "K7Q4MX2PNV",
            revocation_observer=self.claims,
        )
        self.app = HomeApplication(
            configuration_store=self.configuration,
            arbitration_engine=ArbitrationEngine(configuration=self.configuration.read),
            admin_token="admin-secret",
            device_credentials={},
            credential_service=self.service,
            conversation_claim_store=self.claims,
        )
        self.cookie: str | None = None

    def call(self, method, path, body=None, *, origin=ORIGIN, cookie=True, device=None):
        headers = {"Host": HOST, "Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        if cookie and self.cookie:
            headers["Cookie"] = self.cookie
        if device is not None:
            headers["Authorization"] = f"Device {device}"
        payload = b"" if body is None else json.dumps(body).encode()
        return self.app.handle(method, path, headers, payload)

    def sign_in(self):
        response = self.call(
            "POST", "/pair/api/session", {"admin_token": "admin-secret"}
        )
        assert response.status == 200
        cookie = dict(response.headers)["Set-Cookie"]
        self.cookie = cookie.split(";", 1)[0]
        return cookie

    def request_pairing(self, code="k7q4m-x2pnv", endpoint_type="tui"):
        response = self.call(
            "POST",
            "/api/v1/enrollment/requests",
            {
                "schema": 1,
                "enrollment_code": code,
                "endpoint_id": f"{endpoint_type}-1",
                "label": "Amanda's MacBook",
                "type": endpoint_type,
                "requested_rooms": [],
                "requested_capabilities": ["client_claim"],
                "secure_storage": "platform_secure_store",
            },
            cookie=False,
        )
        assert response.status == 200, response.body
        return response.body


@pytest.fixture
def page(tmp_path):
    return Page(tmp_path)


def test_page_is_self_contained_with_a_strict_policy(page) -> None:
    response = page.call("GET", "/pair", cookie=False)

    headers = dict(response.headers)
    assert response.status == 200
    assert response.content_type.startswith("text/html")
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "https://" not in response.body
    assert "__NONCE__" not in response.body


def test_sign_in_sets_a_hardened_session_cookie(page) -> None:
    cookie = page.sign_in()

    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=Strict" in cookie
    assert "admin-secret" not in cookie


def test_wrong_token_and_missing_session_are_refused(page) -> None:
    wrong = page.call("POST", "/pair/api/session", {"admin_token": "nope"})
    state = page.call("GET", "/pair/api/state")

    assert wrong.status == 401
    assert state.status == 401


def test_state_changes_need_a_same_origin_browser_request(page) -> None:
    page.sign_in()

    foreign = page.call("POST", "/pair/api/offers", {}, origin="https://evil.example")
    missing = page.call("POST", "/pair/api/offers", {}, origin=None)

    assert foreign.status == 403
    assert missing.status == 403


def test_offer_shows_a_typeable_code_link_and_qr(page) -> None:
    page.sign_in()

    offer = page.call("POST", "/pair/api/offers", {}).body

    assert offer["code"] == "K7Q4M-X2PNV"
    assert offer["home"] == ORIGIN
    assert offer["link"] == (
        "hermes-home://pair?home=https%3A%2F%2Fhome.example.ts.net&code=K7Q4MX2PNV"
    )
    assert offer["qr_svg"].startswith("<svg")


def test_page_approval_pairs_a_tui_end_to_end(page) -> None:
    page.sign_in()
    page.call("POST", "/pair/api/offers", {})
    request = page.request_pairing()

    waiting = page.call("GET", "/pair/api/state").body["requests"]
    assert [(r["label"], r["confirmation_code"]) for r in waiting] == [
        ("Amanda's MacBook", request["confirmation_code"])
    ]
    approved = page.call(
        "POST",
        f"/pair/api/requests/{request['request_id']}/approve",
        {"profiles": ["amanda", "spark"]},
    )
    assert approved.status == 200, approved.body

    material = page.call(
        "POST",
        f"/api/v1/enrollment/requests/{request['request_id']}/consume",
        {
            "schema": 1,
            "enrollment_code": "K7Q4MX2PNV",
            "secure_storage": "platform_secure_store",
        },
        cookie=False,
    )

    assert material.status == 200, material.body
    assert {g["label"] for g in material.body["client_grants"]} == {"Amanda", "Spark"}
    (device,) = page.call("GET", "/pair/api/state").body["devices"]
    assert device["label"] == "Amanda's MacBook"
    assert {(g["profile"], g["status"]) for g in device["grants"]} == {
        ("Amanda", "active"),
        ("Spark", "active"),
    }


def test_room_devices_cannot_be_approved_from_the_page(page) -> None:
    page.sign_in()
    page.call("POST", "/pair/api/offers", {})
    request = page.request_pairing(endpoint_type="puck")

    response = page.call(
        "POST",
        f"/pair/api/requests/{request['request_id']}/approve",
        {"profiles": ["amanda"]},
    )

    assert response.status == 400
    assert response.body["error"]["code"] == "unsupported_endpoint_type"


def test_page_can_reject_unpair_and_remove_a_profile(page) -> None:
    page.sign_in()
    page.call("POST", "/pair/api/offers", {})
    request = page.request_pairing()
    page.call(
        "POST",
        f"/pair/api/requests/{request['request_id']}/approve",
        {"profiles": ["amanda", "spark"]},
    )
    material = page.call(
        "POST",
        f"/api/v1/enrollment/requests/{request['request_id']}/consume",
        {
            "schema": 1,
            "enrollment_code": "K7Q4MX2PNV",
            "secure_storage": "platform_secure_store",
        },
        cookie=False,
    ).body
    spark = next(g for g in material["client_grants"] if g["label"] == "Spark")

    removed = page.call("POST", f"/pair/api/grants/{spark['grant_id']}/revoke", {})
    assert removed.status == 200
    (device,) = page.call("GET", "/pair/api/state").body["devices"]
    assert [g["profile"] for g in device["grants"]] == ["Amanda"]

    unpaired = page.call(
        "POST", f"/pair/api/devices/{material['device_id']}/revoke", {}
    )
    assert unpaired.status == 200
    assert page.call("GET", "/pair/api/state").body["devices"] == []


def test_sign_out_ends_the_session(page) -> None:
    page.sign_in()

    page.call("POST", "/pair/api/logout", {})

    assert page.call("GET", "/pair/api/state").status == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/enrollment/offers"),
        ("GET", "/api/v1/enrollment/requests"),
        ("POST", "/api/v1/enrollment/requests/r-1/approve"),
        ("POST", "/api/v1/devices/d-1/revoke"),
        ("GET", "/api/v1/configuration"),
        ("GET", "/metrics"),
        ("POST", "/api/v1/devices/d-1/credentials/rotate"),
        ("POST", "/api/v1/enrollment/requests/r-1/reject"),
        ("GET", "/api/v1/diagnostics/status"),
    ],
)
@pytest.mark.parametrize(
    "proxy_header", [("X-Forwarded-For", "100.64.0.9"), ("Forwarded", "for=100.64.0.9")]
)
def test_admin_token_routes_refuse_proxied_requests(
    page, method, path, proxy_header
) -> None:
    headers = {
        "Authorization": "Bearer admin-secret",
        "Content-Type": "application/json",
        proxy_header[0]: proxy_header[1],
    }

    response = page.app.handle(method, path, headers, b'{"schema": 1}')

    assert response.status == 403
    assert response.body["error"]["code"] == "admin_local_only"


def test_device_routes_still_work_through_the_proxy(page) -> None:
    page.sign_in()
    page.call("POST", "/pair/api/offers", {})
    headers = {"Content-Type": "application/json", "X-Forwarded-For": "100.64.0.9"}
    body = {
        "schema": 1,
        "enrollment_code": "K7Q4MX2PNV",
        "endpoint_id": "tui-1",
        "label": "Laptop",
        "type": "tui",
        "requested_rooms": [],
        "requested_capabilities": ["client_claim"],
        "secure_storage": "platform_secure_store",
    }

    response = page.app.handle(
        "POST", "/api/v1/enrollment/requests", headers, json.dumps(body).encode()
    )

    assert response.status == 200


def _paired(page, profiles):
    page.call("POST", "/pair/api/offers", {})
    request = page.request_pairing()
    page.call(
        "POST",
        f"/pair/api/requests/{request['request_id']}/approve",
        {"profiles": profiles},
    )
    return page.call(
        "POST",
        f"/api/v1/enrollment/requests/{request['request_id']}/consume",
        {
            "schema": 1,
            "enrollment_code": "K7Q4MX2PNV",
            "secure_storage": "platform_secure_store",
        },
        cookie=False,
    ).body


def test_waiting_request_shows_what_the_device_asked_for(page) -> None:
    page.sign_in()
    page.call("POST", "/pair/api/offers", {})
    page.request_pairing()

    (request,) = page.call("GET", "/pair/api/state").body["requests"]

    assert request["requested_capabilities"] == ["client_claim"]
    assert request["requested_rooms"] == []


def test_offers_need_an_https_home_address(page) -> None:
    page.sign_in()

    response = page.call("POST", "/pair/api/offers", {}, origin=f"http://{HOST}")

    assert response.status == 409
    assert response.body["error"]["code"] == "https_required"


def test_pair_again_preselects_the_previous_profiles(page) -> None:
    page.sign_in()

    page.call("POST", "/pair/api/offers", {"profiles": ["amanda", "spark"]})
    page.request_pairing()

    (request,) = page.call("GET", "/pair/api/state").body["requests"]
    assert request["preselected_profiles"] == ["amanda", "spark"]


def test_removing_a_profile_on_the_page_closes_its_live_claims(page) -> None:
    page.sign_in()
    material = _paired(page, ["amanda"])
    (grant,) = material["client_grants"]
    handle = page.claims.create_client_claim(
        claim_id="claim-1",
        device_id=material["device_id"],
        grant_id=grant["grant_id"],
        profile_id="amanda",
        configuration_revision=1,
        credential_generation=1,
    )

    page.call("POST", f"/pair/api/grants/{grant['grant_id']}/revoke", {})

    assert page.claims.resolve(handle, material["device_id"]) is None


def test_page_actions_use_the_matching_status(page) -> None:
    page.sign_in()

    missing = page.call("POST", "/pair/api/grants/nope/revoke", {})

    assert missing.status == 404


def test_page_sessions_expire_and_the_oldest_is_evicted() -> None:
    from hermes_home.api import pairing

    clock = [1_000.0]
    tokens = iter(f"session-{index}" for index in range(100))
    surface = pairing.PairingSurface(
        authenticate_admin=lambda headers: True,
        credential_service=object(),
        configuration=dict,
        clock=lambda: clock[0],
        token_factory=lambda: next(tokens),
    )
    headers = {"Host": HOST, "Origin": ORIGIN, "Content-Type": "application/json"}
    body = json.dumps({"admin_token": "anything"}).encode()

    for _ in range(pairing.MAX_SESSIONS + 1):
        surface.handle("POST", "/pair/api/session", headers, body)

    assert not surface._signed_in({"Cookie": "hermes_home_pair=session-0"})
    assert surface._signed_in({"Cookie": "hermes_home_pair=session-1"})
    clock[0] += pairing.SESSION_SECONDS
    assert not surface._signed_in({"Cookie": "hermes_home_pair=session-1"})
