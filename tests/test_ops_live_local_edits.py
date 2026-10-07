"""The versioned live host edits must stay self-consistent and safe to run.

``deploy/ops/standard-speak-stream-timeout`` and
``deploy/ops/qwen3-streaming-logging`` capture two edits made directly on
production hosts. These tests need neither host: they check the recorded hashes,
that the patches round-trip, that the scripts behave idempotently against a
synthetic checkout, and that the logging module is content-free.
"""

import hashlib
import importlib.util
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).parents[1] / "deploy" / "ops"
SPEAK = OPS / "standard-speak-stream-timeout"
QWEN = OPS / "qwen3-streaming-logging"
SPEAK_FILES = ("hermes_cli/web_routers/audio.py", "tools/tts_streaming.py")
needs_git_and_sh = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("sh") is None,
    reason="git and sh are required",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args: str, cwd: Path, env: dict[str, str] | None = None):
    return subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


def _load_logging_module():
    spec = importlib.util.spec_from_file_location(
        "qwen3_server_logging_under_test", QWEN / "qwen3_server_logging.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Qwen3 streaming logging -------------------------------------------------


def test_qwen3_readme_and_installer_hashes_match_the_shipped_files() -> None:
    readme = (QWEN / "README.md").read_text()
    installer = (QWEN / "install.ps1").read_text()

    for name in (
        "qwen3_server_logging.py",
        "qwen3_streaming_server.py",
        "start_qwen3_streaming_optimized.ps1",
        "rollback.ps1",
    ):
        digest = sha256(QWEN / name)
        assert re.search(rf"{digest}\s+{re.escape(name)}", readme), name
    for name in (
        "qwen3_server_logging.py",
        "qwen3_streaming_server.py",
        "start_qwen3_streaming_optimized.ps1",
    ):
        assert sha256(QWEN / name) in installer, name


@needs_git_and_sh
def test_qwen3_patch_reverses_the_shipped_copies_to_the_recorded_originals(
    tmp_path,
) -> None:
    readme = (QWEN / "README.md").read_text()
    installer = (QWEN / "install.ps1").read_text()
    names = ("qwen3_streaming_server.py", "start_qwen3_streaming_optimized.ps1")
    for name in names:
        shutil.copyfile(QWEN / name, tmp_path / name)

    reverse = run(
        "git", "apply", "--reverse", str(QWEN / "qwen3-streaming-logging.patch"),
        cwd=tmp_path,
    )  # fmt: skip

    assert reverse.returncode == 0, reverse.stderr
    for name in names:
        original = re.search(
            rf"([0-9a-f]{{64}})\s+{re.escape(name)}\s+\(original", readme
        ).group(1)
        assert sha256(tmp_path / name) == original
        assert original in installer


def test_qwen3_python_files_compile() -> None:
    for name in ("qwen3_server_logging.py", "qwen3_streaming_server.py"):
        compile((QWEN / name).read_text(), name, "exec")


def test_qwen3_server_hooks_are_guarded_so_logging_cannot_stop_speech() -> None:
    server = (QWEN / "qwen3_streaming_server.py").read_text()

    for call in ("install_logging()", "start_health_monitor(service, args)"):
        assert re.search(
            rf"try:\n(?:        import qwen3_server_logging\n\n)?"
            rf"        qwen3_server_logging\.{re.escape(call)}\n"
            rf"    except Exception:\n        LOGGER\.exception\(",
            server,
        ), call


def test_qwen3_tracebacks_omit_exception_messages() -> None:
    module = _load_logging_module()
    formatter = module.ContentFreeFormatter("%(message)s")
    echoed = "synthesized sentence the provider echoed"
    private = "second private sentence"

    try:
        try:
            raise ValueError(echoed)
        except ValueError as cause:
            raise RuntimeError(private) from cause
    except RuntimeError:
        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
        text = formatter.format(record)

    assert "synthesized sentence" not in text
    assert "second private sentence" not in text
    assert "builtins.ValueError: <message omitted>" in text
    assert "builtins.RuntimeError: <message omitted>" in text
    assert "test_qwen3_tracebacks_omit_exception_messages" in text


def test_qwen3_access_filter_counts_healthz_and_tracks_the_active_request() -> None:
    module = _load_logging_module()
    access = module._AccessFilter()

    def emit(message: str) -> bool:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, message, None, None)
        return access.filter(record)

    assert emit('127.0.0.1 "GET /healthz HTTP/1.1" 200 -') is False
    assert emit('127.0.0.1 "GET /healthz HTTP/1.1" 503 -') is True
    assert module.STATE.healthz_ok == 1

    assert emit("timing request=ab12 event=request_start mode=stream") is True
    assert module.STATE.active_request == "ab12"
    assert module.STATE.requests_total == 1
    emit("timing request=ff00 event=response_complete status=ok")
    assert module.STATE.active_request == "ab12"
    emit("timing request=ab12 event=response_complete status=ok")
    assert module.STATE.active_request is None


def test_qwen3_install_logging_writes_a_rotating_file(tmp_path, monkeypatch) -> None:
    module = _load_logging_module()
    monkeypatch.setenv("QWEN3_LOG_DIR", str(tmp_path))
    root = logging.getLogger()
    server_log = logging.getLogger(module.SERVER_LOGGER_NAME)
    handlers_before = list(root.handlers)
    level_before = root.level
    root.setLevel(logging.INFO)
    filters_before = list(server_log.filters)
    atexit_hooks = []
    monkeypatch.setattr(module.atexit, "register", atexit_hooks.append)
    try:
        path = module.install_logging()
        added = [h for h in root.handlers if h not in handlers_before]
        rotating = [h for h in added if hasattr(h, "maxBytes")]

        assert path == tmp_path / "qwen3-streaming.log"
        assert [(h.maxBytes, h.backupCount) for h in rotating] == [(10 * 1024**2, 5)]
        server_log.info("timing request=ab12 event=request_start text_chars=5")
        for handler in added:
            handler.flush()
        contents = path.read_text()
        assert "event=process_start" in contents
        assert "rotation=10MBx5" in contents
        assert "event=request_start text_chars=5" in contents
        assert len(atexit_hooks) == 1
    finally:
        root.setLevel(level_before)
        for handler in list(root.handlers):
            if handler not in handlers_before:
                root.removeHandler(handler)
                handler.close()
        for stale in list(server_log.filters):
            if stale not in filters_before:
                server_log.removeFilter(stale)


def test_qwen3_installer_restarts_only_the_streaming_task_and_is_opt_in() -> None:
    installer = (QWEN / "install.ps1").read_text()

    assert installer.count("Stop-ScheduledTask") == 2  # script + generated rollback
    assert "[switch]$Restart" in installer
    assert '$TaskName = "Qwen3-TTS-Streaming-Optimized"' in installer
    assert "if ($Restart)" in installer
    assert "busy=1" in installer


# --- Standard speak-stream timeout ----------------------------------------------


def test_speak_timeout_readme_records_the_shipped_patch_hash() -> None:
    readme = (SPEAK / "README.md").read_text()
    digest = sha256(SPEAK / "standard-speak-stream-timeout.patch")

    assert re.search(rf"{digest}\s+standard-speak-stream-timeout\.patch", readme)


def test_speak_timeout_patch_is_exactly_the_two_runtime_edits() -> None:
    patch = (SPEAK / "standard-speak-stream-timeout.patch").read_text()

    assert re.findall(r"^diff --git a/(\S+) b/", patch, re.MULTILINE) == [
        "hermes_cli/web_routers/audio.py",
        "tools/tts_streaming.py",
    ]
    assert "tests/" not in patch
    assert "+_DEFAULT_STREAM_TIMEOUT_SECONDS = 15.0" in patch
    assert 'section.get("stream_timeout_seconds"' in patch
    assert "+            max_retries=0," in patch
    assert "0 < value <= 120" in patch
    assert "type(exc).__name__" in patch
    assert (
        '-            _log.warning("speak-stream synthesis failed: %s", exc)' in patch
    )


def test_speak_timeout_scripts_never_restart_the_pilot() -> None:
    for name in ("apply.sh", "verify.sh", "rollback.sh"):
        for line in (SPEAK / name).read_text().splitlines():
            stripped = line.strip()
            if not re.search(
                r"launchctl\s+(kickstart|kill|stop|start|bootout|bootstrap|load|unload)",
                stripped,
            ):
                continue
            assert stripped.startswith(("#", "echo", "RESTART=")), (name, line)


@needs_git_and_sh
def test_speak_timeout_scripts_have_valid_posix_syntax() -> None:
    for name in ("apply.sh", "verify.sh", "rollback.sh"):
        assert run("sh", "-n", str(SPEAK / name), cwd=SPEAK).returncode == 0, name


def _pre_image_files(root: Path) -> None:
    """Rebuild the patch's pre-image from its own hunks.

    The real files live on the media server; each hunk's context and removed
    lines are enough for ``git apply`` to find its place in a synthetic file.
    """
    patch = (SPEAK / "standard-speak-stream-timeout.patch").read_text()
    for block in re.split(r"(?m)^(?=diff --git )", patch)[1:]:
        name = re.match(r"diff --git a/(\S+) b/", block).group(1)
        parts = re.split(r"(?m)^@@.*@@.*\n", block)[1:]
        lines: list[str] = []
        for part in parts:
            lines += [row[1:] for row in part.splitlines() if row[:1] in (" ", "-")] + [
                "# filler"
            ] * 20
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n")


@needs_git_and_sh
def test_speak_timeout_apply_verify_and_rollback_are_idempotent(tmp_path) -> None:
    checkout = tmp_path / "hermes-agent"
    checkout.mkdir()
    _pre_image_files(checkout)
    identity = ("-c", "user.email=ops@example.invalid", "-c", "user.name=ops")
    assert run("git", "init", "-q", cwd=checkout).returncode == 0
    assert run("git", "add", "-A", cwd=checkout).returncode == 0
    assert run("git", *identity, "commit", "-qm", "base", cwd=checkout).returncode == 0
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "HERMES_AGENT_DIR": str(checkout),
        "BACKUP_ROOT": str(tmp_path / "backups"),
    }
    original = {name: sha256(checkout / name) for name in SPEAK_FILES}

    def script(name: str, *args: str):
        return run("sh", str(SPEAK / name), *args, cwd=tmp_path, env=env)

    before = script("verify.sh")
    assert before.returncode == 1
    assert "FAIL tools/tts_streaming.py lacks stream_timeout_seconds" in before.stdout
    assert "apply.sh" in before.stdout and "git apply" in before.stdout

    probe = script("apply.sh", "--check")
    assert probe.returncode == 0
    assert "would apply cleanly" in probe.stdout
    assert {n: sha256(checkout / n) for n in SPEAK_FILES} == original

    applied = script("apply.sh")
    assert applied.returncode == 0, applied.stderr
    assert "NOT restarted" in applied.stdout
    patched = {name: sha256(checkout / name) for name in SPEAK_FILES}
    assert patched != original
    backups = list((tmp_path / "backups").glob("standard-speak-timeout-*"))
    assert len(backups) == 1
    for name in SPEAK_FILES:
        assert sha256(backups[0] / name) == original[name]

    again = script("apply.sh")
    assert again.returncode == 0
    assert "already applied" in again.stdout
    assert {n: sha256(checkout / n) for n in SPEAK_FILES} == patched
    assert len(list((tmp_path / "backups").glob("standard-speak-timeout-*"))) == 1

    after = script("verify.sh")
    assert after.returncode == 0, after.stdout
    assert "ok   checked-in patch is fully present" in after.stdout

    rolled_back = script("rollback.sh")
    assert rolled_back.returncode == 0
    assert {n: sha256(checkout / n) for n in SPEAK_FILES} == original
    assert "already rolled back" in script("rollback.sh").stdout


@needs_git_and_sh
def test_speak_timeout_apply_reports_a_conflict_without_changing_files(
    tmp_path,
) -> None:
    checkout = tmp_path / "hermes-agent"
    checkout.mkdir()
    _pre_image_files(checkout)
    path = checkout / "tools" / "tts_streaming.py"
    path.write_text(path.read_text().replace("client = OpenAI(", "client = Other("))
    identity = ("-c", "user.email=ops@example.invalid", "-c", "user.name=ops")
    run("git", "init", "-q", cwd=checkout)
    run("git", "add", "-A", cwd=checkout)
    run("git", *identity, "commit", "-qm", "base", cwd=checkout)
    before = {name: sha256(checkout / name) for name in SPEAK_FILES}
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "HERMES_AGENT_DIR": str(checkout),
        "BACKUP_ROOT": str(tmp_path / "backups"),
    }

    result = run("sh", str(SPEAK / "apply.sh"), cwd=tmp_path, env=env)

    assert result.returncode == 2
    assert "CONFLICT" in result.stderr
    assert {n: sha256(checkout / n) for n in SPEAK_FILES} == before
    assert not (tmp_path / "backups").exists()
