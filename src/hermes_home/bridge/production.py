"""Production ports for the Home-to-Standard Hermes bridge.

The normal Home service deliberately keeps Profile and conversation authority
outside the route adapter.  This module supplies the small, operator-managed
pilot seam needed to run the existing :class:`HomeBridge` against a deployed
Standard gateway before HOME-NW-05 provides the durable claim API.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from websockets.sync.client import connect

from hermes_home.bridge.standard import (
    STANDARD_GATEWAY_PATH,
    AudioSocket,
    BridgeProtocolError,
    ConversationGrant,
    HomeBridge,
    JsonSocket,
)

GRANT_FILE_SCHEMA = 1
MAX_GRANTS = 256
MAX_IDENTIFIER_LENGTH = 256


class ConversationGrantStore:
    """Resolve and durably update a bounded operator-managed grant file.

    This is a deployment bootstrap for live evidence, not the final
    HOME-NW-05 claim authority.  The file contains only server-side binding
    data.  Endpoint callers still provide an opaque handle and Home verifies
    the authenticated device before returning a grant.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        self._grants = self._load()

    def resolve(self, handle: str, device_id: str) -> ConversationGrant | None:
        with self._lock:
            self._grants = self._load()
            grant = self._grants.get(handle)
            if grant is None or grant.device_id != device_id:
                return None
            return grant

    def persist_session(self, grant: ConversationGrant, session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("durable Standard session ID must be non-empty")
        with self._lock:
            grants = self._load()
            current = grants.get(grant.handle)
            if current is None:
                raise ValueError("conversation grant is no longer present")
            if (
                current.device_id != grant.device_id
                or current.profile_id != grant.profile_id
                or current.status != grant.status
            ):
                raise ValueError("conversation grant binding changed")
            grants[grant.handle] = replace(current, session_id=session_id)
            self._write(grants)
            self._grants = grants

    def _load(self) -> dict[str, ConversationGrant]:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("cannot read conversation grants file") from error
        if not isinstance(document, Mapping) or set(document) != {"schema", "grants"}:
            raise ValueError("conversation grants file has invalid fields")
        if document["schema"] != GRANT_FILE_SCHEMA:
            raise ValueError("conversation grants file has an unsupported schema")
        raw_grants = document["grants"]
        if not isinstance(raw_grants, list) or len(raw_grants) > MAX_GRANTS:
            raise ValueError("conversation grants file has too many grants")

        grants: dict[str, ConversationGrant] = {}
        for raw in raw_grants:
            if not isinstance(raw, Mapping):
                raise TypeError("conversation grant must be an object")
            expected = {"handle", "device_id", "profile_id", "status"}
            optional = {"session_id"}
            if set(raw) - expected - optional or expected - set(raw):
                raise ValueError("conversation grant has invalid fields")
            handle = _identifier(raw["handle"], "conversation handle")
            device_id = _identifier(raw["device_id"], "device ID")
            profile_id = _identifier(raw["profile_id"], "Profile ID")
            status = raw["status"]
            if status not in {"active", "revoked", "closed"}:
                raise ValueError("conversation grant has an invalid status")
            session_id = raw.get("session_id")
            if session_id in (None, ""):
                normalized_session_id = None
            else:
                normalized_session_id = _identifier(session_id, "durable session ID")
            if handle in grants:
                raise ValueError("conversation grants file has duplicate handles")
            grants[handle] = ConversationGrant(
                handle=handle,
                device_id=device_id,
                profile_id=profile_id,
                session_id=normalized_session_id,
                status=status,
            )
        return grants

    def _write(self, grants: Mapping[str, ConversationGrant]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema": GRANT_FILE_SCHEMA,
            "grants": [
                _grant_record(grant)
                for grant in sorted(grants.values(), key=lambda item: item.handle)
            ],
        }
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = stream.name
                os.chmod(temporary_path, 0o600)
                json.dump(document, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._path)
        except OSError as error:
            raise OSError("cannot persist conversation grants file") from error
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass


@dataclass(slots=True)
class WebsocketsJsonSocket:
    """Adapt one synchronous ``websockets`` connection to the JSON port."""

    connection: object

    def send_json(self, frame: Mapping[str, object]) -> None:
        self.connection.send(  # type: ignore[attr-defined]
            json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        )

    def receive_json(self, timeout: float | None = None) -> Mapping[str, object]:
        raw = self.connection.recv(timeout=timeout)  # type: ignore[attr-defined]
        if not isinstance(raw, str):
            raise BridgeProtocolError("Standard gateway returned a non-JSON frame")
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise BridgeProtocolError(
                "Standard gateway returned invalid JSON"
            ) from error
        if not isinstance(frame, Mapping):
            raise BridgeProtocolError("Standard gateway JSON frame is not an object")
        return dict(frame)

    def close(self) -> None:
        self.connection.close()  # type: ignore[attr-defined]


@dataclass(slots=True)
class WebsocketsAudioSocket:
    """Adapt one synchronous ``websockets`` connection to the audio port."""

    connection: object

    def send_json(self, frame: Mapping[str, object]) -> None:
        self.connection.send(  # type: ignore[attr-defined]
            json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        )

    def receive(self, timeout: float | None = None) -> object:
        raw = self.connection.recv(timeout=timeout)  # type: ignore[attr-defined]
        if isinstance(raw, bytes):
            return raw
        if not isinstance(raw, str):
            raise BridgeProtocolError("Standard audio returned an invalid frame")
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise BridgeProtocolError("Standard audio returned invalid JSON") from error
        if not isinstance(frame, Mapping):
            raise BridgeProtocolError("Standard audio JSON frame is not an object")
        return dict(frame)

    def close(self) -> None:
        self.connection.close()  # type: ignore[attr-defined]


class WebsocketsJsonSocketFactory:
    """Open bounded synchronous Standard JSON gateway connections."""

    def __init__(self, *, open_timeout: float = 10.0) -> None:
        self._open_timeout = open_timeout

    def open(self, url: str) -> JsonSocket:
        return WebsocketsJsonSocket(
            connect(url, open_timeout=self._open_timeout, max_size=1_048_576)
        )


class WebsocketsAudioSocketFactory:
    """Open synchronous Standard response-audio connections."""

    def __init__(self, *, open_timeout: float = 30.0) -> None:
        self._open_timeout = open_timeout

    def open(self, url: str) -> AudioSocket:
        return WebsocketsAudioSocket(
            connect(url, open_timeout=self._open_timeout, max_size=4 * 1_048_576)
        )


def create_standard_bridge_factory(
    *,
    gateway_url: str,
    hermes_token: str,
    grants_file: Path,
    device_authenticator,
    connect_timeout: float = 10.0,
    audio_timeout: float = 30.0,
) -> Callable[[], HomeBridge]:
    """Build the production HomeBridge factory for one approved Standard target."""

    _validate_gateway_url(gateway_url)
    if not isinstance(hermes_token, str) or not hermes_token.strip():
        raise ValueError("Standard gateway token must be non-empty")
    grant_store = ConversationGrantStore(grants_file)
    gateway_factory = WebsocketsJsonSocketFactory(open_timeout=connect_timeout)
    audio_factory = WebsocketsAudioSocketFactory(open_timeout=audio_timeout)

    def factory() -> HomeBridge:
        return HomeBridge(
            gateway_url=gateway_url,
            hermes_token=hermes_token,
            device_authenticator=device_authenticator,
            conversation_resolver=grant_store.resolve,
            gateway_socket_factory=gateway_factory,
            audio_socket_factory=audio_factory,
            session_persistor=grant_store.persist_session,
            audio_timeout=audio_timeout,
        )

    return factory


def _validate_gateway_url(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Standard gateway URL must be non-empty")
    parts = urlsplit(value)
    if parts.scheme not in {"ws", "wss"} or not parts.netloc:
        raise ValueError("Standard gateway URL must use ws or wss")
    if parts.path.rstrip("/") != STANDARD_GATEWAY_PATH:
        raise ValueError(f"Standard gateway URL must end in {STANDARD_GATEWAY_PATH}")
    if parts.fragment:
        raise ValueError("Standard gateway URL must not contain a fragment")


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if (
        len(normalized) > MAX_IDENTIFIER_LENGTH
        or "\r" in normalized
        or "\n" in normalized
    ):
        raise ValueError(f"{label} is too long or contains a line break")
    return normalized


def _grant_record(grant: ConversationGrant) -> dict[str, object]:
    record: dict[str, object] = {
        "handle": grant.handle,
        "device_id": grant.device_id,
        "profile_id": grant.profile_id,
        "status": grant.status,
    }
    if grant.session_id is not None:
        record["session_id"] = grant.session_id
    return record


__all__ = [
    "GRANT_FILE_SCHEMA",
    "ConversationGrantStore",
    "WebsocketsAudioSocket",
    "WebsocketsAudioSocketFactory",
    "WebsocketsJsonSocket",
    "WebsocketsJsonSocketFactory",
    "create_standard_bridge_factory",
]
