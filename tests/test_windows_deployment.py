from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
INSTALLER = PROJECT_ROOT / "deploy" / "windows" / "install.ps1"
README = PROJECT_ROOT / "deploy" / "windows" / "README.md"


def test_windows_installer_owns_bridge_listener_settings() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    assert "[string] $BridgeBindHost = '127.0.0.1'" in installer
    assert "[int] $BridgePort = 8766" in installer
    assert "[string] $BridgeRouteId = 'local'" in installer
    assert (
        "SetEnvironmentVariable('HERMES_HOME_BRIDGE_BIND_HOST', $BridgeBindHost, 'Machine')"
        in installer
    )
    assert (
        "SetEnvironmentVariable('HERMES_HOME_BRIDGE_PORT', [string] $BridgePort, 'Machine')"
        in installer
    )
    assert (
        "SetEnvironmentVariable('HERMES_HOME_BRIDGE_ROUTE_ID', $BridgeRouteId.Trim(), 'Machine')"
        in installer
    )


def test_windows_installer_owns_standard_pilot_settings_without_embedding_secrets() -> (
    None
):
    installer = INSTALLER.read_text(encoding="utf-8")

    assert "[string] $StandardGatewayUrl = ''" in installer
    assert "[string] $StandardTokenFile = ''" in installer
    assert "[string] $ConversationGrantsFile = ''" in installer
    assert (
        "SetEnvironmentVariable('HERMES_HOME_STANDARD_GATEWAY_URL', $StandardGatewayUrl.Trim(), 'Machine')"
        in installer
    )
    assert (
        "SetEnvironmentVariable('HERMES_HOME_STANDARD_TOKEN_FILE', $standardTokenPath, 'Machine')"
        in installer
    )
    assert (
        "SetEnvironmentVariable('HERMES_HOME_CONVERSATION_GRANTS_FILE', $conversationGrantsPath, 'Machine')"
        in installer
    )
    assert "server-secret" not in installer


def test_windows_deployment_docs_describe_the_tailnet_bridge_path() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "-BridgeBindHost 127.0.0.1 -BridgePort 8766" in readme
    assert "-BridgeRouteId caticornqueen-tailnet" in readme
    assert (
        "tailscale serve --bg --https=443 `" in readme
        and "--set-path=/api/v1/bridge/ws `" in readme
        and "http://127.0.0.1:8766/api/v1/bridge/ws" in readme
    )
