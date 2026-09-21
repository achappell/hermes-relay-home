"""Semantic tests for the published v1 Touch claim JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).parents[1] / "docs/contracts/v1/touch-claim.schema.json"
FIXTURE_PATH = Path(__file__).parent / "fixtures/touch-claim.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_touch_claim_schema_is_draft_2020_12_valid_and_accepts_fixture() -> None:
    validator = _validator()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert validator.is_valid(fixture)


@pytest.mark.parametrize(
    "field",
    ["profile_id", "session_id", "wake_mapping_id", "acoustic_evidence"],
)
def test_touch_claim_schema_rejects_wake_or_session_fields(field: str) -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture[field] = "forbidden"

    assert not _validator().is_valid(fixture)


def test_touch_claim_schema_rejects_non_tap_initiation_and_extra_fields() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture["initiation"] = {"kind": "wake", "observed_at_ms": 1}

    assert not _validator().is_valid(fixture)

    fixture["initiation"] = {
        "kind": "tap",
        "observed_at_ms": 1,
        "audio": "forbidden",
    }
    assert not _validator().is_valid(fixture)
