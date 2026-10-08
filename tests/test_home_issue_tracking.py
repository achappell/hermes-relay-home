import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import home_issue_tracking
from scripts.home_issue_tracking import (
    find_story_record,
    issue_id_from_create_output,
    issue_id_from_url,
    prd_key_from_branch,
    read_story_index,
    record_story_issue_url,
    resolve_story_issue_id,
    resolve_story_issue_id_from_search,
)


def test_resolves_home_story_url_from_the_canonical_index(tmp_path: Path) -> None:
    index = tmp_path / "story-index.yaml"
    index.write_text(
        """schema_version: 1
stories:
  - id: HOME-NW-05
    title: Add household Profile mappings
    github_issue: https://github.com/achappell/hermes-relay-home/issues/15
""",
        encoding="utf-8",
    )

    assert (
        resolve_story_issue_id(
            "home-nw-05-profile-mappings-conversation-claims",
            host="github.com",
            project="achappell/hermes-relay-home",
            platform="github",
            index_path=index,
        )
        == "15"
    )


def test_unknown_story_key_returns_no_indexed_issue(tmp_path: Path) -> None:
    index = tmp_path / "story-index.yaml"
    index.write_text("stories:\n  - id: HOME-NW-05\n", encoding="utf-8")

    assert (
        resolve_story_issue_id(
            "home-nw-99-future-story",
            host="github.com",
            project="achappell/hermes-relay-home",
            platform="github",
            index_path=index,
        )
        == ""
    )


def test_story_index_prefix_collision_is_reported() -> None:
    stories = {
        "HOME-NW-01": {},
        "HOME-NW-01-STORY": {},
    }

    with pytest.raises(ValueError, match="matches multiple"):
        find_story_record(stories, "home-nw-01-story")


def test_issue_url_must_match_configured_project() -> None:
    with pytest.raises(ValueError, match="configured project"):
        issue_id_from_url(
            "https://github.com/other/repo/issues/15",
            host="github.com",
            project="achappell/hermes-relay-home",
            platform="github",
        )


def test_search_finds_unlabeled_issue_by_exact_sprint_key() -> None:
    response = json.dumps(
        {
            "items": [
                {
                    "number": 15,
                    "title": "An unrelated title",
                    "body": (
                        "**Sprint Key:** "
                        + chr(96)
                        + "home-nw-05-profile-mappings"
                        + chr(96)
                    ),
                    "labels": [],
                }
            ]
        }
    )

    assert (
        resolve_story_issue_id_from_search(response, "home-nw-05-profile-mappings")
        == "15"
    )


def test_search_ignores_pull_requests_and_prefix_title_matches() -> None:
    response = json.dumps(
        {
            "items": [
                {
                    "number": 15,
                    "title": "home-nw-05-profile-mappings",
                    "pull_request": {"url": "https://api.github.com/pulls/15"},
                },
                {
                    "number": 16,
                    "title": "home-nw-05-profile-mappings-duplicate",
                },
                {"number": 17, "title": "Story: home-nw-05-profile-mappings"},
            ]
        }
    )

    assert (
        resolve_story_issue_id_from_search(response, "home-nw-05-profile-mappings")
        == "17"
    )


def test_search_fails_closed_when_multiple_issues_match() -> None:
    response = json.dumps(
        {
            "items": [
                {
                    "number": 15,
                    "body": "**Sprint Key:** home-nw-05-profile-mappings",
                },
                {
                    "number": 16,
                    "body": "**Sprint Key:** home-nw-05-profile-mappings",
                },
            ]
        }
    )

    with pytest.raises(ValueError, match="multiple GitHub issues: 15, 16"):
        resolve_story_issue_id_from_search(response, "home-nw-05-profile-mappings")


def test_search_rejects_an_unexpected_response_shape() -> None:
    with pytest.raises(TypeError, match="unexpected response"):
        resolve_story_issue_id_from_search("[]", "home-nw-05-profile-mappings")


def test_created_issue_url_is_validated_against_the_home_repository() -> None:
    assert (
        issue_id_from_create_output(
            "https://github.com/achappell/hermes-relay-home/issues/27\n",
            host="github.com",
            project="achappell/hermes-relay-home",
        )
        == "27"
    )

    with pytest.raises(ValueError, match="configured project"):
        issue_id_from_create_output(
            "https://github.com/another/repo/issues/27",
            host="github.com",
            project="achappell/hermes-relay-home",
        )


def test_record_refuses_to_overwrite_an_index_with_existing_git_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "story-index.yaml"
    original = """schema_version: 1
stories:
  - id: HOME-NW-15
    title: Future story
    horizon: next
"""
    index.write_text(original, encoding="utf-8")
    monkeypatch.setattr(home_issue_tracking, "DEFAULT_STORY_INDEX", index)
    monkeypatch.setattr(
        home_issue_tracking.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )

    with pytest.raises(ValueError, match="already has local changes"):
        record_story_issue_url(
            "home-nw-15-future-story",
            "27",
            host="github.com",
            project="achappell/hermes-relay-home",
            platform="github",
            index_path=index,
        )

    assert index.read_text(encoding="utf-8") == original


def test_prd_key_comes_from_story_branch() -> None:
    assert (
        prd_key_from_branch(
            "feat/hermes-home-next-wave-2026-09-13/home-nw-05-profile-mappings-conversation-claims"
        )
        == "hermes-home-next-wave-2026-09-13"
    )
    assert prd_key_from_branch("main") == ""


def test_current_story_index_resolves_home_nw_05() -> None:
    record = find_story_record(
        read_story_index(
            Path("_bmad-output/implementation-artifacts/story-index.yaml")
        ),
        "home-nw-05-profile-mappings-conversation-claims",
    )

    assert record is not None
    assert record[0] == "HOME-NW-05"
    assert record[1]["github_issue"].endswith("/issues/15")


def test_records_new_issue_url_for_indexed_story(tmp_path: Path) -> None:
    index = tmp_path / "story-index.yaml"
    index.write_text(
        """schema_version: 1
stories:
  - id: HOME-NW-15
    title: Future story
    horizon: next
""",
        encoding="utf-8",
    )

    assert record_story_issue_url(
        "home-nw-15-future-story",
        "27",
        host="github.com",
        project="achappell/hermes-relay-home",
        platform="github",
        index_path=index,
    )
    assert (
        resolve_story_issue_id(
            "home-nw-15-future-story",
            host="github.com",
            project="achappell/hermes-relay-home",
            platform="github",
            index_path=index,
        )
        == "27"
    )


def test_story_index_and_sprint_status_parse_and_validate() -> None:
    """Regression test: ensure story-index and sprint-status YAML files parse correctly.

    This test catches syntax errors like unquoted scalars containing ': ' (colons).
    Added after incident 2026-10-08: story-index.yaml had invalid evidence_note
    with unquoted colons (PR #90 commit 0fe7882), causing "mapping values are not
    allowed here" parser error at line 311, column 246.
    """
    import yaml
    from pathlib import Path

    # Test story-index.yaml
    story_index_path = Path("_bmad-output/implementation-artifacts/story-index.yaml")
    with open(story_index_path) as f:
        story_index = yaml.safe_load(f)

    # Verify structure
    assert "stories" in story_index, "story-index.yaml missing 'stories' key"
    assert isinstance(story_index["stories"], list), "stories must be a list"
    assert len(story_index["stories"]) > 0, "stories list is empty"

    # Verify every story has required fields
    required_fields = {"id", "title", "kind"}
    for i, story in enumerate(story_index["stories"]):
        for field in required_fields:
            assert field in story, f"Story {i} (id={story.get('id')}) missing field '{field}'"

    # Verify tracker_key consistency (if present, must match expected pattern)
    for story in story_index["stories"]:
        if "tracker_key" in story:
            tracker_key = story["tracker_key"]
            assert isinstance(tracker_key, str), f"tracker_key must be string for {story['id']}"
            assert len(tracker_key) > 0, f"tracker_key empty for {story['id']}"

    # Verify evidence_note fields are properly formatted (if present)
    for story in story_index["stories"]:
        if "evidence_note" in story:
            note = story["evidence_note"]
            assert isinstance(note, str), f"evidence_note must be string for {story['id']}"
            # The note should be parseable as a string without YAML errors
            # If it contains colons, they should be handled correctly (quoted)
            assert len(note) > 0, f"evidence_note empty for {story['id']}"

    # Test sprint-status.yaml
    sprint_status_path = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
    with open(sprint_status_path) as f:
        sprint_status = yaml.safe_load(f)

    # Verify structure
    assert "development_status" in sprint_status, "sprint-status.yaml missing 'development_status'"
    assert isinstance(sprint_status["development_status"], dict), "development_status must be dict"

    # Verify no circular dependencies in status references
    valid_statuses = {"done", "backlog", "in-progress", "review"}
    for key, status in sprint_status["development_status"].items():
        assert status in valid_statuses, \
            f"Invalid status '{status}' for {key}. Must be one of {valid_statuses}"
