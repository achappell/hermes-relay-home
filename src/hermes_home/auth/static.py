"""Small in-process credential adapter for local deployments and tests."""

from __future__ import annotations

from collections.abc import Mapping


class StaticCredentialAuthenticator:
    """Authenticate configured admin and device credentials without persistence."""

    def __init__(
        self,
        *,
        admin_token: str,
        device_credentials: Mapping[str, str],
    ) -> None:
        self._admin_token = admin_token
        self._device_credentials = dict(device_credentials)

    def authenticate_admin(self, headers: Mapping[str, str]) -> bool:
        return self._authorization(headers) == f"Bearer {self._admin_token}"

    def authenticate_device(self, headers: Mapping[str, str]) -> str | None:
        authorization = self._authorization(headers)
        if not authorization.startswith("Device "):
            return None
        credential = authorization.removeprefix("Device ")
        return self._device_credentials.get(credential)

    @staticmethod
    def _authorization(headers: Mapping[str, str]) -> str:
        for name, value in headers.items():
            if name.lower() == "authorization":
                return value
        return ""
