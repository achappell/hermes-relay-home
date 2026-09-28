import json
import uuid

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.observability.client_reports import (
    ClientReportStore,
    ReportRateLimited,
)
from hermes_home.storage.sqlite import SQLiteConfigurationStore


def report(now=1790000000):
    return {
        "schema": 1,
        "report_id": str(uuid.uuid4()),
        "created_at": now,
        "app_version": "0.5.0",
        "build": "1",
        "platform": "ios",
        "os_version": "26.6.2",
        "model": "iPhone18,1",
        "events": [
            {"time": now, "name": "connection_lost", "launch_id": str(uuid.uuid4())}
        ],
    }


def test_report_survives_restart_deduplicates_and_expires(tmp_path):
    clock = [1790000000.0]
    store = ClientReportStore(tmp_path / "home.db", clock=lambda: clock[0])
    value = report()
    assert store.receive("device-a", json.dumps(value)) == value["report_id"]
    store.close()
    store = ClientReportStore(tmp_path / "home.db", clock=lambda: clock[0])
    assert store.receive("device-a", json.dumps(value)) == value["report_id"]
    assert len(store.recent()["reports"]) == 1
    assert store.recent()["reports"][0]["device_id"] == "device-a"
    clock[0] += 8 * 86400
    assert store.recent()["reports"] == []
    store.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(prompt="private"),
        lambda r: r.update(device_id="another-device"),
        lambda r: r["events"][0].update(text="private"),
        lambda r: r["events"][0].update(name="private message"),
        lambda r: r["events"][0].update(code="secret-token"),
        lambda r: r["events"][0].update(duration_ms=True),
        lambda r: r.update(model="private text"),
        lambda r: r.update(events=[r["events"][0]] * 101),
        lambda r: r.update(created_at=float("nan")),
        lambda r: r.update(schema=True),
    ],
)
def test_content_fields_and_invalid_values_are_rejected(mutation):
    store = ClientReportStore(clock=lambda: 1790000000)
    value = report()
    mutation(value)
    with pytest.raises((ValueError, TypeError)):
        store.receive("device", json.dumps(value))
    assert store.recent()["reports"] == []
    store.close()


def test_rate_limit_is_per_authenticated_device_and_duplicates_are_safe():
    store = ClientReportStore(clock=lambda: 1790000000)
    value = report()
    store.receive("a", json.dumps(value))
    store.receive("a", json.dumps(value))
    with pytest.raises(ReportRateLimited):
        store.receive("a", json.dumps(report()))
    store.receive("b", json.dumps(report()))
    assert len(store.recent()["reports"]) == 2
    store.close()


def test_device_bound_and_global_storage_caps():
    clock = [1790000000]
    store = ClientReportStore(clock=lambda: clock[0])
    for i in range(105):
        store.receive("a", json.dumps(report(clock[0])))
        clock[0] += 31
    assert (
        store._db.execute("SELECT COUNT(*) FROM client_diagnostic_reports").fetchone()[
            0
        ]
        == 100
    )
    for i in range(1005):
        store.receive(f"device-{i}", json.dumps(report(clock[0])))
    assert (
        store._db.execute("SELECT COUNT(*) FROM client_diagnostic_reports").fetchone()[
            0
        ]
        == 1000
    )
    store.close()


def test_http_authentication_and_no_device_read_access(tmp_path):
    config = SQLiteConfigurationStore(tmp_path / "home.db")
    config.replace(
        expected_revision=0,
        candidate={"rooms": [], "profiles": [], "devices": [], "wake_mappings": []},
    )
    reports = ClientReportStore(clock=lambda: 1790000000)
    app = HomeApplication(
        configuration_store=config,
        arbitration_engine=ArbitrationEngine(configuration=config.read),
        admin_token="admin",
        device_credentials={"device-token": "device-a"},
        client_reports=reports,
    )
    body = json.dumps(report())
    assert app.handle("POST", "/api/v1/client-diagnostics", {}, body).status == 401
    assert (
        app.handle(
            "POST",
            "/api/v1/client-diagnostics",
            {"Authorization": "Bearer admin"},
            body,
        ).status
        == 401
    )
    response = app.handle(
        "POST",
        "/api/v1/client-diagnostics",
        {"Authorization": "Device device-token"},
        body,
    )
    assert response.status == 200
    assert response.body["report_id"] == json.loads(body)["report_id"]
    assert (
        app.handle(
            "GET",
            "/api/v1/client-diagnostics",
            {"Authorization": "Device device-token"},
            b"",
        ).status
        == 404
    )
    reports.close()
