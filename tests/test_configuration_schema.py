"""Semantic tests for the published v1 configuration JSON Schema."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).parents[1] / "docs/contracts/v1/configuration.schema.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _configuration() -> dict[str, object]:
    return {
        "revision": 1,
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "profiles": [],
        "wake_mappings": [],
        "devices": [
            {
                "id": "puck-kitchen",
                "name": "Kitchen Puck",
                "room_id": "kitchen",
                "priority": 1,
                "capabilities": {"wake_claim": True},
            }
        ],
    }


def test_configuration_schema_is_draft_2020_12_valid() -> None:
    _validator()


@pytest.mark.parametrize("interactive_choice", [None, False, True])
def test_configuration_schema_accepts_omitted_and_boolean_choice_capability(
    interactive_choice: bool | None,
) -> None:
    candidate = _configuration()
    capabilities = candidate["devices"][0]["capabilities"]
    if interactive_choice is not None:
        capabilities["interactive_choice"] = interactive_choice

    assert _validator().is_valid(candidate)


@pytest.mark.parametrize(
    ("sensitive_entry", "consequence_confirm"),
    [(None, None), (False, True), (True, False), (True, True)],
)
def test_configuration_schema_accepts_protected_capability_flags(
    sensitive_entry: bool | None,
    consequence_confirm: bool | None,
) -> None:
    candidate = _configuration()
    capabilities = candidate["devices"][0]["capabilities"]
    if sensitive_entry is not None:
        capabilities["sensitive_entry"] = sensitive_entry
    if consequence_confirm is not None:
        capabilities["consequence_confirm"] = consequence_confirm

    assert _validator().is_valid(candidate)


@pytest.mark.parametrize("capability", ["sensitive_entry", "consequence_confirm"])
def test_configuration_schema_rejects_non_boolean_protected_capability(
    capability: str,
) -> None:
    candidate = _configuration()
    candidate["devices"][0]["capabilities"][capability] = "yes"

    assert not _validator().is_valid(candidate)


@pytest.mark.parametrize("invalid_value", ["yes", 1, None])
def test_configuration_schema_rejects_non_boolean_choice_capability(
    invalid_value: object,
) -> None:
    candidate = _configuration()
    candidate["devices"][0]["capabilities"]["interactive_choice"] = invalid_value

    assert not _validator().is_valid(candidate)


@pytest.mark.parametrize("location", ["root", "capabilities"])
def test_configuration_schema_rejects_unknown_fields(location: str) -> None:
    candidate = _configuration()
    if location == "root":
        candidate["unexpected"] = True
    else:
        candidate["devices"][0]["capabilities"]["unexpected"] = True

    assert not _validator().is_valid(candidate)


def test_schema_capability_omission_remains_a_valid_legacy_snapshot() -> None:
    candidate = copy.deepcopy(_configuration())

    assert "interactive_choice" not in candidate["devices"][0]["capabilities"]
    assert _validator().is_valid(candidate)


def _with_client_devices(*entries: object) -> dict[str, object]:
    candidate = _configuration()
    candidate["client_devices"] = list(entries)
    return candidate


def _client_entry(device_id: str = "browser-1", value: object = True) -> dict:
    return {"id": device_id, "capabilities": {"interactive_choice": value}}


def test_schema_client_devices_are_optional() -> None:
    assert "client_devices" not in _configuration()
    assert _validator().is_valid(_configuration())
    assert _validator().is_valid(_with_client_devices())
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert "client_devices" not in schema["required"]


@pytest.mark.parametrize("value", [True, False])
def test_schema_accepts_boolean_client_device_choice_capability(value: bool) -> None:
    assert _validator().is_valid(_with_client_devices(_client_entry(value=value)))


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "browser-1"},
        {"id": "browser-1", "capabilities": {}},
        {"id": "browser-1", "capabilities": {"interactive_choice": "yes"}},
        {"id": "browser-1", "capabilities": {"interactive_choice": None}},
        {
            "id": "browser-1",
            "capabilities": {"interactive_choice": True, "sensitive_entry": True},
        },
        {
            "id": "browser-1",
            "capabilities": {"interactive_choice": True},
            "name": "Browser",
        },
        {"id": "", "capabilities": {"interactive_choice": True}},
        {"id": "x" * 129, "capabilities": {"interactive_choice": True}},
    ],
)
def test_schema_rejects_malformed_client_devices(entry: dict) -> None:
    assert not _validator().is_valid(_with_client_devices(entry))


def test_schema_rejects_a_non_array_client_devices() -> None:
    candidate = _configuration()
    candidate["client_devices"] = {"browser-1": True}

    assert not _validator().is_valid(candidate)
