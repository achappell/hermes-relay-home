"""Framework-independent endpoint credential policy types."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import Protocol, TypeVar

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

LOGGER = logging.getLogger(__name__)

SUPPORTED_CREDENTIAL_CAPABILITIES = frozenset(
    {
        "client_claim",
        "consequence_confirm",
        "health_view",
        "sensitive_entry",
        "touch_claim",
        "wake_claim",
        "watch_view",
    }
)
ENROLLMENT_TTL_SECONDS = 300.0
CREDENTIAL_TTL_SECONDS = 90 * 24 * 60 * 60
RENEWAL_WINDOW_SECONDS = 14 * 24 * 60 * 60
ROTATION_OVERLAP_SECONDS = 600
SECURE_STORAGE_PLATFORM = "platform_secure_store"
CLIENT_ENDPOINT_TYPES = frozenset({"tui", "ios", "android"})
PENDING_OWNER_GRANT_TTL_SECONDS = 24 * 60 * 60
MAX_CLIENT_GRANTS = 16

T = TypeVar("T")


class CredentialStore(Protocol):
    """The transaction boundary required by the credential domain."""

    def mutate(self, mutation: Callable[[dict[str, object]], T]) -> T: ...

    def read_state(self) -> dict[str, object]: ...


class RevocationObserver(Protocol):
    """Notification port for interrupting reachable endpoint activity."""

    def on_revoked(self, event: RevocationEvent) -> None: ...


class CredentialValidationError(ValueError):
    """Raised when endpoint credential policy data is invalid."""


class CredentialStateError(RuntimeError):
    """Raised when a credential lifecycle transition is not allowed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TouchBinding:
    """The one Home-approved Room/Profile pair for a Touch endpoint."""

    room_id: str
    profile_id: str


@dataclass(frozen=True, slots=True)
class CredentialScope:
    """The exact Home-owned authority granted to one endpoint."""

    rooms: tuple[str, ...]
    capabilities: tuple[str, ...]
    wake_mappings: tuple[str, ...] = ()
    touch_binding: TouchBinding | None = None

    @classmethod
    def from_values(
        cls,
        *,
        rooms: Iterable[object],
        capabilities: Iterable[object],
        wake_mappings: Iterable[object] = (),
        touch_binding: object = None,
    ) -> CredentialScope:
        normalized_rooms = _bounded_values(rooms, "rooms")
        normalized_capabilities = _bounded_values(capabilities, "capabilities")
        normalized_mappings = _bounded_values(wake_mappings, "wake_mappings")
        normalized_touch_binding = _touch_binding(touch_binding)
        unknown = set(normalized_capabilities) - SUPPORTED_CREDENTIAL_CAPABILITIES
        if unknown:
            raise CredentialValidationError("scope contains an unsupported capability")
        return cls(
            rooms=normalized_rooms,
            capabilities=normalized_capabilities,
            wake_mappings=normalized_mappings,
            touch_binding=normalized_touch_binding,
        )

    def assert_subset_of(self, requested: CredentialScope) -> None:
        """Reject an approval that grants more than the endpoint requested."""
        if not set(self.rooms).issubset(requested.rooms) or not set(
            self.capabilities
        ).issubset(requested.capabilities):
            raise CredentialValidationError("approved scope exceeds requested scope")


def _bounded_values(value: Iterable[object], field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise CredentialValidationError(f"scope.{field} must be an array")
    try:
        values = tuple(value)
    except TypeError as error:
        raise CredentialValidationError(f"scope.{field} must be an array") from error
    if any(type(item) is not str or not 1 <= len(item) <= 128 for item in values):
        raise CredentialValidationError(f"scope.{field} contains an invalid identifier")
    if len(set(values)) != len(values):
        raise CredentialValidationError(f"scope.{field} contains duplicate values")
    return values


@dataclass(frozen=True, slots=True)
class CredentialOffer:
    """An offer with secret material held only by the current caller."""

    offer_id: str
    enrollment_code: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True, slots=True)
class EnrollmentRequest:
    """A redacted endpoint request awaiting trusted approval."""

    request_id: str
    offer_id: str
    endpoint_id: str
    label: str
    endpoint_type: str
    requested_scope: CredentialScope
    requested_profile_mappings: tuple[tuple[str, str], ...]
    secure_storage: str
    confirmation_code: str
    expires_at: float
    status: str
    approved_scope: CredentialScope | None = None
    approved_client_profiles: tuple[str, ...] = ()

    def to_public(self) -> dict[str, object]:
        """Return review metadata without enrollment or endpoint credentials."""
        payload: dict[str, object] = {
            "request_id": self.request_id,
            "offer_id": self.offer_id,
            "endpoint_id": self.endpoint_id,
            "label": self.label,
            "type": self.endpoint_type,
            "requested_scope": _scope_record(self.requested_scope),
            "requested_profile_mappings": [
                {"profile_id": profile_id, "label": label}
                for profile_id, label in self.requested_profile_mappings
            ],
            "secure_storage": self.secure_storage,
            "confirmation_code": self.confirmation_code,
            "expires_at": self.expires_at,
            "status": self.status,
        }
        if self.approved_scope is not None:
            payload["approved_scope"] = _scope_record(self.approved_scope)
        if self.approved_client_profiles:
            payload["approved_client_profiles"] = list(self.approved_client_profiles)
        return payload


@dataclass(frozen=True, slots=True)
class AuthenticatedDevice:
    """The non-secret identity and scope recovered from a device credential."""

    device_id: str
    endpoint_id: str
    generation: int
    scope: CredentialScope


@dataclass(frozen=True, slots=True)
class ClientGrant:
    """One personal client's grant to converse as one Profile.

    Grants live beside, not inside, the credential scope: owner decisions,
    renewal, and rotation must never rewrite a frozen credential record.
    """

    grant_id: str
    device_id: str
    profile_id: str
    status: str
    device_label: str
    device_type: str
    created_at: float
    bootstrap: bool = False


@dataclass(frozen=True, slots=True)
class PairedDevice:
    """Redacted current credential state for the admin pairing surface."""

    device_id: str
    label: str
    device_type: str
    generation: int
    status: str
    issued_at: float
    expires_at: float
    grants: tuple[ClientGrant, ...] = ()


@dataclass(frozen=True, slots=True)
class CredentialMaterial:
    """One-time endpoint credential material returned to a trusted caller."""

    device_id: str
    credential: str = field(repr=False)
    generation: int
    expires_at: float
    scope: CredentialScope
    client_grants: tuple[ClientGrant, ...] = ()


@dataclass(frozen=True, slots=True)
class RevocationEvent:
    """Redacted information sent to active-work adapters after revocation."""

    device_id: str
    generation: int
    reason: str | None = None


class CredentialProtector:
    """Hash credentials and encrypt only the short-lived retry material."""

    def __init__(self, root_secret: bytes) -> None:
        if type(root_secret) is not bytes or len(root_secret) != 32:
            raise CredentialValidationError("root secret must contain 32 bytes")
        self._root_secret = root_secret
        self._replacement_key = hmac.new(
            root_secret,
            b"hermes-home/replacement-key/v1",
            hashlib.sha256,
        ).digest()

    def digest(self, token: str) -> str:
        message = b"hermes-home/credential-digest/v1\0" + token.encode("utf-8")
        return hmac.new(self._root_secret, message, hashlib.sha256).hexdigest()

    def encrypt_replacement(
        self,
        token: str,
        *,
        device_id: str,
        generation: int,
        request_id: str,
    ) -> dict[str, str]:
        nonce = secrets.token_bytes(12)
        associated_data = _rotation_aad(device_id, generation, request_id)
        ciphertext = AESGCM(self._replacement_key).encrypt(
            nonce,
            token.encode("utf-8"),
            associated_data,
        )
        return {
            "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
        }

    def decrypt_replacement(self, record: Mapping[str, object]) -> str:
        try:
            nonce = base64.urlsafe_b64decode(str(record["nonce"]))
            ciphertext = base64.urlsafe_b64decode(str(record["ciphertext"]))
            device_id = str(record["device_id"])
            generation = int(record["generation"])
            request_id = str(record["request_id"])
            plaintext = AESGCM(self._replacement_key).decrypt(
                nonce,
                ciphertext,
                _rotation_aad(device_id, generation, request_id),
            )
            return plaintext.decode("utf-8")
        except (
            InvalidTag,
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
        ) as error:
            raise CredentialStateError("service_unavailable") from error


class CredentialService:
    """Apply pairing and endpoint-credential policy transactionally."""

    def __init__(
        self,
        *,
        store: CredentialStore,
        root_secret: bytes,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
        token_factory: Callable[[], str] | None = None,
        confirmation_factory: Callable[[], str] | None = None,
        revocation_observer: RevocationObserver | None = None,
    ) -> None:
        if type(root_secret) is not bytes or len(root_secret) != 32:
            raise CredentialValidationError("root secret must contain 32 bytes")
        self._store = store
        self._root_secret = root_secret
        self._protector = CredentialProtector(root_secret)
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._confirmation_factory = confirmation_factory or _confirmation_code
        self._revocation_observer = revocation_observer

    def create_offer(self, *, expires_in_seconds: int = 300) -> CredentialOffer:
        """Create a short-lived offer and return its code to the caller once."""
        if type(expires_in_seconds) is not int or not 1 <= expires_in_seconds <= 300:
            raise CredentialValidationError(
                "offer expiry must be between 1 and 300 seconds"
            )
        offer_id = _identifier(self._id_factory(), "offer_id")
        enrollment_code = self._token_factory()
        if type(enrollment_code) is not str or not enrollment_code:
            raise CredentialValidationError("enrollment code must not be blank")
        digest = self._digest(enrollment_code)

        def add_offer(state: dict[str, object]) -> float:
            now = self._now()
            expires_at = now + float(expires_in_seconds)
            record = {
                "id": offer_id,
                "code_digest": digest,
                "expires_at": expires_at,
                "status": "offered",
            }
            _records(state, "offers").append(record)
            return expires_at

        expires_at = self._store.mutate(add_offer)
        return CredentialOffer(
            offer_id=offer_id,
            enrollment_code=enrollment_code,
            expires_at=expires_at,
        )

    def submit_request(
        self,
        *,
        enrollment_code: str,
        endpoint_id: str,
        label: str,
        endpoint_type: str,
        requested_rooms: Iterable[object],
        requested_capabilities: Iterable[object],
        requested_profile_mappings: object = (),
        secure_storage: str,
    ) -> EnrollmentRequest:
        """Turn possession of an offer code into a pending request only."""
        if type(enrollment_code) is not str or not enrollment_code:
            raise CredentialValidationError("enrollment code must not be blank")
        endpoint_id = _identifier(endpoint_id, "endpoint_id")
        label = _identifier(label, "label")
        endpoint_type = _identifier(endpoint_type, "type")
        if secure_storage != SECURE_STORAGE_PLATFORM:
            raise CredentialValidationError("platform secure storage is required")
        requested_scope = CredentialScope.from_values(
            rooms=requested_rooms,
            capabilities=requested_capabilities,
        )
        profile_mappings = _profile_mappings(requested_profile_mappings)
        request_id = _identifier(self._id_factory(), "request_id")
        confirmation_code = self._confirmation_factory()
        if not _is_confirmation_code(confirmation_code):
            raise CredentialValidationError(
                "confirmation code must contain 8 characters"
            )
        digest = self._digest(enrollment_code)

        def add_request(state: dict[str, object]) -> EnrollmentRequest:
            now = self._now()
            offer = _find_record(_records(state, "offers"), digest=digest)
            if offer is None:
                raise CredentialStateError("unauthorized")
            if offer["status"] != "offered":
                raise CredentialStateError("expired_or_consumed")
            if now >= _timestamp(offer["expires_at"]):
                offer["status"] = "expired"
                raise CredentialStateError("expired_or_consumed")

            offer["status"] = "pending"
            record: dict[str, object] = {
                "id": request_id,
                "offer_id": offer["id"],
                "endpoint_id": endpoint_id,
                "label": label,
                "type": endpoint_type,
                "requested_scope": _scope_record(requested_scope),
                "requested_profile_mappings": [
                    {"profile_id": profile_id, "label": mapping_label}
                    for profile_id, mapping_label in profile_mappings
                ],
                "secure_storage": secure_storage,
                "confirmation_code": confirmation_code,
                "expires_at": offer["expires_at"],
                "status": "pending",
                "approved_scope": None,
            }
            _records(state, "requests").append(record)
            return _request_from_record(record)

        return self._store.mutate(add_request)

    def authenticate_device(
        self,
        credential: str,
        *,
        allow_replaced: bool = False,
    ) -> AuthenticatedDevice | None:
        """Return an active binding, never the credential itself."""
        if type(credential) is not str or not credential:
            return None
        digest = self._digest(credential)

        def find_device(
            state: dict[str, object],
            *,
            clean_expired: bool,
        ) -> tuple[AuthenticatedDevice | None, bool]:
            now = self._now()
            cleanup_needed = False
            for replacement in _records(state, "replacements"):
                if replacement.get("status") == "active" and now >= _timestamp(
                    replacement.get("overlap_until")
                ):
                    cleanup_needed = True
                    if clean_expired:
                        _invalidate_replacement(replacement, "expired")
            found: AuthenticatedDevice | None = None
            for record in _records(state, "credentials"):
                status = record.get("status")
                expires_at = _timestamp(record.get("expires_at"))
                overlap_until = _timestamp(record.get("overlap_until", 0.0))
                expired = (status == "active" and now >= expires_at) or (
                    status == "replaced" and now >= overlap_until
                )
                if expired:
                    cleanup_needed = True
                    status = "expired"
                    if clean_expired:
                        record["status"] = status
                equal = hmac.compare_digest(str(record.get("digest", "")), digest)
                accepted = status == "active" or (
                    allow_replaced and status == "replaced" and now < overlap_until
                )
                if equal and accepted:
                    found = _authenticated_device(record)
            return found, cleanup_needed

        found, cleanup_needed = find_device(
            self._store.read_state(), clean_expired=False
        )
        if not cleanup_needed:
            return found

        def clean_state(state: dict[str, object]) -> AuthenticatedDevice | None:
            found, _ = find_device(state, clean_expired=True)
            return found

        return self._store.mutate(clean_state)

    def current_scope(self, device_id: str, generation: int) -> CredentialScope | None:
        """Return the current non-secret scope for one active generation."""
        device_id = _identifier(device_id, "device_id")
        if type(generation) is not int or generation < 1:
            raise CredentialValidationError("generation must be a positive integer")
        now = self._now()
        for record in _records(self._store.read_state(), "credentials"):
            if (
                record.get("device_id") != device_id
                or record.get("generation") != generation
                or record.get("status") != "active"
            ):
                continue
            if now >= _timestamp(record.get("expires_at")):
                return None
            return _scope_from_record(record.get("scope"))
        return None

    def list_requests(self) -> tuple[EnrollmentRequest, ...]:
        """Return redacted enrollment request metadata for the admin surface."""

        def collect(state: dict[str, object]) -> tuple[EnrollmentRequest, ...]:
            now = self._now()
            _expire_enrollment_state(state, now)
            return tuple(
                _request_from_record(record) for record in _records(state, "requests")
            )

        return self._store.mutate(collect)

    def approve_request(
        self,
        request_id: str,
        scope: CredentialScope,
        *,
        configured_rooms: Iterable[object],
        configured_wake_mappings: Iterable[object] = (),
        configured_profiles: Iterable[object] = (),
        client_profiles: Iterable[object] = (),
    ) -> EnrollmentRequest:
        """Approve only a pending request and only within Home's room policy."""
        request_id = _identifier(request_id, "request_id")
        if not isinstance(scope, CredentialScope):
            raise CredentialValidationError("approved scope is invalid")
        available_rooms = set(_bounded_values(configured_rooms, "configured_rooms"))
        available_mappings = set(
            _bounded_values(configured_wake_mappings, "configured_wake_mappings")
        )
        available_profiles = set(
            _bounded_values(configured_profiles, "configured_profiles")
        )
        approved_client_profiles = _bounded_values(client_profiles, "client_profiles")
        if len(approved_client_profiles) > MAX_CLIENT_GRANTS:
            raise CredentialValidationError("too many client profiles")

        def approve(state: dict[str, object]) -> EnrollmentRequest:
            now = self._now()
            record = _find_record_by_id(_records(state, "requests"), request_id)
            if record is None:
                raise CredentialStateError("not_found")
            if record["status"] == "expired":
                raise CredentialStateError("expired_or_consumed")
            if record["status"] != "pending":
                raise CredentialStateError("conflict")
            if now >= _timestamp(record["expires_at"]):
                record["status"] = "expired"
                raise CredentialStateError("expired_or_consumed")
            requested = _scope_from_record(record["requested_scope"])
            scope.assert_subset_of(requested)
            if not set(scope.rooms).issubset(available_rooms):
                raise CredentialValidationError(
                    "approved scope references an unavailable room"
                )
            if not set(scope.wake_mappings).issubset(available_mappings):
                raise CredentialValidationError(
                    "approved scope references an unavailable wake mapping"
                )
            if scope.touch_binding is not None:
                if "touch_claim" not in scope.capabilities:
                    raise CredentialValidationError(
                        "touch binding requires touch_claim capability"
                    )
                if scope.touch_binding.room_id not in scope.rooms:
                    raise CredentialValidationError(
                        "touch binding room must be in the approved rooms"
                    )
                if scope.touch_binding.profile_id not in available_profiles:
                    raise CredentialValidationError(
                        "approved scope references an unavailable touch profile"
                    )
            if "client_claim" in scope.capabilities:
                if record["type"] not in CLIENT_ENDPOINT_TYPES:
                    raise CredentialValidationError(
                        "client_claim is limited to personal client endpoints"
                    )
                if not approved_client_profiles:
                    raise CredentialValidationError(
                        "client_claim requires at least one client profile"
                    )
                if not set(approved_client_profiles).issubset(available_profiles):
                    raise CredentialValidationError(
                        "approved scope references an unavailable client profile"
                    )
            elif approved_client_profiles:
                raise CredentialValidationError(
                    "client profiles require client_claim capability"
                )
            record["approved_scope"] = _scope_record(scope)
            record["approved_client_profiles"] = list(approved_client_profiles)
            record["status"] = "approved"
            return _request_from_record(record)

        return self._store.mutate(approve)

    def reject_request(
        self, request_id: str, *, reason: str | None = None
    ) -> EnrollmentRequest:
        """Reject a pending request without creating a credential."""
        request_id = _identifier(request_id, "request_id")
        if reason is not None and (type(reason) is not str or len(reason) > 256):
            raise CredentialValidationError(
                "reason must contain at most 256 characters"
            )

        def reject(state: dict[str, object]) -> EnrollmentRequest:
            now = self._now()
            record = _find_record_by_id(_records(state, "requests"), request_id)
            if record is None:
                raise CredentialStateError("not_found")
            if record["status"] == "expired":
                raise CredentialStateError("expired_or_consumed")
            if record["status"] != "pending":
                raise CredentialStateError("conflict")
            if now >= _timestamp(record["expires_at"]):
                record["status"] = "expired"
                raise CredentialStateError("expired_or_consumed")
            record["status"] = "rejected"
            if reason:
                record["rejection_reason"] = reason
            return _request_from_record(record)

        return self._store.mutate(reject)

    def consume_request(
        self,
        request_id: str,
        *,
        enrollment_code: str,
        secure_storage: str,
        shared_profiles: Iterable[object] = (),
    ) -> CredentialMaterial:
        """Atomically consume approval and persist a new endpoint credential."""
        request_id = _identifier(request_id, "request_id")
        if type(enrollment_code) is not str or not enrollment_code:
            raise CredentialValidationError("enrollment code must not be blank")
        if secure_storage != SECURE_STORAGE_PLATFORM:
            raise CredentialValidationError("platform secure storage is required")
        digest = self._digest(enrollment_code)
        shared = frozenset(_bounded_values(shared_profiles, "shared_profiles"))

        def consume(
            state: dict[str, object],
        ) -> tuple[CredentialMaterial, RevocationEvent | None]:
            now = self._now()
            request = _find_record_by_id(_records(state, "requests"), request_id)
            if request is None:
                raise CredentialStateError("not_found")
            if request["status"] == "consumed":
                raise CredentialStateError("expired_or_consumed")
            if request["status"] == "expired":
                raise CredentialStateError("expired_or_consumed")
            if now >= _timestamp(request["expires_at"]) and request["status"] in {
                "pending",
                "approved",
            }:
                request["status"] = "expired"
                raise CredentialStateError("expired_or_consumed")
            if request["status"] == "pending":
                raise CredentialStateError("approval_pending")
            if request["status"] == "rejected":
                raise CredentialStateError("rejected")
            if request["status"] != "approved":
                raise CredentialStateError("conflict")
            if request["secure_storage"] != secure_storage:
                raise CredentialValidationError("platform secure storage is required")

            offer = _find_record_by_id(_records(state, "offers"), request["offer_id"])
            if offer is None or not hmac.compare_digest(
                str(offer.get("code_digest", "")), digest
            ):
                raise CredentialStateError("unauthorized")
            if offer["status"] != "pending":
                raise CredentialStateError("expired_or_consumed")

            token = self._token_factory()
            if type(token) is not str or not token:
                raise CredentialValidationError("credential material must not be blank")
            credentials = _records(state, "credentials")
            matching = [
                item
                for item in credentials
                if item.get("endpoint_id") == request["endpoint_id"]
            ]
            revocation_event: RevocationEvent | None = None
            if matching:
                device_id = _identifier(matching[0]["device_id"], "device_id")
                generation = (
                    max(
                        _integer(item.get("generation"), "generation")
                        for item in matching
                    )
                    + 1
                )
                for item in matching:
                    item["status"] = "revoked"
                    item["revoked_at"] = now
                for replacement in _records(state, "replacements"):
                    if replacement.get("device_id") == device_id:
                        _invalidate_replacement(replacement, "revoked")
                revocation_event = RevocationEvent(
                    device_id=device_id,
                    generation=generation - 1,
                    reason="re-enrollment",
                )
            else:
                device_id = _identifier(self._id_factory(), "device_id")
                generation = 1
            approved_scope = _scope_from_record(request["approved_scope"])
            expires_at = now + CREDENTIAL_TTL_SECONDS
            credentials.append(
                {
                    "device_id": device_id,
                    "endpoint_id": request["endpoint_id"],
                    "generation": generation,
                    "status": "active",
                    "issued_at": now,
                    "expires_at": expires_at,
                    "digest": self._digest(token),
                    "scope": _scope_record(approved_scope),
                    "label": request["label"],
                    "type": request["type"],
                }
            )
            grants = self._issue_client_grants(
                state,
                device_id=device_id,
                request=request,
                shared=shared,
                now=now,
            )
            request["status"] = "consumed"
            offer["status"] = "consumed"
            return (
                CredentialMaterial(
                    device_id=device_id,
                    credential=token,
                    generation=generation,
                    expires_at=expires_at,
                    scope=approved_scope,
                    client_grants=grants,
                ),
                revocation_event,
            )

        material, revocation_event = self._store.mutate(consume)
        self._notify_revocation(revocation_event)
        return material

    def renew(
        self,
        *,
        device_id: str,
        credential: str,
        request_id: str,
        expected_generation: int,
    ) -> CredentialMaterial:
        """Renew an endpoint credential with one idempotent replacement."""
        device_id = _identifier(device_id, "device_id")
        request_id = _identifier(request_id, "request_id")
        if type(expected_generation) is not int or expected_generation < 1:
            raise CredentialValidationError("generation must be a positive integer")
        authenticated = self.authenticate_device(credential, allow_replaced=True)
        if (
            authenticated is None
            or authenticated.device_id != device_id
            or authenticated.generation != expected_generation
        ):
            raise CredentialStateError("unauthorized")
        return self._replace_credential(
            device_id=device_id,
            request_id=request_id,
            expected_generation=expected_generation,
            renewal_only=True,
        )

    def rotate(
        self,
        *,
        device_id: str,
        request_id: str,
        expected_generation: int,
    ) -> CredentialMaterial:
        """Rotate an active credential at the trusted admin's request."""
        device_id = _identifier(device_id, "device_id")
        request_id = _identifier(request_id, "request_id")
        if type(expected_generation) is not int or expected_generation < 1:
            raise CredentialValidationError("generation must be a positive integer")
        return self._replace_credential(
            device_id=device_id,
            request_id=request_id,
            expected_generation=expected_generation,
            renewal_only=False,
        )

    def _replace_credential(
        self,
        *,
        device_id: str,
        request_id: str,
        expected_generation: int,
        renewal_only: bool,
    ) -> CredentialMaterial:
        def replace(state: dict[str, object]) -> CredentialMaterial:
            now = self._now()
            credentials = _records(state, "credentials")
            current = _find_credential(credentials, device_id, expected_generation)
            if current is None:
                raise CredentialStateError("not_found")
            existing = _find_replacement(
                _records(state, "replacements"),
                device_id,
                expected_generation,
                request_id,
            )
            if existing is not None:
                if existing.get("status") != "active":
                    raise CredentialStateError("expired_or_consumed")
                if now >= _timestamp(existing["overlap_until"]):
                    _invalidate_replacement(existing, "expired")
                    raise CredentialStateError("expired_or_consumed")
                replacement = self._protector.decrypt_replacement(existing)
                return CredentialMaterial(
                    device_id=device_id,
                    credential=replacement,
                    generation=_integer(existing["new_generation"], "generation"),
                    expires_at=_timestamp(existing["new_expires_at"]),
                    scope=_scope_from_record(existing["scope"]),
                )
            if any(
                item.get("device_id") == device_id
                and item.get("request_id") == request_id
                for item in _records(state, "replacements")
            ):
                raise CredentialStateError("conflict")
            if any(
                item.get("device_id") == device_id
                and item.get("generation") == expected_generation
                for item in _records(state, "replacements")
            ):
                raise CredentialStateError("conflict")
            if current["status"] != "active":
                raise CredentialStateError("expired_or_consumed")
            current_expires_at = _timestamp(current["expires_at"])
            if now >= current_expires_at:
                current["status"] = "expired"
                raise CredentialStateError("expired_or_consumed")
            if renewal_only and current_expires_at - now > RENEWAL_WINDOW_SECONDS:
                raise CredentialStateError("conflict")

            token = self._token_factory()
            if type(token) is not str or not token:
                raise CredentialValidationError("credential material must not be blank")
            scope = _scope_from_record(current["scope"])
            new_generation = expected_generation + 1
            new_expires_at = now + CREDENTIAL_TTL_SECONDS
            overlap_until = now + ROTATION_OVERLAP_SECONDS
            encrypted = self._protector.encrypt_replacement(
                token,
                device_id=device_id,
                generation=expected_generation,
                request_id=request_id,
            )
            current["status"] = "replaced"
            current["overlap_until"] = overlap_until
            credentials.append(
                {
                    "device_id": device_id,
                    "endpoint_id": current["endpoint_id"],
                    "generation": new_generation,
                    "status": "active",
                    "issued_at": now,
                    "expires_at": new_expires_at,
                    "digest": self._digest(token),
                    "scope": _scope_record(scope),
                    **{
                        key: current[key] for key in ("label", "type") if key in current
                    },
                }
            )
            _records(state, "replacements").append(
                {
                    "device_id": device_id,
                    "generation": expected_generation,
                    "new_generation": new_generation,
                    "request_id": request_id,
                    "status": "active",
                    "overlap_until": overlap_until,
                    "new_expires_at": new_expires_at,
                    "scope": _scope_record(scope),
                    **encrypted,
                }
            )
            return CredentialMaterial(
                device_id=device_id,
                credential=token,
                generation=new_generation,
                expires_at=new_expires_at,
                scope=scope,
            )

        return self._store.mutate(replace)

    def revoke(self, device_id: str, *, reason: str | None = None) -> RevocationEvent:
        """Commit revocation, then best-effort notify active-work adapters."""
        device_id = _identifier(device_id, "device_id")
        if reason is not None and (
            type(reason) is not str or not 1 <= len(reason) <= 256
        ):
            raise CredentialValidationError("reason must contain 1-256 characters")

        def mark_revoked(state: dict[str, object]) -> RevocationEvent:
            credentials = [
                record
                for record in _records(state, "credentials")
                if record.get("device_id") == device_id
            ]
            if not credentials:
                raise CredentialStateError("not_found")
            if all(record.get("status") == "revoked" for record in credentials):
                raise CredentialStateError("conflict")
            generation = max(
                _integer(record.get("generation"), "generation")
                for record in credentials
            )
            for record in credentials:
                record["status"] = "revoked"
                record["revoked_at"] = self._now()
                if reason is not None:
                    record["revocation_reason"] = reason
            for replacement in _records(state, "replacements"):
                if replacement.get("device_id") == device_id:
                    _invalidate_replacement(replacement, "revoked")
            _end_device_grants(state, device_id, self._now())
            return RevocationEvent(
                device_id=device_id,
                generation=generation,
                reason=reason,
            )

        event = self._store.mutate(mark_revoked)
        self._notify_revocation(event)
        return event

    def client_grants(self, device_id: str) -> tuple[ClientGrant, ...]:
        """Return a device's current active and pending-owner grants."""
        device_id = _identifier(device_id, "device_id")
        now = self._now()
        return tuple(
            grant
            for grant in _current_grants(self._store.read_state(), now)
            if grant.device_id == device_id
        )

    def active_client_grant(self, device_id: str, grant_id: str) -> ClientGrant | None:
        """Return one active grant held by this device, or ``None``."""
        device_id = _identifier(device_id, "device_id")
        grant_id = _identifier(grant_id, "grant_id")
        for grant in self.client_grants(device_id):
            if grant.grant_id == grant_id and grant.status == "active":
                return grant
        return None

    def pending_owner_grants(self, approver_device_id: str) -> tuple[ClientGrant, ...]:
        """Return grants awaiting a decision from a holder of the same Profile."""
        approver_device_id = _identifier(approver_device_id, "device_id")
        now = self._now()
        grants = _current_grants(self._store.read_state(), now)
        owned = {
            grant.profile_id
            for grant in grants
            if grant.device_id == approver_device_id and grant.status == "active"
        }
        return tuple(
            grant
            for grant in grants
            if grant.status == "pending_owner"
            and grant.profile_id in owned
            and grant.device_id != approver_device_id
        )

    def decide_owner_grant(
        self,
        approver_device_id: str,
        grant_id: str,
        *,
        approve: bool,
    ) -> ClientGrant:
        """Let a device holding an owned Profile approve or reject a pending grant."""
        approver_device_id = _identifier(approver_device_id, "device_id")
        grant_id = _identifier(grant_id, "grant_id")
        if type(approve) is not bool:
            raise CredentialValidationError("approve must be a boolean")

        def decide(state: dict[str, object]) -> ClientGrant:
            now = self._now()
            records = _client_grant_records(state)
            _expire_pending_grants(records, now)
            record = _find_record_by_id(records, grant_id)
            if record is None or record.get("status") != "pending_owner":
                raise CredentialStateError("not_found")
            if record.get("device_id") == approver_device_id:
                raise CredentialStateError("unauthorized")
            holds_profile = any(
                item.get("device_id") == approver_device_id
                and item.get("profile_id") == record.get("profile_id")
                and item.get("status") == "active"
                for item in records
            )
            if not holds_profile:
                raise CredentialStateError("unauthorized")
            record["status"] = "active" if approve else "rejected"
            record["decided_at"] = now
            record["decided_by"] = approver_device_id
            return _grant_from_record(record)

        return self._store.mutate(decide)

    def profile_holders(self, device_id: str) -> tuple[ClientGrant, ...]:
        """Return every current grant for the Profiles this device holds."""
        device_id = _identifier(device_id, "device_id")
        now = self._now()
        grants = _current_grants(self._store.read_state(), now)
        held = {
            grant.profile_id
            for grant in grants
            if grant.device_id == device_id and grant.status == "active"
        }
        return tuple(grant for grant in grants if grant.profile_id in held)

    def revoke_client_grant(
        self,
        grant_id: str,
        *,
        requester_device_id: str | None = None,
    ) -> ClientGrant:
        """End one grant; a device may revoke only grants for its own Profiles."""
        grant_id = _identifier(grant_id, "grant_id")
        if requester_device_id is not None:
            requester_device_id = _identifier(requester_device_id, "device_id")

        def revoke(state: dict[str, object]) -> ClientGrant:
            now = self._now()
            records = _client_grant_records(state)
            _expire_pending_grants(records, now)
            record = _find_record_by_id(records, grant_id)
            if record is None or record.get("status") not in {
                "active",
                "pending_owner",
            }:
                raise CredentialStateError("not_found")
            if requester_device_id is not None and not any(
                item.get("device_id") == requester_device_id
                and item.get("profile_id") == record.get("profile_id")
                and item.get("status") == "active"
                for item in records
            ):
                raise CredentialStateError("unauthorized")
            record["status"] = "revoked"
            record["decided_at"] = now
            return _grant_from_record(record)

        return self._store.mutate(revoke)

    def paired_devices(self) -> tuple[PairedDevice, ...]:
        """Return each device's newest credential and current grants."""
        state = self._store.read_state()
        now = self._now()
        grants = _current_grants(state, now)
        newest: dict[str, dict[str, object]] = {}
        for record in _records(state, "credentials"):
            device_id = record.get("device_id")
            if not isinstance(device_id, str):
                continue
            current = newest.get(device_id)
            if current is None or _integer(
                record.get("generation"), "generation"
            ) > _integer(current.get("generation"), "generation"):
                newest[device_id] = record
        devices: list[PairedDevice] = []
        for device_id, record in newest.items():
            status = str(record.get("status", "unknown"))
            expires_at = _timestamp(record.get("expires_at"))
            if status == "active" and now >= expires_at:
                status = "expired"
            devices.append(
                PairedDevice(
                    device_id=device_id,
                    label=str(record.get("label") or record.get("endpoint_id")),
                    device_type=str(record.get("type") or "unknown"),
                    generation=_integer(record.get("generation"), "generation"),
                    status=status,
                    issued_at=_timestamp(record.get("issued_at")),
                    expires_at=expires_at,
                    grants=tuple(
                        grant for grant in grants if grant.device_id == device_id
                    ),
                )
            )
        return tuple(sorted(devices, key=lambda device: device.label))

    def _issue_client_grants(
        self,
        state: dict[str, object],
        *,
        device_id: str,
        request: Mapping[str, object],
        shared: frozenset[str],
        now: float,
    ) -> tuple[ClientGrant, ...]:
        records = _client_grant_records(state)
        _expire_pending_grants(records, now)
        _end_device_grants(state, device_id, now)
        profiles = _bounded_values(
            request.get("approved_client_profiles") or (), "client_profiles"
        )
        issued: list[ClientGrant] = []
        for profile_id in profiles:
            held_elsewhere = any(
                item.get("profile_id") == profile_id
                and item.get("status") == "active"
                and item.get("device_id") != device_id
                for item in records
            )
            if profile_id in shared:
                status, bootstrap = "active", False
            elif held_elsewhere:
                status, bootstrap = "pending_owner", False
            else:
                # First device for an owned Profile: the admin's approval stands
                # and is recorded, so every later holder list shows it.
                status, bootstrap = "active", True
            record: dict[str, object] = {
                "id": _identifier(f"grant-{self._id_factory()}", "grant_id"),
                "device_id": device_id,
                "profile_id": profile_id,
                "status": status,
                "device_label": request["label"],
                "device_type": request["type"],
                "created_at": now,
                "bootstrap": bootstrap,
            }
            if status == "pending_owner":
                record["pending_expires_at"] = now + PENDING_OWNER_GRANT_TTL_SECONDS
            records.append(record)
            issued.append(_grant_from_record(record))
        return tuple(issued)

    def _notify_revocation(self, event: RevocationEvent | None) -> None:
        if event is None or self._revocation_observer is None:
            return
        try:  # The durable revocation commit must not depend on observers.
            self._revocation_observer.on_revoked(event)
        except Exception as error:  # noqa: BLE001
            LOGGER.debug(
                "revocation observer failed: %s",
                type(error).__name__,
            )

    def _digest(self, token: str) -> str:
        return self._protector.digest(token)

    def _now(self) -> float:
        return _timestamp(self._clock())


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise CredentialValidationError(f"{field} must be a string of 1-128 characters")
    return value


def _profile_mappings(value: object) -> tuple[tuple[str, str], ...]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise CredentialValidationError("requested_profile_mappings must be an array")
    records = tuple(value)
    if len(records) > 16:
        raise CredentialValidationError("too many requested profile mappings")
    normalized: list[tuple[str, str]] = []
    for record in records:
        if not isinstance(record, Mapping) or set(record) != {"profile_id", "label"}:
            raise CredentialValidationError("invalid requested profile mapping")
        normalized.append(
            (
                _identifier(record["profile_id"], "profile_id"),
                _identifier(record["label"], "profile label"),
            )
        )
    return tuple(normalized)


def _records(state: dict[str, object], name: str) -> list[dict[str, object]]:
    records = state.get(name)
    if not isinstance(records, list):
        raise CredentialValidationError(f"credential state.{name} must be an array")
    return records


def _find_record(
    records: Iterable[dict[str, object]],
    *,
    digest: str,
) -> dict[str, object] | None:
    for record in records:
        if hmac.compare_digest(str(record.get("code_digest", "")), digest):
            return record
    return None


def _find_record_by_id(
    records: Iterable[dict[str, object]],
    identifier: str,
) -> dict[str, object] | None:
    for record in records:
        if record.get("id") == identifier:
            return record
    return None


def _expire_enrollment_state(state: dict[str, object], now: float) -> None:
    for offer in _records(state, "offers"):
        if offer["status"] in {"offered", "pending"} and now >= _timestamp(
            offer["expires_at"]
        ):
            offer["status"] = "expired"
    for request in _records(state, "requests"):
        if request["status"] in {"pending", "approved"} and now >= _timestamp(
            request["expires_at"]
        ):
            request["status"] = "expired"


def _client_grant_records(state: dict[str, object]) -> list[dict[str, object]]:
    records = state.setdefault("client_grants", [])
    if not isinstance(records, list):
        raise CredentialValidationError(
            "credential state.client_grants must be an array"
        )
    return records


def _expire_pending_grants(records: Iterable[dict[str, object]], now: float) -> None:
    for record in records:
        if record.get("status") == "pending_owner" and now >= _timestamp(
            record.get("pending_expires_at")
        ):
            record["status"] = "expired"


def _end_device_grants(state: dict[str, object], device_id: str, now: float) -> None:
    for record in _client_grant_records(state):
        if record.get("device_id") == device_id and record.get("status") in {
            "active",
            "pending_owner",
        }:
            record["status"] = "revoked"
            record["decided_at"] = now


def _current_grants(state: Mapping[str, object], now: float) -> tuple[ClientGrant, ...]:
    records = state.get("client_grants") or []
    if not isinstance(records, list):
        raise CredentialValidationError(
            "credential state.client_grants must be an array"
        )
    current: list[ClientGrant] = []
    for record in records:
        status = record.get("status")
        if status == "active" or (
            status == "pending_owner"
            and now < _timestamp(record.get("pending_expires_at"))
        ):
            current.append(_grant_from_record(record))
    return tuple(current)


def _grant_from_record(record: Mapping[str, object]) -> ClientGrant:
    return ClientGrant(
        grant_id=_identifier(record["id"], "grant_id"),
        device_id=_identifier(record["device_id"], "device_id"),
        profile_id=_identifier(record["profile_id"], "profile_id"),
        status=_identifier(record["status"], "status"),
        device_label=_identifier(record["device_label"], "label"),
        device_type=_identifier(record["device_type"], "type"),
        created_at=_timestamp(record["created_at"]),
        bootstrap=record.get("bootstrap") is True,
    )


def _invalidate_replacement(record: dict[str, object], status: str) -> None:
    record["status"] = status
    record["nonce"] = ""
    record["ciphertext"] = ""


def _find_credential(
    records: Iterable[dict[str, object]],
    device_id: str,
    generation: int,
) -> dict[str, object] | None:
    for record in records:
        if (
            record.get("device_id") == device_id
            and record.get("generation") == generation
        ):
            return record
    return None


def _find_replacement(
    records: Iterable[dict[str, object]],
    device_id: str,
    generation: int,
    request_id: str,
) -> dict[str, object] | None:
    for record in records:
        if (
            record.get("device_id") == device_id
            and record.get("generation") == generation
            and record.get("request_id") == request_id
        ):
            return record
    return None


def _touch_binding(value: object) -> TouchBinding | None:
    if value is None:
        return None
    if isinstance(value, TouchBinding):
        room_id, profile_id = _bounded_values(
            (value.room_id, value.profile_id),
            "touch_binding",
        )
        return TouchBinding(room_id=room_id, profile_id=profile_id)
    if not isinstance(value, Mapping) or set(value) != {"room_id", "profile_id"}:
        raise CredentialValidationError("scope.touch_binding is invalid")
    room_id, profile_id = _bounded_values(
        (value["room_id"], value["profile_id"]),
        "touch_binding",
    )
    return TouchBinding(room_id=room_id, profile_id=profile_id)


def _scope_record(scope: CredentialScope) -> dict[str, object]:
    record: dict[str, object] = {
        "rooms": list(scope.rooms),
        "capabilities": list(scope.capabilities),
        "wake_mappings": list(scope.wake_mappings),
    }
    if scope.touch_binding is not None:
        record["touch_binding"] = {
            "room_id": scope.touch_binding.room_id,
            "profile_id": scope.touch_binding.profile_id,
        }
    return record


def _scope_from_record(value: object) -> CredentialScope:
    if not isinstance(value, Mapping):
        raise CredentialValidationError("credential scope is invalid")
    return CredentialScope.from_values(
        rooms=value.get("rooms", ()),
        capabilities=value.get("capabilities", ()),
        wake_mappings=value.get("wake_mappings", ()),
        touch_binding=value.get("touch_binding"),
    )


def _request_from_record(record: Mapping[str, object]) -> EnrollmentRequest:
    mappings = _profile_mappings(record["requested_profile_mappings"])
    approved = record.get("approved_scope")
    return EnrollmentRequest(
        request_id=_identifier(record["id"], "request_id"),
        offer_id=_identifier(record["offer_id"], "offer_id"),
        endpoint_id=_identifier(record["endpoint_id"], "endpoint_id"),
        label=_identifier(record["label"], "label"),
        endpoint_type=_identifier(record["type"], "type"),
        requested_scope=_scope_from_record(record["requested_scope"]),
        requested_profile_mappings=mappings,
        secure_storage=_identifier(record["secure_storage"], "secure_storage"),
        confirmation_code=_identifier(record["confirmation_code"], "confirmation_code"),
        expires_at=_timestamp(record["expires_at"]),
        status=_identifier(record["status"], "status"),
        approved_scope=None if approved is None else _scope_from_record(approved),
        approved_client_profiles=_bounded_values(
            record.get("approved_client_profiles") or (), "client_profiles"
        ),
    )


def _authenticated_device(record: Mapping[str, object]) -> AuthenticatedDevice:
    return AuthenticatedDevice(
        device_id=_identifier(record["device_id"], "device_id"),
        endpoint_id=_identifier(record["endpoint_id"], "endpoint_id"),
        generation=_integer(record["generation"], "generation"),
        scope=_scope_from_record(record["scope"]),
    )


def _timestamp(value: object) -> float:
    if type(value) not in (int, float):
        raise CredentialValidationError("credential timestamp is invalid")
    try:
        timestamp = float(value)
    except (OverflowError, ValueError) as error:
        raise CredentialValidationError("credential timestamp is invalid") from error
    if not isfinite(timestamp):
        raise CredentialValidationError("credential timestamp is invalid")
    return timestamp


def _integer(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise CredentialValidationError(f"{field} must be a positive integer")
    return value


def _confirmation_code() -> str:
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _is_confirmation_code(value: object) -> bool:
    alphabet = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    return (
        type(value) is str
        and len(value) == 8
        and all(character in alphabet for character in value)
    )


def _rotation_aad(device_id: str, generation: int, request_id: str) -> bytes:
    return f"{device_id}\0{generation}\0{request_id}".encode()
