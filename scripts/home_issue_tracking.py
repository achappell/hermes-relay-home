"""Home-specific metadata helpers used by the local BMAD workflows."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

DEFAULT_STORY_INDEX = Path("_bmad-output/implementation-artifacts/story-index.yaml")


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_story_index(path: Path) -> dict[str, dict[str, str]]:
    """Read the small, flat story records in Home's checked-in index."""
    stories: dict[str, dict[str, str]] = {}
    current_id: str | None = None
    current_fields: dict[str, str] = {}

    for line in path.read_text(encoding="utf-8").splitlines():
        id_match = re.match(r"^  - id:\s*(.*?)\s*$", line)
        if id_match:
            if current_id is not None:
                stories[current_id] = current_fields
            current_id = _scalar(id_match.group(1))
            current_fields = {}
            continue

        if current_id is None:
            continue
        field_match = re.match(r"^    ([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*?)\s*$", line)
        if field_match:
            current_fields[field_match.group(1)] = _scalar(field_match.group(2))

    if current_id is not None:
        stories[current_id] = current_fields
    return stories


def find_story_record(
    stories: dict[str, dict[str, str]], story_key: str
) -> tuple[str, dict[str, str]] | None:
    """Match a BMAD key such as ``home-nw-05-suffix`` to ``HOME-NW-05``."""
    normalized_key = story_key.strip().lower()
    matches = [
        (story_id, fields)
        for story_id, fields in stories.items()
        if normalized_key == re.sub(r"[^a-z0-9]+", "-", story_id.lower()).strip("-")
        or normalized_key.startswith(
            re.sub(r"[^a-z0-9]+", "-", story_id.lower()).strip("-") + "-"
        )
    ]
    if len(matches) > 1:
        raise ValueError(
            f"story key {story_key!r} matches multiple story-index records"
        )
    return matches[0] if matches else None


def issue_id_from_url(issue_url: str, *, host: str, project: str, platform: str) -> str:
    """Return the issue number only when the indexed URL belongs to this project."""
    parsed = urlparse(issue_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != host.lower():
        raise ValueError(
            f"indexed issue URL does not belong to configured host {host!r}"
        )

    path_parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    project_parts = project.strip("/").split("/")
    if path_parts[: len(project_parts)] != project_parts:
        raise ValueError(
            f"indexed issue URL does not belong to configured project {project!r}"
        )

    remaining = path_parts[len(project_parts) :]
    if platform == "github" and len(remaining) == 2 and remaining[0] == "issues":
        issue_number = remaining[1]
    elif (
        platform == "gitlab"
        and len(remaining) == 3
        and remaining[:2] == ["-", "issues"]
    ):
        issue_number = remaining[2]
    else:
        raise ValueError(f"indexed URL is not an issue URL for platform {platform!r}")

    if not issue_number.isdecimal():
        raise ValueError("indexed issue URL has a nonnumeric issue identifier")
    return issue_number


def resolve_story_issue_id(
    story_key: str,
    *,
    host: str,
    project: str,
    platform: str,
    index_path: Path = DEFAULT_STORY_INDEX,
) -> str:
    stories = read_story_index(index_path)
    record = find_story_record(stories, story_key)
    if record is None:
        return ""
    issue_url = record[1].get("github_issue", "")
    if not issue_url:
        return ""
    return issue_id_from_url(issue_url, host=host, project=project, platform=platform)


def resolve_story_issue_id_from_search(search_result: str, story_key: str) -> str:
    """Return a unique issue that explicitly carries this story key.

    GitHub's issue search may find issues with a missing ``prd:<key>`` label.
    Match the exact sprint key in the issue body, or the full story key in the
    title, and ignore pull requests. An ambiguous result fails closed.
    """
    payload = json.loads(search_result)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise TypeError("GitHub issue search returned an unexpected response")

    key = story_key.strip()
    if not key:
        return ""
    quote = chr(96)
    sprint_key_pattern = re.compile(
        r"(?im)^\*\*Sprint Key:\*\*\s*"
        + quote
        + "?"
        + re.escape(key)
        + quote
        + r"?\s*$"
    )
    title_pattern = re.compile(
        r"(?<![a-z0-9-])" + re.escape(key) + r"(?![a-z0-9-])", re.IGNORECASE
    )
    matches: dict[str, None] = {}
    for item in payload["items"]:
        if not isinstance(item, dict) or item.get("pull_request"):
            continue
        title = str(item.get("title") or "")
        body = str(item.get("body") or "")
        issue_number = str(item.get("number") or "")
        if issue_number.isdecimal() and (
            sprint_key_pattern.search(body) or title_pattern.search(title)
        ):
            matches[issue_number] = None

    if len(matches) > 1:
        ids = ", ".join(matches)
        raise ValueError(f"story key {key!r} matches multiple GitHub issues: {ids}")
    return next(iter(matches), "")


def issue_id_from_create_output(create_output: str, *, host: str, project: str) -> str:
    """Extract and validate the issue URL printed by gh issue create."""
    urls = set(re.findall(r"https://[^\s]+/issues/[0-9]+", create_output))
    if len(urls) != 1:
        raise ValueError("gh issue create did not return exactly one issue URL")
    return issue_id_from_url(
        next(iter(urls)), host=host, project=project, platform="github"
    )


def _require_clean_default_story_index(index_path: Path) -> None:
    """Avoid staging unrelated local edits when the workflow records an issue."""
    if index_path != DEFAULT_STORY_INDEX:
        return

    for diff_args in ([], ["--cached"]):
        result = subprocess.run(
            ["git", "diff", "--quiet", *diff_args, "--", str(index_path)],
            capture_output=True,
            check=False,
        )
        if result.returncode == 1:
            raise ValueError(
                "story-index.yaml already has local changes; review and commit "
                "those changes before recording a new issue URL"
            )
        if result.returncode != 0:
            raise ValueError("could not verify that story-index.yaml is clean")


def record_story_issue_url(
    story_key: str,
    issue_id: str,
    *,
    host: str,
    project: str,
    platform: str,
    index_path: Path = DEFAULT_STORY_INDEX,
) -> bool:
    """Store a newly created issue URL in the indexed Home story record."""
    stories = read_story_index(index_path)
    record = find_story_record(stories, story_key)
    if record is None:
        return False

    story_id, fields = record
    if platform != "github":
        raise ValueError("Home story-index issue URLs are currently GitHub URLs")
    if not issue_id.isdecimal():
        raise ValueError("issue identifier must be numeric")

    issue_url = f"https://{host}/{project.strip('/')}/issues/{issue_id}"
    existing_url = fields.get("github_issue", "")
    if existing_url:
        existing_id = issue_id_from_url(
            existing_url, host=host, project=project, platform=platform
        )
        if existing_id != issue_id:
            raise ValueError(
                f"story {story_id} already links to issue {existing_id}, not {issue_id}"
            )
        return False

    _require_clean_default_story_index(index_path)
    lines = index_path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(
        index
        for index, line in enumerate(lines)
        if re.match(r"^  - id:\s*" + re.escape(story_id) + r"\s*$", line)
    )
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if re.match(r"^  - id:\s*", lines[index])
        ),
        len(lines),
    )
    field_line = next(
        (
            index
            for index in range(start + 1, end)
            if re.match(r"^    github_issue:", lines[index])
        ),
        None,
    )
    if field_line is not None:
        lines[field_line] = f"    github_issue: {issue_url}\n"
    else:
        insert_at = next(
            (
                index
                for index in range(start + 1, end)
                if re.match(r"^    horizon:", lines[index])
            ),
            end,
        )
        lines.insert(insert_at, f"    github_issue: {issue_url}\n")
    index_path.write_text("".join(lines), encoding="utf-8")
    return True


def prd_key_from_branch(branch: str) -> str:
    """Read the PRD key from the configured ``feat/{prd_key}/{story}`` shape."""
    parts = branch.strip().split("/")
    if len(parts) >= 3 and parts[0] == "feat" and parts[1] and parts[2]:
        return parts[1]
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    issue = commands.add_parser("issue-id")
    issue.add_argument("story_key")
    issue.add_argument("host")
    issue.add_argument("project")
    issue.add_argument("platform")

    prd = commands.add_parser("prd-key")
    prd.add_argument("branch")

    search = commands.add_parser("search-issue-id")
    search.add_argument("story_key")

    created = commands.add_parser("created-issue-id")
    created.add_argument("create_output")
    created.add_argument("host")
    created.add_argument("project")

    record = commands.add_parser("record-issue-url")
    record.add_argument("story_key")
    record.add_argument("host")
    record.add_argument("project")
    record.add_argument("platform")
    record.add_argument("issue_id")

    args = parser.parse_args()
    try:
        if args.command == "issue-id":
            print(
                resolve_story_issue_id(
                    args.story_key,
                    host=args.host,
                    project=args.project,
                    platform=args.platform,
                )
            )
        elif args.command == "prd-key":
            print(prd_key_from_branch(args.branch))
        elif args.command == "search-issue-id":
            print(resolve_story_issue_id_from_search(sys.stdin.read(), args.story_key))
        elif args.command == "created-issue-id":
            print(
                issue_id_from_create_output(
                    args.create_output, host=args.host, project=args.project
                )
            )
        elif args.command == "record-issue-url":
            changed = record_story_issue_url(
                args.story_key,
                args.issue_id,
                host=args.host,
                project=args.project,
                platform=args.platform,
            )
            print("recorded" if changed else "already recorded or story not indexed")
    except (OSError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
