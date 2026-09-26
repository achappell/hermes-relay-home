---
story: HOME-NW-17
status: passed-local
validated: 2026-09-24
baseline_commit: ce5f1a6a864a4750a143e30737a27a1e9e446000
---

# HOME-NW-17 validation record

## Verification boundary

Home now pairs personal clients (TUI, iOS, macOS, Android) from a signed-in pairing page and admits their conversations through `POST /api/v1/client-claims`. Deterministic tests cover:

- client grants: shared Profiles, first-device bootstrap, owner approval and rejection from a paired client, the 24-hour pending expiry, holder listing, grants surviving renewal and ending on revocation or re-enrollment, and the personal-client type restriction;
- the typed pairing states `approval_pending` and `rejected`, and typed short codes in any case with or without a dash;
- client claims in the production SQLite store: no Room or arbitration, no idle tail, the reconnect grace and its reset, the per-device claim limit, `session_busy`, grant-scoped session references, grant-claim closing, and in-place migration of an existing claim table;
- the HTTP routes: claims with new, most-recent, and referenced sessions; session listing without Standard IDs; owner decisions and revocation closing claims; stale configuration and unavailable Profiles; Room devices refused;
- the pairing page: strict CSP, hardened session cookie, same-origin enforcement, the offer's code, link, and QR, the full page-approved pairing, reject/unpair/remove-Profile, sign-out, and admin-token routes refusing proxied requests while device routes still work;
- the Standard session directory against a scripted gateway, and the published client-claim schema.

The code review (2026-09-24) fixes are covered by added tests: grant re-checks on open, shared-Profile revocation, credential capability separation, live-holder ownership, configuration-free Room enrollment, https-only offers, Pair again, requested capabilities, real-server cookie and CSP delivery, runtime wiring of the client settings and session directory, legacy credential state, the full admin-route guard list with both proxy headers, and page-session expiry and eviction.

The page's JavaScript was exercised in a headless DOM (jsdom 24) with a scripted API: a ticked Profile survived two 2-second refreshes, Pair again pre-ticked the previous Profiles and sent them with the offer, requested capabilities rendered, and Approve submitted the chosen Profiles. The same script against the pre-review page reproduced the defect (the ticked Profile was lost). The script lives outside the repository.

The page was also exercised against a real local Home HTTP server: sign-in cookie flags, offer, device request with a dashed typed code, confirmation code in state, approval with Profiles, consumption with labelled grants, and the device list all behaved as specified. A Chrome session loaded and rendered the page, but browser automation could not click through it (the extension reported interference from another extension), so interactive browser behaviour is not claimed.

Not validated here: deployment to CaticornQueen, the live Tailscale Serve paths and their `X-Forwarded-For` header, live Standard `session.list`/`session.most_recent` against the deployed Hermes, and any client (TUI-HOME-01 and the mobile stories consume this contract). No sibling repository was changed.

## Observed checks

| Check | Exact command | Result |
| --- | --- | --- |
| Python runtime | `uv run --python 3.14 --locked --extra dev python --version` | `Python 3.14.7` |
| Focused HOME-NW-17, credential, configuration, runtime, and server suite | `uv run --python 3.14 --locked --extra dev pytest -q tests/test_client_grants.py tests/test_client_claim_store.py tests/test_client_claims_api.py tests/test_pairing_page.py tests/test_session_directory.py tests/test_client_claim_schema.py tests/test_credentials.py tests/test_credentials_api.py tests/test_configuration_validation.py tests/test_runtime.py tests/test_http_server.py` | `170 passed in 3.94s` |
| Full Home test suite | `uv run --python 3.14 --locked --extra dev pytest -q` | `677 passed in 7.45s` |
| Ruff lint | `uvx ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx ruff format --check src tests` | `68 files already formatted` |
| Lockfile check | `uv lock --check` | Passed |
| Whitespace/diff | `git diff --check` | Passed; no output |
| Guard test for `session_busy` | Disabled the store check and reran the client-claim suites | 2 tests failed as expected; restored |

A new runtime dependency, `segno` (BSD-3-Clause, pure Python), renders the pairing QR code. The claim-table migration runs once on startup and preserves existing rows. No credentials, `.env` files, or generated local state were added.

## Pairing UI follow-up (2026-09-26)

Amanda's browser testing found that the generated QR SVG was clipped when sized below its intrinsic dimensions: Segno's SVG had fixed `width` and `height` attributes but no `viewBox`, so the browser cropped the right and bottom instead of scaling it. The QR now includes a matching `viewBox` and four-module quiet zone, and its CSS size is capped by the available column width. The Copy link action now falls back to `document.execCommand("copy")` when the Clipboard API is missing or rejects the write, and announces success or a manual-copy recovery message; the displayed link is a read-only, keyboard-selectable field for manual recovery.

The offer test now parses the SVG and asserts that its `viewBox` matches the intrinsic dimensions. In a local browser harness using the actual pairing page and generated SVG, the QR rendered fully at 320 CSS px (200×200, no horizontal page overflow, with a 16px link field) and at the 561 px breakpoint (200×200, no horizontal page overflow). Clicking Copy link and pasting into a local verification field produced the exact pairing URI both with the Clipboard API and with that API disabled to exercise the fallback.

| Check | Exact command or interaction | Result |
| --- | --- | --- |
| Pair page tests | `../../.venv/bin/python -m pytest -q tests/test_pairing_page.py` | `34 passed in 0.38s` |
| Ruff lint | `uvx ruff check src tests` | All checks passed |
| Ruff format | `uvx ruff format --check src tests` | 68 files already formatted |
| Whitespace and diff | `git diff --check` | Passed; no output |
| Responsive QR and copy | Local pair-page harness at 320 px and 561 px; copy then paste with Clipboard API present and disabled | Complete QR, no horizontal overflow, exact URI pasted in both paths |

This local verification used a stub and created no Home offer or credential. At that point, QR camera decoding, screen-reader behavior, cross-browser fallback behavior, and live deployment remained unverified; deployment verification is recorded below.

## Pairing UI correction and copy feedback (2026-09-26)

The user-provided live screenshot still showed the clipped QR, confirming that the installed Home had not received the worktree change. During follow-up verification, the first local preview also loaded the editable package from the main checkout; checking `pairing.__file__` exposed the mismatch. Restarting the preview with `PYTHONPATH=src` loaded the worktree version. Chrome then showed the whole QR with its four-module quiet zone. Clicking Copy link changed the button to green “Copied” and announced “Pairing link copied.”; the button resets after three seconds. The QR and button were verified in this corrected local preview.

The final focused test and lint checks pass, and a wheel was built to `/tmp/hermes-home-pairing-fix/hermes_relay_home-0.1.0-py3-none-any.whl`; its packaged sources contain the viewBox fix and three-second confirmation. It was subsequently installed using a package-only update; see the deployment and verification section below. No live offer or credential was created.

| Check | Exact command or interaction | Result |
| --- | --- | --- |
| Pair page tests | `../../.venv/bin/python -m pytest -q tests/test_pairing_page.py` | `35 passed in 0.36s` |
| Ruff lint | `uvx ruff check src tests` | All checks passed |
| Ruff format | `uvx ruff format --check src tests` | 68 files already formatted |
| Whitespace and diff | `git diff --check` | Passed; no output |
| Corrected local browser preview | Worktree-backed Home pairing page; create offer and copy at desktop layout | Full QR, four-module quiet zone, green “Copied” button, and live status visible |
| Windows package build | `uv build --wheel --out-dir /tmp/hermes-home-pairing-fix` | Built `hermes_relay_home-0.1.0-py3-none-any.whl`; packaged fix confirmed |

## Deployment and verification (2026-09-26)

After Amanda approved deployment, the wheel was copied to CaticornQueen and its SHA-256 matched the local artifact. The existing `Hermes Home` scheduled task was stopped; `uv pip install --python C:\ProgramData\HermesHome\venv\Scripts\python.exe --no-deps --force-reinstall` installed only the Home package; then the task was restarted. The existing machine environment, credentials, and Prometheus configuration were left untouched. A backup of the prior `hermes_home` package and distribution metadata is at `C:\ProgramData\HermesHome\backups\pairing-ui-20260926-135637`.

The scheduled task returned to `Running`. Both the local Home `/pair` route and the Tailscale `/pair` route returned HTTP 200 and served the new Copy feedback markup and styles. A fake-link smoke call through Home's installed Python 3.14.7 environment generated a 270×270 SVG with a matching `viewBox`; the installed QR generator source has the four-module border. No live pairing offer was created; complete QR rendering and copy feedback were verified in the worktree-backed local browser preview. Live camera decoding, screen-reader behavior, and cross-browser clipboard fallback remain unverified.
