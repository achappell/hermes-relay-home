---
story: HOME-NW-07
status: passed
validated: 2026-09-17
baseline_commit: 73d58e5092e6a24796463120382f495842ea0750
---

# HOME-NW-07 validation record

## Verification boundary

The Home suite and injected Standard gateway tests verify the Home-owned
projection, freshness, claim and turn binding, replay rejection, operation
gates, diagnostics privacy, and the optional Device capability. The current
pinned Standard contract does not advertise typed choices and the TUI Home
adapter does not yet carry the complete response. This validates the Home
slice against capable fakes; it does not claim live Standard, TUI, endpoint, or
deployment support. No sibling repository was changed.

## Observed checks

| Check | Command | Result |
| --- | --- | --- |
| Python runtime | `uv run --python 3.14 --locked --extra dev python --version` | `Python 3.14.7` |
| Focused bridge and diagnostics suite | `uv run --python 3.14 --locked --extra dev pytest -q tests/test_standard_bridge.py tests/test_bridge_endpoint.py tests/test_diagnostics.py` | `202 passed in 0.76s` |
| Full Home suite | `uv run --python 3.14 --locked --extra dev pytest -q` | `452 passed in 5.99s` |
| Ruff lint | `uvx ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx ruff format --check src tests` | `52 files already formatted` |
| Lockfile | `uv lock --check` | `Resolved 11 packages in 6ms` |
| Configuration schema | `uv run --python 3.14 --locked --extra dev python -m json.tool docs/contracts/v1/configuration.schema.json` | Passed; valid JSON |
| Whitespace/diff | `git diff --check` | Passed; no output |

## Review

All 17 findings were triaged individually. Seven patch groups were applied and
verified; the other 10 claims were rejected with evidence in the implementation
spec. Nothing was deferred.

No credentials, `.env` files, generated local state, or database migrations were
added.
