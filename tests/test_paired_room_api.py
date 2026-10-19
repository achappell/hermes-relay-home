import http.client
import json
import secrets
import threading
from copy import deepcopy
from itertools import count

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.api.responses import HTTPResponse
from hermes_home.bridge.production import ConversationGrantStore
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.domain.credentials import CredentialService
from hermes_home.runtime import create_runtime, load_settings
from hermes_home.storage.credentials import SQLiteCredentialStore
from hermes_home.storage.sqlite import SQLiteConfigurationStore
from tests.test_credentials_api import CONFIGURATION


class Home:
    def __init__(self, tmp_path, *, runtime=None, initialize=True):
        self.runtime = runtime
        self.thread = None
        self.http = False
        if runtime is None:
            database = tmp_path / "home.sqlite3"
            self.configuration = SQLiteConfigurationStore(database)
            self.credentials = SQLiteCredentialStore(database)
            self.claims = ConversationGrantStore(
                database, configuration=self.configuration.read
            )
            ids = count(1)
            self.service = CredentialService(
                store=self.credentials,
                root_secret=b"r" * 32,
                id_factory=lambda: f"identity-{next(ids)}",
            )
            self.app = HomeApplication(
                configuration_store=self.configuration,
                arbitration_engine=ArbitrationEngine(
                    configuration=self.configuration.read
                ),
                admin_token="isolated-admin",
                device_credentials={},
                credential_service=self.service,
                conversation_claim_store=self.claims,
            )
        else:
            self.configuration = runtime.store
            self.credentials = runtime.credential_store
            self.claims = runtime.conversation_store
            self.app = runtime.server.RequestHandlerClass.application
            self.service = self.app._credential_service
        if initialize:
            candidate = deepcopy(CONFIGURATION)
            candidate["devices"] = []
            candidate["rooms"].append({"id": "hall", "name": "Hall"})
            candidate["wake_mappings"].append(
                {
                    "id": "other",
                    "phrase": "Other wake",
                    "profile_id": "family",
                    "active": True,
                }
            )
            self.publish(candidate)

    def call(self, method, path, body=None, *, credential=None, admin=False):
        headers = {"Content-Type": "application/json"}
        if credential is not None:
            headers["Authorization"] = f"Device {credential}"
        elif admin:
            headers["Authorization"] = "Bearer isolated-admin"
        encoded = json.dumps(body) if body is not None else b""
        if self.http:
            connection = http.client.HTTPConnection(
                *self.runtime.server.server_address, timeout=3
            )
            try:
                connection.request(method, path, encoded, headers)
                response = connection.getresponse()
                return HTTPResponse(response.status, json.loads(response.read()))
            finally:
                connection.close()
        return self.app.handle(method, path, headers, encoded)

    def candidate(self):
        candidate = deepcopy(self.configuration.read())
        candidate.pop("revision")
        return candidate

    def publish(self, candidate):
        revision = self.configuration.read()["revision"]
        response = self.call(
            "PUT",
            "/api/v1/configuration",
            {"schema": 1, "expected_revision": revision, "snapshot": candidate},
            admin=True,
        )
        assert response.status == 200
        return response.body["snapshot"]["revision"]

    def pending(self, endpoint="paired-bridge", room="kitchen", mapping="hey-hermes"):
        offer = self.call(
            "POST", "/api/v1/enrollment/offers", {"schema": 1}, admin=True
        )
        code = offer.body["enrollment_code"]
        request = self.call(
            "POST",
            "/api/v1/enrollment/requests",
            {
                "schema": 1,
                "enrollment_code": code,
                "endpoint_id": endpoint,
                "label": endpoint,
                "type": "puck",
                "requested_rooms": [room],
                "requested_capabilities": ["wake_claim"],
                "secure_storage": "service_private_file",
            },
        )
        assert request.status == 200
        base = f"/api/v1/enrollment/requests/{request.body['request_id']}"
        approved = self.call(
            "POST",
            base + "/approve",
            {
                "schema": 1,
                "scope": {
                    "rooms": [room],
                    "capabilities": ["wake_claim"],
                    "wake_mapping_grant": {"mode": "selected", "ids": [mapping]},
                },
            },
            admin=True,
        )
        assert approved.status == 200
        return base, code

    def consume(self, pending):
        base, code = pending
        response = self.call(
            "POST",
            base + "/consume",
            {
                "schema": 1,
                "enrollment_code": code,
                "secure_storage": "service_private_file",
            },
        )
        assert response.status == 200
        return response.body

    def pair(self, endpoint="paired-bridge", room="kitchen", mapping="hey-hermes"):
        return self.consume(self.pending(endpoint, room, mapping))

    @staticmethod
    def device(device_id, *, room="kitchen", paired=False):
        device = {
            "id": device_id,
            "name": device_id,
            "room_id": room,
            "priority": 1,
            "capabilities": {"wake_claim": True},
        }
        if paired:
            device["wake_admission"] = {
                "mode": "paired_room",
                "endpoint_id": "paired-bridge",
                "wake_mapping_ids": ["hey-hermes"],
            }
        return device

    def approve(self, endpoint):
        candidate = self.candidate()
        candidate["devices"].append(self.device(endpoint["device_id"], paired=True))
        return self.publish(candidate)

    def claim(self, endpoint, claim_id="wake-1", **changes):
        request = {
            "schema": 1,
            "claim_id": claim_id,
            "device_id": endpoint["device_id"],
            "wake_mapping_id": "hey-hermes",
            "configuration_revision": self.configuration.read()["revision"],
            "initiation": {"kind": "wake_word"},
        }
        request.update(changes)
        return self.call(
            "POST",
            "/api/v1/paired-room-claims",
            request,
            credential=endpoint["credential"],
        )

    def discovery(self, endpoint):
        return self.call(
            "GET",
            f"/api/v1/devices/{endpoint['device_id']}/configuration",
            credential=endpoint["credential"],
        )

    def retire(self, endpoint, handle):
        return self.call(
            "POST",
            "/api/v1/paired-room-claims/close",
            {"schema": 1, "conversation_handle": handle},
            credential=endpoint["credential"],
        )

    def close(self):
        if self.thread is not None:
            self.runtime.server.shutdown()
            self.thread.join(timeout=3)
        if self.runtime is not None:
            self.runtime.close()
        else:
            self.claims.close()
            self.credentials.close()
            self.configuration.close()


@pytest.fixture
def home(tmp_path):
    home = Home(tmp_path)
    yield home
    home.close()


def _error(response, status, code):
    assert response.status == status
    assert response.body == {"schema": 1, "error": {"code": code}}


def test_paired_configuration_is_explicit_and_grant_uses_no_evidence(home):
    endpoint = home.pair()
    assert "wake_admission" not in home.discovery(endpoint).body["snapshot"]
    _error(home.claim(endpoint), 403, "forbidden")
    revision = home.approve(endpoint)
    assert home.discovery(endpoint).body == {
        "schema": 1,
        "snapshot": {
            "revision": revision,
            "wake_mappings": [{"id": "hey-hermes", "phrase": "Hey Hermes"}],
            "wake_admission": {
                "mode": "paired_room",
                "room_id": "kitchen",
                "wake_mapping_ids": ["hey-hermes"],
            },
        },
    }
    grant = home.claim(endpoint)
    assert grant.status == 200
    assert grant.body["decision"] == "granted"
    assert grant.body["admission_mode"] == "paired_room"
    assert grant.body["configuration_revision"] == revision
    resolved = home.claims.resolve(
        grant.body["conversation_handle"], endpoint["device_id"]
    )
    assert resolved.device_id == endpoint["device_id"]
    assert resolved.profile_id == "family"
    _error(home.claim(endpoint), 409, "duplicate_claim")
    _error(home.claim(endpoint, "wake-2"), 409, "conversation_active")


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": True},
        {"configuration_revision": True},
        {"claim_id": ""},
        {"initiation": {"kind": "wake_word", "observed_at_ms": 1}},
        {"initiation": {"kind": "tap"}},
        {"acoustic_evidence": {"value": 1}},
        {"wake_mapping_id": None},
        {"room_id": "kitchen"},
    ],
)
def test_paired_request_rejects_noncontract_authority(home, changes):
    endpoint = home.pair()
    home.approve(endpoint)
    _error(home.claim(endpoint, **changes), 400, "invalid_request")


def test_paired_identity_scope_revision_and_revocation_fail_closed(home):
    endpoint = home.pair()
    revision = home.approve(endpoint)
    _error(home.claim(endpoint, device_id="another-device"), 401, "unauthorized")
    _error(
        home.claim(endpoint, configuration_revision=revision - 1),
        409,
        "stale_configuration",
    )
    _error(home.claim(endpoint, wake_mapping_id="other"), 403, "forbidden")
    grant = home.claim(endpoint)
    assert grant.status == 200
    revoked = home.call(
        "POST",
        f"/api/v1/devices/{endpoint['device_id']}/revoke",
        {"schema": 1},
        admin=True,
    )
    assert revoked.status == 200
    assert (
        home.claims.resolve(grant.body["conversation_handle"], endpoint["device_id"])
        is None
    )
    _error(home.claim(endpoint, "wake-after-revoke"), 401, "unauthorized")


@pytest.mark.parametrize("change", ["remove", "endpoint", "unrelated_revision"])
def test_configuration_publish_retires_paired_authority(home, change):
    endpoint = home.pair()
    home.approve(endpoint)
    grant = home.claim(endpoint)
    candidate = home.candidate()
    if change == "remove":
        candidate["devices"][0].pop("wake_admission")
    elif change == "endpoint":
        candidate["devices"][0]["wake_admission"]["endpoint_id"] = "different-endpoint"
    else:
        candidate["rooms"][1]["name"] = "Renamed Hall"
    home.publish(candidate)
    assert (
        home.claims.resolve(grant.body["conversation_handle"], endpoint["device_id"])
        is None
    )
    discovered = home.discovery(endpoint)
    if change == "endpoint":
        _error(discovered, 403, "forbidden")
    else:
        assert discovered.status == 200
        assert ("wake_admission" in discovered.body["snapshot"]) == (change != "remove")


def test_new_credential_competitor_invalidates_grant_without_revision_change(home):
    endpoint = home.pair()
    pending = home.pending("other-bridge", "hall")
    # Deterministic identity factory: this pending request will consume as identity-6.
    candidate = home.candidate()
    candidate["devices"] = [
        home.device(endpoint["device_id"], paired=True),
        home.device("identity-6", room="hall"),
    ]
    revision = home.publish(candidate)
    grant = home.claim(endpoint)
    assert grant.status == 200
    competitor = home.consume(pending)
    assert competitor["device_id"] == "identity-6"
    assert home.configuration.read()["revision"] == revision
    assert (
        home.claims.resolve(grant.body["conversation_handle"], endpoint["device_id"])
        is None
    )
    _error(home.discovery(endpoint), 403, "forbidden")
    _error(home.claim(endpoint, "next-wake"), 403, "forbidden")


def test_other_mapping_device_does_not_disable_paired_or_strict_path(home):
    endpoint = home.pair()
    other = home.pair("other-bridge", "hall", "other")
    candidate = home.candidate()
    candidate["devices"] = [
        home.device(endpoint["device_id"], paired=True),
        home.device(other["device_id"], room="hall"),
    ]
    home.publish(candidate)
    assert home.claim(endpoint).status == 200
    strict = {
        "schema": 1,
        "claim_id": "strict-other",
        "device_id": other["device_id"],
        "wake_mapping_id": "other",
        "configuration_revision": home.configuration.read()["revision"],
        "observation": {"kind": "wake_word", "observed_at_ms": 1},
        "availability": "ready",
        "acoustic_evidence": {"value": 0.8},
    }
    response = home.call(
        "POST", "/api/v1/wake-claims", strict, credential=other["credential"]
    )
    assert response.status == 200
    assert response.body["decision"] == "granted"


def test_final_credential_recheck_discards_new_grant(home, monkeypatch):
    endpoint = home.pair()
    home.approve(endpoint)
    original = home.claims.create_from_decision
    handles = []

    def revoke_after_creation(decision, **kwargs):
        handle = original(decision, **kwargs)
        handles.append(handle)
        home.service.revoke(endpoint["device_id"])
        return handle

    monkeypatch.setattr(home.claims, "create_from_decision", revoke_after_creation)
    _error(home.claim(endpoint), 401, "unauthorized")
    assert home.claims.resolve(handles[0], endpoint["device_id"]) is None


def test_actual_production_runtime_http_paired_contract(tmp_path):
    admin = tmp_path / "admin"
    root = tmp_path / "root"
    standard = tmp_path / "standard"
    admin.write_text("isolated-admin", encoding="utf-8")
    root.write_text(secrets.token_hex(32), encoding="utf-8")
    standard.write_text("isolated-unused-standard", encoding="utf-8")
    runtime = create_runtime(
        load_settings(
            {
                "HERMES_HOME_DATA_DIR": str(tmp_path / "data"),
                "HERMES_HOME_ADMIN_TOKEN_FILE": str(admin),
                "HERMES_HOME_CREDENTIAL_ROOT_SECRET_FILE": str(root),
                "HERMES_HOME_STANDARD_GATEWAY_URL": "ws://127.0.0.1:1/api/ws",
                "HERMES_HOME_STANDARD_TOKEN_FILE": str(standard),
                "HERMES_HOME_PORT": "0",
                "HERMES_HOME_BRIDGE_PORT": "0",
            }
        )
    )
    home = Home(tmp_path, runtime=runtime)
    home.thread = threading.Thread(target=runtime.server.serve_forever, daemon=True)
    home.thread.start()
    home.http = True
    try:
        endpoint = home.pair()
        revision = home.approve(endpoint)
        assert (
            home.discovery(endpoint).body["snapshot"]["wake_admission"]["mode"]
            == "paired_room"
        )
        _error(
            home.claim(endpoint, configuration_revision=revision - 1),
            409,
            "stale_configuration",
        )
        grant = home.claim(endpoint)
        assert grant.status == 200
        assert (
            runtime.conversation_store.resolve(
                grant.body["conversation_handle"], endpoint["device_id"]
            )
            is not None
        )
        _error(home.claim(endpoint), 409, "duplicate_claim")
        candidate = home.candidate()
        candidate["devices"][0].pop("wake_admission")
        home.publish(candidate)
        assert (
            runtime.conversation_store.resolve(
                grant.body["conversation_handle"], endpoint["device_id"]
            )
            is None
        )
        _error(home.claim(endpoint, "not-approved"), 403, "forbidden")
        assert (
            home.call(
                "POST",
                f"/api/v1/devices/{endpoint['device_id']}/revoke",
                {"schema": 1},
                admin=True,
            ).status
            == 200
        )
        _error(home.discovery(endpoint), 401, "unauthorized")
    finally:
        home.close()


def test_cold_owned_paired_close_survives_same_sqlite_and_root_restart(tmp_path):
    original = Home(tmp_path)
    endpoint = original.pair()
    revision = original.approve(endpoint)
    grant = original.claim(endpoint)
    assert grant.status == 200
    handle = grant.body["conversation_handle"]
    original.close()
    recovered = Home(tmp_path, initialize=False)
    try:
        assert recovered.configuration.read()["revision"] == revision
        assert (
            recovered.discovery(endpoint).body["snapshot"]["wake_admission"]["mode"]
            == "paired_room"
        )
        assert recovered.claims.resolve(handle, endpoint["device_id"]) is None
        receipt = {
            "schema": 1,
            "conversation_handle": handle,
            "admission_mode": "paired_room",
            "status": "closed",
        }
        assert recovered.retire(endpoint, handle).body == receipt
        assert recovered.retire(endpoint, handle).status == 200
        candidate = recovered.candidate()
        candidate["devices"][0].pop("wake_admission")
        recovered.publish(candidate)
        assert recovered.retire(endpoint, handle).body == receipt
        _error(recovered.retire(endpoint, "unknown-handle"), 404, "not_found")
    finally:
        recovered.close()


def test_owned_paired_close_does_not_disclose_foreign_or_retire_strict_claim(home):
    endpoint = home.pair()
    other = home.pair("other-bridge", "hall")
    home.approve(endpoint)
    grant = home.claim(endpoint)
    handle = grant.body["conversation_handle"]
    _error(home.retire(other, handle), 404, "not_found")
    assert home.claims.resolve(handle, endpoint["device_id"]) is not None
    assert home.retire(endpoint, handle).status == 200
    strict = {
        "schema": 1,
        "claim_id": "strict-owned",
        "device_id": endpoint["device_id"],
        "wake_mapping_id": "hey-hermes",
        "configuration_revision": home.configuration.read()["revision"],
        "observation": {"kind": "wake_word"},
        "availability": "ready",
        "acoustic_evidence": {"value": 0.8},
    }
    response = home.call(
        "POST", "/api/v1/wake-claims", strict, credential=endpoint["credential"]
    )
    assert response.status == 200
    strict_handle = response.body["conversation_handle"]
    _error(home.retire(endpoint, strict_handle), 404, "not_found")
    assert home.claims.resolve(strict_handle, endpoint["device_id"]) is not None
    _error(home.claim(endpoint, "strict-owned"), 409, "duplicate_claim")
    assert (
        home.call(
            "POST",
            f"/api/v1/devices/{endpoint['device_id']}/revoke",
            {"schema": 1},
            admin=True,
        ).status
        == 200
    )
    _error(home.retire(endpoint, handle), 401, "unauthorized")


@pytest.mark.parametrize("unavailable", ["configuration", "credentials", "claim_store"])
def test_paired_admission_storage_unavailable_never_returns_authority(
    home, monkeypatch, unavailable
):
    endpoint = home.pair()
    revision = home.approve(endpoint)
    target, method = {
        "configuration": (home.configuration, "read"),
        "credentials": (home.service, "active_wake_devices"),
        "claim_store": (home.claims, "create_from_decision"),
    }[unavailable]

    def failed(*_args, **_kwargs):
        raise OSError("isolated storage unavailable")

    monkeypatch.setattr(target, method, failed)
    response = home.call(
        "POST",
        "/api/v1/paired-room-claims",
        {
            "schema": 1,
            "claim_id": "unavailable-wake",
            "device_id": endpoint["device_id"],
            "wake_mapping_id": "hey-hermes",
            "configuration_revision": revision,
            "initiation": {"kind": "wake_word"},
        },
        credential=endpoint["credential"],
    )
    _error(response, 503, "service_unavailable")
    assert (
        home.claims._connection.execute(
            "SELECT COUNT(*) FROM conversation_claims WHERE status = 'active'"
        ).fetchone()[0]
        == 0
    )
