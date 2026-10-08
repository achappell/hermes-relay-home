# HOME-NW-17 browser admission — implementation validation

Date: 2026-10-08. State: implementation review, not deployed acceptance.

## Scope and provenance

Isolated worktree `.worktrees/browser-admission-contract`, branch `feat/home-browser-admission-contract`. Base/dependency: corrected Home spec PR #102, `3614fde02b557b76deb0aca4fad517e922f44489`. No changes to the TUI, live Home, host configuration, production grants, secrets, or monitoring. TUI PR #226 (`760da1865991009b57bb1e73784525b34afdd486`) remains the downstream consumer specification; its acceptance is not closed by this work.

## Exercised verification

Supported interpreter: Python 3.14.8. All commands below ran in the isolated worktree.

- `uv run --extra dev --python 3.14 pytest -q -s`: **1115 passed, 26 skipped, 7 warnings in 30.66s**, before final transactional label-tightening tests. Warnings concern existing WebSocket `connect()` context-manager deprecation in production/pilot proxy tests; no test failed.
- `uvx ruff check src tests`: **All checks passed!**
- `uvx ruff format --check src tests`: **79 files already formatted**.
- Independent reviewer separately exercised `tests/test_browser_admission.py`: **18 passed in 0.71s**, also before the final label-tightening follow-on; final rerun is recorded below when available.


### Final targeted verification and retained full-suite failure

After transactional label enforcement and atomic grant-list/revision discovery:

```text
uv run --extra dev --python 3.14 pytest -q -s tests/test_browser_admission.py tests/test_client_grants.py tests/test_client_claims_api.py tests/test_credentials.py tests/test_pairing_page.py tests/test_home_issue_tracking.py tests/test_home_issue_tracking_workflows.py tests/test_home_next_wave_contracts.py tests/test_standard_compatibility_artifacts.py
155 passed in 1.73s
uvx ruff check src tests
All checks passed!
uvx ruff format --check src tests
79 files already formatted
```

The latest full-suite attempt (after label tightening, before the final atomic
projection correction) was **1 failed, 1116 passed, 26 skipped, 7 warnings in
29.78s**. Failure:
`tests/test_bridge_server.py::test_healthy_idle_background_disconnect_retains_upstream_until_grace[True]`,
`KeyError: 'result'` at lines 644/659. This remains a failed full-suite gate;
the earlier green run does not replace it. It was not rerun to obtain green.

Source investigation, without rerunning the failure:

- The test's local `request()` helper (632–644) consumes the next WebSocket
  frame and assumes it contains `result`; it does not match the request ID.
  The same module already has `_receive_rpc()` (92–96) that correctly filters
  notifications by request ID, but this helper does not use it.
- Its `IdleBridge.next_audio()` (605–606) immediately returns an unavailable
  audio frame. `bridge/endpoint.py:1316–1318` starts the audio thread before
  returning the prompt result; dispatch sends the RPC response only at
  `endpoint.py:618–622,678`. The audio thread can send at 2238, using the
  `audio.frame` notification at 2359–2368, which has no `result`. Thus a valid
  concurrent notification ordering can produce exactly this helper failure.
- The actual failing received frame was not retained by pytest. Its identity
  as that audio notification is **[INFERENCE]**, not claimed packet evidence.
  The ordering hazard itself is established by source, not merely an unchanged
  filename or a successful retry.
- This test constructs a private `ListenerBridge` and `create_bridge_server`;
  it does not instantiate `HomeApplication`, `CredentialService`, configuration
  storage, or browser enrollment/grant/revision paths. The new browser policy
  cannot alter this fixture's response logic; scheduling may expose its existing
  first-frame assumption.
- Baseline `3614fde02b557b76deb0aca4fad517e922f44489` and current worktree blob
  hashes match exactly for the failing path:
  `test_bridge_server.py` = `c88e0fd7a6de820cfa00822daed45ee30577d7c8`;
  `api/bridge_server.py` = `f576d8cda1929f2fc272ec988dfd70eb79775126`;
  `bridge/endpoint.py` = `4db38fff118324e61e9a92175374cf1d2768d438`.

Disposition: isolate the existing notification-ordering test hazard rather than
change unrelated bridge behavior or suppress its exception in this Home
admission PR. A separate test correction should reuse request-ID matching.
The full-suite failure is disclosed in the PR; no claim of an all-green final
suite or reproduced baseline run is made.

Independent reviewer additionally ran the label-tightened browser tests:
**20 passed in 0.69s**, with the actual loopback smoke output. The final atomic
projection delta received a subsequent read-only recheck, not another independent
test run. Permanent tests assert authorization, durable grant transitions,
identity/revision, and real HTTP behavior; no throwaway smoke script was created.
Initial setup attempts did not pass: plain `uv run` omitted the optional test dependencies; then the new test module used an incorrect import path. Both were corrected. First regression run had two failures because the new storage error message omitted the existing `secure storage` wording; preserving that wording fixed them. These are not hidden successful runs.

## Actual localhost HTTP smoke

`tests/test_browser_admission.py::test_real_loopback_browser_contract_smoke` starts Home's real `create_server` on ephemeral `127.0.0.1`, uses `http.client` network requests, and persists configuration/credentials/claims in a pytest temporary SQLite database. No production credentials are loaded. The session-directory mock from a reused test fixture is disabled for this smoke; no upstream session is contacted. This proves Home HTTP/domain behavior, not real Standard conversation delivery or deployment.

Observed output:

```text
LOCAL HOME HTTP SMOKE: browser enrollment/consume=200; health=403; browser grant-add=401; approve/reject=403; admin later grant=200; idempotent replay=same grant; discovery revision advanced; claim=200; grant revoke=200 and claim closed; device revoke=200; revoked credential=401
```

Additional behavioral assertions cover endpoint-specific attestation; consume type/attestation substitution without consuming approval; all denied browser capabilities; direct domain approve/reject denial; existing native owner approval; shared/owned/explicit bootstrap addition rules; no wildcard; same-key conflict and revoked-grant replay; distinct replacement identity; rename revision and stable identity; label collision rejection; admin-token proxy denial; same-origin signed-in pairing tool addition and browser Device-credential denial.

## Gates

- Home implementation review/merge remains open; implementation PR stacks on PR #102 and must be retargeted to main after that dependency merges, not merged into the docs branch.
- No deployment, host smoke, browser appliance credential-file persistence/restart acceptance, browser/TUI integration, Tailscale exposure acceptance, or production authorization was performed.
- H5/self-health/new monitoring is not implemented or approved here.
- Browser WK acceptance remains open and owned by the TUI repository.
