import json
import threading

from hermes_home.api.application import HomeApplication
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.storage.sqlite import SQLiteConfigurationStore

CONFIGURATION = {
    "rooms": [{"id": "kitchen", "name": "Kitchen"}],
    "wake_mappings": [{"id": "hey-hermes", "name": "Hey Hermes"}],
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

WAKE_CLAIM = {
    "schema": 1,
    "claim_id": "claim-api-1",
    "device_id": "puck-kitchen",
    "wake_mapping_id": "hey-hermes",
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
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    return application, store


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
                "wake_mappings": [],
                "devices": [],
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
                    "snapshot": {"rooms": [], "wake_mappings": [], "devices": []},
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
