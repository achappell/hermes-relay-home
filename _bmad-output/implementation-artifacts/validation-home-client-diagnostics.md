# HOME-NW-06 client-report validation — 2026-09-28

Status: review. Local implementation and deployed intake/read-only metadata are verified at the scope below; attributed real-client acceptance remains pending.

## Automated evidence

- 134 passing focused pytest checks across client reports, pairing, HTTP/API, runtime and diagnostics storage/recording with Python 3.14.
- Tests cover active Device authentication, revoked credentials, no device read access, signed-in/same-origin review, storage failures, unknown/content field rejection, durable restart, deduplication, seven-day retention, per-device rate and global storage limits.
- Ruff check passed; Ruff format check reported 71 files already formatted. Whitespace and source diff review passed.
- Apple counterpart built and passed 65 focused tests on each of macOS and iOS Simulator, including persisted upload retries and exact acknowledgment validation.

## Manual smoke

A local HTTP fixture with synthetic credentials/report data served the real pairing page. In Chrome, signed in, refreshed reports, expanded a report with device metadata and launch marker, opened recent Home events, and signed out. Reports disappeared on sign-out. No production data or credentials were used.

## Remaining acceptance

The deployed Home code and tailnet mapping were observed in the 2026-10-08 UTC supplement below. Remaining acceptance is an attributable, explicitly opted-in Apple client failure plus app restart, followed by verified received reports and recent Home events. The schema-1 viewer offers timestamp comparison, not exact per-turn correlation. This slice does not reopen the historical HOME-NW-06 acceptance or claim completed real-device acceptance.

## Android platform amendment — 2026-10-06

Status: review. Local validator/contract change only; deployed acceptance remains pending.

- Branch `feat/home-client-reports-android-platform` off `origin/main` `790f59e`, Python 3.14.
- Full suite (`uv run --python 3.14 --locked --extra dev pytest -q`) run twice: `1024 passed` both times. `ruff check src tests`: all checks passed. `ruff format --check src tests`: 74 files already formatted.
- New tests cover Android schema 1 and schema 2 with correlation, exact-case platform, unknown/malformed platforms, model boundary (1/40 accepted, 41 and out-of-charset/whitespace/control/Unicode rejected, no normalization), Apple vocabulary unchanged and cross-platform models rejected, exact key set (no `manufacturer`), size bound, restart persistence, `/pair` review payload, device HTTP path (200 and 400), and no content reaching storage or review output. No existing Apple test was edited.
- Not done here: deployment to CaticornQueen and device acceptance with an Android client (separate, owner-approved steps). Unblocks ANDROID-DIAG-02 and ANDROID-DIAG-03.

## Read-only deployed acceptance supplement — 2026-10-08 UTC

Disposition: **review, not done**. This pass used existing approved operator
access only. It did not deploy, change configuration or consent, control a
client/device, submit a synthetic report, interrupt a connection, revoke or
unpair a device, run stress tests, or watch CI.

### Observed Home proof

- All 34 installed Python source files matched canonical revision
  `d803994d1d47c63bb1b3c92cff42695de19a4434`, with zero mismatches.
  The canonical source aggregate also matched the running process journal's
  startup artifact SHA-256:
  `a367de2ec24a3f0ce0cd9a5d1824a49897fb36750d1414c9aa6929ebf6d5b93b`.
  The separately stored deployment revision was stale and was not used as
  evidence of loaded code.
- Existing-admin authenticated loopback `GET /api/v1/diagnostics/status`
  returned 200; `GET /pair` returned 200. The existing tailnet Serve
  configuration included `/api/v1/client-diagnostics` and `/pair`, both
  mapped to the loopback service. An empty, unauthenticated report POST
  returned 401 through both loopback and the real tailnet route. Signed-out
  `POST /pair/api/client-diagnostics` also returned 401.
- These are actual deployed route/access checks, **not** a successful
  authenticated client upload or signed-in browser review acceptance. No
  Device credential or invented report was used for the smoke.
- SQLite was opened with `mode=ro` and `PRAGMA query_only=ON`; the report-store
  API was not invoked to avoid its pruning-on-read behavior. There were
  **53 retained reports from two reporting devices**: 20 schema-1 and
  33 schema-2, all declaring the allowed `ios` platform. All 53 passed the
  installed strict validator at their original receipt times.
- Last retained receipt timestamps were `2026-10-05T03:07:15.766407Z`
  for schema 1 and `2026-10-07T13:28:03.744466Z` for schema 2. No report
  content, credentials, device identifiers, report IDs or correlation IDs
  were exported.
- Read-only metadata contained 29 linked associations. There were 862
  report-event tuples with an existing exact authenticated-Device/Home-socket/
  request-token link and zero missing/unlinked tuples among those supplying
  both required identifiers. Retained client assertions included 214
  `client_response_received` and 216 `client_request_resolved` events.
  These are stored assertions, not independent proof of client receipt.
  Schema-2 origins were marked `unverified` (149) or `unavailable` (29).

### Client ownership, consent and remaining gates

The Android acceptance owner confirmed ANDROID-DIAG-01 is manual **Share
diagnostics**, with no automatic upload or report receipt supplied. It is not
evidence for ANDROID-DIAG-02/03 or this automatic-intake acceptance. The Apple
owner had not attributed an existing receipt to the intended installed
client, an existing per-Home opt-in, and a controlled failure/restart journey.
No opt-in was enabled by this Home verification pass.

The remaining gates are real-client/build/consent attribution, failure plus
restart and durable automatic upload, the client's matching acknowledgement,
and authorized signed-in reviewer observation. Historical retained reports,
manual sharing, and route-only HTTP 401 checks cannot substitute for them.
The Android platform amendment's distinct device acceptance also remains
pending; this pass did not exercise an Android report upload.

Home's **safe-event exporter is not wired**; that is separate from this
working client-report intake. See the installed-source diagnosis and explicit
contract-scope question in
[the connection-diagnostics validation](validation-home-nw-06-connection-failure-diagnostics.md#read-only-deployed-acceptance-supplement--2026-10-08-utc).
No network or authentication outage is inferred from its status flag.
