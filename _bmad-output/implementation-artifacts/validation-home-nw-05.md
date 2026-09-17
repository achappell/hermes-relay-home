---
story: HOME-NW-05
status: verified
validated: 2026-09-16
baseline_commit: eb950026ce591179c0d2c4c6069c01c09cfc3fee
tested_on_main: 142317a8542c2e3263d1d173e087d069f1f0805d
---

# HOME-NW-05 validation record

## Boundary proved

Home owns the current Profile list, household wake phrases, exact per-device
mapping grants, and active conversation claims. Configuration publishes reject
ambiguous active phrases and invalid references. Legacy SQLite snapshots fail
closed until an administrator publishes the new v1 shape; the migration never
guesses a Profile from a Device field or mapping label.

Arbitration uses independent 250 ms rounds per Room, keeps acoustic ranking,
per-Room priority, and no-loser-promotion behavior, and requires the current
configuration revision and exact mapping grant. Unopened grants expire after
90 seconds and release their Room; opening clears that separate first-open
deadline. A winner receives one opaque
conversation handle bound in Home to its original device, Room, mapping,
Profile, credential generation, and independent Standard Session. Follow-ups
stay on that Session. Mapping remaps and removals preserve active bindings;
explicit endpoint, Profile, or mapping revocation closes the claim. Content-free
activity events control the idle timer, which defaults to 8 seconds after
playback completion.

The Windows installer and contract documentation now use the Home SQLite
claim store; operator-managed conversation grants are removed.

## Verification boundary

Evidence is from deterministic Home tests and injected Standard gateway/audio
sockets. It proves the Home authorization, arbitration, persistence, endpoint
redaction, session-binding, activity, revocation, restart, and failure
boundaries. It does not claim a live Standard server connection, physical
endpoint audio, or deployment validation.

## Tested source revision

The worktree began at main commit
`eb950026ce591179c0d2c4c6069c01c09cfc3fee`. While implementation was in
progress, main advanced with the HOME-NW-06 diagnostics follow-up. This branch
was fast-forwarded and the HOME-NW-05 work restored on top of current main
`142317a8542c2e3263d1d173e087d069f1f0805d`; checks below were rerun there. The
canonical worktree was not modified.

## Observed checks

| Check | Exact command | Recorded result |
| --- | --- | --- |
| Focused HOME-NW-05 suite | `uv run pytest -q tests/test_api_application.py tests/test_arbitration.py tests/test_bridge_endpoint.py tests/test_configuration_validation.py tests/test_credentials_api.py tests/test_http_server.py tests/test_production_bridge.py tests/test_runtime.py tests/test_sqlite_configuration_store.py tests/test_standard_bridge.py tests/test_windows_deployment.py` | `232 passed in 3.23s` |
| Full Home test suite | `uv run pytest -q` | `381 passed in 3.98s` |
| Python runtime | `uv run python --version` | `Python 3.14.7` |
| Ruff lint | `uvx ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx ruff format --check src tests` | `49 files already formatted` |
| Lockfile check | `uv lock --check` | Passed; `uv` resolved 11 packages |
| Diff whitespace check | `git diff --check` | Passed; no output |

No credentials, `.env` files, or generated local state were added.
