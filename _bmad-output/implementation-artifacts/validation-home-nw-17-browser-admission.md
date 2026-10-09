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


### Pre-integration verification and historical full-suite failure

After transactional label enforcement, atomic grant-list/revision discovery, and validating configuration shape before label-policy access:

```text
uv run --extra dev --python 3.14 pytest -q -s tests/test_browser_admission.py tests/test_client_grants.py tests/test_client_claims_api.py tests/test_credentials.py tests/test_pairing_page.py tests/test_home_issue_tracking.py tests/test_home_issue_tracking_workflows.py tests/test_home_next_wave_contracts.py tests/test_standard_compatibility_artifacts.py
157 passed in 1.73s
uvx ruff check src tests
All checks passed!
uvx ruff format --check src tests
79 files already formatted
```

The pre-correction full-suite attempt (after label tightening, before the final atomic
projection correction) was **1 failed, 1116 passed, 26 skipped, 7 warnings in
29.78s**. Failure:
`tests/test_bridge_server.py::test_healthy_idle_background_disconnect_retains_upstream_until_grace[True]`,
`KeyError: 'result'` at lines 644/659. The known failure was investigated without
rerunning to confirm it; subsequent user authorization permitted the test fix below.

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
- Baseline `3614fde02b557b76deb0aca4fad517e922f44489` and then-current worktree blob
  hashes match exactly for the failing path:
  `test_bridge_server.py` = `c88e0fd7a6de820cfa00822daed45ee30577d7c8`;
  `api/bridge_server.py` = `f576d8cda1929f2fc272ec988dfd70eb79775126`;
  `bridge/endpoint.py` = `4db38fff118324e61e9a92175374cf1d2768d438`.

The user subsequently authorized correcting the test gate in this PR.
`tests/test_bridge_server.py:644` now reuses `_receive_rpc(client, method)` instead
of assuming the first frame is a response. No production behavior, notification
ordering, completion assertion, or reconnect/shutdown coverage changed.

Independent reviewer additionally ran the label-tightened browser tests:
**20 passed in 0.69s**, with the actual loopback smoke output. The final atomic
projection delta received a subsequent read-only recheck, not another independent
test run. Permanent tests assert authorization, durable grant transitions,
identity/revision, and real HTTP behavior; no throwaway smoke script was created.

Final compatibility correction reuses `validate_candidate` before the new label
policy reads Profile fields. Two permanent regressions assert malformed snapshots
return 400 and leave durable configuration unchanged; this avoids introducing
KeyError/500 responses before the existing configuration validator can run.

Initial setup attempts did not pass: plain `uv run` omitted the optional test dependencies; then the new test module used an incorrect import path. Both were corrected. First regression run had two failures because the new storage error message omitted the existing `secure storage` wording; preserving that wording fixed them. These are not hidden successful runs.

## Actual localhost HTTP smoke

`tests/test_browser_admission.py::test_real_loopback_browser_contract_smoke` starts Home's real `create_server` on ephemeral `127.0.0.1`, uses `http.client` network requests, and persists configuration/credentials/claims in a pytest temporary SQLite database. No production credentials are loaded. The session-directory mock from a reused test fixture is disabled for this smoke; no upstream session is contacted. This proves Home HTTP/domain behavior, not real Standard conversation delivery or deployment.

Observed output:

```text
LOCAL HOME HTTP SMOKE: browser enrollment/consume=200; health=403; browser grant-add=401; approve/reject=403; admin later grant=200; idempotent replay=same grant; discovery revision advanced; claim=200; grant revoke=200 and claim closed; device revoke=200; revoked credential=401
```

Additional behavioral assertions cover endpoint-specific attestation; consume type/attestation substitution without consuming approval; all denied browser capabilities; direct domain approve/reject denial; existing native owner approval; shared/owned/explicit bootstrap addition rules; no wildcard; same-key conflict and revoked-grant replay; distinct replacement identity; rename revision and stable identity; label collision rejection; admin-token proxy denial; same-origin signed-in pairing tool addition and browser Device-credential denial.

## Implementation-stage gates (historical)

- Home implementation review/merge remains open. PR #102 has merged; PR #103 is now based on `main`. No PR #103 merge or deployment was performed.
- No deployment, host smoke, browser appliance credential-file persistence/restart acceptance, browser/TUI integration, Tailscale exposure acceptance, or production authorization was performed.
- H5/self-health/new monitoring is not implemented or approved here.
- Browser WK acceptance remains open and owned by the TUI repository.

## Authorized test correction and main integration

Before integrating main, a deterministic in-memory WebSocket receive sequence
queued `audio.frame`, then the matching `prompt.submit` response, then
`message.complete`. Reusing `_receive_rpc` selected the submitted result and
left the subsequent completion intact:

```text
DETERMINISTIC INTERLEAVING PASS: audio.frame precedes matching prompt.submit response; submitted result selected; subsequent message.complete preserved
```

This was a throwaway `python -c` scenario: no script/artifact was added to the
repository. Both exact changed test variants then passed (2 passed in 2.19s),
and the pre-merge full suite passed (1119 passed, 26 skipped, 7 warnings in
31.86s). Independent reviewer inspected the one-line fix without rerunning tests.

Preserved that fix in commit `30fb28a`, then fetched and merged
`origin/main` = `6e9110719ab55028e16bf538a968379a38f32d35` with merge commit
`3e792b784d9852b8d9f81222c035695ee877437f`; no rebase or force push.
Read and resolved three conflicted files:

- `spec-home-nw-17-browser-admission.md`: kept the owner-approved implementation
  contract, immutable baseline, and `in-review` status instead of the parent
  draft stub; original corrected proposal remains in merged PR #102 history.
- `sprint-status.yaml`: kept Home browser `review`, not parent's `backlog`.
- `story-index.yaml`: kept the validation link and approved implementation/
  not-deployed evidence, preserving all non-conflicting main changes.

PR #103 was retargeted to `main`; the merged parent dependency is resolved.

### Final merged-tree verification

After all conflict resolutions and the authorized one-line test correction:

```text
uv run --extra dev --python 3.14 pytest -q -s tests/test_bridge_server.py::test_healthy_idle_background_disconnect_retains_upstream_until_grace tests/test_browser_admission.py tests/test_home_issue_tracking.py tests/test_home_issue_tracking_workflows.py tests/test_home_next_wave_contracts.py tests/test_standard_compatibility_artifacts.py
55 passed in 3.15s
uv run --extra dev --python 3.14 pytest -q
1119 passed, 26 skipped, 7 warnings in 30.52s
uvx ruff check src tests
All checks passed!
uvx ruff format --check src tests
79 files already formatted
git diff --check
(exit 0, no output)
```

The targeted run included both idle-recovery variants, the actual browser HTTP
smoke (same status output above), native tracking, and Standard compatibility.
The final full-suite gate passes after a real test correction, not an unchanged
retry. The seven WebSocket deprecation warnings and 26 skips remain explicit.
No production workaround, suppressed failure, dropped completion assertion,
deployment, PR merge, or CI watch was introduced.

## Authorized Home-only production rollout

After PR #103 merged, the user authorized updating Home on CaticornQueen.
This section supersedes the historical no-deployment statements above only
for Home. Browser appliance pairing, Ops deployment, real turns and browser
WK acceptance remain open; the Ops owner reports the TUI release-tag gate.

### Exact source and verified package

- Previous deployed revision: `d3d816a0ee40c5d44778864daacf6f510291d006`.
- Deployed merged `origin/main`: `3a0eec0b9739524e2a8c54fd5a3308b3c3abb90b`.
- Built `hermes_relay_home-0.1.0-py3-none-any.whl` from that clean source;
  SHA256 `4b5553b25fb9790009106fac6a73efae6aff75935c7f181f7b12278498559a80`.
- All 35 wheel Python sources matched the checkout; staged wheel checksum
  matched; all 35 installed Python sources matched after the update.
- Merged-source verification: **1119 passed, 26 skipped, 7 warnings in
  31.77s**; Ruff checks passed; **79 files already formatted**; wheel build
  succeeded. The earlier test race is corrected; no full-suite failure remains.
- Used the documented merged-source wheel/package-only procedure in
  `deploy/windows/README.md`, not a newly created public release/tag.

### Live safety and preservation

Strict existing SSH alias/host keys were used throughout. Active claims were
zero initially, during backup, and immediately before stopping Home
(411 closed claims). Only the SYSTEM `Hermes Home` task was restarted;
`uv pip install --no-deps --force-reinstall` exited zero. No installer,
configuration reset, dependencies, other services, user grants or credentials
were changed. Host Python remained 3.14.7.

SQLite integrity was `ok` before and after. Durable credential-state SHA256
remained `2c24570fedc901335238d8547f7c4af8f975c0f2efc234ad4db7dba596bdf508`;
configuration response SHA256 remained
`d2e71cb9ea5cab5800f11ecc49c92e3f2cf1c81e6fb32709c3f60a59b9721af3`.
All 11 secret files, runner, scheduled-task definition and Prometheus/Alloy
configuration hashes were unchanged. Of 19 Home machine settings, only the
deployment revision changed.

Home remains bound to `0.0.0.0:8780` behind the existing Private-profile
TCP8780 inbound firewall allowance restricted to `100.64.0.0/10`; bridge
remains `127.0.0.1:8766`. Tailscale Serve stayed byte-for-byte unchanged, with
Home proxies targeting loopback and no Funnel stanza. Prometheus retained
its localhost scrape; `up{job="hermes-home"}` was **1**.

Recursive ACL inspection found zero Users/Authenticated Users/Everyone
grants before and after. Database remains SYSTEM/Admin only; diagnostics
remain SYSTEM Modify/Admin Read, with the existing Alloy ReadAndExecute
exception only on export. Alloy stayed running with its original PID and
loopback readiness HTTP200. No legacy-gateway/media-server action occurred.

### Actual installed-service smoke

Authenticated metrics/configuration/diagnostics returned **200**;
unauthenticated metrics/client-claims returned **401**; `/pair` returned
**200** locally and over tailnet HTTPS. `/healthz` returned the existing
**404** because Home does not expose that route; it is not the readiness
criterion. Post-update active claims remained zero.

Synthetic invalid-code enrollment probes returned **401** for browser
`service_private_file` (storage policy accepted, nonexistent offer denied)
and **400** for native TUI using the same attestation (storage policy denied).
Durable credential-state equality was checked after these probes: no offer,
device, grant or pairing was created. Tailnet-proxied admin grant-add returned
**403**, preserving its local-admin boundary. No tokens, credential contents,
private Profile selections or grant/device identifiers were printed.

After restart, exporter counters showed **4** records written and **1**
successful upload batch; queue **1**, dropped-unuploaded **0**, collector
reachable. Alloy reported **4140** sent entries, **0** dropped and **0**
batch retries. Existing export files remained, with new-day safe events
written. This is observed local export/shipper continuity, not a separate
Loki/end-to-end monitoring acceptance claim.

### Private backup and rollback

Backup directory:
`C:\ProgramData\HermesHome\backups\home-browser-3a0eec0-20261008`
(SYSTEM/Admin only). It contains 44 previous package/metadata files,
online SQLite backup, runner, task definition, machine settings and private
hash manifests. Package archive SHA256:
`1010c996a81e8475e6f458c04496c5d89cbde082ebf9a51b2a3938c6cca03a26`;
SQLite backup SHA256:
`79c47681433b81d7efd9e523781de4195e4be3c4dd91600674db99f36511c328`,
integrity `ok`.

`rollback-package.ps1` parsed with zero errors, SHA256
`DA605FD5E67759E4F8FFF71D7611F62CB0D5B3675D6A3FBDBF6920D33C457DA5`.
It was **not executed**. It refuses active claims or active browser
credentials: old code lacks the browser approval-authority restriction, so
rollback after later pairing requires separately coordinated operator action.
Otherwise it backs up current DB, restores/verifies the old package and
deployment revision, then starts only Home, preserving live DB/configuration
and durable pairing. It never automatically revokes user grants.

Home readiness/revision/endpoint were sent privately to the Ops owner.
Ops deployment, appliance pairing, Profile approvals, real browser turn
and browser lifecycle/WK acceptance remain **unperformed** behind the
reported TUI tagged-release prerequisite. No deployment or evidence PR was
merged and no CI watch was started.
