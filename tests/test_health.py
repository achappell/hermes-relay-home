from __future__ import annotations

import json
import time
from collections import deque

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.bridge.production import (
    ConversationGrantStore,
    StandardHealthProbeProvider,
)
from hermes_home.bridge.standard import BridgeProtocolError, BridgeTimeoutError
from hermes_home.domain.arbitration import ArbitrationEngine, WakeDecision
from hermes_home.domain.conversations import InMemoryConversationClaimStore
from hermes_home.domain.credentials import CredentialScope, CredentialService
from hermes_home.domain.health import (
    HealthDeliveryState,
    HealthProbeResult,
    HealthResult,
    HealthStage,
    HealthValidationError,
    bounded_health_body,
    run_health_check,
)
from hermes_home.storage.credentials import InMemoryCredentialStore
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


class FakeHealthProvider:
    def __init__(self, *, failures=None, device_local=None) -> None:
        self.calls: list[str] = []
        self.failures = dict(failures or {})
        self.device_local = device_local or HealthProbeResult.unsupported()

    def probe(self, stage, *, device_id, room_id, timeout):
        del device_id, room_id, timeout
        self.calls.append(stage)
        if stage == "device_local":
            return self.device_local
        return self.failures.get(stage, HealthProbeResult.verified())


class UncertainDeliveryProvider(FakeHealthProvider):
    def delivery_state(self, device_id):
        del device_id
        return HealthDeliveryState(status="uncertain")


def _application(tmp_path, *, provider=None, capabilities=("health_view",)):
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    store.replace(expected_revision=0, candidate=CONFIGURATION)
    claims = InMemoryConversationClaimStore(
        handle_factory=lambda: "opaque-health-conversation"
    )
    application = HomeApplication(
        configuration_store=store,
        arbitration_engine=ArbitrationEngine(configuration=store.read),
        admin_token="admin-secret",
        device_credentials={"health-secret": "health-phone"},
        conversation_claim_store=claims,
        static_device_scopes={
            "health-phone": CredentialScope.from_values(
                rooms=["kitchen"],
                capabilities=capabilities,
            )
        },
        health_probe_provider=provider,
    )
    return application, store, claims


def _health(application: HomeApplication, *, target="puck-kitchen"):
    return application.handle(
        "GET",
        f"/api/v1/devices/{target}/health",
        {"Authorization": "Device health-secret"},
        b"",
    )


def test_health_returns_four_distinct_boundaries_and_unsupported_local_checks(
    tmp_path,
) -> None:
    provider = FakeHealthProvider()
    application, store, _claims = _application(tmp_path, provider=provider)
    try:
        response = _health(application)

        assert response.status == 200
        health = response.body["health"]
        assert health["status"] == "degraded"
        assert health["correlation_id"].startswith("corr-")
        assert [stage["name"] for stage in health["stages"]] == [
            "route",
            "authorization",
            "bridge",
            "standard",
            "device_local",
        ]
        assert all(stage["status"] == "verified" for stage in health["stages"][:4])
        assert health["stages"][-1] == {
            "name": "device_local",
            "status": "unsupported",
            "reason": "unsupported",
            "next_action": "check_endpoint_capabilities",
        }
        assert health["delivery"] == {"status": "idle"}
        assert provider.calls == ["route", "bridge", "standard", "device_local"]
        encoded = json.dumps(response.body)
        assert "health-secret" not in encoded
        assert "puck-kitchen" not in encoded
    finally:
        store.close()


def test_health_reports_a_typed_boundary_failure_and_never_raw_probe_text(
    tmp_path,
) -> None:
    provider = FakeHealthProvider(
        failures={
            "standard": HealthProbeResult.unavailable(
                "standard_unavailable", next_action="inspect_standard_gateway"
            )
        }
    )
    application, store, _claims = _application(tmp_path, provider=provider)
    try:
        response = _health(application)

        assert response.status == 200
        health = response.body["health"]
        assert health["status"] == "unavailable"
        assert health["stages"][3] == {
            "name": "standard",
            "status": "unavailable",
            "reason": "standard_unavailable",
            "next_action": "inspect_standard_gateway",
        }
        assert "bearer-token" not in json.dumps(response.body)
        assert "wss://private.example" not in json.dumps(response.body)
    finally:
        store.close()


def test_health_returns_a_stale_projection_if_the_target_moves_during_the_check(
    tmp_path,
) -> None:
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    store.replace(expected_revision=0, candidate=CONFIGURATION)

    class ReconfiguredProvider(FakeHealthProvider):
        def __init__(self) -> None:
            super().__init__()
            self.reconfigured = False

        def probe(self, stage, *, device_id, room_id, timeout):
            result = super().probe(
                stage, device_id=device_id, room_id=room_id, timeout=timeout
            )
            if stage == "route" and not self.reconfigured:
                self.reconfigured = True
                self._store.replace(
                    expected_revision=1,
                    candidate={**CONFIGURATION, "devices": []},
                )
            return result

    provider = ReconfiguredProvider()
    provider._store = store
    application = HomeApplication(
        configuration_store=store,
        arbitration_engine=ArbitrationEngine(configuration=store.read),
        admin_token="admin-secret",
        device_credentials={"health-secret": "health-phone"},
        conversation_claim_store=InMemoryConversationClaimStore(),
        static_device_scopes={
            "health-phone": CredentialScope.from_values(
                rooms=["kitchen"], capabilities=["health_view"]
            )
        },
        health_probe_provider=provider,
    )
    try:
        response = _health(application)

        assert response.status == 200
        assert response.body["health"]["status"] == "unavailable"
        assert response.body["health"]["stages"][0] == {
            "name": "route",
            "status": "stale",
            "reason": "stale_target",
            "next_action": "refresh_configuration",
        }
    finally:
        store.close()


def test_health_stops_target_probes_when_authorization_is_unavailable() -> None:
    class ExplodingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def probe(self, stage, *, device_id, room_id, timeout):
            del stage, device_id, room_id, timeout
            self.calls += 1
            raise AssertionError("target probe ran after authorization failed")

    class ExplodingDelivery:
        def __init__(self) -> None:
            self.calls = 0

        def delivery_state(self, device_id):
            del device_id
            self.calls += 1
            raise AssertionError("delivery probe ran after authorization failed")

    provider = ExplodingProvider()
    delivery = ExplodingDelivery()
    result = run_health_check(
        correlation_id="corr-health-auth",
        device_id="device",
        room_id="room",
        authorization=HealthProbeResult.unavailable(
            "authorization_unavailable", next_action="refresh_pairing"
        ),
        provider=provider,
        delivery_provider=delivery,
    )

    assert result.status == "unavailable"
    assert provider.calls == 0
    assert delivery.calls == 0
    assert all(
        stage.status == "unavailable"
        for stage in result.stages
        if stage.name != "authorization"
    )
    assert result.delivery.status == "unavailable"


def test_health_maps_unexpected_probe_exceptions_to_safe_results() -> None:
    class BrokenProvider:
        def probe(self, stage, *, device_id, room_id, timeout):
            del stage, device_id, room_id, timeout
            raise KeyError("private probe payload")

    result = run_health_check(
        correlation_id="corr-health-exception",
        device_id="device",
        room_id="room",
        authorization=HealthProbeResult.verified(),
        provider=BrokenProvider(),
    )

    assert result.status == "unavailable"
    assert next(stage for stage in result.stages if stage.name == "standard") == (
        HealthStage(
            name="standard",
            status="unavailable",
            reason="standard_unavailable",
            next_action="inspect_standard_gateway",
        )
    )
    assert "private probe payload" not in json.dumps(result.to_endpoint())


def test_health_models_reject_inconsistent_overall_and_delivery_states() -> None:
    stages = tuple(
        HealthStage(name=name, status="verified")
        for name in ("route", "authorization", "bridge", "standard")
    )
    with pytest.raises(HealthValidationError, match="overall status"):
        HealthResult(
            correlation_id="corr-health-invalid",
            status="healthy",
            stages=stages,
            delivery=HealthDeliveryState(status="idle"),
        )
    with pytest.raises(HealthValidationError, match="idle delivery"):
        HealthDeliveryState(status="idle", activity="turn")
    with pytest.raises(HealthValidationError, match="active delivery"):
        HealthDeliveryState(status="active")


def test_health_requires_capability_and_room_scope_before_any_probe(tmp_path) -> None:
    provider = FakeHealthProvider()
    application, store, _claims = _application(
        tmp_path,
        provider=provider,
        capabilities=("watch_view",),
    )
    try:
        assert _health(application).status == 403
        assert provider.calls == []
    finally:
        store.close()


def test_health_reads_the_production_delivery_projection_without_mutation(
    tmp_path,
) -> None:
    configuration_store = SQLiteConfigurationStore(tmp_path / "configuration.sqlite3")
    configuration_store.replace(expected_revision=0, candidate=CONFIGURATION)
    claims = ConversationGrantStore(
        tmp_path / "claims.sqlite3",
        configuration=configuration_store.read,
        handle_factory=lambda: "durable-health-conversation",
    )
    provider = FakeHealthProvider()
    application = HomeApplication(
        configuration_store=configuration_store,
        arbitration_engine=ArbitrationEngine(configuration=configuration_store.read),
        admin_token="admin-secret",
        device_credentials={"health-secret": "health-phone"},
        conversation_claim_store=claims,
        static_device_scopes={
            "health-phone": CredentialScope.from_values(
                rooms=["kitchen"], capabilities=["health_view"]
            )
        },
        health_probe_provider=provider,
    )
    handle = claims.create_from_decision(
        WakeDecision(
            claim_id="durable-health-claim",
            decision="granted",
            arbitration_id="durable-health-arbitration",
            configuration_revision=1,
            device_id="puck-kitchen",
            room_id="kitchen",
            wake_mapping_id="hey-hermes",
            profile_id="family",
        ),
        credential_generation=None,
    )
    claims.mark_open(handle, "puck-kitchen")
    claims.record_activity(handle, "puck-kitchen", "turn")
    before = claims.delivery_state("puck-kitchen")
    try:
        response = _health(application)

        assert response.status == 200
        assert response.body["health"]["delivery"] == {
            "status": "active",
            "activity": "turn",
        }
        assert claims.delivery_state("puck-kitchen") == before
    finally:
        claims.close()
        configuration_store.close()


def test_health_accepts_a_paired_credential_with_health_capability(tmp_path) -> None:
    configuration_store = SQLiteConfigurationStore(tmp_path / "configuration.sqlite3")
    configuration_store.replace(expected_revision=0, candidate=CONFIGURATION)
    credential_store = InMemoryCredentialStore()
    service = CredentialService(
        store=credential_store,
        root_secret=b"h" * 32,
        id_factory=iter(["health-offer", "health-request", "health-device"]).__next__,
        token_factory=iter(["health-enrollment-code", "health-token"]).__next__,
        confirmation_factory=lambda: "ABCD2345",
    )
    offer = service.create_offer()
    request = service.submit_request(
        enrollment_code=offer.enrollment_code,
        endpoint_id="health-endpoint",
        label="Health Phone",
        endpoint_type="phone",
        requested_rooms=["kitchen"],
        requested_capabilities=["health_view"],
        secure_storage="platform_secure_store",
    )
    service.approve_request(
        request.request_id,
        CredentialScope.from_values(rooms=["kitchen"], capabilities=["health_view"]),
        configured_rooms=["kitchen"],
    )
    material = service.consume_request(
        request.request_id,
        enrollment_code=offer.enrollment_code,
        secure_storage="platform_secure_store",
    )
    claims = InMemoryConversationClaimStore()
    application = HomeApplication(
        configuration_store=configuration_store,
        arbitration_engine=ArbitrationEngine(configuration=configuration_store.read),
        admin_token="admin-secret",
        device_credentials={},
        credential_service=service,
        conversation_claim_store=claims,
        health_probe_provider=FakeHealthProvider(),
    )
    try:
        response = application.handle(
            "GET",
            "/api/v1/devices/puck-kitchen/health",
            {"Authorization": f"Device {material.credential}"},
            b"",
        )
        assert response.status == 200
    finally:
        configuration_store.close()

    second_path = tmp_path / "out-of-scope"
    second_path.mkdir()
    provider = FakeHealthProvider()
    application, store, _claims = _application(second_path, provider=provider)
    try:
        assert _health(application, target="unknown-device").status == 404
        assert provider.calls == []
        revoked = application.handle(
            "GET",
            "/api/v1/devices/puck-kitchen/health",
            {"Authorization": "Device revoked-secret"},
            b"",
        )
        assert revoked.status == 401
        assert provider.calls == []
    finally:
        store.close()


def test_health_reports_delivery_separately_without_mutating_an_active_claim(
    tmp_path,
) -> None:
    provider = FakeHealthProvider(device_local=HealthProbeResult.verified())
    application, store, claims = _application(tmp_path, provider=provider)
    try:
        handle = claims.create_from_decision(
            WakeDecision(
                claim_id="health-claim",
                decision="granted",
                arbitration_id="health-arbitration",
                configuration_revision=1,
                device_id="puck-kitchen",
                room_id="kitchen",
                wake_mapping_id="hey-hermes",
                profile_id="family",
            ),
            credential_generation=None,
        )
        claims.mark_open(handle, "puck-kitchen")
        before = dict(claims._claims[handle])

        response = _health(application)

        assert response.body["health"]["status"] == "healthy"
        assert response.body["health"]["delivery"] == {
            "status": "active",
            "activity": "open",
        }
        assert claims._claims[handle] == before
    finally:
        store.close()


def test_health_preserves_an_uncertain_delivery_state_without_replaying_it(
    tmp_path,
) -> None:
    provider = UncertainDeliveryProvider(device_local=HealthProbeResult.verified())
    application, store, claims = _application(tmp_path, provider=provider)
    try:
        handle = claims.create_from_decision(
            WakeDecision(
                claim_id="uncertain-health-claim",
                decision="granted",
                arbitration_id="uncertain-health-arbitration",
                configuration_revision=1,
                device_id="puck-kitchen",
                room_id="kitchen",
                wake_mapping_id="hey-hermes",
                profile_id="family",
            ),
            credential_generation=None,
        )
        before = dict(claims._claims[handle])

        response = _health(application)

        assert response.body["health"]["delivery"] == {"status": "uncertain"}
        assert claims._claims[handle] == before
    finally:
        store.close()


def test_health_deadline_maps_a_slow_probe_to_a_bounded_timeout() -> None:
    class SlowProvider:
        def probe(self, stage, *, device_id, room_id, timeout):
            del device_id, room_id, timeout
            if stage == "standard":
                time.sleep(0.1)
            return HealthProbeResult.verified()

    result = run_health_check(
        correlation_id="corr-health-timeout",
        device_id="device",
        room_id="room",
        authorization=HealthProbeResult.verified(),
        provider=SlowProvider(),
        timeout=0.01,
    )

    standard = next(stage for stage in result.stages if stage.name == "standard")
    assert standard.status == "timed_out"
    assert standard.reason == "standard_timeout"
    assert result.status == "unavailable"


def test_health_response_size_bound_discards_oversized_probe_projection() -> None:
    class HugeResult(HealthResult):
        def __init__(self) -> None:
            super().__init__(
                correlation_id="corr-health-size",
                status="degraded",
                stages=(
                    HealthStage(name="route", status="verified"),
                    HealthStage(name="authorization", status="verified"),
                    HealthStage(name="bridge", status="verified"),
                    HealthStage(name="standard", status="verified"),
                    HealthStage(
                        name="device_local",
                        status="unsupported",
                        reason="unsupported",
                        next_action="check_endpoint_capabilities",
                    ),
                ),
                delivery=HealthDeliveryState(status="idle"),
            )

        def to_endpoint(self):
            return {
                "schema": 1,
                "health": {
                    "status": "healthy",
                    "correlation_id": self.correlation_id,
                    "stages": [],
                    "delivery": {"status": "idle"},
                    "probe_payload": "x" * 100_000,
                },
            }

    body = bounded_health_body(HugeResult(), max_bytes=2_000)

    assert body["health"]["status"] == "unavailable"
    assert all(
        stage["reason"] == "response_too_large" for stage in body["health"]["stages"]
    )
    assert len(json.dumps(body, separators=(",", ":")).encode()) <= 2_000
    assert "probe_payload" not in json.dumps(body)


def test_health_diagnostics_are_correlated_fingerprinted_and_content_free(
    tmp_path,
) -> None:
    provider = FakeHealthProvider()
    application, store, _claims = _application(tmp_path, provider=provider)
    try:
        response = _health(application)
        correlation_id = response.body["health"]["correlation_id"]
        events = application._diagnostics.timeline(correlation_id)

        health_events = [event for event in events if event.phase == "health"]
        assert len(health_events) == 1
        event = health_events[0]
        assert event.route_id == "health"
        assert event.outcome == "completed"
        assert event.endpoint_fingerprint is not None
        serialized = json.dumps([item.to_dict() for item in events])
        assert "puck-kitchen" not in serialized
        assert "health-secret" not in serialized
    finally:
        store.close()


def test_health_http_metrics_use_the_device_health_route_label(tmp_path) -> None:
    application, store, _claims = _application(tmp_path, provider=FakeHealthProvider())
    try:
        assert _health(application).status == 200
        response = application.handle(
            "GET",
            "/metrics",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert response.status == 200
        assert (
            'hermes_home_http_requests_total{method="GET",route="device_health",status="200"} 1'
            in response.body
        )
    finally:
        store.close()


class FakeJsonSocket:
    def __init__(self) -> None:
        self.incoming = deque(
            [
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "type": "gateway.ready",
                        "payload": {"version": "test"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": "home-1",
                    "result": {"ok": True},
                },
            ]
        )
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def send_json(self, frame):
        self.sent.append(frame)

    def receive_json(self, timeout=None):
        del timeout
        if not self.incoming:
            raise TimeoutError("fixture exhausted")
        return self.incoming.popleft()

    def close(self):
        self.closed = True


class FakeJsonSocketFactory:
    def __init__(self, socket) -> None:
        self.socket = socket
        self.urls: list[str] = []

    def open(self, url):
        self.urls.append(url)
        return self.socket


def test_standard_health_probe_uses_ready_and_ping_without_a_conversation() -> None:
    socket = FakeJsonSocket()
    factory = FakeJsonSocketFactory(socket)
    provider = StandardHealthProbeProvider(
        gateway_url="wss://standard.example/api/ws",
        hermes_token="server-secret",
        socket_factory=factory,
    )

    result = provider.probe(
        "standard",
        device_id="puck-kitchen",
        room_id="kitchen",
        timeout=1.0,
    )

    assert result == HealthProbeResult.verified()
    assert socket.closed is True
    assert [frame["method"] for frame in socket.sent] == ["gateway.ping"]
    assert "server-secret" in factory.urls[0]
    assert all("conversation" not in frame["method"] for frame in socket.sent)


def test_production_health_probe_maps_route_and_bridge_failures() -> None:
    class FailedSelection:
        status = "unavailable"

        def __init__(self, reason: str) -> None:
            self.safe_failure_reason = reason

    class Selector:
        def __init__(self, reason: str) -> None:
            self.reason = reason

        def select(self):
            return FailedSelection(self.reason)

    provider = StandardHealthProbeProvider(
        gateway_url="wss://standard.example/api/ws",
        hermes_token="server-secret",
        route_selector=Selector("route_identity_mismatch"),
        bridge_probe=lambda device_id, room_id, timeout: HealthProbeResult.unavailable(
            "bridge_unavailable", next_action="inspect_home_bridge"
        ),
    )

    route = provider.probe(
        "route", device_id="puck-kitchen", room_id="kitchen", timeout=1.0
    )
    bridge = provider.probe(
        "bridge", device_id="puck-kitchen", room_id="kitchen", timeout=1.0
    )

    assert route == HealthProbeResult.unavailable(
        "route_identity_mismatch", next_action="refresh_pairing"
    )
    assert bridge == HealthProbeResult.unavailable(
        "bridge_unavailable", next_action="inspect_home_bridge"
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            BridgeTimeoutError("slow"),
            HealthProbeResult.timed_out(
                reason="standard_timeout", next_action="inspect_standard_gateway"
            ),
        ),
        (
            BridgeProtocolError("bad frame"),
            HealthProbeResult.unavailable(
                "standard_protocol_error", next_action="inspect_standard_gateway"
            ),
        ),
        (
            OSError("offline"),
            HealthProbeResult.unavailable(
                "standard_unavailable", next_action="inspect_standard_gateway"
            ),
        ),
    ],
)
def test_production_health_probe_maps_standard_failures_and_closes_client(
    monkeypatch, error, expected
) -> None:
    closed: list[bool] = []

    class FailedClient:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def probe_readiness(self, *, timeout):
            del timeout
            raise error

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        "hermes_home.bridge.production.StandardGatewayClient", FailedClient
    )
    provider = StandardHealthProbeProvider(
        gateway_url="wss://standard.example/api/ws",
        hermes_token="server-secret",
    )

    result = provider.probe(
        "standard", device_id="puck-kitchen", room_id="kitchen", timeout=1.0
    )

    assert result == expected
    assert closed == [True]
