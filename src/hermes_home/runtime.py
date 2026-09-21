"""Production runtime assembly for the local Home service."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from websockets.sync.server import Server

from hermes_home.api.application import HomeApplication
from hermes_home.api.bridge_server import create_bridge_server
from hermes_home.api.server import create_server
from hermes_home.bridge.endpoint import BridgeRoute
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.domain.credentials import CredentialService
from hermes_home.domain.health import HealthProbeResult
from hermes_home.observability.diagnostics import DiagnosticsRecorder
from hermes_home.observability.metrics import MetricsRegistry
from hermes_home.storage.credentials import SQLiteCredentialStore
from hermes_home.storage.diagnostics import SQLiteDiagnosticsStore
from hermes_home.storage.sqlite import SQLiteConfigurationStore


class RuntimeConfigurationError(ValueError):
    """Raised when the process cannot build a safe runtime configuration."""


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Resolved process settings, including credentials held in memory."""

    data_dir: Path
    database_path: Path
    bind_host: str
    port: int
    admin_token_file: Path
    admin_token: str = field(repr=False)
    device_credentials_file: Path | None
    device_credentials: dict[str, str] = field(repr=False)
    credential_root_secret_file: Path | None = None
    credential_root_secret: bytes | None = field(default=None, repr=False)
    bridge_bind_host: str | None = None
    bridge_port: int = 8766
    bridge_route_id: str = "local"
    standard_gateway_url: str | None = None
    standard_token_file: Path | None = None
    standard_token: str | None = field(default=None, repr=False)
    conversation_idle_timeout_seconds: float = 8.0

    @property
    def auth_mode(self) -> str:
        if self.credential_root_secret is not None:
            return "paired"
        if self.device_credentials_file is not None:
            return "legacy"
        return "disabled"


@dataclass(slots=True)
class HomeRuntime:
    """Owned server and storage resources for one Home process."""

    server: ThreadingHTTPServer
    store: SQLiteConfigurationStore
    diagnostics_store: SQLiteDiagnosticsStore
    diagnostics: DiagnosticsRecorder
    credential_store: SQLiteCredentialStore | None = None
    conversation_store: object | None = field(default=None, repr=False)
    bridge_server: Server | None = None
    bridge_thread: Thread | None = field(default=None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.bridge_server is not None:
            try:
                self.bridge_server.shutdown()
            except Exception as error:  # noqa: BLE001 - shutdown is best effort
                del error
        if self.bridge_thread is not None:
            self.bridge_thread.join(timeout=2)
        self.server.server_close()
        if self.credential_store is not None:
            self.credential_store.close()
        if self.conversation_store is not None:
            self.conversation_store.close()
        self.diagnostics_store.close()
        self.store.close()


def load_settings(
    environ: Mapping[str, str] | None = None,
) -> RuntimeSettings:
    """Load runtime settings from environment variables and secret files."""
    values = os.environ if environ is None else environ
    data_dir = _path_value(
        values.get("HERMES_HOME_DATA_DIR"),
        default=Path.home() / ".hermes-home",
        name="data directory",
    )
    database_path = _path_value(
        values.get("HERMES_HOME_DATABASE"),
        default=data_dir / "home.sqlite3",
        name="database path",
    )
    admin_token_file = _path_value(
        values.get("HERMES_HOME_ADMIN_TOKEN_FILE"),
        default=data_dir / "admin-token",
        name="admin token file",
    )
    bind_host = values.get("HERMES_HOME_BIND_HOST", "127.0.0.1").strip()
    if not bind_host:
        raise RuntimeConfigurationError("bind host must not be blank")

    port = _port_value(values.get("HERMES_HOME_PORT", "8765"))
    admin_token = _read_secret(admin_token_file)

    device_credentials_value = values.get("HERMES_HOME_DEVICE_CREDENTIALS_FILE")
    if device_credentials_value and device_credentials_value.strip():
        device_credentials_file = _path_value(
            device_credentials_value,
            default=None,
            name="device credentials file",
        )
        device_credentials = _read_device_credentials(device_credentials_file)
    else:
        device_credentials_file = None
        device_credentials = {}

    credential_root_value = values.get("HERMES_HOME_CREDENTIAL_ROOT_SECRET_FILE")
    if credential_root_value and credential_root_value.strip():
        credential_root_secret_file = _path_value(
            credential_root_value,
            default=None,
            name="credential root secret file",
        )
        credential_root_secret = _read_root_secret(credential_root_secret_file)
    else:
        credential_root_secret_file = None
        credential_root_secret = None
    if credential_root_secret is not None and device_credentials_file is not None:
        raise RuntimeConfigurationError(
            "credential sources cannot be configured together"
        )

    bridge_bind_host = values.get("HERMES_HOME_BRIDGE_BIND_HOST", "127.0.0.1").strip()
    if not bridge_bind_host:
        raise RuntimeConfigurationError("bridge bind host must not be blank")
    bridge_port = _port_value(values.get("HERMES_HOME_BRIDGE_PORT", "8766"))
    bridge_route_id = values.get("HERMES_HOME_BRIDGE_ROUTE_ID", "local").strip()
    if not bridge_route_id:
        raise RuntimeConfigurationError("bridge route ID must not be blank")

    standard_gateway_url = values.get("HERMES_HOME_STANDARD_GATEWAY_URL", "").strip()
    standard_token_file_value = values.get("HERMES_HOME_STANDARD_TOKEN_FILE", "")
    standard_configured = bool(standard_gateway_url or standard_token_file_value)
    if standard_configured and not (standard_gateway_url and standard_token_file_value):
        raise RuntimeConfigurationError(
            "Standard bridge settings require gateway URL and token file"
        )
    conversation_idle_timeout_seconds = _positive_seconds_value(
        values.get("HERMES_HOME_CONVERSATION_IDLE_TIMEOUT_SECONDS", "8")
    )
    if standard_configured:
        if device_credentials_file is None and credential_root_secret is None:
            raise RuntimeConfigurationError(
                "Standard bridge settings require paired or device credentials"
            )
        standard_token_file = _path_value(
            standard_token_file_value,
            default=None,
            name="Standard token file",
        )
        try:
            standard_token = standard_token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise RuntimeConfigurationError(
                "cannot read Standard token file"
            ) from error
        if not standard_token:
            raise RuntimeConfigurationError("Standard token must not be blank")
    else:
        standard_gateway_url = None
        standard_token_file = None
        standard_token = None

    return RuntimeSettings(
        data_dir=data_dir,
        database_path=database_path,
        bind_host=bind_host,
        port=port,
        admin_token_file=admin_token_file,
        admin_token=admin_token,
        device_credentials_file=device_credentials_file,
        device_credentials=device_credentials,
        credential_root_secret_file=credential_root_secret_file,
        credential_root_secret=credential_root_secret,
        bridge_bind_host=bridge_bind_host,
        bridge_port=bridge_port,
        bridge_route_id=bridge_route_id,
        standard_gateway_url=standard_gateway_url,
        standard_token_file=standard_token_file,
        standard_token=standard_token,
        conversation_idle_timeout_seconds=conversation_idle_timeout_seconds,
    )


def create_runtime(
    settings: RuntimeSettings,
    *,
    bridge_factory: Callable[[], object] | None = None,
    bridge_server_factory: Callable[..., Server] | None = None,
    health_probe_provider: object | None = None,
) -> HomeRuntime:
    """Build the application, server, and durable store for one process."""
    if (
        settings.credential_root_secret is not None
        and settings.device_credentials_file is not None
    ):
        raise RuntimeConfigurationError(
            "credential sources cannot be configured together"
        )
    if settings.standard_gateway_url is not None and settings.auth_mode == "disabled":
        raise RuntimeConfigurationError(
            "Standard bridge settings require paired or device credentials"
        )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = SQLiteConfigurationStore(settings.database_path)
    diagnostics_store: SQLiteDiagnosticsStore | None = None
    credential_store = None
    conversation_store = None
    server: ThreadingHTTPServer | None = None
    bridge_server = None
    bridge_thread = None
    bridge_thread_started = False
    bridge_runtime_state: dict[str, object] = {
        "factory": bridge_factory,
        "server": None,
        "started": False,
    }

    def probe_bridge(*_args: object) -> HealthProbeResult:
        if (
            bridge_runtime_state["factory"] is None
            or bridge_runtime_state["started"] is not True
        ):
            return HealthProbeResult.unavailable(
                "bridge_unavailable", next_action="inspect_home_bridge"
            )
        server = bridge_runtime_state["server"]
        if server is None:
            return HealthProbeResult.unavailable(
                "bridge_unavailable", next_action="inspect_home_bridge"
            )
        is_serving = getattr(server, "is_serving", None)
        if callable(is_serving):
            try:
                if not is_serving():
                    return HealthProbeResult.unavailable(
                        "bridge_unavailable", next_action="inspect_home_bridge"
                    )
            except Exception:  # noqa: BLE001 - health must fail closed
                return HealthProbeResult.unavailable(
                    "bridge_unavailable", next_action="inspect_home_bridge"
                )
        return HealthProbeResult.verified()

    try:
        diagnostics_store = SQLiteDiagnosticsStore(settings.database_path)
        metrics = MetricsRegistry()
        diagnostics = DiagnosticsRecorder(
            store=diagnostics_store,
            metrics=metrics,
        )
        if settings.standard_gateway_url is not None or bridge_factory is not None:
            from hermes_home.bridge.production import ConversationGrantStore

            conversation_store = ConversationGrantStore(
                settings.database_path,
                configuration=store.read,
                idle_timeout_seconds=settings.conversation_idle_timeout_seconds,
                route_id=settings.bridge_route_id,
            )
        credential_service = None
        if settings.credential_root_secret is not None:
            credential_store = SQLiteCredentialStore(settings.database_path)
            credential_service = CredentialService(
                store=credential_store,
                root_secret=settings.credential_root_secret,
                revocation_observer=conversation_store,
            )
        if health_probe_provider is None and settings.standard_gateway_url is not None:
            if settings.standard_token is None:
                raise RuntimeConfigurationError(
                    "Standard bridge settings are incomplete"
                )
            from hermes_home.bridge.production import StandardHealthProbeProvider

            health_probe_provider = StandardHealthProbeProvider(
                gateway_url=settings.standard_gateway_url,
                hermes_token=settings.standard_token,
                bridge_probe=probe_bridge,
                bridge_configured=True,
            )
        engine = ArbitrationEngine(configuration=store.read)
        application = HomeApplication(
            configuration_store=store,
            arbitration_engine=engine,
            admin_token=settings.admin_token,
            device_credentials=settings.device_credentials,
            credential_service=credential_service,
            conversation_claim_store=conversation_store,
            health_probe_provider=health_probe_provider,
            metrics=metrics,
            diagnostics=diagnostics,
        )
        if bridge_factory is None and settings.standard_gateway_url is not None:
            if settings.standard_token is None or conversation_store is None:
                raise RuntimeConfigurationError(
                    "Standard bridge settings are incomplete"
                )
            from hermes_home.bridge.production import create_standard_bridge_factory

            bridge_factory = create_standard_bridge_factory(
                gateway_url=settings.standard_gateway_url,
                hermes_token=settings.standard_token,
                conversation_store=conversation_store,
                device_authenticator=application.device_authenticator,
            )
        bridge_runtime_state["factory"] = bridge_factory
        server = create_server(
            application,
            host=settings.bind_host,
            port=settings.port,
        )
        server_factory = bridge_server_factory or create_bridge_server
        bridge_server = server_factory(
            bridge_factory=bridge_factory,
            route=BridgeRoute(id=settings.bridge_route_id),
            host=settings.bridge_bind_host or settings.bind_host,
            port=settings.bridge_port,
            diagnostics=diagnostics,
        )
        bridge_runtime_state["server"] = bridge_server
        bridge_thread = Thread(
            target=bridge_server.serve_forever,
            name="hermes-home-bridge-server",
            daemon=True,
        )
        bridge_thread.start()
        bridge_thread_started = True
        bridge_runtime_state["started"] = True
    except Exception:
        if bridge_server is not None:
            try:
                bridge_server.shutdown()
            except Exception as error:  # noqa: BLE001 - shutdown is best effort
                del error
        if bridge_thread is not None and bridge_thread_started:
            bridge_thread.join(timeout=2)
        if server is not None:
            server.server_close()
        if credential_store is not None:
            credential_store.close()
        if conversation_store is not None:
            conversation_store.close()
        if diagnostics_store is not None:
            diagnostics_store.close()
        store.close()
        raise
    assert diagnostics_store is not None
    return HomeRuntime(
        server=server,
        store=store,
        diagnostics_store=diagnostics_store,
        diagnostics=diagnostics,
        credential_store=credential_store,
        conversation_store=conversation_store,
        bridge_server=bridge_server,
        bridge_thread=bridge_thread,
    )


def run(settings: RuntimeSettings | None = None) -> None:
    """Run the Home HTTP server until the process is interrupted."""
    runtime = create_runtime(settings or load_settings())
    try:
        runtime.server.serve_forever()
    finally:
        runtime.close()


def main() -> None:
    """Console-script entry point for the Home service."""
    run()


def _path_value(
    value: str | None,
    *,
    default: Path | None,
    name: str,
) -> Path:
    if value is None:
        if default is None:
            raise RuntimeConfigurationError(f"{name} must be configured")
        return default.expanduser()
    text = value.strip()
    if not text:
        raise RuntimeConfigurationError(f"{name} must not be blank")
    return Path(text).expanduser()


def _port_value(value: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise RuntimeConfigurationError("port must be an integer") from error
    if not 0 <= port <= 65535:
        raise RuntimeConfigurationError("port must be between 0 and 65535")
    return port


def _positive_seconds_value(value: str) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as error:
        raise RuntimeConfigurationError(
            "conversation idle timeout must be a positive number of seconds"
        ) from error
    if not math.isfinite(seconds) or not 0 < seconds <= 600:
        raise RuntimeConfigurationError(
            "conversation idle timeout must be a positive number of seconds no greater than 600"
        )
    return seconds


def _read_secret(path: Path) -> str:
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeConfigurationError("cannot read admin token file") from error
    if not secret:
        raise RuntimeConfigurationError("admin token must not be blank")
    return secret


def _read_root_secret(path: Path) -> bytes:
    try:
        encoded = path.read_text(encoding="utf-8").strip()
        if len(encoded) != 64:
            raise ValueError("root secret must contain 64 hex characters")
        secret = bytes.fromhex(encoded)
    except (OSError, ValueError) as error:
        raise RuntimeConfigurationError(
            "cannot read credential root secret file"
        ) from error
    if len(secret) != 32:
        raise RuntimeConfigurationError(
            "credential root secret must contain 64 hex characters"
        )
    return secret


def _read_device_credentials(path: Path) -> dict[str, str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeConfigurationError(
            "cannot read device credentials file"
        ) from error
    if not isinstance(document, dict) or any(
        type(credential) is not str
        or not credential
        or type(device_id) is not str
        or not device_id
        for credential, device_id in document.items()
    ):
        raise RuntimeConfigurationError(
            "device credentials file must map non-empty credentials to device IDs"
        )
    return dict(document)


if __name__ == "__main__":
    main()
