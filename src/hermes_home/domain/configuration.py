"""Validation for the canonical Home configuration snapshot."""

from __future__ import annotations

from collections.abc import Mapping

CONFIGURATION_KEYS = frozenset({"rooms", "wake_mappings", "devices"})
SNAPSHOT_KEYS = CONFIGURATION_KEYS | {"revision"}


class ConfigurationValidationError(ValueError):
    """Raised when a configuration does not satisfy the Home contract."""


def validate_candidate(candidate: Mapping[str, object]) -> dict[str, object]:
    """Validate and copy a revision-free configuration candidate."""
    _require_mapping(candidate, "configuration")
    _require_keys(candidate, CONFIGURATION_KEYS, "configuration")

    rooms = _validate_named_entities(candidate["rooms"], "rooms")
    wake_mappings = _validate_named_entities(
        candidate["wake_mappings"], "wake_mappings"
    )
    devices = _validate_devices(candidate["devices"], rooms)

    return {
        "rooms": rooms,
        "wake_mappings": wake_mappings,
        "devices": devices,
    }


def validate_snapshot(snapshot: Mapping[str, object]) -> dict[str, object]:
    """Validate and copy a persisted, revisioned configuration snapshot."""
    _require_mapping(snapshot, "snapshot")
    _require_keys(snapshot, SNAPSHOT_KEYS, "snapshot")
    revision = snapshot["revision"]
    if type(revision) is not int or revision < 0:
        raise ConfigurationValidationError(
            "snapshot.revision must be a non-negative integer"
        )

    candidate = validate_candidate(
        {
            "rooms": snapshot["rooms"],
            "wake_mappings": snapshot["wake_mappings"],
            "devices": snapshot["devices"],
        }
    )
    return {"revision": revision, **candidate}


def _validate_named_entities(value: object, field: str) -> list[dict[str, str]]:
    if type(value) is not list:
        raise ConfigurationValidationError(f"{field} must be an array")

    entities: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for index, entity in enumerate(value):
        path = f"{field}[{index}]"
        _require_mapping(entity, path)
        _require_keys(entity, {"id", "name"}, path)
        entity_id = _require_id(entity["id"], f"{path}.id")
        name = _require_name(entity["name"], f"{path}.name")
        if entity_id in seen_ids:
            raise ConfigurationValidationError(f"duplicate id {entity_id!r} in {field}")
        seen_ids.add(entity_id)
        entities.append({"id": entity_id, "name": name})
    return entities


def _validate_devices(
    value: object,
    rooms: list[dict[str, str]],
) -> list[dict[str, object]]:
    if type(value) is not list:
        raise ConfigurationValidationError("devices must be an array")

    room_ids = {room["id"] for room in rooms}
    device_ids: set[str] = set()
    priorities_by_room: dict[str, set[int]] = {}
    devices: list[dict[str, object]] = []

    required_keys = {
        "id",
        "name",
        "room_id",
        "profile_id",
        "priority",
        "capabilities",
    }
    for index, device in enumerate(value):
        path = f"devices[{index}]"
        _require_mapping(device, path)
        _require_keys(device, required_keys, path)
        device_id = _require_id(device["id"], f"{path}.id")
        name = _require_name(device["name"], f"{path}.name")
        room_id = _require_id(device["room_id"], f"{path}.room_id")
        profile_id = _require_id(device["profile_id"], f"{path}.profile_id")
        priority = device["priority"]
        if type(priority) is not int or priority < 1:
            raise ConfigurationValidationError(
                f"{path}.priority must be a positive integer"
            )
        capabilities = device["capabilities"]
        _require_mapping(capabilities, f"{path}.capabilities")
        _require_keys(capabilities, {"wake_claim"}, f"{path}.capabilities")
        wake_claim = capabilities["wake_claim"]
        if type(wake_claim) is not bool:
            raise ConfigurationValidationError(
                f"{path}.capabilities.wake_claim must be a boolean"
            )
        if device_id in device_ids:
            raise ConfigurationValidationError(f"duplicate id {device_id!r} in devices")
        if room_id not in room_ids:
            raise ConfigurationValidationError(
                f"{path}.room_id references unknown room {room_id!r}"
            )
        room_priorities = priorities_by_room.setdefault(room_id, set())
        if priority in room_priorities:
            raise ConfigurationValidationError(
                f"duplicate priority {priority} in room {room_id!r}"
            )

        device_ids.add(device_id)
        room_priorities.add(priority)
        devices.append(
            {
                "id": device_id,
                "name": name,
                "room_id": room_id,
                "profile_id": profile_id,
                "priority": priority,
                "capabilities": {"wake_claim": wake_claim},
            }
        )
    return devices


def _require_mapping(value: object, path: str) -> None:
    if not isinstance(value, Mapping):
        raise ConfigurationValidationError(f"{path} must be an object")


def _require_keys(
    value: Mapping[str, object],
    expected: set[str] | frozenset[str],
    path: str,
) -> None:
    actual = set(value)
    if any(type(key) is not str for key in actual):
        raise ConfigurationValidationError(f"{path} has invalid fields")
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unknown {', '.join(extra)}")
        raise ConfigurationValidationError(
            f"{path} has invalid fields ({'; '.join(details)})"
        )


def _require_id(value: object, path: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise ConfigurationValidationError(
            f"{path} must be a string of 1-128 characters"
        )
    return value


def _require_name(value: object, path: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise ConfigurationValidationError(
            f"{path} must be a string of 1-128 characters"
        )
    return value
