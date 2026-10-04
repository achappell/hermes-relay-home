"""HOME-NW-18: device-authenticated list and close of a device's client claims."""

from __future__ import annotations

import itertools
import json
import logging

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.domain.configuration import ConfigurationMigrationRequired
from hermes_home.observability.diagnostics import (
    DiagnosticsRecorder,
    InMemoryDiagnosticsStore,
)
from hermes_home.observability.metrics import MetricsRegistry
from tests.test_client_claims_api import Home, _grant

CREDENTIAL_LIFETIME_SECONDS = 90 * 24 * 3600


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class CapturingDiagnostics(DiagnosticsRecorder):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.accepted = []

    def record(self, event) -> bool:
        accepted = super().record(event)
        if accepted:
            self.accepted.append(event)
        return accepted


class ListHome(Home):
    def __init__(self, tmp_path, **kwargs) -> None:
        refs = itertools.count(1)
        self.credential_clock = Clock()
        kwargs.setdefault("claim_ref_factory", lambda: f"cref-{next(refs)}")
        super().__init__(tmp_path, credential_clock=self.credential_clock, **kwargs)
        self.metrics = MetricsRegistry()
        self.diagnostics = CapturingDiagnostics(
            store=InMemoryDiagnosticsStore(), metrics=self.metrics
        )
        self.app = HomeApplication(
            configuration_store=self.configuration,
            arbitration_engine=ArbitrationEngine(configuration=self.configuration.read),
            admin_token="admin-secret",
            device_credentials={},
            credential_service=self.service,
            conversation_claim_store=self.claims,
            session_directory=self.directory,
            metrics=self.metrics,
            diagnostics=self.diagnostics,
        )

    def list(self, material, *, headers=None):
        request_headers = {"Authorization": f"Device {material['credential']}"}
        request_headers.update(headers or {})
        return self.app.handle("GET", "/api/v1/client-claims", request_headers, b"")

    def close(self, material, refs, *, headers=None):
        request_headers = {
            "Authorization": f"Device {material['credential']}",
            "Content-Type": "application/json",
        }
        request_headers.update(headers or {})
        body = json.dumps({"schema": 1, "claim_refs": refs}).encode()
        return self.app.handle(
            "POST", "/api/v1/client-claims/close", request_headers, body
        )

    def new_claim(self, material, grant_id, claim_id):
        response = self.claim(material, grant_id, claim_id)
        assert response.status == 200, response.body
        return response.body


@pytest.fixture
def home(tmp_path):
    return ListHome(tmp_path, claims_per_device=8)


def _states(response) -> list[str]:
    return [claim["state"] for claim in response.body["claims"]]


def test_create_returns_a_claim_ref(home) -> None:
    material = home.pair("ipad", ["spark"])
    body = home.new_claim(material, _grant(material, "Spark")["grant_id"], "claim-1")

    assert body["claim_ref"] == "cref-1"
    assert body["claim_ref"] != body["conversation_handle"]


def test_pilot_claim_leak_reproduces_then_clears(home) -> None:
    material = home.pair("ipad", ["spark"])
    grant_id = _grant(material, "Spark")["grant_id"]
    live = home.new_claim(material, grant_id, "claim-live")
    home.claims.mark_open(live["conversation_handle"], material["device_id"])
    leaked = [
        home.new_claim(material, grant_id, f"claim-leak-{index}")["claim_ref"]
        for index in range(7)
    ]

    refused = home.claim(material, grant_id, "claim-next")
    assert refused.status == 409
    assert refused.body["error"]["code"] == "claim_limit"
    assert any(
        event.route_id == "client_claims" and event.failure_code == "claim_denied"
        for event in home.diagnostics.accepted
    )

    listed = home.list(material)
    assert listed.status == 200
    assert listed.body["max_claims"] == 8
    assert sorted(_states(listed)) == ["connecting"] * 7 + ["idle"]
    assert {claim["profile_label"] for claim in listed.body["claims"]} == {"Spark"}

    closed = home.close(material, leaked)
    assert closed.status == 200
    assert closed.body["results"] == [
        {"claim_ref": ref, "result": "closed"} for ref in leaked
    ]
    assert home.claim(material, grant_id, "claim-next").status == 200


def test_list_shape_never_carries_handles_profile_or_session_ids(home) -> None:
    material = home.pair("laptop", ["amanda"])
    grant_id = _grant(material, "Amanda")["grant_id"]
    body = home.claim(
        material, grant_id, "claim-1", session={"mode": "most_recent"}
    ).body
    home.claims.mark_open(body["conversation_handle"], material["device_id"])

    listed = home.list(material)

    (claim,) = listed.body["claims"]
    assert set(claim) == {
        "claim_ref",
        "grant_id",
        "profile_label",
        "session_ref",
        "created_at",
        "opened_at",
        "state",
    }
    assert claim["session_ref"] == body["session"]["session_ref"]
    assert isinstance(claim["created_at"], int)
    assert isinstance(claim["opened_at"], int)
    text = json.dumps(listed.body)
    for secret in (body["conversation_handle"], "amanda", "stored-2"):
        assert secret not in text


def test_connecting_claim_has_no_opened_at(home) -> None:
    material = home.pair("laptop", ["amanda"])
    home.new_claim(material, _grant(material, "Amanda")["grant_id"], "claim-1")

    (claim,) = home.list(material).body["claims"]

    assert claim["state"] == "connecting"
    assert claim["opened_at"] is None
    assert claim["session_ref"] is None


def test_max_claims_follows_the_configured_limit(tmp_path) -> None:
    home = ListHome(tmp_path, claims_per_device=3)
    material = home.pair("laptop", ["amanda"])

    assert home.list(material).body == {"schema": 1, "max_claims": 3, "claims": []}


def test_list_is_isolated_per_device(home) -> None:
    laptop = home.pair("laptop", ["spark"])
    phone = home.pair("phone", ["spark"])
    home.new_claim(laptop, _grant(laptop, "Spark")["grant_id"], "claim-laptop")
    phone_ref = home.new_claim(
        phone, _grant(phone, "Spark")["grant_id"], "claim-phone"
    )["claim_ref"]

    assert [claim["claim_ref"] for claim in home.list(phone).body["claims"]] == [
        phone_ref
    ]


def test_close_reports_not_open_for_anything_not_the_callers(home) -> None:
    laptop = home.pair("laptop", ["spark"])
    phone = home.pair("phone", ["spark"])
    other = home.new_claim(phone, _grant(phone, "Spark")["grant_id"], "claim-phone")
    own = home.new_claim(laptop, _grant(laptop, "Spark")["grant_id"], "claim-laptop")

    first = home.close(laptop, [other["claim_ref"], own["claim_ref"], "cref-random"])
    again = home.close(laptop, [own["claim_ref"]])

    assert [result["result"] for result in first.body["results"]] == [
        "not_open",
        "closed",
        "not_open",
    ]
    assert again.status == 200
    assert again.body["results"] == [
        {"claim_ref": own["claim_ref"], "result": "not_open"}
    ]
    assert (
        home.claims.resolve(other["conversation_handle"], phone["device_id"])
        is not None
    )


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ({"schema": 1, "claim_refs": []}, "application/json"),
        ({"schema": 1, "claim_refs": [f"cref-{i}" for i in range(65)]}, None),
        ({"schema": 1, "claim_refs": ["cref-1", "cref-1"]}, None),
        ({"schema": 1, "claim_refs": [7]}, None),
        ({"schema": 1, "claim_refs": ["x" * 129]}, None),
        ({"schema": 1, "claim_refs": ["cref-1"], "claim_id": "c"}, None),
        ({"schema": 1}, None),
        ({"schema": 1, "claim_refs": ["cref-1"]}, "text/plain"),
    ],
)
def test_close_body_validation(home, body, content_type) -> None:
    material = home.pair("laptop", ["amanda"])
    headers = {
        "Authorization": f"Device {material['credential']}",
        "Content-Type": content_type or "application/json",
    }

    response = home.app.handle(
        "POST", "/api/v1/client-claims/close", headers, json.dumps(body).encode()
    )

    assert response.status == 400
    assert response.body == {"schema": 1, "error": {"code": "invalid_request"}}


def test_proxied_requests_are_answered(home) -> None:
    material = home.pair("laptop", ["amanda"])
    ref = home.new_claim(material, _grant(material, "Amanda")["grant_id"], "c-1")[
        "claim_ref"
    ]
    proxied = {"X-Forwarded-For": "100.64.0.9"}

    assert home.list(material, headers=proxied).status == 200
    closed = home.close(material, [ref], headers=proxied)
    assert closed.status == 200
    assert closed.body["results"][0]["result"] == "closed"


def test_unreadable_configuration_degrades_labels_only(home, monkeypatch) -> None:
    material = home.pair("laptop", ["amanda"])
    home.new_claim(material, _grant(material, "Amanda")["grant_id"], "claim-1")

    def broken():
        raise RuntimeError("configuration unavailable")

    monkeypatch.setattr(home.configuration, "read", broken)
    listed = home.list(material)

    assert listed.status == 200
    assert listed.body["claims"][0]["profile_label"] is None
    assert listed.body["claims"][0]["state"] == "connecting"


def test_list_and_close_never_call_the_session_directory(home) -> None:
    material = home.pair("laptop", ["amanda"])
    ref = home.new_claim(material, _grant(material, "Amanda")["grant_id"], "c-1")[
        "claim_ref"
    ]
    home.directory.requests.clear()
    home.app._session_directory = None

    assert home.list(material).status == 200
    assert home.close(material, [ref]).status == 200
    assert home.directory.requests == []

    class BrokenDirectory:
        def list_sessions(self, *_args, **_kwargs):
            raise AssertionError("claim routes must not query Standard")

        def most_recent(self, *_args, **_kwargs):
            raise AssertionError("claim routes must not query Standard")

    home.app._session_directory = BrokenDirectory()
    assert home.list(material).status == 200
    assert home.close(material, [ref]).status == 200


def test_revoked_replaced_and_expired_credentials_are_unauthorized(home) -> None:
    revoked = home.pair("revoked", ["amanda"])
    replaced = home.pair("replaced", ["amanda"])
    expired = home.pair("expired", ["amanda"])
    assert (
        home.call(
            "POST",
            f"/api/v1/devices/{revoked['device_id']}/revoke",
            {"schema": 1},
            admin=True,
        ).status
        == 200
    )
    assert (
        home.call(
            "POST",
            f"/api/v1/devices/{replaced['device_id']}/credentials/rotate",
            {"schema": 1, "request_id": "rotate-1", "generation": 1},
            admin=True,
        ).status
        == 200
    )

    for material in (revoked, replaced):
        assert home.list(material).status == 401
        assert home.close(material, ["cref-1"]).status == 401

    home.credential_clock.now += CREDENTIAL_LIFETIME_SECONDS + 1
    assert home.list(expired).status == 401
    assert home.close(expired, ["cref-1"]).status == 401


def test_credential_without_client_claim_is_refused(home) -> None:
    offer = home.call(
        "POST", "/api/v1/enrollment/offers", {"schema": 1}, admin=True
    ).body
    code = offer["enrollment_code"]
    request_id = home.call(
        "POST",
        "/api/v1/enrollment/requests",
        {
            "schema": 1,
            "enrollment_code": code,
            "endpoint_id": "watch",
            "label": "watch",
            "type": "tui",
            "requested_rooms": [],
            "requested_capabilities": ["watch_view"],
            "secure_storage": "platform_secure_store",
        },
    ).body["request_id"]
    home.call(
        "POST",
        f"/api/v1/enrollment/requests/{request_id}/approve",
        {
            "schema": 1,
            "scope": {
                "rooms": [],
                "capabilities": ["watch_view"],
                "wake_mapping_grant": {"mode": "selected", "ids": []},
            },
        },
        admin=True,
    )
    material = home.call(
        "POST",
        f"/api/v1/enrollment/requests/{request_id}/consume",
        {
            "schema": 1,
            "enrollment_code": code,
            "secure_storage": "platform_secure_store",
        },
    ).body

    for response in (home.list(material), home.close(material, ["cref-1"])):
        assert response.status == 403
        assert response.body["error"]["code"] == "client_claim_unavailable"


def test_identifiers_never_reach_logs_metrics_or_diagnostics(home, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    material = home.pair("laptop", ["amanda"])
    grant_id = _grant(material, "Amanda")["grant_id"]
    body = home.claim(
        material, grant_id, "claim-1", session={"mode": "most_recent"}
    ).body
    handle = body["conversation_handle"]
    home.claims.mark_open(handle, material["device_id"])
    home.diagnostics.accepted.clear()

    listed = home.list(material)
    session_ref = listed.body["claims"][0]["session_ref"]
    home.close(material, [body["claim_ref"]])
    home.close(material, [])

    routes = {event.route_id for event in home.diagnostics.accepted}
    assert {"client_claims", "client_claim_close"} <= routes
    captured = "\n".join(
        [
            caplog.text,
            home.metrics.render(),
            json.dumps([event.to_dict() for event in home.diagnostics.accepted]),
        ]
    )
    assert 'route="client_claim_close"' in captured
    for secret in (body["claim_ref"], handle, handle[:8], session_ref, grant_id):
        assert secret not in captured


def test_migration_required_degrades_list_and_is_recorded_for_create(
    home, monkeypatch
) -> None:
    material = home.pair("laptop", ["amanda"])
    grant_id = _grant(material, "Amanda")["grant_id"]
    home.new_claim(material, grant_id, "claim-1")

    def migration_required():
        raise ConfigurationMigrationRequired(3)

    monkeypatch.setattr(home.configuration, "read", migration_required)
    listed = home.list(material)
    assert listed.status == 200
    assert listed.body["claims"][0]["profile_label"] is None

    refused = home.claim(material, grant_id, "claim-2")
    assert refused.status == 409
    assert refused.body["error"]["code"] == "configuration_migration_required"
    assert any(
        event.route_id == "client_claims" and event.failure_code == "conflict"
        for event in home.diagnostics.accepted
    )


def test_client_sessions_route_is_recorded_with_safe_route_id(home) -> None:
    material = home.pair("laptop", ["amanda"])
    grant_id = _grant(material, "Amanda")["grant_id"]
    response = home.call(
        "POST",
        "/api/v1/client-sessions/list",
        {"schema": 1, "grant_id": grant_id, "limit": 10},
        credential=material["credential"],
    )
    assert response.status == 200
    assert any(
        event.route_id == "client_sessions" for event in home.diagnostics.accepted
    )


def test_claim_routes_return_503_when_store_operations_fail(home, monkeypatch) -> None:
    material = home.pair("laptop", ["amanda"])

    def unavailable(*_args, **_kwargs):
        raise OSError("store unavailable")

    monkeypatch.setattr(home.claims, "client_claims", unavailable)
    assert home.list(material).body["error"]["code"] == "service_unavailable"
    monkeypatch.setattr(home.claims, "close_client_claims", unavailable)
    assert (
        home.close(material, ["cref-random"]).body["error"]["code"]
        == "service_unavailable"
    )
    home.app._conversation_claim_store = None
    assert home.list(material).status == 503
    assert home.close(material, ["cref-random"]).status == 503


def test_list_degrades_session_ref_lookup_to_null(home, monkeypatch) -> None:
    material = home.pair("laptop", ["amanda"])
    grant_id = _grant(material, "Amanda")["grant_id"]
    home.claims.create_client_claim(
        claim_id="claim-session-ref-failure",
        device_id=material["device_id"],
        grant_id=grant_id,
        profile_id="amanda",
        configuration_revision=1,
        credential_generation=1,
        session_id="stored-session",
    )

    def unavailable(*_args, **_kwargs):
        raise OSError("session reference unavailable")

    monkeypatch.setattr(home.claims, "session_ref", unavailable)
    listed = home.list(material)
    assert listed.status == 200
    assert listed.body["claims"][0]["session_ref"] is None


def test_client_claim_routes_refuse_when_credential_service_is_missing(home) -> None:
    material = home.pair("laptop", ["amanda"])
    home.app._credential_service = None
    assert home.list(material).body["error"]["code"] == "client_claim_unavailable"
    assert (
        home.close(material, ["cref-random"]).body["error"]["code"]
        == "client_claim_unavailable"
    )
