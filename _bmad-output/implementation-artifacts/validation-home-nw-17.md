---
story: HOME-NW-17
status: passed-local
validated: 2026-09-24
baseline_commit: ce5f1a6a864a4750a143e30737a27a1e9e446000
---

# HOME-NW-17 validation record

## Verification boundary

Home now pairs personal clients (TUI, iOS, Android) from a signed-in pairing page and admits their conversations through `POST /api/v1/client-claims`. Deterministic tests cover:

- client grants: shared Profiles, first-device bootstrap, owner approval and rejection from a paired client, the 24-hour pending expiry, holder listing, grants surviving renewal and ending on revocation or re-enrollment, and the personal-client type restriction;
- the typed pairing states `approval_pending` and `rejected`, and typed short codes in any case with or without a dash;
- client claims in the production SQLite store: no Room or arbitration, no idle tail, the reconnect grace and its reset, the per-device claim limit, `session_busy`, grant-scoped session references, grant-claim closing, and in-place migration of an existing claim table;
- the HTTP routes: claims with new, most-recent, and referenced sessions; session listing without Standard IDs; owner decisions and revocation closing claims; stale configuration and unavailable Profiles; Room devices refused;
- the pairing page: strict CSP, hardened session cookie, same-origin enforcement, the offer's code, link, and QR, the full page-approved pairing, reject/unpair/remove-Profile, sign-out, and admin-token routes refusing proxied requests while device routes still work;
- the Standard session directory against a scripted gateway, and the published client-claim schema.

The page was also exercised against a real local Home HTTP server: sign-in cookie flags, offer, device request with a dashed typed code, confirmation code in state, approval with Profiles, consumption with labelled grants, and the device list all behaved as specified. A Chrome session loaded and rendered the page, but browser automation could not click through it (the extension reported interference from another extension), so interactive browser behaviour is not claimed.

Not validated here: deployment to CaticornQueen, the live Tailscale Serve paths and their `X-Forwarded-For` header, live Standard `session.list`/`session.most_recent` against the deployed Hermes, and any client (TUI-HOME-01 and the mobile stories consume this contract). No sibling repository was changed.

## Observed checks

| Check | Exact command | Result |
| --- | --- | --- |
| Python runtime | `uv run --python 3.14 --locked --extra dev python --version` | `Python 3.14.7` |
| Focused HOME-NW-17, credential, and configuration suite | `uv run --python 3.14 --locked --extra dev pytest -q tests/test_client_grants.py tests/test_client_claim_store.py tests/test_client_claims_api.py tests/test_pairing_page.py tests/test_session_directory.py tests/test_client_claim_schema.py tests/test_credentials.py tests/test_credentials_api.py tests/test_configuration_validation.py` | `118 passed in 4.69s` |
| Full Home test suite | `uv run --python 3.14 --locked --extra dev pytest -q` | `646 passed in 9.33s` |
| Ruff lint | `uvx ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx ruff format --check src tests` | `68 files already formatted` |
| Lockfile check | `uv lock --check` | Passed |
| Whitespace/diff | `git diff --check` | Passed; no output |
| Guard test for `session_busy` | Disabled the store check and reran the client-claim suites | 2 tests failed as expected; restored |

A new runtime dependency, `segno` (BSD-3-Clause, pure Python), renders the pairing QR code. The claim-table migration runs once on startup and preserves existing rows. No credentials, `.env` files, or generated local state were added.
