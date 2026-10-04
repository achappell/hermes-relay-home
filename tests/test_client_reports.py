import json
import uuid

import pytest

from hermes_home.api.application import HomeApplication
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.observability.client_reports import (
    ClientReportStore,
    ReportRateLimited,
    validate_report,
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


def report_v2(now=1790000000):
    value = report(now)
    value["schema"] = 2
    value["origins"] = [
        {
            "launch_id": value["events"][0]["launch_id"],
            "app_version": "0.6.0",
            "build_number": "106",
            "os_version": "26.6.1",
            "source_revision": "a" * 40,
            "artifact_sha256": "b" * 64,
            "provenance_status": "verified",
        }
    ]
    value["events"][0].update(
        event_id="evt-" + uuid.uuid4().hex,
        sequence=0,
        connection_id="conn-" + uuid.uuid4().hex,
        home_connection_id="conn-" + uuid.uuid4().hex,
        request_id="req-" + uuid.uuid4().hex,
        correlation_id="corr-" + uuid.uuid4().hex,
        correlation_state="linked",
        leg="client_home",
        pending_state="awaiting_response",
        sent_close_code=1000,
        received_close_code=None,
        observed_status_code=1006,
        phase="closing",
        code="transport_unavailable",
        uncertain=True,
        duration_ms=86400000,
    )
    return value


@pytest.mark.parametrize("factory", [report, report_v2])
def test_report_schemas_preserve_valid_payload(factory):
    value = factory()
    assert validate_report(json.dumps(value), 1790000000) == value


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.pop("origins"),
        lambda r: r.update(origins=[]),
        lambda r: r.update(origins={}),
        lambda r: r.update(origins=r["origins"] * 101),
        lambda r: r["origins"].append(dict(r["origins"][0])),
        lambda r: r["origins"][0].update(launch_id=str(uuid.uuid4())),
        lambda r: r["origins"][0].update(prompt="private"),
        lambda r: r["origins"][0].pop("source_revision"),
        lambda r: r["origins"][0].update(source_revision="A" * 40),
        lambda r: r["origins"][0].update(source_revision="a" * 41),
        lambda r: r["origins"][0].update(artifact_sha256="b" * 63),
        lambda r: r["origins"][0].update(artifact_sha256=123),
        lambda r: r["origins"][0].update(provenance_status="trusted"),
        lambda r: r["origins"][0].update(app_version=None),
        lambda r: r["origins"][0].update(build_number="private"),
        lambda r: r["origins"][0].update(os_version=True),
        lambda r: r["events"][0].pop("event_id"),
        lambda r: r["events"][0].pop("sequence"),
        lambda r: r["events"][0].update(event_id=str(uuid.uuid4())),
        lambda r: r["events"][0].update(connection_id="conn-" + "A" * 32),
        lambda r: r["events"][0].update(home_connection_id="private"),
        lambda r: r["events"][0].update(request_id="corr-" + "a" * 32),
        lambda r: r["events"][0].update(correlation_id=None),
        lambda r: r["events"][0].update(sequence=True),
        lambda r: r["events"][0].update(sequence=-1),
        lambda r: r["events"][0].update(sequence=2**63),
        lambda r: r["events"][0].update(correlation_state="trusted"),
        lambda r: r["events"][0].update(leg="private"),
        lambda r: r["events"][0].update(pending_state=[]),
        lambda r: r["events"][0].update(sent_close_code=1006),
        lambda r: r["events"][0].update(received_close_code=1005),
        lambda r: r["events"][0].update(received_close_code=1015),
        lambda r: r["events"][0].update(observed_status_code=5000),
        lambda r: r["events"][0].update(sent_close_code=True),
        lambda r: r["events"][0].update(received_close_code=999),
        lambda r: r["events"][0].update(observed_status_code=1000.0),
        lambda r: r["events"][0].update(close_reason="private"),
        lambda r: r["events"][0].update(response_kind="accepted"),
        lambda r: r["events"][0].update(process_id="proc-" + "a" * 32),
        lambda r: r["events"][0].update(name="response_write_outcome"),
        lambda r: r["events"][0].update(phase="private"),
    ],
)
def test_schema2_rejects_invalid_origins_and_expanded_fields(mutation):
    value = report_v2()
    mutation(value)
    with pytest.raises(ValueError):
        validate_report(json.dumps(value), 1790000000)


def test_schema2_accepts_retained_legacy_origin_without_backfilling():
    value = report_v2()
    value["origins"][0].update(
        app_version=None,
        build_number=None,
        os_version=None,
        source_revision=None,
        artifact_sha256=None,
        provenance_status="unavailable",
    )
    assert validate_report(json.dumps(value), 1790000000) == value


@pytest.mark.parametrize(
    "name", ["client_response_received", "client_request_resolved"]
)
@pytest.mark.parametrize("response_kind", ["accepted", "rejection"])
def test_schema2_accepts_received_and_resolved_events(name, response_kind):
    value = report_v2()
    value["events"][0].update(name=name, phase="response", response_kind=response_kind)
    assert validate_report(json.dumps(value), 1790000000) == value


def test_schema2_rejects_conflicting_duplicate_event_ids():
    value = report_v2()
    value["events"].append(dict(value["events"][0]))
    assert validate_report(json.dumps(value), 1790000000) == value
    value["events"][1]["sequence"] += 1
    with pytest.raises(ValueError, match="conflicting event identifier"):
        validate_report(json.dumps(value), 1790000000)


def test_schema2_accepts_one_hundred_distinct_origins_and_events():
    value = report_v2()
    value["origins"] = []
    value["events"] = []
    for sequence in range(100):
        launch_id = str(uuid.uuid4())
        value["origins"].append(
            {
                "launch_id": launch_id,
                "app_version": None,
                "build_number": None,
                "os_version": None,
                "source_revision": None,
                "artifact_sha256": None,
                "provenance_status": "unavailable",
            }
        )
        value["events"].append(
            {
                "time": value["created_at"],
                "name": "launch",
                "launch_id": launch_id,
                "event_id": "evt-" + uuid.uuid4().hex,
                "sequence": sequence,
            }
        )
    assert validate_report(json.dumps(value), 1790000000) == value


@pytest.mark.parametrize("status", ["unverified", "unavailable"])
def test_schema2_accepts_source_metadata_formats(status):
    value = report_v2()
    value["origins"][0].update(
        source_revision="c" * 64,
        artifact_sha256=None,
        provenance_status=status,
    )
    value["events"][0].update(
        sequence=2**63 - 1,
        sent_close_code=4999,
        received_close_code=1000,
        observed_status_code=None,
    )
    assert validate_report(json.dumps(value), 1790000000) == value


@pytest.mark.parametrize("factory", [report, report_v2])
def test_report_body_size_limit_counts_complete_utf8_body(factory):
    body = json.dumps(factory()).encode()
    body += b" " * (65536 - len(body))
    validate_report(body, 1790000000)
    with pytest.raises(ValueError, match="report too large"):
        validate_report(body + b" ", 1790000000)
    with pytest.raises(ValueError, match="report too large"):
        validate_report(body.decode() + "\u00e9", 1790000000)


def test_schema1_does_not_accept_schema2_fields_or_names():
    expanded = report_v2()
    for key in expanded["events"][0].keys() - {
        "time",
        "name",
        "launch_id",
        "phase",
        "code",
        "uncertain",
        "duration_ms",
    }:
        value = report()
        value["events"][0][key] = expanded["events"][0][key]
        with pytest.raises(ValueError):
            validate_report(json.dumps(value), 1790000000)
    value = report()
    value["origins"] = expanded["origins"]
    with pytest.raises(ValueError):
        validate_report(json.dumps(value), 1790000000)
    value = report()
    value["events"][0]["name"] = "client_response_received"
    with pytest.raises(ValueError):
        validate_report(json.dumps(value), 1790000000)


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
@pytest.mark.parametrize("factory", [report, report_v2])
def test_content_fields_and_invalid_values_are_rejected(mutation, factory):
    store = ClientReportStore(clock=lambda: 1790000000)
    value = factory()
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


@pytest.mark.parametrize("factory", [report, report_v2])
def test_http_authentication_and_no_device_read_access(tmp_path, factory):
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
    body = json.dumps(factory())
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


@pytest.mark.parametrize("response_kind", ["private-canary", {}, None, True])
@pytest.mark.parametrize(
    "name", ["client_response_received", "client_request_resolved"]
)
def test_schema2_rejects_unsafe_response_kind(name, response_kind):
    value = report_v2()
    value["events"][0].update(name=name, response_kind=response_kind)
    with pytest.raises(ValueError):
        validate_report(json.dumps(value), 1790000000)


def association(
    store, device="device-a", socket=None, token=None, *, ambiguous=False, finalize=True
):
    socket = socket or "conn-" + uuid.uuid4().hex
    token = token or "req-" + uuid.uuid4().hex
    correlation = "corr-" + uuid.uuid4().hex
    process = "proc-" + uuid.uuid4().hex
    store.record_association(
        device, socket, token, correlation, process, ambiguous=ambiguous
    )
    if finalize:
        store.finalize_associations(device, socket, {token} if ambiguous else set())
    store._association_queue.join()
    return socket, token, correlation, process


def test_associations_survive_restart_and_require_exact_authenticated_scope(tmp_path):
    now = 1790000000
    path = tmp_path / "home.db"
    store = ClientReportStore(path, clock=lambda: now)
    socket, token, correlation, process = association(store)
    store.close()
    store = ClientReportStore(path, clock=lambda: now)
    assert store.lookup_association("device-a", socket, token) == {
        "state": "linked",
        "correlation_id": correlation,
        "process_id": process,
        "observed_at": now,
    }
    assert store.lookup_association("device-b", socket, token) == {
        "state": "unavailable"
    }
    assert store.lookup_association("device-a", "conn-" + "0" * 32, token) == {
        "state": "unavailable"
    }
    assert store.lookup_association("device-a", socket, "req-" + "0" * 32) == {
        "state": "unavailable"
    }
    store.close()


def test_duplicate_associations_remain_ambiguous_without_extending_retention(tmp_path):
    clock = [1790000000]
    path = tmp_path / "home.db"
    store = ClientReportStore(path, clock=lambda: clock[0])
    socket, token, _, _ = association(store)
    clock[0] += 6 * 86400
    association(store, socket=socket, token=token, ambiguous=True)
    assert store.lookup_association("device-a", socket, token) == {"state": "ambiguous"}
    other_socket, _, _, _ = association(store, token=token)
    assert (
        store.lookup_association("device-a", other_socket, token)["state"] == "linked"
    )
    store.close()
    store = ClientReportStore(path, clock=lambda: clock[0])
    assert store.lookup_association("device-a", socket, token) == {"state": "ambiguous"}
    clock[0] += 86400
    assert store.lookup_association("device-a", socket, token) == {
        "state": "unavailable"
    }
    losses = store.recent()["association_losses"]
    assert losses["expired"] == 1
    assert losses["conflicts"] == 1
    store.close()


def test_report_associations_ignore_client_claimed_correlation():
    now = 1790000000
    store = ClientReportStore(clock=lambda: now)
    socket, token, correlation, _ = association(store)
    value = report_v2(now)
    value["events"][0].update(home_connection_id=socket, request_id=token)
    claimed = value["events"][0]["correlation_id"]
    store.receive("device-a", json.dumps(value))
    store.receive("device-b", json.dumps(value))
    items = {item["device_id"]: item for item in store.recent()["reports"]}
    assert items["device-a"]["associations"][0]["correlation_id"] == correlation
    assert items["device-a"]["report"]["events"][0]["correlation_id"] == claimed
    assert items["device-b"]["associations"] == [
        {"event_id": value["events"][0]["event_id"], "state": "unavailable"}
    ]
    store.close()


def test_schema2_rejects_conflicting_retained_events_and_origins():
    clock = [1790000000]
    store = ClientReportStore(clock=lambda: clock[0])
    value = report_v2()
    store.receive("device-a", json.dumps(value))
    clock[0] += 31
    value["report_id"] = str(uuid.uuid4())
    value["events"][0]["sequence"] += 1
    with pytest.raises(ValueError, match="conflicting event identifier"):
        store.receive("device-a", json.dumps(value))
    value["events"][0]["sequence"] -= 1
    value["origins"][0]["app_version"] = "9"
    with pytest.raises(ValueError, match="conflicting event origin"):
        store.receive("device-a", json.dumps(value))
    store.close()


def test_association_caps_evict_oldest_and_account_loss():
    now = 1790000000
    store = ClientReportStore(clock=lambda: now)
    first_socket, first_token, _, _ = association(store)
    for index in range(1024):
        association(store, socket=f"conn-{index:032x}")
    assert store.lookup_association("device-a", first_socket, first_token) == {
        "state": "unavailable"
    }
    for index in range(3073):
        association(store, device=f"other-{index}")
    assert (
        store._db.execute(
            "SELECT COUNT(*) FROM client_diagnostic_associations"
        ).fetchone()[0]
        == 4096
    )
    assert store.recent()["association_losses"]["evicted"] == 2
    store.close()


def test_corrupt_association_is_unavailable():
    store = ClientReportStore(clock=lambda: 1790000000)
    socket, token, _, _ = association(store)
    with store._lock, store._db:
        store._db.execute(
            "UPDATE client_diagnostic_associations SET correlation_id='private-canary'"
        )
    assert store.lookup_association("device-a", socket, token) == {
        "state": "unavailable"
    }
    store.close()


def test_association_queue_is_nonblocking_bounded_and_counts_failures(monkeypatch):
    from threading import Event

    store = ClientReportStore(clock=lambda: 1790000000)
    entered, release = Event(), Event()

    def blocked(*args):
        entered.set()
        release.wait(5)
        raise OSError("private-canary")

    monkeypatch.setattr(store, "_persist_association", blocked)
    args = (
        "device",
        "conn-" + "a" * 32,
        "req-" + "b" * 32,
        "corr-" + "c" * 32,
        "proc-" + "d" * 32,
    )
    store.record_association(*args)
    assert entered.wait(1)
    for _ in range(1025):
        store.record_association(*args)
    assert store._association_queue.qsize() == 1024
    assert store.recent()["association_losses"]["queue_dropped"] == 1
    release.set()
    store._association_queue.join()
    assert store.recent()["association_losses"]["sink_failed"] == 1025
    store.close()


def test_unfinalized_socket_is_unavailable_across_restart(tmp_path):
    path = tmp_path / "home.db"
    store = ClientReportStore(path, clock=lambda: 1790000000)
    socket, token, _, _ = association(store, finalize=False)
    assert store.lookup_association("device-a", socket, token) == {
        "state": "unavailable"
    }
    store.close()
    store = ClientReportStore(path, clock=lambda: 1790000000)
    assert store.lookup_association("device-a", socket, token) == {
        "state": "unavailable"
    }
    store.finalize_associations("device-a", socket, {token})
    store._association_queue.join()
    assert store.lookup_association("device-a", socket, token) == {"state": "ambiguous"}
    store.close()


def test_dropped_finalization_cannot_promote_a_provisional_association(monkeypatch):
    from queue import Full

    store = ClientReportStore(clock=lambda: 1790000000)
    socket, token, _, _ = association(store, finalize=False)

    def full(item):
        raise Full

    monkeypatch.setattr(store._association_queue, "put_nowait", full)
    store.finalize_associations("device-a", socket, {token})
    assert store.lookup_association("device-a", socket, token) == {
        "state": "unavailable"
    }
    assert store.recent()["association_losses"]["queue_dropped"] == 1
    store.close()


def test_shutdown_does_not_wait_for_blocked_sqlite(monkeypatch):
    from threading import Event
    from time import monotonic

    store = ClientReportStore(clock=lambda: 1790000000)
    entered, release = Event(), Event()

    def blocked(*args):
        entered.set()
        release.wait(5)

    monkeypatch.setattr(store, "_persist_association", blocked)
    store.record_association(
        "device",
        "conn-" + "a" * 32,
        "req-" + "b" * 32,
        "corr-" + "c" * 32,
        "proc-" + "d" * 32,
    )
    assert entered.wait(1)
    started = monotonic()
    store.close()
    elapsed = monotonic() - started
    release.set()
    store._association_worker.join(timeout=1)
    assert elapsed < 0.25


def test_conflicting_schema2_retry_is_rejected_without_replacing_original():
    store = ClientReportStore(clock=lambda: 1790000000)
    value = report_v2()
    store.receive("device", json.dumps(value))
    original_sequence = value["events"][0]["sequence"]
    value["events"][0]["sequence"] += 1
    with pytest.raises(ValueError, match="conflicting report identifier"):
        store.receive("device", json.dumps(value))
    assert (
        store.recent()["reports"][0]["report"]["events"][0]["sequence"]
        == original_sequence
    )
    store.close()


def test_failed_finalize_keeps_durable_evidence_unavailable(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "home.db"
    store = ClientReportStore(path, clock=lambda: 1790000000)
    socket, token, _, _ = association(store, finalize=False)

    def unavailable(*args):
        raise sqlite3.OperationalError("private-canary")

    monkeypatch.setattr(store, "_finalize_associations", unavailable)
    store.finalize_associations("device-a", socket, set())
    store._association_queue.join()
    assert store.recent()["association_losses"]["sink_failed"] == 1
    store.close()
    store = ClientReportStore(path, clock=lambda: 1790000000)
    assert store.lookup_association("device-a", socket, token) == {
        "state": "unavailable"
    }
    store.close()
