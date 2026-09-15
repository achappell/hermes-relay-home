import json

from hermes_home.api.application import HomeApplication
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.observability.diagnostics import (
    DiagnosticEvent,
    DiagnosticsRecorder,
    InMemoryDiagnosticsStore,
)
from hermes_home.storage.sqlite import SQLiteConfigurationStore


def _application(tmp_path):
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    diagnostics = DiagnosticsRecorder(
        store=InMemoryDiagnosticsStore(), clock=lambda: 100.0
    )
    application = HomeApplication(
        configuration_store=store,
        arbitration_engine=ArbitrationEngine(configuration=store.read),
        admin_token="admin-secret",
        device_credentials={"device-secret": "endpoint-1"},
        diagnostics=diagnostics,
        clock=lambda: 100.0,
    )
    return application, store, diagnostics


def test_diagnostics_status_is_available_to_admin_or_authenticated_endpoint(
    tmp_path,
) -> None:
    application, store, diagnostics = _application(tmp_path)
    try:
        diagnostics.record(
            DiagnosticEvent.create(
                correlation_id="corr-status",
                source="home",
                phase="telemetry",
                outcome="queued",
                occurred_at=100.0,
            )
        )

        unauthorized = application.handle("GET", "/api/v1/diagnostics/status", {}, b"")
        device = application.handle(
            "GET",
            "/api/v1/diagnostics/status",
            {"Authorization": "Device device-secret"},
            b"",
        )
        admin = application.handle(
            "GET",
            "/api/v1/diagnostics/status",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert unauthorized.status == 401
        assert device.status == 200
        assert admin.status == 200
        assert device.body == admin.body
        assert device.body["queued_event_count"] == 1
        assert "endpoint-1" not in json.dumps(device.body)
    finally:
        store.close()


def test_admin_timeline_read_returns_ordered_safe_events_only(tmp_path) -> None:
    application, store, diagnostics = _application(tmp_path)
    try:
        diagnostics.record(
            DiagnosticEvent.create(
                event_id="event-02",
                correlation_id="corr-timeline",
                source="home",
                phase="turn",
                outcome="completed",
                occurred_at=102.0,
                duration_ms=20,
            )
        )
        diagnostics.record(
            DiagnosticEvent.create(
                event_id="event-01",
                correlation_id="corr-timeline",
                source="endpoint",
                phase="turn",
                outcome="started",
                occurred_at=101.0,
            )
        )

        response = application.handle(
            "GET",
            "/api/v1/diagnostics/timeline/corr-timeline",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert response.status == 200
        assert response.body["schema"] == 1
        assert [event["event_id"] for event in response.body["events"]] == [
            "event-01",
            "event-02",
        ]
        assert "prompt" not in json.dumps(response.body)
        assert "credential" not in json.dumps(response.body)
    finally:
        store.close()


def test_timeline_read_rejects_unsafe_correlation_ids(tmp_path) -> None:
    application, store, _ = _application(tmp_path)
    try:
        response = application.handle(
            "GET",
            "/api/v1/diagnostics/timeline/not safe",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert response.status == 400
        assert response.body == {"schema": 1, "error": {"code": "invalid_request"}}
    finally:
        store.close()


def test_diagnostics_failure_cannot_change_an_ordinary_http_response(tmp_path) -> None:
    class BrokenDiagnostics:
        def new_correlation_id(self) -> str:
            return "corr-http"

        def now(self) -> float:
            return 100.0

        def record(self, event) -> bool:
            del event
            raise RuntimeError("diagnostics unavailable")

    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    application = HomeApplication(
        configuration_store=store,
        arbitration_engine=ArbitrationEngine(configuration=store.read),
        admin_token="admin-secret",
        device_credentials={},
        diagnostics=BrokenDiagnostics(),
    )

    try:
        response = application.handle(
            "GET",
            "/api/v1/configuration",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert response.status == 200
    finally:
        store.close()
