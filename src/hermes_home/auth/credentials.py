"""Durable Home endpoint authentication."""

from __future__ import annotations

import hmac
from collections.abc import Mapping

from hermes_home.domain.credentials import AuthenticatedDevice, CredentialService


class PersistentCredentialAuthenticator:
    """Authenticate the Home admin and durable endpoint credentials."""

    def __init__(self, *, admin_token: str, service: CredentialService) -> None:
        self._admin_token = admin_token
        self._service = service

    def authenticate_admin(self, headers: Mapping[str, str]) -> bool:
        return hmac.compare_digest(
            self._authorization(headers), f"Bearer {self._admin_token}"
        )

    def authenticate_device(self, headers: Mapping[str, str]) -> str | None:
        authenticated = self.authenticate_device_context(headers)
        return None if authenticated is None else authenticated.device_id

    def authenticate_device_context(
        self,
        headers: Mapping[str, str],
        *,
        allow_replaced: bool = False,
    ) -> AuthenticatedDevice | None:
        authorization = self._authorization(headers)
        if not authorization.startswith("Device "):
            return None
        credential = authorization.removeprefix("Device ")
        if not credential:
            return None
        return self._service.authenticate_device(
            credential,
            allow_replaced=allow_replaced,
        )

    @staticmethod
    def _authorization(headers: Mapping[str, str]) -> str:
        for name, value in headers.items():
            if name.lower() == "authorization":
                return value if isinstance(value, str) else ""
        return ""
