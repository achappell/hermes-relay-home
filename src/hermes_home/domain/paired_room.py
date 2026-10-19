"""Explicit endpoint-bound paired-room wake authorization, without acoustics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from hermes_home.domain.credentials import AuthenticatedDevice, CredentialStateError


@dataclass(frozen=True, slots=True)
class PairedRoomPolicy:
    """The current scoped paired approval exposed to its own Device."""

    room_id: str
    wake_mapping_ids: tuple[str, ...]

    def to_public(self) -> dict[str, object]:
        """Project only the exact authenticated discovery contract."""
        return {
            "mode": "paired_room",
            "room_id": self.room_id,
            "wake_mapping_ids": list(self.wake_mapping_ids),
        }


def authorize_paired_room(
    snapshot: Mapping[str, object],
    context: AuthenticatedDevice,
    active_wake_devices: Iterable[AuthenticatedDevice],
    *,
    wake_mapping_id: str | None = None,
    configuration_revision: int | None = None,
) -> PairedRoomPolicy:
    """Intersect explicit approval with current scope and household eligibility.

    ``snapshot`` is a validated Home configuration. Active contexts must come
    from CredentialService's read-only current-generation wake view. Discovery
    exposes only approved mappings in this credential's scope; admission also
    requires the requested mapping to belong to that intersection.
    """
    if configuration_revision is not None and (
        type(configuration_revision) is not int
        or configuration_revision != snapshot["revision"]
    ):
        raise CredentialStateError("stale_configuration")

    active_contexts = tuple(active_wake_devices)
    if context not in active_contexts or "wake_claim" not in context.scope.capabilities:
        raise CredentialStateError("forbidden")

    devices = {device["id"]: device for device in snapshot["devices"]}
    device = devices.get(context.device_id)
    if device is None or device["capabilities"]["wake_claim"] is not True:
        raise CredentialStateError("forbidden")
    room_id = device["room_id"]
    if room_id not in context.scope.rooms or not any(
        room["id"] == room_id for room in snapshot["rooms"]
    ):
        raise CredentialStateError("forbidden")
    approval = device.get("wake_admission")
    if (
        not isinstance(approval, Mapping)
        or set(approval) != {"mode", "endpoint_id", "wake_mapping_ids"}
        or approval["mode"] != "paired_room"
        or approval["endpoint_id"] != context.endpoint_id
        or sum("wake_admission" in item for item in devices.values()) != 1
    ):
        raise CredentialStateError("forbidden")

    mapping_ids = tuple(
        mapping_id
        for mapping_id in approval["wake_mapping_ids"]
        if mapping_id in context.scope.wake_mappings
    )
    if not mapping_ids or (
        wake_mapping_id is not None and wake_mapping_id not in mapping_ids
    ):
        raise CredentialStateError("forbidden")
    mappings = {mapping["id"]: mapping for mapping in snapshot["wake_mappings"]}
    profiles = {profile["id"]: profile for profile in snapshot["profiles"]}
    for mapping_id in mapping_ids:
        mapping = mappings.get(mapping_id)
        profile = profiles.get(mapping["profile_id"]) if mapping is not None else None
        if (
            mapping is None
            or mapping["active"] is not True
            or profile is None
            or profile["available"] is not True
        ):
            raise CredentialStateError("forbidden")
        if wake_mapping_id is not None and mapping_id != wake_mapping_id:
            continue
        for candidate in active_contexts:
            if candidate.device_id == context.device_id:
                continue
            competitor = devices.get(candidate.device_id)
            if (
                competitor is not None
                and competitor["capabilities"]["wake_claim"] is True
                and "wake_claim" in candidate.scope.capabilities
                and competitor["room_id"] in candidate.scope.rooms
                and mapping_id in candidate.scope.wake_mappings
            ):
                raise CredentialStateError("forbidden")
    return PairedRoomPolicy(room_id=room_id, wake_mapping_ids=mapping_ids)
