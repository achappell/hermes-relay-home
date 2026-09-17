import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "_bmad/_config/custom/workflows/common"


def test_story_issue_lookup_uses_index_and_exact_key_before_fallback() -> None:
    workflow = (WORKFLOWS / "find-issue.yaml").read_text(encoding="utf-8")

    indexed_lookup = workflow.index("scripts/home_issue_tracking.py issue-id")
    exact_key_search = workflow.index("scripts/home_issue_tracking.py search-issue-id")
    legacy_search = workflow.index("glab api")
    assert indexed_lookup < exact_key_search < legacy_search

    ensure_issue = (WORKFLOWS / "ensure-issue.yaml").read_text(encoding="utf-8")
    assert "record-issue-url" in ensure_issue


def test_story_completion_creates_pr_and_passes_ci_before_status_update() -> None:
    workflow = (WORKFLOWS / "post-dev-complete.yaml").read_text(encoding="utf-8")
    dev_finish = workflow.split("# PHASE 2:", maxsplit=1)[1].split(
        "# PHASE 3:", maxsplit=1
    )[0]

    assert (
        dev_finish.index("common/ensure-story-mr")
        < dev_finish.index("common/wait-for-green-ci")
        < dev_finish.index("common/update-issue-status")
    )
    assert dev_finish.index("issue_url_record_status") < dev_finish.index(
        'git commit --allow-empty -m "dev'
    )
    assert 'git commit --only -m "track issue {story_key}"' in workflow
    assert "issue_tracking.story_base_branch" in workflow


def test_story_pr_uses_the_configured_base_and_checks_that_pr() -> None:
    ensure_pr = (WORKFLOWS / "ensure-story-mr.yaml").read_text(encoding="utf-8")
    check_ci = (WORKFLOWS / "get-mr-pipeline.yaml").read_text(encoding="utf-8")

    assert 'value: "{story_base_branch}"' in ensure_pr
    assert 'gh pr edit {mr_iid} --base "{story_base_branch}"' in ensure_pr
    assert "gh pr checks {mr_iid}" in check_ci
    assert 'gh run list --limit 1 -R "{mr_repo}"' not in check_ci
    assert "any(bucket == 'pass' for bucket in buckets)" in check_ci


def test_missing_pr_checks_are_not_written_as_green() -> None:
    ci_status = (WORKFLOWS / "write-ci-status.yaml").read_text(encoding="utf-8")

    assert '"status": "red", "diagnostic": "No PR checks appeared"' in ci_status
    assert (
        '"status": "red", "diagnostic": "No PR exists for this story branch"'
        in ci_status
    )


def test_status_update_ensures_labels_first() -> None:
    workflow = (WORKFLOWS / "update-issue-status.yaml").read_text(encoding="utf-8")

    assert workflow.index("INCLUDE: common/ensure-labels") < workflow.index(
        "gh issue edit"
    )


def test_home_workflow_deployment_copies_match_the_repo_owned_sources() -> None:
    result = subprocess.run(
        [
            str(ROOT / "scripts/apply_home_issue_tracking_overrides.sh"),
            "--check",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_issue_link_commit_leaves_other_staged_paths_alone(tmp_path: Path) -> None:
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            check=True,
            text=True,
        )

    (tmp_path / "story-index.yaml").write_text("issue: old\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("before\n", encoding="utf-8")
    git("init", "--quiet")
    git("config", "user.name", "Home Tracking Test")
    git("config", "user.email", "home-tracking-test@example.invalid")
    git("add", "story-index.yaml", "other.txt")
    git("commit", "--quiet", "-m", "baseline")

    (tmp_path / "story-index.yaml").write_text("issue: new\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("staged unrelated change\n", encoding="utf-8")
    git("add", "other.txt")
    git("commit", "--only", "-m", "track issue", "--", "story-index.yaml")

    committed_paths = git(
        "show", "--pretty=format:", "--name-only", "HEAD"
    ).stdout.splitlines()
    remaining_staged_paths = git("diff", "--cached", "--name-only").stdout.splitlines()
    assert committed_paths == ["story-index.yaml"]
    assert remaining_staged_paths == ["other.txt"]
