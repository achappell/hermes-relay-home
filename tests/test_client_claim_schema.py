"""Semantic tests for the published v1 personal-client claim JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).parents[1] / "docs/contracts/v1/client-claim.schema.json"
FIXTURE_PATH = Path(__file__).parent / "fixtures/client-claim.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_client_claim_schema_accepts_the_fixture_and_every_session_mode() -> None:
    validator = _validator()
    fixture = _fixture()
    assert validator.is_valid(fixture)
    for session in ({"mode": "new"}, {"mode": "most_recent"}):
        assert validator.is_valid({**fixture, "session": session})
    fixture.pop("session")
    assert validator.is_valid(fixture)


@pytest.mark.parametrize(
    "field", ["profile_id", "session_id", "room_id", "acoustic_evidence"]
)
def test_client_claim_schema_rejects_room_and_identity_fields(field: str) -> None:
    fixture = _fixture()
    fixture[field] = "forbidden"

    assert not _validator().is_valid(fixture)


def test_client_claim_schema_rejects_malformed_session_choices() -> None:
    validator = _validator()
    fixture = _fixture()
    for session in (
        {"mode": "resume"},
        {"mode": "new", "session_ref": "sref-1"},
        {"mode": "latest"},
    ):
        assert not validator.is_valid({**fixture, "session": session})
