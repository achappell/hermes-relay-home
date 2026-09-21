---
story: HOME-NW-16
status: passed
validated: 2026-09-21
baseline_commit: 0ff709452dba1c840f8d4bd9aa95635699dc001c
---

# HOME-NW-16 validation record

## Verification boundary

The Home implementation now admits paired Touch endpoints through a separate
non-acoustic route and binds each grant to the approved Room/Profile authority
held in the credential scope. Deterministic tests cover authorization,
configuration currency, Profile availability, endpoint redaction, live-room
contention, same-device double taps, idle-tail takeover and expiry, pairing
serialization, the durable SQLite claim store, and the published request
schema. The shared bridge claim machinery is exercised through both the
in-memory application store and the production SQLite store.

This validates the Home slice against deterministic configuration, credential,
and claim-store fakes. It does not claim physical Touch hardware, endpoint
audio, live Standard Hermes connectivity, or deployment validation. No sibling
repository was changed.

## Observed checks

| Check | Exact command | Result |
| --- | --- | --- |
| Python runtime | `uv run --python 3.14 --locked --extra dev python --version` | `Python 3.14.7` |
| Focused HOME-NW-16 and credential/contract suite | `uv run --python 3.14 --locked --extra dev pytest -q tests/test_touch_claims.py tests/test_touch_claim_schema.py tests/test_credentials.py tests/test_credentials_api.py tests/test_contract_fixtures.py` | `64 passed in 1.28s` |
| Full Home test suite | `uv run --python 3.14 --locked --extra dev pytest -q` | `581 passed in 6.38s` |
| Ruff lint | `uvx ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx ruff format --check src tests` | `59 files already formatted` |
| Lockfile check | `uv lock --check` | Passed |
| Whitespace/diff | `git diff --check` | Passed; no output |

No credentials, `.env` files, generated local state, or database migrations
were added.
