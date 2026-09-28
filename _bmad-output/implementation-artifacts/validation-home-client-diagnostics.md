# HOME-NW-06 client-report validation — 2026-09-28

Status: review. Local implementation verified; deployed acceptance remains pending.

## Automated evidence

- 134 passing focused pytest checks across client reports, pairing, HTTP/API, runtime and diagnostics storage/recording with Python 3.14.
- Tests cover active Device authentication, revoked credentials, no device read access, signed-in/same-origin review, storage failures, unknown/content field rejection, durable restart, deduplication, seven-day retention, per-device rate and global storage limits.
- Ruff check passed; Ruff format check reported 71 files already formatted. Whitespace and source diff review passed.
- Apple counterpart built and passed 65 focused tests on each of macOS and iOS Simulator, including persisted upload retries and exact acknowledgment validation.

## Manual smoke

A local HTTP fixture with synthetic credentials/report data served the real pairing page. In Chrome, signed in, refreshed reports, expanded a report with device metadata and launch marker, opened recent Home events, and signed out. Reports disappeared on sign-out. No production data or credentials were used.

## Remaining acceptance

Deploy the Home code and add the documented tailnet Serve mapping for `/api/v1/client-diagnostics`. Install and explicitly enable an Apple device, reproduce failure plus app restart, and verify received reports and recent Home events. The viewer offers timestamp comparison, not exact per-turn correlation. This slice does not reopen the historical HOME-NW-06 acceptance or claim real-device/deployed validation.
