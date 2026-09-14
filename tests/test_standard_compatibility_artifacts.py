from pathlib import Path

SPEC_DIR = (
    Path(__file__).resolve().parents[1]
    / "_bmad-output"
    / "specs"
    / "spec-standard-hermes-compatibility-migration"
)


def test_standard_baseline_pins_immutable_release_and_endpoint_split():
    baseline = (SPEC_DIR / "standard-baseline.md").read_text()

    assert "Hermes release | `0.21.1`" in baseline
    assert "2237be355906fbe6065ce1815711eee52b2d646e" in baseline
    assert "`ws(s)://<hermes-host>/api/ws`" in baseline
    assert "`ws(s)://<hermes-host>/api/audio/speak-stream`" in baseline
    assert "no `speech_timing` or word-offset event" in baseline
    assert "Rollback-only endpoint" in baseline


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
