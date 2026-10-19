# HOME-NW-17-room-device-admission validation

Date: 2026-10-19

## Provenance and scope

- Isolated worktree: `.worktrees/puck-room-pairing`.
- Implementation branch: `feat/puck-room-device-pairing`.
- Base: `b2f31c6355271b057201ce02c1ff8b39ca90e56c` from PR109's
  `spec/puck-home-room-device`. The original spec worktree/branch and main
  checkout were not edited.
- Existing `puck` endpoint identity is retained. Only its
  `service_private_file` mode receives the new one-Room, wake-only,
  nonempty-mapping constraints. Existing platform-store Puck behavior,
  browser storage/client grants, personal clients and Touch are unchanged.
- No wake admission/arbitration/evidence, claim lifecycle, firmware or consumer
  implementation changes were made.

## Automated checks observed

Supported environment: CPython 3.14.8, isolated worktree virtual environment.

```sh
uv run --extra dev python -m pytest -q \
  tests/test_room_device_enrollment.py tests/test_credentials.py \
  tests/test_credentials_api.py tests/test_browser_admission.py \
  tests/test_pairing_page.py tests/test_client_grants.py tests/test_touch_claims.py
```

Observed: **179 passed in 2.97 seconds**.

```sh
uvx --from ruff ruff check src tests
uvx --from ruff ruff format --check src tests
```

Observed: **All checks passed; 83 files already formatted**.

```sh
uv run --extra dev python -m pytest -q
```

Observed: **1,352 passed, 26 skipped, 7 warnings in 28.90 seconds**. Warnings
were WebSocket `connect()` context-manager deprecations in existing production
bridge/proxy/transport tests; no new warning or runtime behavior was suppressed.

After independent review, only test coverage changed. Targeted verification:

```sh
.venv/bin/python -m pytest -q tests/test_room_device_enrollment.py
uvx --from ruff ruff check tests/test_room_device_enrollment.py
uvx --from ruff ruff format --check tests/test_room_device_enrollment.py
```

Observed: **34 passed in 0.77 seconds; All checks passed; 1 file already
formatted**. Added configured Room/mapping/evidence denials, nonempty/empty
`all_current_profiles` snapshot boundaries, platform-store multi-Room
compatibility, complete issued-secret redaction and approved-request SQLite
reopen coverage. The full-suite result above precedes these test-only review
additions; final full verification is parent-owned.

Permanent `tests/test_room_device_enrollment.py` coverage includes request
narrowing, unrelated types, invalid storage, approval scope/Room/mapping
boundaries, unavailable configuration, domain approval guard and real HTTP
transport. The HTTP case covers pending/unauthenticated/self-approval denial,
confirmation and secret-safe listing, changed storage/type/proof rejection,
one-time consume, exact returned scope, unconfigured-device claim denial,
revision-checked Device publication/discovery, browser credential isolation,
early renewal denial, successful in-window renewal, idempotent renewal retry,
rotation, revoked/old credential rejection and same-endpoint re-enrollment.
The existing synthetic wake fixture is used only to assert scope/configuration
and missing-evidence denials; it is not calibrated proximity evidence or a live
wake exercise.

## Final independent verification and publication signoff

The independent final verification reviewer used the existing isolated
CPython **3.14.8** environment after all test-only patches:

```sh
.venv/bin/python -m pytest -q tests/test_room_device_enrollment.py \
  tests/test_credentials.py tests/test_credentials_api.py \
  tests/test_browser_admission.py tests/test_pairing_page.py \
  tests/test_client_grants.py tests/test_touch_claims.py
.venv/bin/python -m pytest -q
uvx --from ruff ruff check src tests
uvx --from ruff ruff format --check src tests
```

Observed: **183 focused tests passed in 2.44 seconds; 1,356 full-suite tests
passed, 26 skipped, 7 existing warnings in 28.00 seconds; All checks passed;
83 files already formatted**. No warning was suppressed.

The reviewer independently ran production `load_settings`/`create_runtime`
and ephemeral loopback HTTP with random temporary secrets and actual SQLite.
Observed exit **0**: own Puck request, pending/unauthenticated denial,
confirmation listing, administrator approval, one-time exact-scope consume,
revision-checked generated Device publication, stale-409 rejection, authorized
mapping discovery, revoke and revoked configuration/renewal denial. This
independent smoke used no mocks, claim requests, acoustic scores, audio,
upstream connections, live hosts or live secrets.

All three required independent review layers completed before author triage.
Five targeted coverage patches were applied; the two snapshot/evidence
findings are addressed by including this artifact. The specification records
every finding and evidence. Parent granted code/docs publication signoff on
2026-10-19 after reading the actual final verification output.

## Separate production-runtime HTTP exercise

A standalone Python process imported production `load_settings` and
`create_runtime`, started the real HTTP server on an ephemeral loopback port,
and used `http.client.HTTPConnection` for all enrollment/configuration/lifecycle
operations. It used randomly generated temporary admin/root secrets, paired
SQLite storage and a private temporary directory; no fake upstream, test
fixture application, live credential or remote Home was used.

Observed exit status: **0**, with these successful operations:

1. Publish an isolated Room, available Profile and active Wake Mapping.
2. Create offer; submit `puck`/private-file request; verify confirmation listing
   and absence of the enrollment secret in that listing.
3. Deny pending consume and unauthenticated approval; approve exact scope.
4. Consume once, validate exact authority, write credential material directly to
   a temporary mode-0600 file; deny second consume.
5. Publish generated Device identity at the current configuration revision;
   reject the stale revision and discover only the authorized mapping.
6. Reject early ordinary renewal using real time; rotate administratively and
   recover the same replacement through the existing renewal retry endpoint.
7. Reject the replaced credential for normal requests; authenticate replacement;
   revoke and reject both replacement and old-token renewal retry.
8. Confirm temporary JSONL diagnostics contain none of the enrollment/device
   secrets; shut down the runtime and remove temporary state.

Successful final-window ordinary renewal is additionally exercised over real
HTTP by the permanent test using an injected credential clock. This distinction
avoids pretending that a 90-day credential aged during the standalone smoke.

## Review and deployment boundary

Home implementation PR109 merged at reviewed source
`4c3f62122fbb771f5ee20bacba8566274194143e`; live deployment and its own endpoint
enrollment complete this Home story. The formal story/spec statuses are done.
The follow-up publication preserves only reviewed recovery tooling and actual
live evidence, not a duplicate implementation or broader Puck acceptance.

**Home is deployed and its own Living Room Puck endpoint is enrolled.** After the
independent review and private-copy rehearsal, parent approved the package-only
cutover. Custodian completed exactly one reviewed swap at host time 08:59:32
on 2026-10-19, deploying source
`4c3f62122fbb771f5ee20bacba8566274194143e` / wheel version 0.2.0.
The previous installed source was verified at
`3a0eec0b9739524e2a8c54fd5a3308b3c3abb90b`.

The initial four apparent mismatches were against a **pre-upgrade** backup
manifest, not the installed version's manifest. All 35 installed Python files
are accounted for: the other 31 match that old manifest, and the four changed
files (`api/application.py`, `api/pairing.py`, `api/pairing_page.py`,
`domain/credentials.py`) exactly match the committed browser-admission upgrade
at the verified revision. The signed provenance record is stale; it does not
invalidate this source-hash verification. There is no unresolved source drift.

The existing October-8 rollback kit restores the earlier pre-upgrade package
and refuses later browser credentials, so it was not used. Custodian prepared
a fresh current-package and online-SQLite recovery point, task/settings and
secret-hash capture, coherent old wheel and reviewed package-only swap/rollback
script. The final private-copy rehearsal propagated driver/SSH exit 0 before
parent approved cutover. Recovery retains the live database and credentials.
Merging PR109 was not a deployment prerequisite and no merge is authorized.

An authenticated read-only Home API snapshot found revision 97, sole Room
`kitchen`, sole active mapping `hey-missy` targeting `amanda`, and no Puck
Device. That snapshot alone did not establish physical assignment. The owner
subsequently explicitly approved **Living Room** and the selected mapping
`hey-missy` targeting Amanda. Add a new Room through the normal revision-checked
configuration API; do not rename Kitchen or move the existing iPhone. The
Puck must receive its own one-Room/wake-only credential with that selected
mapping, not a browser credential or an expanded all-current grant.

Only the designated custodian performed the Home package-only deployment.
This implementation/review worker performed no live mutations. Existing
browser pairing/state, secret values, Standard token, Puck bridge service,
firmware and diagnostics configuration were not changed by the cutover.

The consumer runbook `hermes-relay-tui/docs/puck-ops-deployment.md` (on its
`p-home-01-deploy-tooling` worktree) specifies a `hermes-puck`-owned 0700 state
directory and mode-0600 `bridge.env`, with startup owner/mode checks. It also
requires approved pairing and a P4 trusted provider before household rollout.
Home's credential lifecycle alone does not implement bridge-side automatic
renewal, credential application (3-P-2), re-enrollment UI (3-P-6), calibrated
proximity, or first-connect claim recovery. Those boundaries remain explicit;
this change does not fabricate their acceptance.

The P4 calibrated provider is a separate runtime-admission blocker, not a
prerequisite for Home credential enrollment itself. No calibrated evidence,
claim-recovery behavior or consumer-renewal implementation is added here.

## Exact reviewed wheel preparation

For the owner-approved live pairing continuation, source revision
`4c3f62122fbb771f5ee20bacba8566274194143e` was exported using `git archive`
into an isolated temporary build directory. The successful CI run exposed no
downloadable wheel artifacts, so `uv build --wheel --no-sources` built that
exact committed tree. No version, dependency lock, or product source changed.

- Artifact: `hermes_relay_home-0.2.0-py3-none-any.whl`, 203,485 bytes.
- SHA-256: `91a3a8f3ff01cfa6c3a7545e7948f5c7d2cb0ba3a4bf75e34320d694afdfca96`.
- All 35 Python source members byte-match the reviewed Git tree, with no
  extra or missing Python files.
- Purelib `py3-none-any`, Python `>=3.14,<3.15`; console entrypoint remains
  `hermes-home = hermes_home.runtime:main`. Runtime dependency bounds are
  unchanged from the current installed source.
- Static compatibility inspection against live source
  `3a0eec0b9739524e2a8c54fd5a3308b3c3abb90b` found no storage/schema/migration
  source changes. The build includes the previously merged main changes in
  addition to the pairing guard; it is not represented as a 22-line-only
  cutover from the older deployed source.
- Existing configuration remains valid when optional `client_devices` is
  absent. Do not introduce a nonempty `client_devices` field during this
  room-only operation: the old recovery build cannot parse that new field.
  Custodian subsequently completed dependency checks and the private-current-DB
  new/old compatibility rehearsal successfully before approved cutover.

The supported minimal path is package-only replacement, not rerunning the
installer. Preserve existing dependency versions, task, runner, bindings,
ACLs, firewall, Prometheus and secret files. Rollback restores the freshly
preserved current package and deployment revision while retaining the live
database/credentials; it must not overwrite them with a pre-enrollment backup.
The exact reviewed/executed operator helper is preserved in
[`deploy/windows/home-package-swap.ps1`](../../deploy/windows/home-package-swap.ps1).
This is a bounded package-only procedure, not a generic updater. Future
wheel/schema/dependency compatibility needs fresh review. Live production
rollback was **not** executed: old-package compatibility was exercised only
against the post-enrollment private database copy. Custodian alone performed
live mutations; this implementation/review worker did not.

## Recovery-plan review checkpoint

Custodian reported a fresh private backup at
`C:\ProgramData\HermesHome\backups\home-3a0eec0-pre-pr109-20261019-081824\`.
Its online SQLite backup passed `integrity_check`, contains nine tables and
reported zero active claims. The preserved old wheel's 35 Python members
match the current installed source, including top-level
`hermes_home_diagnostics.py`; it is the coherent rollback artifact rather than
the auxiliary directory ZIP, which omits that top-level helper. Installed
cryptography 50.0.1, segno 1.6.6 and websockets 17.1 satisfy the reviewed wheel's
unchanged requirements. Custodian verified the incoming reviewed wheel hash.

Independent review supports package-only rollback via the preserved old wheel
with `--force-reinstall --no-deps`, retaining the live database and credentials.
Rollback must also restore the previous deployment-revision environment value
after a cutover changes it, leave all other settings/secrets untouched, take an
online pre-rollback backup, stop/wait for Home processes and both listeners,
and verify coherent package hashes before restarting. Independent review of
the prepared swap/rollback and private-copy rehearsal scripts completed;
final rehearsal driver/SSH exit 0 preceded parent cutover approval. The
subsequent actual deployment evidence is recorded below.

The stale signed file is
`C:\ProgramData\HermesHome\provenance\deployment.json`. Custodian found no
references from the live runner; reviewed runtime/install/scripts likewise
have no supported reader or signing generator for that file. Preserve its
original bytes and hash in the fresh backup; do not hand-edit its signed claim
or fabricate a new signature. It is a historical operations artifact, not this
runtime's client-facing loaded-source provenance.

Runtime diagnostics instead compute an artifact hash from all package Python
sources plus the top-level diagnostics module, read version 0.2.0 from installed
distribution metadata, and explicitly report `provenance_status: unverified`
with no configured source revision. The unsigned exact-build receipt records
the wheel/source comparison without claiming cryptographic attestation. Any
deployment-revision environment change is an operations receipt, not a
runtime-verified signature. Wheel-based replacement/recovery preserves coherent
module bytes and distribution metadata together.

Reviewed private operations-script hashes:

- Package swap/rollback:
  `0103c3ea233e7abf55b9e661c5ada3e54ff43c96c495c3957a8bb496f38cece1`.
- Isolated compatibility rehearsal:
  `2fa9790c20e8ba9b1b959098c2c0bd989af877b8f3b28de8bb0ee209630fc526`.

Review corrected missing status/scope assertions, module/helper/distribution
origin/version checks, both-listener/authenticated startup verification,
private backup ACL preflight, canonical wheel naming/dry-run preflight,
PowerShell child-output/exit-code separation and fixture-isolation guards
before destructive reset. Custodian reported PowerShell parse zero errors
and scalar helper returns 0/3. The independent reviewer read the final
scripts and matched the hashes; it did not execute live operations.

## Actual Home package-only cutover

Custodian's successful run began at **2026-10-19 08:59:32 host time**, using
the unchanged reviewed swap script and exact reviewed wheel above.
Nonsecret durable transcript:
`C:\ProgramData\HermesHome\backups\home-3a0eec0-pre-pr109-20261019-081824\cutover-20261019-085932.log`.
Fresh private online pre-swap DB backup directory:
`C:\ProgramData\HermesHome\backups\home-swap-4c3f621-20261019-085932`.

Reported actual transcript/readback facts, relayed and checked by parent:

- Dry-run and package-only install: 0.1.0 → 0.2.0, no dependency changes.
- Online SQLite backup `integrity_check`: `ok`.
- Installed wheel `RECORD`: **zero mismatches**.
- Deployment-revision environment:
  `4c3f62122fbb771f5ee20bacba8566274194143e`.
- Both existing listeners 8780 and 8766 ready; authenticated configuration
  readback revision **97**; transcript result **SUCCESS**.
- New Home processes **36548 / 39964**, started **08:59:34**.
- All **11 secret-file hashes unchanged**.
- Existing ops browser remained active at PID **229999**; its pairing-file
  SHA-256 remained
  `b00c26c6d64a347dc086a5b93625caae0afbb5da080ca1158043347e7481b746`.
  Browser web port 8875 returned HTTP 200 with no error journal; zero active
  Home claims.
- Parent directly read the existing HTTPS `/pair` route and received actual
  Hermes Home Pairing sign-in HTML.

This was **one actual cutover**, not repeated successful swaps. The 08:50 WMI
launch quoting attempt was a no-op; the 08:54 PowerShell/native-stderr
redirection attempt failed in preflight before `Stop-ScheduledTask`. Neither
changed the live Home process/package. Only the 08:59 run executed the
reviewed swap. `/api/v1/health` returning 404 is not a regression: that endpoint
does not exist and was not used as a readiness requirement.

The preceding deployment checkpoint was recorded before enrollment finished.
The completed enrollment receipt below supersedes that temporary status; no
audio/voice acceptance is inferred. This worker remains local evidence-only.

## Actual Living Room Puck enrollment

Custodian reported successful live enrollment on 2026-10-19, host time CDT:
configuration revision **97 → 98** added `living-room` without changing Kitchen,
iPhone, Profiles or existing mappings; generated Device publication activated
revision **99**. Device ID `c9dd86cfa9634330bf412e560969b7a9`, stable endpoint ID
`ops-hermes-puck-bridge`, generation **1**, approximately 90-day expiry.
Exact scope: Room `living-room`, capability `wake_claim`, selected mapping
`hey-missy` → Amanda. No client grants or fabricated conversation handle.

The ops `hermes-puck` private directory is mode 0700; `home-enrollment.json`
and `bridge.env` are both owned by `hermes-puck`, mode 0600. `bridge.env` contains
only `HOME_DEVICE_ID`, `HOME_DEVICE_CREDENTIAL`, `HOME_WAKE_MAPPING_ID`:
no handle and no Puck token. No secret values are published here.
TLS-authenticated Device configuration returned only `hey-missy`; missing/bad
credentials returned 401. Device-authenticated approval returned 403.
The published admin-configuration route returned 502 because it is not exposed;
that is route isolation evidence, not a claim of a direct admin API 401 test.
Existing browser credential remained active and browser PID 229999 unchanged.

Admin enrollment listings expose requested authority under
`requested_scope.{rooms,capabilities,wake_mappings}`, not `requested_rooms`.
The old recovery wheel SHA-256 is
`4b5553b25fb9790009106fac6a73efae6aff75935c7f181f7b12278498559a80`.
Recovery uses the preserved stage-3a0eec0 wheel and the reviewed helper with
the full previous revision, retaining the live database and new credentials.
No live rollback, bridge start, calibrated provider bypass, audio/voice
acceptance, firmware flash or hardware reboot is established by this receipt.

For future operator transcript capture, avoid wrapping native `uv` stderr in
PowerShell `2>&1` / `*>&1` under `$ErrorActionPreference='Stop'`: routine native
progress output caused the 08:54 preflight to fail before stopping Home.
The direct approved script with transcript capture succeeded at 08:59.
The private `bridge.env` remains intentionally partial: no conversation handle
or `PUCK_DEVICE_TOKEN`, and no bridge service was started. P4 calibrated
provider, consumer automatic renewal and supported claim/handle recovery remain
separate gates. Hardware/audio tests belong to the separate TUI workstream;
no audio event or voice acceptance is recorded here.
