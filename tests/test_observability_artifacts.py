import json
from pathlib import Path

OBSERVABILITY = Path(__file__).parents[1] / "observability"


def test_diagnostics_review_boundary_is_documented_without_content_logging() -> None:
    readme = (OBSERVABILITY / "README.md").read_text()

    assert "GET /api/v1/diagnostics/status" in readme
    assert "GET /api/v1/diagnostics/timeline/{correlation_id}" in readme
    assert "14 days" in readme
    assert "30 days" in readme
    assert "raw audio" in readme
    assert "preview-before-approval" in readme


def test_grafana_dashboards_are_valid_and_reference_the_home_metrics() -> None:
    for name, uid in (
        ("hermes-home-overview.json", "hermes-home-overview"),
        ("hermes-home-debug.json", "hermes-home-debug"),
    ):
        dashboard = json.loads(
            (OBSERVABILITY / "grafana" / "dashboards" / name).read_text()
        )

        assert dashboard["uid"] == uid
        assert dashboard["templating"]["list"][0]["name"] == "DS_PROMETHEUS"
        assert dashboard["panels"]
        expressions = [
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
        ]
        assert expressions
        assert all(
            "hermes_home_" in expression or expression.startswith("up{")
            for expression in expressions
        )


def test_grafana_provisioning_points_at_versioned_dashboard_artifacts() -> None:
    dashboard_provider = (
        OBSERVABILITY / "grafana" / "provisioning" / "dashboards" / "home-service.yaml"
    ).read_text()
    datasource_provider = (
        OBSERVABILITY / "grafana" / "provisioning" / "datasources" / "prometheus.yaml"
    ).read_text()

    assert "type: file" in dashboard_provider
    assert "path: /etc/grafana/dashboards/hermes-home" in dashboard_provider
    assert "name: Prometheus" in datasource_provider
    assert "type: prometheus" in datasource_provider


def test_windows_deployment_artifacts_install_a_supervised_scraped_runtime() -> None:
    deployment = Path(__file__).parents[1] / "deploy" / "windows"
    installer = (deployment / "install.ps1").read_text()
    runner = (deployment / "run.ps1").read_text()

    assert "HERMES_HOME_ADMIN_TOKEN_FILE" in installer
    assert "Register-ScheduledTask" in installer
    assert "bearer_token_file" in installer
    assert "promtool.exe" in installer
    assert "Restart-Service" in installer
    assert "hermes_home" in runner
    # PowerShell must not own the native redirection (stderr would end the runner).
    assert "2>&1" in runner and "$env:ComSpec" in runner
    assert "& $python" not in runner
    assert "PYTHONUNBUFFERED" in runner


def test_windows_installer_stops_the_previous_runtime_before_updating_its_venv() -> (
    None
):
    installer = (
        Path(__file__).parents[1] / "deploy" / "windows" / "install.ps1"
    ).read_text()

    assert installer.index(
        "Stop-ExistingHermesHomeTask -Name $TaskName"
    ) < installer.index("& $uv python install 3.14")


def test_windows_installer_restores_a_running_task_when_upgrade_fails() -> None:
    installer = (
        Path(__file__).parents[1] / "deploy" / "windows" / "install.ps1"
    ).read_text()

    assert "$taskWasRunning" in installer
    assert "catch {" in installer
    assert (
        "Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue"
        in installer
    )


def test_ops_alloy_artifact_scrapes_home_with_a_bearer_secret() -> None:
    artifact = (
        Path(__file__).parents[1] / "deploy" / "ops" / "hermes-home.alloy"
    ).read_text()

    assert 'prometheus.scrape "hermes_home"' in artifact
    assert 'job_name        = "hermes-home"' in artifact
    assert "prometheus.remote_write.default.receiver" in artifact
    assert 'type             = "Bearer"' in artifact
    assert 'credentials_file = "/etc/alloy/secrets/hermes-home-admin-token"' in artifact


def test_standard_pilot_ops_artifacts_keep_the_token_out_of_launchd() -> None:
    deployment = Path(__file__).parents[1] / "deploy" / "ops"
    wrapper = (deployment / "hermes-standard-home-pilot.sh").read_text()
    proxy_wrapper = (deployment / "hermes-standard-home-pilot-proxy.sh").read_text()
    plist = (deployment / "com.hermes.home-standard-pilot.plist").read_text()
    proxy_plist = (
        deployment / "com.hermes.home-standard-pilot-proxy.plist"
    ).read_text()

    assert "HERMES_DASHBOARD_SESSION_TOKEN" in wrapper
    assert "standard-token" in wrapper
    assert "--isolated" in wrapper
    assert "standard-token" not in plist
    assert "HERMES_HOME_STANDARD_PROFILE" in plist
    assert "HERMES_STANDARD_PILOT_TOKEN_FILE" in proxy_wrapper
    assert "standard-token" in proxy_wrapper
    assert "standard-token" not in proxy_plist


def test_standard_pilot_relay_is_loopback_limited_and_path_limited() -> None:
    relay = (
        Path(__file__).parents[1]
        / "deploy"
        / "ops"
        / "hermes-standard-home-pilot-proxy.py"
    ).read_text()

    assert 'DEFAULT_PROXY_HOST = "127.0.0.1"' in relay
    assert 'DEFAULT_UPSTREAM_URI = "ws://127.0.0.1:9120"' in relay
    assert 'STANDARD_JSON_PATH = "/api/ws"' in relay
    assert 'STANDARD_AUDIO_PATH = "/api/audio/speak-stream"' in relay
    assert "proxy=None" in relay
