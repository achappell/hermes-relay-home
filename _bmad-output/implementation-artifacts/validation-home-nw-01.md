---
story: HOME-NW-01
status: verified
validated: 2026-09-15
baseline_revision: f220265eb1fe98326728930bedece37726fbbf05
standard_release: 0.21.1
standard_commit: 2237be355906fbe6065ce1815711eee52b2d646e
---

# HOME-NW-01 validation record

## Boundary proved

The Home bridge uses the pinned Standard Hermes compatibility boundary. Its
command capability list comes from the Standard `commands.catalog` response's
`pairs`; a command list in `gateway.ready` is not treated as proof. Dispatch
remains limited to a command explicitly advertised by that catalog.

The deterministic bridge fixtures also cover the pinned separate audio
sidecar's valid PCM and `fallback` shapes, structured-prompt identity and
sensitivity, bounded reconnect/readiness, typed transport outcomes, and the
existing no-replay rule after uncertain delivery. The server-held Hermes token,
runtime Session ID, and Profile ID remain outside endpoint-safe output.

## Verification boundary

Evidence is from injected JSON and audio socket ports in `tests/`; it is not a
live deployment claim. This record does not claim live Hermes validation,
route-roaming or browser bootstrap, physical hardware, or front-end migration.
Those gates belong to the owning later stories and surface repositories.

Standard event sequence/cursor replay remains a later decision. Reconnect
resumes the existing Session but does not automatically replay an uncertain
prompt or prior response.

## Tested source revision

The Home source under test was the worktree based at revision
`f220265eb1fe98326728930bedece37726fbbf05`, which is the recorded
`baseline_revision`, with the HOME-NW-01 changes applied as working-tree
changes. The Standard wire shapes used by the fixtures were compared with the
immutable source checkout at commit
`2237be355906fbe6065ce1815711eee52b2d646e`, the pinned `0.21.1` baseline. No
live Hermes process or moving Standard branch was executed by this Home check.

## Verification commands

These are the exact repository verification commands for this record:

| Check | Exact command | Recorded result |
| --- | --- | --- |
| Focused bridge and compatibility-artifact tests | `PYTHONPATH="$PWD/src" uv run --isolated --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_standard_bridge.py tests/test_standard_compatibility_artifacts.py` | `82 passed in 0.20s` |
| Full Home test suite | `PYTHONPATH="$PWD/src" uv run --isolated --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q` | `214 passed in 3.23s` |
| Ruff lint | `uvx --from ruff ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx --from ruff ruff format --check src tests` | `38 files already formatted` |
| Lockfile check | `uv lock --check` | passed; `uv` resolved 11 packages |
| Diff whitespace check | `git diff --check` | passed; no output |

## Verification performed

All checks in the table passed against the deterministic Home bridge seam.
