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
