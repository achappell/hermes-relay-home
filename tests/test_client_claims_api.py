import itertools
import json

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.bridge.production import ConversationGrantStore
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.domain.credentials import CredentialService
from hermes_home.storage.credentials import SQLiteCredentialStore
from hermes_home.storage.sqlite import SQLiteConfigurationStore

CONFIGURATION = {
    "rooms": [{"id": "kitchen", "name": "Kitchen"}],
    "profiles": [
        {"id": "amanda", "name": "Amanda", "available": True},
        {"id": "jensen", "name": "Jensen", "available": True},
        {"id": "spark", "name": "Spark", "available": True, "shared": True},
    ],
    "wake_mappings": [],
    "devices": [],
}
ADMIN = {"Authorization": "Bearer admin-secret", "Content-Type": "application/json"}


class FakeDirectory:
    def __init__(self) -> None:
        self.sessions = {
            "amanda": [
                {
                    "id": "stored-2",
                    "title": "Groceries",
                    "started_at": 20,
                    "message_count": 4,
                },
                {"id": "stored-1", "title": "", "started_at": 10, "message_count": 2},
            ]
        }
        self.requests: list[tuple[str, str]] = []

    def list_sessions(self, profile_id: str, limit: int):
        self.requests.append(("list", profile_id))
        return self.sessions.get(profile_id, [])[:limit]

    def most_recent(self, profile_id: str):
        self.requests.append(("most_recent", profile_id))
        rows = self.sessions.get(profile_id, [])
        return rows[0]["id"] if rows else None


class Home:
    def __init__(self, tmp_path) -> None:
        ids = itertools.count(1)
        tokens = itertools.count(1)
        handles = itertools.count(1)
        refs = itertools.count(1)
        self.configuration = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
        self.configuration.replace(expected_revision=0, candidate=CONFIGURATION)
        self.claims = ConversationGrantStore(
            tmp_path / "home.sqlite3",
            configuration=self.configuration.read,
            handle_factory=lambda: f"handle-{next(handles)}",
            session_ref_factory=lambda: f"sref-{next(refs)}",
            client_claims_per_device=2,
        )
        self.service = CredentialService(
            store=SQLiteCredentialStore(tmp_path / "home.sqlite3"),
            root_secret=b"r" * 32,
            clock=lambda: 1_000.0,
            id_factory=lambda: f"id-{next(ids)}",
            token_factory=lambda: f"token-{next(tokens)}",
            confirmation_factory=lambda: "ABCD2345",
            revocation_observer=self.claims,
        )
        self.claims.set_client_grant_checker(
            lambda device_id, grant_id: (
                self.service.active_client_grant(device_id, grant_id) is not None
            )
        )
        self.directory = FakeDirectory()
        self.app = HomeApplication(
            configuration_store=self.configuration,
            arbitration_engine=ArbitrationEngine(configuration=self.configuration.read),
            admin_token="admin-secret",
            device_credentials={},
            credential_service=self.service,
            conversation_claim_store=self.claims,
            session_directory=self.directory,
        )

    def call(self, method, path, body=None, *, credential=None, admin=False):
        headers = dict(ADMIN) if admin else {"Content-Type": "application/json"}
        if credential is not None:
            headers["Authorization"] = f"Device {credential}"
        payload = b"" if body is None else json.dumps(body).encode()
        return self.app.handle(method, path, headers, payload)

    def pair(self, endpoint_id: str, profiles: list[str], endpoint_type="tui"):
        offer = self.call(
            "POST", "/api/v1/enrollment/offers", {"schema": 1}, admin=True
        ).body
        request = self.call(
            "POST",
            "/api/v1/enrollment/requests",
            {
                "schema": 1,
                "enrollment_code": offer["enrollment_code"],
                "endpoint_id": endpoint_id,
                "label": f"{endpoint_id} label",
                "type": endpoint_type,
                "requested_rooms": [],
                "requested_capabilities": ["client_claim"],
                "secure_storage": "platform_secure_store",
            },
        ).body
        consume_path = f"/api/v1/enrollment/requests/{request['request_id']}/consume"
        consume_body = {
            "schema": 1,
            "enrollment_code": offer["enrollment_code"],
            "secure_storage": "platform_secure_store",
        }
        pending = self.call("POST", consume_path, consume_body)
        assert pending.status == 409
        assert pending.body["error"]["code"] == "approval_pending"
        approved = self.call(
            "POST",
            f"/api/v1/enrollment/requests/{request['request_id']}/approve",
            {
                "schema": 1,
                "scope": {
                    "rooms": [],
                    "capabilities": ["client_claim"],
                    "wake_mapping_grant": {"mode": "selected", "ids": []},
                    "client_grants": [{"profile_id": p} for p in profiles],
                },
            },
            admin=True,
        )
        assert approved.status == 200, approved.body
        material = self.call("POST", consume_path, consume_body)
        assert material.status == 200, material.body
        return material.body

    def configuration_for(self, material):
        response = self.call(
            "GET",
            f"/api/v1/devices/{material['device_id']}/configuration",
            credential=material["credential"],
        )
        assert response.status == 200, response.body
        return response.body["snapshot"]

    def claim(self, material, grant_id, claim_id, session=None):
        body = {
            "schema": 1,
            "claim_id": claim_id,
            "device_id": material["device_id"],
            "configuration_revision": 1,
            "grant_id": grant_id,
        }
        if session is not None:
            body["session"] = session
        return self.call(
            "POST", "/api/v1/client-claims", body, credential=material["credential"]
        )


@pytest.fixture
def home(tmp_path):
    return Home(tmp_path)


def _grant(material, label):
    return next(g for g in material["client_grants"] if g["label"] == label)


def test_paired_tui_receives_labelled_grants_without_profile_ids(home) -> None:
    material = home.pair("laptop", ["amanda", "spark"])

    assert {g["label"]: g["status"] for g in material["client_grants"]} == {
        "Amanda": "active",
        "Spark": "active",
    }
    assert "amanda" not in json.dumps(material)
    snapshot = home.configuration_for(material)
    assert [g["label"] for g in snapshot["client_grants"]] == ["Amanda", "Spark"]


def test_new_claim_opens_without_a_session(home) -> None:
    material = home.pair("laptop", ["amanda"])
    grant = _grant(material, "Amanda")

    response = home.claim(material, grant["grant_id"], "claim-1")

    assert response.status == 200, response.body
    assert response.body["session"] == {"mode": "new"}
    resolved = home.claims.resolve(
        response.body["conversation_handle"], material["device_id"]
    )
    assert resolved.profile_id == "amanda"
    assert resolved.session_id is None


def test_list_then_resume_a_session_by_reference(home) -> None:
    material = home.pair("laptop", ["amanda"])
    grant = _grant(material, "Amanda")

    listed = home.call(
        "POST",
        "/api/v1/client-sessions/list",
        {"schema": 1, "grant_id": grant["grant_id"], "limit": 10},
        credential=material["credential"],
    )

    assert listed.status == 200, listed.body
    sessions = listed.body["sessions"]
    assert [s["title"] for s in sessions] == ["Groceries", ""]
    assert "stored-" not in json.dumps(listed.body)
    ref = sessions[1]["session_ref"]

    claimed = home.claim(
        material,
        grant["grant_id"],
        "claim-1",
        session={"mode": "resume", "session_ref": ref},
    )

    assert claimed.status == 200, claimed.body
    assert claimed.body["session"] == {"mode": "resumed", "session_ref": ref}
    handle = claimed.body["conversation_handle"]
    assert home.claims.resolve(handle, material["device_id"]).session_id == "stored-1"
    relisted = home.call(
        "POST",
        "/api/v1/client-sessions/list",
        {"schema": 1, "grant_id": grant["grant_id"]},
        credential=material["credential"],
    )
    assert [s["active"] for s in relisted.body["sessions"]] == [False, True]


def test_resuming_a_busy_session_is_refused(home) -> None:
    material = home.pair("laptop", ["amanda"])
    grant = _grant(material, "Amanda")

    first = home.claim(material, grant["grant_id"], "claim-1", {"mode": "most_recent"})
    second = home.claim(material, grant["grant_id"], "claim-2", {"mode": "most_recent"})

    assert first.status == 200
    assert first.body["session"]["mode"] == "resumed"
    assert second.status == 409
    assert second.body["error"]["code"] == "session_busy"


def test_unknown_or_foreign_session_reference_is_unavailable(home) -> None:
    material = home.pair("laptop", ["amanda", "spark"])
    amanda = _grant(material, "Amanda")
    spark = _grant(material, "Spark")
    ref = home.call(
        "POST",
        "/api/v1/client-sessions/list",
        {"schema": 1, "grant_id": amanda["grant_id"]},
        credential=material["credential"],
    ).body["sessions"][0]["session_ref"]

    response = home.claim(
        material,
        spark["grant_id"],
        "claim-1",
        session={"mode": "resume", "session_ref": ref},
    )

    assert response.status == 404
    assert response.body["error"]["code"] == "session_unavailable"


def test_claim_limit_is_enforced_per_device(home) -> None:
    material = home.pair("laptop", ["amanda"])
    grant = _grant(material, "Amanda")
    home.claim(material, grant["grant_id"], "claim-1")
    home.claim(material, grant["grant_id"], "claim-2")

    response = home.claim(material, grant["grant_id"], "claim-3")

    assert response.status == 409
    assert response.body["error"]["code"] == "claim_limit"


def test_owner_approves_a_second_device_from_their_own_client(home) -> None:
    owner = home.pair("jensen-phone", ["jensen"], endpoint_type="ios")
    laptop = home.pair("laptop", ["jensen"])
    pending_grant = _grant(laptop, "Jensen")
    assert pending_grant["status"] == "pending_owner"

    blocked = home.claim(laptop, pending_grant["grant_id"], "claim-1")
    assert blocked.status == 409
    assert blocked.body["error"]["code"] == "grant_pending"

    pending = home.call(
        "GET", "/api/v1/profile-grants/pending", credential=owner["credential"]
    )
    assert pending.status == 200
    (request,) = pending.body["pending"]
    assert request["device_label"] == "laptop label"
    assert request["profile_label"] == "Jensen"
    assert "device_id" not in request

    decided = home.call(
        "POST",
        f"/api/v1/profile-grants/{request['grant_id']}/approve",
        {"schema": 1},
        credential=owner["credential"],
    )
    assert decided.status == 200
    assert home.claim(laptop, pending_grant["grant_id"], "claim-2").status == 200


def test_holders_show_every_device_for_the_profile(home) -> None:
    owner = home.pair("jensen-phone", ["jensen"], endpoint_type="ios")
    home.pair("laptop", ["jensen"])

    holders = home.call(
        "GET", "/api/v1/profile-grants/holders", credential=owner["credential"]
    ).body["holders"]

    assert {(h["device_label"], h["status"], h["this_device"]) for h in holders} == {
        ("jensen-phone label", "active", True),
        ("laptop label", "pending_owner", False),
    }
    bootstrap = next(h for h in holders if h["this_device"])
    assert bootstrap["bootstrap"] is True


def test_owner_revocation_closes_the_devices_claims(home) -> None:
    owner = home.pair("jensen-phone", ["jensen"], endpoint_type="ios")
    laptop = home.pair("laptop", ["jensen"])
    grant = _grant(laptop, "Jensen")
    home.call(
        "POST",
        f"/api/v1/profile-grants/{grant['grant_id']}/approve",
        {"schema": 1},
        credential=owner["credential"],
    )
    claimed = home.claim(laptop, grant["grant_id"], "claim-1")
    handle = claimed.body["conversation_handle"]

    revoked = home.call(
        "POST",
        f"/api/v1/profile-grants/{grant['grant_id']}/revoke",
        {"schema": 1},
        credential=owner["credential"],
    )

    assert revoked.status == 200
    assert home.claims.resolve(handle, laptop["device_id"]) is None


def test_device_revocation_closes_client_claims(home) -> None:
    material = home.pair("laptop", ["amanda"])
    grant = _grant(material, "Amanda")
    handle = home.claim(material, grant["grant_id"], "claim-1").body[
        "conversation_handle"
    ]

    home.service.revoke(material["device_id"])

    assert home.claims.resolve(handle, material["device_id"]) is None


def test_stale_revision_and_unavailable_profile_are_typed(home) -> None:
    material = home.pair("laptop", ["amanda"])
    grant = _grant(material, "Amanda")
    body = {
        "schema": 1,
        "claim_id": "claim-1",
        "device_id": material["device_id"],
        "configuration_revision": 0,
        "grant_id": grant["grant_id"],
    }

    stale = home.call(
        "POST", "/api/v1/client-claims", body, credential=material["credential"]
    )

    assert stale.body["error"]["code"] == "stale_configuration"
    snapshot = home.configuration.read()
    candidate = {
        key: snapshot[key] for key in ("rooms", "profiles", "wake_mappings", "devices")
    }
    candidate["profiles"] = [
        {**profile, "available": profile["id"] != "amanda"}
        for profile in candidate["profiles"]
    ]
    home.configuration.replace(expected_revision=1, candidate=candidate)
    body["configuration_revision"] = 2
    unavailable = home.call(
        "POST", "/api/v1/client-claims", body, credential=material["credential"]
    )
    assert unavailable.body["error"]["code"] == "profile_unavailable"


def test_room_devices_cannot_use_client_claims(home) -> None:
    offer = home.call(
        "POST", "/api/v1/enrollment/offers", {"schema": 1}, admin=True
    ).body
    request = home.call(
        "POST",
        "/api/v1/enrollment/requests",
        {
            "schema": 1,
            "enrollment_code": offer["enrollment_code"],
            "endpoint_id": "puck",
            "label": "Kitchen Puck",
            "type": "puck",
            "requested_rooms": [],
            "requested_capabilities": ["client_claim"],
            "secure_storage": "platform_secure_store",
        },
    ).body

    approved = home.call(
        "POST",
        f"/api/v1/enrollment/requests/{request['request_id']}/approve",
        {
            "schema": 1,
            "scope": {
                "rooms": [],
                "capabilities": ["client_claim"],
                "wake_mapping_grant": {"mode": "selected", "ids": []},
                "client_grants": [{"profile_id": "amanda"}],
            },
        },
        admin=True,
    )

    assert approved.status == 400


def test_claim_on_a_grant_revoked_without_its_sweep_cannot_open(home) -> None:
    material = home.pair("laptop", ["amanda"])
    grant = _grant(material, "Amanda")
    handle = home.claim(material, grant["grant_id"], "claim-1").body[
        "conversation_handle"
    ]

    # Revoke in the domain only, as if the claim sweep had failed.
    home.service.revoke_client_grant(grant["grant_id"])

    assert home.claims.resolve(handle, material["device_id"]) is None


def test_session_list_refuses_an_unavailable_profile(home) -> None:
    material = home.pair("laptop", ["amanda"])
    grant = _grant(material, "Amanda")
    snapshot = home.configuration.read()
    candidate = {
        key: snapshot[key] for key in ("rooms", "profiles", "wake_mappings", "devices")
    }
    candidate["profiles"] = [
        {**profile, "available": profile["id"] != "amanda"}
        for profile in candidate["profiles"]
    ]
    home.configuration.replace(expected_revision=1, candidate=candidate)

    response = home.call(
        "POST",
        "/api/v1/client-sessions/list",
        {"schema": 1, "grant_id": grant["grant_id"]},
        credential=material["credential"],
    )

    assert response.status == 409
    assert response.body["error"]["code"] == "profile_unavailable"


def test_shared_profile_holder_cannot_remove_another_device(home) -> None:
    phone = home.pair("phone", ["spark"], endpoint_type="android")
    laptop = home.pair("laptop", ["spark"])

    response = home.call(
        "POST",
        f"/api/v1/profile-grants/{_grant(phone, 'Spark')['grant_id']}/revoke",
        {"schema": 1},
        credential=laptop["credential"],
    )

    assert response.status == 401
