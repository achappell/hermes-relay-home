import json
import unittest
from pathlib import Path

from hermes_home.domain.configuration import validate_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


class ContractFixtureTests(unittest.TestCase):
    def test_configuration_fixture_contains_canonical_revisioned_state(self) -> None:
        configuration = load("configuration.json")

        self.assertEqual(configuration["revision"], 12)
        self.assertTrue(configuration["rooms"])
        self.assertTrue(configuration["wake_mappings"])
        self.assertTrue(configuration["devices"])
        self.assertEqual(validate_snapshot(configuration), configuration)

    def test_wake_claim_fixture_has_no_conversation_or_audio_payload(self) -> None:
        claim = load("wake-claim.json")

        self.assertEqual(claim["schema"], 1)
        self.assertEqual(claim["availability"], "ready")
        self.assertFalse(
            {"profile_id", "wake_phrase", "prompt", "transcript", "audio"}
            & claim.keys()
        )

    def test_touch_claim_fixture_has_only_device_observed_initiation(self) -> None:
        claim = load("touch-claim.json")

        assert claim == {
            "schema": 1,
            "claim_id": "touch-claim-example",
            "device_id": "touch-kitchen",
            "configuration_revision": 12,
            "initiation": {
                "kind": "tap",
                "observed_at_ms": 1720000000000,
            },
        }
        assert not {"profile_id", "session_id", "wake_mapping_id"} & claim.keys()
