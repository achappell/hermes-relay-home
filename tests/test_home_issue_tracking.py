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
