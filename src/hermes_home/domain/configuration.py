"""Validation for the canonical Home configuration snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from unicodedata import normalize

CONFIGURATION_KEYS = frozenset({"rooms", "profiles", "wake_mappings", "devices"})
SNAPSHOT_KEYS = CONFIGURATION_KEYS | {"revision"}


class ConfigurationValidationError(ValueError):
    """Raised when a configuration does not satisfy the Home contract."""


class ConfigurationMigrationRequired(RuntimeError):
    """Raised when stored configuration needs an explicit new-shape publish."""

    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__("configuration requires an explicit migration publish")


def validate_candidate(candidate: Mapping[str, object]) -> dict[str, object]:
    """Validate and copy a revision-free configuration candidate."""
    _require_mapping(candidate, "configuration")
    _require_keys(candidate, CONFIGURATION_KEYS, "configuration")

    rooms = _validate_named_entities(candidate["rooms"], "rooms")
    profiles = _validate_profiles(candidate["profiles"])
    wake_mappings = _validate_wake_mappings(candidate["wake_mappings"], profiles)
    devices = _validate_devices(candidate["devices"], rooms)

    return {
        "rooms": rooms,
        "profiles": profiles,
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
            "profiles": snapshot["profiles"],
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


def _validate_profiles(value: object) -> list[dict[str, object]]:
    if type(value) is not list:
        raise ConfigurationValidationError("profiles must be an array")

    profiles: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for index, profile in enumerate(value):
        path = f"profiles[{index}]"
        _require_mapping(profile, path)
        _require_keys(profile, {"id", "name", "available"}, path)
        profile_id = _require_id(profile["id"], f"{path}.id")
        name = _require_name(profile["name"], f"{path}.name")
        available = profile["available"]
        if type(available) is not bool:
            raise ConfigurationValidationError(f"{path}.available must be a boolean")
        if profile_id in seen_ids:
            raise ConfigurationValidationError(
                f"duplicate id {profile_id!r} in profiles"
            )
        seen_ids.add(profile_id)
        profiles.append({"id": profile_id, "name": name, "available": available})
    return profiles


def _validate_wake_mappings(
    value: object,
    profiles: list[dict[str, object]],
) -> list[dict[str, object]]:
    if type(value) is not list:
        raise ConfigurationValidationError("wake_mappings must be an array")

    profile_ids = {profile["id"] for profile in profiles}
    seen_ids: set[str] = set()
    phrases: dict[str, str] = {}
    mappings: list[dict[str, object]] = []
    for index, mapping in enumerate(value):
        path = f"wake_mappings[{index}]"
        _require_mapping(mapping, path)
        _require_keys(mapping, {"id", "phrase", "profile_id", "active"}, path)
        mapping_id = _require_id(mapping["id"], f"{path}.id")
        phrase = _require_name(mapping["phrase"], f"{path}.phrase")
        profile_id = _require_id(mapping["profile_id"], f"{path}.profile_id")
        active = mapping["active"]
        if type(active) is not bool:
            raise ConfigurationValidationError(f"{path}.active must be a boolean")
        if mapping_id in seen_ids:
            raise ConfigurationValidationError(
                f"duplicate id {mapping_id!r} in wake_mappings"
            )
        if profile_id not in profile_ids:
            raise ConfigurationValidationError(
                f"{path}.profile_id references unknown profile {profile_id!r}"
            )
        normalized_phrase = _normalize_phrase(phrase)
        if not normalized_phrase:
            raise ConfigurationValidationError(
                f"{path}.phrase must contain a non-whitespace wake phrase"
            )
        if active:
            previous_profile = phrases.get(normalized_phrase)
            if previous_profile is not None:
                raise ConfigurationValidationError(
                    f"{path}.phrase duplicates an active wake phrase"
                )
            phrases[normalized_phrase] = profile_id

        seen_ids.add(mapping_id)
        mappings.append(
            {
                "id": mapping_id,
                "phrase": phrase,
                "profile_id": profile_id,
                "active": active,
            }
        )
    return mappings


def _normalize_phrase(value: str) -> str:
    return " ".join(normalize("NFKC", value).split()).casefold()


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

    required_keys = {"id", "name", "room_id", "priority", "capabilities"}
    for index, device in enumerate(value):
        path = f"devices[{index}]"
        _require_mapping(device, path)
        _require_keys(device, required_keys, path)
        device_id = _require_id(device["id"], f"{path}.id")
        name = _require_name(device["name"], f"{path}.name")
        room_id = _require_id(device["room_id"], f"{path}.room_id")
        priority = device["priority"]
        if type(priority) is not int or priority < 1:
            raise ConfigurationValidationError(
                f"{path}.priority must be a positive integer"
            )
        capabilities = device["capabilities"]
        _require_mapping(capabilities, f"{path}.capabilities")
        capability_keys = set(capabilities)
        if (
            any(type(key) is not str for key in capability_keys)
            or "wake_claim" not in capability_keys
            or capability_keys - {"wake_claim", "interactive_choice"}
        ):
            raise ConfigurationValidationError(
                f"{path}.capabilities has invalid fields"
            )
        wake_claim = capabilities["wake_claim"]
        if type(wake_claim) is not bool:
            raise ConfigurationValidationError(
                f"{path}.capabilities.wake_claim must be a boolean"
            )
        interactive_choice = capabilities.get("interactive_choice", False)
        if type(interactive_choice) is not bool:
            raise ConfigurationValidationError(
                f"{path}.capabilities.interactive_choice must be a boolean"
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
        normalized_capabilities = {"wake_claim": wake_claim}
        if interactive_choice:
            normalized_capabilities["interactive_choice"] = True
        devices.append(
            {
                "id": device_id,
                "name": name,
                "room_id": room_id,
                "priority": priority,
                "capabilities": normalized_capabilities,
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
