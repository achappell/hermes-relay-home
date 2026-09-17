import json
import threading
from types import SimpleNamespace

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.domain.conversations import InMemoryConversationClaimStore
from hermes_home.domain.credentials import CredentialScope
from hermes_home.storage.sqlite import SQLiteConfigurationStore

CONFIGURATION = {
    "rooms": [{"id": "kitchen", "name": "Kitchen"}],
    "profiles": [{"id": "family", "name": "Family", "available": True}],
    "wake_mappings": [
        {
            "id": "hey-hermes",
            "phrase": "Hey Hermes",
            "profile_id": "family",
            "active": True,
        }
    ],
    "devices": [
        {
            "id": "puck-kitchen",
            "name": "Kitchen Puck",
            "room_id": "kitchen",
            "priority": 1,
            "capabilities": {"wake_claim": True},
        }
    ],
}

WAKE_CLAIM = {
    "schema": 1,
    "claim_id": "claim-api-1",
    "device_id": "puck-kitchen",
    "wake_mapping_id": "hey-hermes",
    "configuration_revision": 1,
    "observation": {"detector": "device-local"},
    "acoustic_evidence": {"kind": "opaque-v1", "value": 0.91},
    "availability": "ready",
}

TWO_DEVICE_CONFIGURATION = {
    **CONFIGURATION,
    "devices": [
        {
            **CONFIGURATION["devices"][0],
            "id": "puck-near",
            "name": "Near Puck",
            "priority": 2,
        },
        {
            **CONFIGURATION["devices"][0],
            "id": "puck-priority",
            "name": "Priority Puck",
            "priority": 1,
        },
    ],
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class BarrierSleeper:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._barrier = threading.Barrier(2)

    def __call__(self, seconds: float) -> None:
        self._barrier.wait(timeout=2)
        self._clock.now = max(self._clock.now, 0.250)


def _application(tmp_path):
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=store.read,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-api-1",
    )
    application = HomeApplication(
        configuration_store=store,
        arbitration_engine=engine,
        admin_token="admin-secret",
        device_credentials={"device-secret": "puck-kitchen"},
        conversation_claim_store=InMemoryConversationClaimStore(
            handle_factory=lambda: "opaque-conversation-1"
        ),
        static_device_scopes={
            "puck-kitchen": CredentialScope.from_values(
                rooms=["kitchen"],
                capabilities=["wake_claim"],
                wake_mappings=["hey-hermes"],
            )
        },
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    return application, store


def _publish_configuration(application, snapshot, expected_revision):
    return application.handle(
        "PUT",
        "/api/v1/configuration",
        {
            "Authorization": "Bearer admin-secret",
            "Content-Type": "application/json",
        },
        json.dumps(
            {
                "schema": 1,
                "expected_revision": expected_revision,
                "snapshot": snapshot,
            }
        ).encode(),
    )


def _claim_conversation(application):
    return application.handle(
        "POST",
        "/api/v1/wake-claims",
        {
            "Authorization": "Device device-secret",
            "Content-Type": "application/json",
        },
        json.dumps(WAKE_CLAIM).encode(),
    )


def test_get_configuration_returns_the_versioned_active_snapshot(tmp_path) -> None:
    application, store = _application(tmp_path)

    try:
        response = application.handle(
            "GET",
            "/api/v1/configuration",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert response.status == 200
        assert response.body == {
            "schema": 1,
            "snapshot": {
                "revision": 0,
                "rooms": [],
                "profiles": [],
                "wake_mappings": [],
                "devices": [],
            },
        }
    finally:
        store.close()


def test_legacy_configuration_get_requires_an_explicit_new_shape_publish(
    tmp_path,
) -> None:
    application, store = _application(tmp_path)
    legacy = {
        "revision": 6,
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "mappings": [{"id": "family", "name": "Family"}],
        "devices": [
            {
                "id": "puck-kitchen",
                "name": "Kitchen Puck",
                "room_id": "kitchen",
                "profile_id": "family",
                "priority": 1,
                "capabilities": {"wake_claim": True},
            }
        ],
    }
    store._connection.execute(
        "UPDATE configuration SET revision = ?, snapshot = ? WHERE id = 1",
        (6, json.dumps(legacy)),
    )
    store._connection.commit()

    try:
        response = application.handle(
            "GET",
            "/api/v1/configuration",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )
        assert response.status == 409
        assert response.body == {
            "schema": 1,
            "error": {
                "code": "configuration_migration_required",
                "current_revision": 6,
            },
        }

        published = application.handle(
            "PUT",
            "/api/v1/configuration",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            json.dumps(
                {
                    "schema": 1,
                    "expected_revision": 6,
                    "snapshot": CONFIGURATION,
                }
            ).encode(),
        )
        assert published.status == 200
        assert published.body["snapshot"]["revision"] == 7
    finally:
        store.close()


def test_legacy_configuration_wake_claim_returns_migration_revision(tmp_path) -> None:
    application, store = _application(tmp_path)
    legacy = {
        "revision": 6,
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "mappings": [],
        "devices": [
            {
                "id": "puck-kitchen",
                "name": "Kitchen Puck",
                "room_id": "kitchen",
                "profile_id": "family",
                "priority": 1,
                "capabilities": {"wake_claim": True},
            }
        ],
    }
    store._connection.execute(
        "UPDATE configuration SET revision = ?, snapshot = ? WHERE id = 1",
        (6, json.dumps(legacy)),
    )
    store._connection.commit()

    try:
        response = _claim_conversation(application)

        assert response.status == 409
        assert response.body == {
            "schema": 1,
            "error": {
                "code": "configuration_migration_required",
                "current_revision": 6,
            },
        }
    finally:
        store.close()


def test_metrics_route_requires_the_admin_credential(tmp_path) -> None:
    application, store = _application(tmp_path)

    try:
        response = application.handle("GET", "/metrics", {}, b"")

        assert response.status == 401
        assert response.body == {"schema": 1, "error": {"code": "unauthorized"}}
    finally:
        store.close()


def test_metrics_route_exposes_low_cardinality_operational_metrics(tmp_path) -> None:
    application, store = _application(tmp_path)

    try:
        application.handle(
            "GET",
            "/api/v1/configuration",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )
        response = application.handle(
            "GET",
            "/metrics",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert response.status == 200
        assert response.content_type == "text/plain; version=0.0.4; charset=utf-8"
        assert (
            'hermes_home_http_requests_total{method="GET",route="configuration",status="200"} 1'
            in response.body
        )
        assert "hermes_home_configuration_revision 0" in response.body
        assert "claim_id" not in response.body
        assert "device_id" not in response.body
    finally:
        store.close()


def test_metrics_route_exposes_the_current_revision_after_startup(tmp_path) -> None:
    application, store = _application(tmp_path)

    try:
        response = application.handle(
            "GET",
            "/metrics",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert response.status == 200
        assert "hermes_home_configuration_revision 0" in response.body
    finally:
        store.close()


def test_metrics_route_counts_configuration_and_wake_outcomes(tmp_path) -> None:
    application, store = _application(tmp_path)

    try:
        application.handle(
            "PUT",
            "/api/v1/configuration",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            json.dumps(
                {
                    "schema": 1,
                    "expected_revision": 0,
                    "snapshot": CONFIGURATION,
                }
            ).encode(),
        )
        application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            json.dumps(WAKE_CLAIM).encode(),
        )
        response = application.handle(
            "GET",
            "/metrics",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert (
            'hermes_home_configuration_publishes_total{result="success"} 1'
            in response.body
        )
        assert 'hermes_home_wake_claims_total{result="granted"} 1' in response.body
        assert 'hermes_home_wake_decisions_total{decision="granted"} 1' in response.body
    finally:
        store.close()


def test_put_configuration_publishes_atomically_through_the_contract(tmp_path) -> None:
    application, store = _application(tmp_path)

    try:
        response = application.handle(
            "PUT",
            "/api/v1/configuration",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            json.dumps(
                {
                    "schema": 1,
                    "expected_revision": 0,
                    "snapshot": CONFIGURATION,
                }
            ).encode(),
        )

        assert response.status == 200
        assert response.body == {
            "schema": 1,
            "snapshot": {"revision": 1, **CONFIGURATION},
        }
    finally:
        store.close()


@pytest.mark.parametrize("revocation", ["mapping", "profile"])
def test_configuration_revocation_closes_active_conversation_immediately(
    tmp_path, revocation
) -> None:
    application, store = _application(tmp_path)

    try:
        assert _publish_configuration(application, CONFIGURATION, 0).status == 200
        claim = _claim_conversation(application)
        assert claim.status == 200

        if revocation == "mapping":
            revoked = {
                **CONFIGURATION,
                "wake_mappings": [
                    {**CONFIGURATION["wake_mappings"][0], "active": False}
                ],
            }
            close_reason = "mapping_revoked"
        else:
            revoked = {
                **CONFIGURATION,
                "profiles": [{**CONFIGURATION["profiles"][0], "available": False}],
            }
            close_reason = "profile_revoked"
        response = _publish_configuration(application, revoked, 1)

        assert response.status == 200
        assert (
            application._conversation_claim_store._claims["opaque-conversation-1"][
                "status"
            ]
            == "closed"
        )
        assert (
            application._conversation_claim_store._claims["opaque-conversation-1"][
                "close_reason"
            ]
            == close_reason
        )
    finally:
        store.close()


def test_configuration_retry_recovers_claim_cleanup_after_published_write(
    tmp_path,
) -> None:
    application, store = _application(tmp_path)
    assert _publish_configuration(application, CONFIGURATION, 0).status == 200
    claim = _claim_conversation(application)
    assert claim.status == 200
    claim_store = application._conversation_claim_store
    close_mappings = claim_store.close_mapping_claims
    fail_once = [True]

    def close_mappings_once(mapping_ids, *, reason):
        if fail_once[0]:
            fail_once[0] = False
            raise OSError("temporary claim-store failure")
        return close_mappings(mapping_ids, reason=reason)

    claim_store.close_mapping_claims = close_mappings_once
    revoked = {
        **CONFIGURATION,
        "wake_mappings": [{**CONFIGURATION["wake_mappings"][0], "active": False}],
    }

    try:
        first = _publish_configuration(application, revoked, 1)

        assert first.status == 503
        assert store.read()["revision"] == 2
        assert claim_store._claims["opaque-conversation-1"]["status"] == "active"

        retry = _publish_configuration(application, revoked, 1)

        assert retry.status == 409
        assert retry.body["error"] == {
            "code": "revision_conflict",
            "current_revision": 2,
        }
        assert claim_store._claims["opaque-conversation-1"]["status"] == "closed"
    finally:
        store.close()


def test_mapping_remap_and_removal_preserve_active_conversation_binding(
    tmp_path,
) -> None:
    application, store = _application(tmp_path)
    configuration = {
        **CONFIGURATION,
        "profiles": [
            *CONFIGURATION["profiles"],
            {"id": "amanda", "name": "Amanda", "available": True},
        ],
    }

    try:
        assert _publish_configuration(application, configuration, 0).status == 200
        claim = _claim_conversation(application)
        assert claim.status == 200

        remapped = {
            **configuration,
            "wake_mappings": [
                {**configuration["wake_mappings"][0], "profile_id": "amanda"}
            ],
        }
        assert _publish_configuration(application, remapped, 1).status == 200
        assert (
            application._conversation_claim_store._claims["opaque-conversation-1"][
                "profile_id"
            ]
            == "family"
        )
        assert (
            application._conversation_claim_store._claims["opaque-conversation-1"][
                "status"
            ]
            == "active"
        )

        removed = {**remapped, "wake_mappings": []}
        assert _publish_configuration(application, removed, 2).status == 200
        assert (
            application._conversation_claim_store._claims["opaque-conversation-1"][
                "status"
            ]
            == "active"
        )
    finally:
        store.close()


def test_configuration_routes_require_the_admin_credential(tmp_path) -> None:
    application, store = _application(tmp_path)

    try:
        response = application.handle(
            "GET",
            "/api/v1/configuration",
            {},
            b"",
        )

        assert response.status == 401
        assert response.body == {"schema": 1, "error": {"code": "unauthorized"}}
    finally:
        store.close()


def test_configuration_read_returns_service_unavailable_when_store_fails(
    tmp_path,
) -> None:
    application, store = _application(tmp_path)
    store.close()

    response = application.handle(
        "GET",
        "/api/v1/configuration",
        {"Authorization": "Bearer admin-secret"},
        b"",
    )

    assert response.status == 503
    assert response.body == {
        "schema": 1,
        "error": {"code": "service_unavailable"},
    }


def test_json_routes_require_the_application_json_content_type(tmp_path) -> None:
    application, store = _application(tmp_path)

    try:
        response = application.handle(
            "PUT",
            "/api/v1/configuration",
            {"Authorization": "Bearer admin-secret"},
            json.dumps(
                {
                    "schema": 1,
                    "expected_revision": 0,
                    "snapshot": CONFIGURATION,
                }
            ).encode(),
        )

        assert response.status == 400
        assert response.body == {"schema": 1, "error": {"code": "invalid_request"}}
    finally:
        store.close()


def test_stale_put_returns_typed_conflict_without_overwriting_newer_state(
    tmp_path,
) -> None:
    application, store = _application(tmp_path)

    try:
        store.replace(expected_revision=0, candidate=CONFIGURATION)
        response = application.handle(
            "PUT",
            "/api/v1/configuration",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            json.dumps(
                {
                    "schema": 1,
                    "expected_revision": 0,
                    "snapshot": {
                        "rooms": [],
                        "profiles": [],
                        "wake_mappings": [],
                        "devices": [],
                    },
                }
            ).encode(),
        )

        assert response.status == 409
        assert response.body == {
            "schema": 1,
            "error": {"code": "revision_conflict", "current_revision": 1},
        }
        assert store.read()["rooms"] == CONFIGURATION["rooms"]
    finally:
        store.close()


def test_put_rejects_a_boolean_schema_value(tmp_path) -> None:
    application, store = _application(tmp_path)

    try:
        response = application.handle(
            "PUT",
            "/api/v1/configuration",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            json.dumps(
                {
                    "schema": True,
                    "expected_revision": 0,
                    "snapshot": CONFIGURATION,
                }
            ).encode(),
        )

        assert response.status == 400
        assert response.body == {"schema": 1, "error": {"code": "invalid_request"}}
        assert store.read()["revision"] == 0
    finally:
        store.close()


def test_post_wake_claim_waits_for_the_window_and_returns_its_grant(tmp_path) -> None:
    application, store = _application(tmp_path)

    try:
        store.replace(expected_revision=0, candidate=CONFIGURATION)
        response = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            json.dumps(WAKE_CLAIM).encode(),
        )

        assert response.status == 200
        assert response.body == {
            "schema": 1,
            "claim_id": "claim-api-1",
            "decision": "granted",
            "arbitration_id": "arb-api-1",
            "configuration_revision": 1,
            "conversation_handle": "opaque-conversation-1",
        }
    finally:
        store.close()


def test_wake_claim_from_a_stale_configuration_revision_requires_refresh(
    tmp_path,
) -> None:
    application, store = _application(tmp_path)
    store.replace(expected_revision=0, candidate=CONFIGURATION)

    try:
        response = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            json.dumps({**WAKE_CLAIM, "configuration_revision": 0}).encode(),
        )

        assert response.status == 409
        assert response.body == {
            "schema": 1,
            "error": {"code": "stale_configuration"},
        }
    finally:
        store.close()


def test_wake_claim_requires_an_exact_device_mapping_grant(tmp_path) -> None:
    application, store = _application(tmp_path)
    store.replace(expected_revision=0, candidate=CONFIGURATION)
    application._static_device_scopes["puck-kitchen"] = CredentialScope.from_values(
        rooms=["kitchen"], capabilities=["wake_claim"], wake_mappings=[]
    )

    try:
        response = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            json.dumps(WAKE_CLAIM).encode(),
        )

        assert response.status == 403
        assert response.body == {"schema": 1, "error": {"code": "forbidden"}}
    finally:
        store.close()


@pytest.mark.parametrize(
    ("mapping_state", "expected_status", "expected_code"),
    [("missing", 404, "not_found"), ("inactive", 409, "stale_mapping")],
)
def test_wake_claim_distinguishes_unknown_and_inactive_mappings(
    tmp_path,
    mapping_state,
    expected_status,
    expected_code,
) -> None:
    application, store = _application(tmp_path)
    configuration = CONFIGURATION
    if mapping_state == "missing":
        configuration = {**CONFIGURATION, "wake_mappings": []}
    elif mapping_state == "inactive":
        configuration = {
            **CONFIGURATION,
            "wake_mappings": [{**CONFIGURATION["wake_mappings"][0], "active": False}],
        }
    store.replace(expected_revision=0, candidate=configuration)
    mapping_id = "hey-hermes"

    try:
        response = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            json.dumps(
                {
                    **WAKE_CLAIM,
                    "wake_mapping_id": mapping_id,
                    "configuration_revision": 1,
                }
            ).encode(),
        )

        assert response.status == expected_status
        assert response.body == {"schema": 1, "error": {"code": expected_code}}
    finally:
        store.close()


def test_revoked_credential_during_claim_creation_closes_the_new_claim(
    tmp_path,
) -> None:
    application, store = _application(tmp_path)
    store.replace(expected_revision=0, candidate=CONFIGURATION)
    context = SimpleNamespace(
        device_id="puck-kitchen",
        generation=1,
        scope=CredentialScope.from_values(
            rooms=["kitchen"],
            capabilities=["wake_claim"],
            wake_mappings=["hey-hermes"],
        ),
    )
    still_authorized = [True]
    application._durable_device_context = lambda _headers: (
        context if still_authorized[0] else None
    )
    claim_store = application._conversation_claim_store
    create_claim = claim_store.create_from_decision

    def create_then_revoke(decision, *, credential_generation):
        handle = create_claim(
            decision,
            credential_generation=credential_generation,
        )
        still_authorized[0] = False
        return handle

    claim_store.create_from_decision = create_then_revoke

    try:
        response = _claim_conversation(application)

        assert response.status == 401
        claim = claim_store._claims["opaque-conversation-1"]
        assert claim["status"] == "closed"
        assert claim["close_reason"] == "endpoint_revoked"
    finally:
        store.close()


def test_device_configuration_requires_wake_claim_scope(tmp_path) -> None:
    application, store = _application(tmp_path)
    context = SimpleNamespace(
        device_id="puck-kitchen",
        generation=1,
        scope=CredentialScope.from_values(
            rooms=["kitchen"], capabilities=[], wake_mappings=["hey-hermes"]
        ),
    )
    application._durable_device_context = lambda _headers: context

    try:
        response = application.handle(
            "GET",
            "/api/v1/devices/puck-kitchen/configuration",
            {"Authorization": "Device device-secret"},
            b"",
        )

        assert response.status == 403
        assert response.body == {"schema": 1, "error": {"code": "forbidden"}}
    finally:
        store.close()


def test_wake_claim_in_active_room_fails_without_promoting_another_claim(
    tmp_path,
) -> None:
    application, store = _application(tmp_path)
    store.replace(expected_revision=0, candidate=CONFIGURATION)

    try:
        first = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            json.dumps(WAKE_CLAIM).encode(),
        )
        second = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            json.dumps({**WAKE_CLAIM, "claim_id": "claim-api-2"}).encode(),
        )

        assert first.status == 200
        assert second.status == 409
        assert second.body == {
            "schema": 1,
            "error": {"code": "conversation_active"},
        }
    finally:
        store.close()


def test_wake_claim_returns_service_unavailable_when_configuration_store_fails(
    tmp_path,
) -> None:
    application, store = _application(tmp_path)
    store.close()

    response = application.handle(
        "POST",
        "/api/v1/wake-claims",
        {
            "Authorization": "Device device-secret",
            "Content-Type": "application/json",
        },
        json.dumps(WAKE_CLAIM).encode(),
    )

    assert response.status == 503
    assert response.body == {
        "schema": 1,
        "error": {"code": "service_unavailable"},
    }


def test_simultaneous_wake_requests_both_receive_their_claim_specific_decision(
    tmp_path,
) -> None:
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    clock = FakeClock()
    engine = ArbitrationEngine(
        configuration=store.read,
        clock=clock.monotonic,
        arbitration_id_factory=lambda: "arb-api-concurrent",
    )
    sleeper = BarrierSleeper(clock)
    application = HomeApplication(
        configuration_store=store,
        arbitration_engine=engine,
        admin_token="admin-secret",
        device_credentials={
            "near-secret": "puck-near",
            "priority-secret": "puck-priority",
        },
        static_device_scopes={
            "puck-near": CredentialScope.from_values(
                rooms=["kitchen"],
                capabilities=["wake_claim"],
                wake_mappings=["hey-hermes"],
            ),
            "puck-priority": CredentialScope.from_values(
                rooms=["kitchen"],
                capabilities=["wake_claim"],
                wake_mappings=["hey-hermes"],
            ),
        },
        conversation_claim_store=InMemoryConversationClaimStore(
            handle_factory=lambda: "opaque-conversation-2"
        ),
        clock=clock.monotonic,
        sleeper=sleeper,
    )
    store.replace(expected_revision=0, candidate=TWO_DEVICE_CONFIGURATION)
    claims = [
        {**WAKE_CLAIM, "claim_id": "claim-near", "device_id": "puck-near"},
        {
            **WAKE_CLAIM,
            "claim_id": "claim-priority",
            "device_id": "puck-priority",
        },
    ]
    responses = [None, None]

    def submit(index: int) -> None:
        responses[index] = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": (
                    "Device near-secret" if index == 0 else "Device priority-secret"
                ),
                "Content-Type": "application/json",
            },
            json.dumps(claims[index]).encode(),
        )

    threads = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    try:
        assert all(response is not None for response in responses)
        assert {response.status for response in responses} == {200}
        assert {response.body["decision"] for response in responses} == {
            "granted",
            "denied",
        }
    finally:
        store.close()
