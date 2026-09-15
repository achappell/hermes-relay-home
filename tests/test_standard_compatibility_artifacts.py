import re
from pathlib import Path

SPEC_DIR = (
    Path(__file__).resolve().parents[1]
    / "_bmad-output"
    / "specs"
    / "spec-standard-hermes-compatibility-migration"
)
IMPLEMENTATION_DIR = (
    Path(__file__).resolve().parents[1] / "_bmad-output" / "implementation-artifacts"
)
ROUTE_CONTRACT = (
    SPEC_DIR.parent / "spec-home-bridge-route-roaming" / "bridge-contract.md"
)
BASELINE_REVISION = "f220265eb1fe98326728930bedece37726fbbf05"
STANDARD_COMMIT = "2237be355906fbe6065ce1815711eee52b2d646e"


def test_standard_baseline_pins_immutable_release_and_endpoint_split():
    baseline = (SPEC_DIR / "standard-baseline.md").read_text()

    assert "Hermes release | `0.21.1`" in baseline
    assert "2237be355906fbe6065ce1815711eee52b2d646e" in baseline
    assert "`ws(s)://<hermes-host>/api/ws`" in baseline
    assert "`ws(s)://<hermes-host>/api/audio/speak-stream`" in baseline
    assert "no `speech_timing` or word-offset event" in baseline
    assert "Rollback-only endpoint" in baseline
    assert "commands.catalog" in baseline
    assert "two-item `[slash_name, description]` arrays" in baseline
    assert "ignores any ready command list" in baseline


def test_surface_matrix_covers_every_in_scope_surface_and_capability():
    matrix = (SPEC_DIR / "surface-migration-matrix.md").read_text()
    header = next(
        line for line in matrix.splitlines() if line.startswith("| Surface |")
    )

    required_columns = (
        "JSON/session",
        "PCM/audio",
        "Timing",
        "Prompts",
        "Commands",
        "Interrupt",
        "Liveness",
        "Authentication",
        "Recovery",
    )
    surfaces = (
        "Hermes server",
        "Home bridge",
        "TUI",
        "iOS/macOS",
        "Android",
        "ReSpeaker Puck",
        "ESP32 Touch",
        "W/K browser/iPad",
    )

    for column in required_columns:
        assert column in header
    for surface in surfaces:
        assert f"| {surface} |" in matrix


def test_rollout_gates_use_standard_baseline_and_keep_fork_rollback_only():
    baseline = (SPEC_DIR / "standard-baseline.md").read_text()
    rollout = (SPEC_DIR / "compatibility-and-rollout.md").read_text()
    spec = (SPEC_DIR / "SPEC.md").read_text()

    assert "2237be355906fbe6065ce1815711eee52b2d646e" in rollout
    assert "rollback-only" in baseline.lower()
    assert "/voice-session" in baseline
    assert "fork-only names are" in spec


def test_home_nw_01_validation_record_names_pin_and_evidence_boundary():
    validation = (IMPLEMENTATION_DIR / "validation-home-nw-01.md").read_text()
    frontmatter = validation.split("---", 2)[1]
    story_index = (IMPLEMENTATION_DIR / "story-index.yaml").read_text()
    story_match = re.search(
        r"(?ms)^  - id: HOME-NW-01\n(?:(?!^  - id: ).)*",
        story_index,
    )

    assert "story: HOME-NW-01" in frontmatter
    assert "status: verified" in frontmatter
    assert f"baseline_revision: {BASELINE_REVISION}" in frontmatter
    assert "standard_release: 0.21.1" in frontmatter
    assert f"standard_commit: {STANDARD_COMMIT}" in frontmatter
    assert story_match is not None
    assert (
        "validation: _bmad-output/implementation-artifacts/validation-home-nw-01.md"
        in story_match.group(0)
    )

    for section in (
        "## Boundary proved",
        "## Verification boundary",
        "## Tested source revision",
        "## Verification commands",
        "## Verification performed",
    ):
        assert section in validation

    for command in (
        "PYTHONPATH=\"$PWD/src\" uv run --isolated --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_standard_bridge.py tests/test_standard_compatibility_artifacts.py",
        "PYTHONPATH=\"$PWD/src\" uv run --isolated --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q",
        "uvx --from ruff ruff check src tests",
        "uvx --from ruff ruff format --check src tests",
        "uv lock --check",
        "git diff --check",
    ):
        assert command in validation

    assert re.search(r"\b\d+ passed in [\d.]+s", validation)
    assert "All checks passed!" in validation
    assert re.search(r"\b\d+ files already formatted", validation)
    assert "passed; no output" in validation

    assert BASELINE_REVISION in validation
    assert STANDARD_COMMIT in validation
    assert "live deployment claim" in validation
    assert "front-end migration" in validation


def test_home_route_contract_uses_standard_command_catalog():
    route_contract = ROUTE_CONTRACT.read_text()

    assert "Dispatches only a command advertised by Standard `commands.catalog`." in (
        route_contract
    )
