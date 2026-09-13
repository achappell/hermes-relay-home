"""Production runtime assembly for the local Home service."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from pathlib import Path

from hermes_home.api.application import HomeApplication
from hermes_home.api.server import create_server
from hermes_home.domain.arbitration import ArbitrationEngine
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


@dataclass(slots=True)
class HomeRuntime:
    """Owned server and storage resources for one Home process."""

    server: ThreadingHTTPServer
    store: SQLiteConfigurationStore

    def close(self) -> None:
        self.server.server_close()
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

    return RuntimeSettings(
        data_dir=data_dir,
        database_path=database_path,
        bind_host=bind_host,
        port=port,
        admin_token_file=admin_token_file,
        admin_token=admin_token,
        device_credentials_file=device_credentials_file,
        device_credentials=device_credentials,
    )


def create_runtime(settings: RuntimeSettings) -> HomeRuntime:
    """Build the application, server, and durable store for one process."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = SQLiteConfigurationStore(settings.database_path)
    try:
        engine = ArbitrationEngine(configuration=store.read)
        application = HomeApplication(
            configuration_store=store,
            arbitration_engine=engine,
            admin_token=settings.admin_token,
            device_credentials=settings.device_credentials,
        )
        server = create_server(
            application,
            host=settings.bind_host,
            port=settings.port,
        )
    except Exception:
        store.close()
        raise
    return HomeRuntime(server=server, store=store)


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


def _read_secret(path: Path) -> str:
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeConfigurationError("cannot read admin token file") from error
    if not secret:
        raise RuntimeConfigurationError("admin token must not be blank")
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
