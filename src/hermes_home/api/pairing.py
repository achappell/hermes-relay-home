"""The Home pairing page: approve personal clients without raw admin calls.

The page is the first trusted surface for FR-28. It signs in with the Home
admin credential, keeps only a hashed, expiring session on the server, and
performs the same credential operations as the admin API. Nothing here reads a
network-path identity such as Tailscale headers.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import secrets
import time
from collections.abc import Callable, Mapping
from threading import Lock
from urllib.parse import quote, urlsplit

import segno

from hermes_home.api.pairing_page import render_pairing_page
from hermes_home.api.responses import HTTPResponse
from hermes_home.domain.credentials import (
    CLIENT_ENDPOINT_TYPES,
    CredentialScope,
    CredentialService,
    CredentialStateError,
    CredentialValidationError,
    format_short_enrollment_code,
)
from hermes_home.storage.credentials import CredentialStoreError
from hermes_home.storage.sqlite import (
    ConfigurationMigrationRequired,
    ConfigurationStoreError,
)

SESSION_COOKIE = "hermes_home_pair"
SESSION_SECONDS = 12 * 60 * 60
MAX_SESSIONS = 32


class PairingSurface:
    """Serve ``/pair`` and its cookie-authenticated ``/pair/api/*`` calls."""

    def __init__(
        self,
        *,
        authenticate_admin: Callable[[Mapping[str, str]], bool],
        credential_service: CredentialService | None,
        configuration: Callable[[], Mapping[str, object]],
        close_grant_claims: Callable[[str], object] | None = None,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._authenticate_admin = authenticate_admin
        self._service = credential_service
        self._configuration = configuration
        self._close_grant_claims = close_grant_claims
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._sessions: dict[str, float] = {}
        self._lock = Lock()

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | str,
    ) -> HTTPResponse | None:
        """Return a response for pairing paths, or ``None`` for other paths."""
        if path in {"/pair", "/pair/"}:
            if method != "GET":
                return _error(405, "method_not_allowed")
            nonce = secrets.token_urlsafe(16)
            return HTTPResponse(
                200,
                render_pairing_page(nonce),
                content_type="text/html; charset=utf-8",
                headers=_page_headers(nonce),
            )
        if not path.startswith("/pair/api/"):
            return None
        if self._service is None:
            return _error(503, "pairing_unavailable")
        if method != "POST" and not (method == "GET" and path == "/pair/api/state"):
            return _error(405, "method_not_allowed")
        if method == "POST" and not _same_origin(headers):
            return _error(403, "origin_rejected")
        if path == "/pair/api/session":
            return self._sign_in(headers, body)
        if not self._signed_in(headers):
            return _error(401, "sign_in_required")
        if path == "/pair/api/logout":
            return self._sign_out(headers)
        if path == "/pair/api/state":
            return self._state()
        if path == "/pair/api/offers":
            return self._offer(headers)
        parts = path.split("/")
        if len(parts) == 6 and parts[3] in {"requests", "devices", "grants"}:
            return self._action(parts[3], parts[4], parts[5], body)
        return _error(404, "not_found")

    def _sign_in(self, headers: Mapping[str, str], body: bytes | str) -> HTTPResponse:
        try:
            request = _json_body(body, {"admin_token"})
        except ValueError:
            return _error(400, "invalid_request")
        token = request["admin_token"]
        if type(token) is not str or not token:
            return _error(400, "invalid_request")
        if not self._authenticate_admin({"Authorization": f"Bearer {token}"}):
            return _error(401, "unauthorized")
        session = self._token_factory()
        now = self._clock()
        with self._lock:
            self._sessions = {
                key: expiry for key, expiry in self._sessions.items() if expiry > now
            }
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions, key=self._sessions.__getitem__)
                self._sessions.pop(oldest)
            self._sessions[_digest(session)] = now + SESSION_SECONDS
        cookie = (
            f"{SESSION_COOKIE}={session}; Path=/pair; Max-Age={SESSION_SECONDS}; "
            "HttpOnly; Secure; SameSite=Strict"
        )
        del headers
        return HTTPResponse(
            200,
            {"schema": 1, "signed_in": True},
            headers=(("Set-Cookie", cookie), ("Cache-Control", "no-store")),
        )

    def _sign_out(self, headers: Mapping[str, str]) -> HTTPResponse:
        session = _cookie(headers, SESSION_COOKIE)
        if session:
            with self._lock:
                self._sessions.pop(_digest(session), None)
        cookie = (
            f"{SESSION_COOKIE}=; Path=/pair; Max-Age=0; HttpOnly; Secure; "
            "SameSite=Strict"
        )
        return HTTPResponse(
            200, {"schema": 1, "signed_in": False}, headers=(("Set-Cookie", cookie),)
        )

    def _signed_in(self, headers: Mapping[str, str]) -> bool:
        session = _cookie(headers, SESSION_COOKIE)
        if not session:
            return False
        now = self._clock()
        with self._lock:
            expiry = self._sessions.get(_digest(session))
            if expiry is None:
                return False
            if expiry <= now:
                self._sessions.pop(_digest(session), None)
                return False
        return True

    def _state(self) -> HTTPResponse:
        try:
            snapshot = self._configuration()
            requests = self._service.list_requests()
            devices = self._service.paired_devices()
        except (
            ConfigurationMigrationRequired,
            ConfigurationStoreError,
            CredentialStoreError,
            CredentialValidationError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            return _error(503, "service_unavailable")
        names = {profile["id"]: profile["name"] for profile in snapshot["profiles"]}
        return _json(
            {
                "schema": 1,
                "now": self._clock(),
                "profiles": [
                    {
                        "id": profile["id"],
                        "name": profile["name"],
                        "available": profile["available"],
                        "shared": profile.get("shared") is True,
                    }
                    for profile in snapshot["profiles"]
                ],
                "requests": [
                    {
                        "request_id": request.request_id,
                        "label": request.label,
                        "type": request.endpoint_type,
                        "confirmation_code": request.confirmation_code,
                        "expires_at": request.expires_at,
                        "personal_client": request.endpoint_type
                        in CLIENT_ENDPOINT_TYPES,
                    }
                    for request in requests
                    if request.status == "pending"
                ],
                "devices": [
                    {
                        "device_id": device.device_id,
                        "label": device.label,
                        "type": device.device_type,
                        "status": device.status,
                        "issued_at": device.issued_at,
                        "expires_at": device.expires_at,
                        "grants": [
                            {
                                "grant_id": grant.grant_id,
                                "profile": names.get(grant.profile_id, "Unknown"),
                                "status": grant.status,
                                "bootstrap": grant.bootstrap,
                            }
                            for grant in device.grants
                        ],
                    }
                    for device in devices
                    if device.status != "revoked"
                ],
            }
        )

    def _offer(self, headers: Mapping[str, str]) -> HTTPResponse:
        origin = _origin(headers)
        if origin is None:
            return _error(403, "origin_rejected")
        try:
            offer = self._service.create_offer(short_code=True)
        except CredentialStoreError:
            return _error(503, "service_unavailable")
        code = offer.enrollment_code
        link = f"hermes-home://pair?home={quote(origin, safe='')}&code={code}"
        return _json(
            {
                "schema": 1,
                "code": format_short_enrollment_code(code),
                "home": origin,
                "link": link,
                "qr_svg": _qr_svg(link),
                "expires_at": offer.expires_at,
            }
        )

    def _action(
        self,
        kind: str,
        identifier: str,
        action: str,
        body: bytes | str,
    ) -> HTTPResponse:
        try:
            if kind == "requests" and action == "approve":
                request = _json_body(body, {"profiles"})
                return self._approve(identifier, request["profiles"])
            _json_body(body, set())
            if kind == "requests" and action == "reject":
                self._service.reject_request(identifier, reason="rejected on page")
            elif kind == "devices" and action == "revoke":
                self._service.revoke(identifier, reason="revoked on pairing page")
            elif kind == "grants" and action == "revoke":
                grant = self._service.revoke_client_grant(identifier)
                if self._close_grant_claims is not None:
                    self._close_grant_claims(grant.grant_id)
            else:
                return _error(404, "not_found")
        except CredentialStateError as error:
            return _error(409 if error.code != "not_found" else 404, error.code)
        except CredentialValidationError, ValueError, TypeError:
            return _error(400, "invalid_request")
        except CredentialStoreError, OSError, RuntimeError:
            return _error(503, "service_unavailable")
        return _json({"schema": 1, "ok": True})

    def _approve(self, request_id: str, profiles: object) -> HTTPResponse:
        if not isinstance(profiles, list) or not profiles:
            return _error(400, "invalid_request")
        requests = {
            request.request_id: request for request in self._service.list_requests()
        }
        request = requests.get(request_id)
        if request is None:
            return _error(404, "not_found")
        if request.endpoint_type not in CLIENT_ENDPOINT_TYPES:
            return _error(400, "unsupported_endpoint_type")
        snapshot = self._configuration()
        self._service.approve_request(
            request_id,
            CredentialScope.from_values(rooms=(), capabilities=("client_claim",)),
            configured_rooms=[room["id"] for room in snapshot["rooms"]],
            configured_profiles=[
                profile["id"]
                for profile in snapshot["profiles"]
                if profile["available"] is True
            ],
            client_profiles=profiles,
        )
        return _json({"schema": 1, "ok": True})


def _page_headers(nonce: str) -> tuple[tuple[str, str], ...]:
    policy = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        "img-src data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )
    return (
        ("Content-Security-Policy", policy),
        ("Cache-Control", "no-store"),
        ("Referrer-Policy", "no-referrer"),
        ("X-Content-Type-Options", "nosniff"),
    )


def _qr_svg(link: str) -> str:
    buffer = io.BytesIO()
    segno.make(link, error="m").save(
        buffer,
        kind="svg",
        scale=6,
        border=2,
        xmldecl=False,
        dark="#111111",
        light="#ffffff",
    )
    return buffer.getvalue().decode("utf-8")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == wanted:
            return value if isinstance(value, str) else None
    return None


def _cookie(headers: Mapping[str, str], name: str) -> str | None:
    raw = _header(headers, "Cookie")
    if not raw:
        return None
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name and value:
            return value
    return None


def _origin(headers: Mapping[str, str]) -> str | None:
    origin = _header(headers, "Origin")
    if not origin:
        return None
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _same_origin(headers: Mapping[str, str]) -> bool:
    """Require a browser Origin that names this Home's own Host."""
    origin = _origin(headers)
    host = _header(headers, "Host")
    if origin is None or not host:
        return False
    return hmac.compare_digest(urlsplit(origin).netloc.lower(), host.strip().lower())


def _digest(session: str) -> str:
    return hashlib.sha256(session.encode("utf-8")).hexdigest()


def _json_body(body: bytes | str, expected: set[str]) -> dict[str, object]:
    if isinstance(body, bytes):
        body = body.decode("utf-8") if body else ""
    if not body:
        if expected:
            raise ValueError("request body is required")
        return {}
    try:
        value = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError("invalid JSON") from error
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("invalid request fields")
    return value


def _json(body: dict[str, object]) -> HTTPResponse:
    return HTTPResponse(200, body, headers=(("Cache-Control", "no-store"),))


def _error(status: int, code: str) -> HTTPResponse:
    return HTTPResponse(
        status,
        {"schema": 1, "error": {"code": code}},
        headers=(("Cache-Control", "no-store"),),
    )
