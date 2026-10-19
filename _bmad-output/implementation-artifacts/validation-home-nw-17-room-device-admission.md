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

The local Home implementation and review are complete. Parent authorized
normal fast-forward publication to existing PR109, not a duplicate PR.
The story tracker remains `review` until integration; spec `done` describes
local implementation completion only.

**Live Puck enrollment is not complete.** The custodian successfully used raw
SSH/PowerShell against CaticornQueen and found the Home SYSTEM scheduled task
running. Transport access is not the blocker. The installed package points to
the 2026-10-08 wheel with deployment revision
`3a0eec0b9739524e2a8c54fd5a3308b3c3abb90b`, but four source files differ from
that install manifest and the signed provenance names another baseline.
Identification of that drift is still being completed; no exact current-code
provenance is claimed.

The existing October-8 rollback kit restores the old package rather than the
current drifted runtime and refuses a state containing later browser
credentials. A safe reviewed cutover therefore requires fresh current-package
preservation and an online SQLite backup, plus task/settings/secret-hash
capture and a current-state-compatible recovery plan. These are distinct from
the already completed local code review and require parent deployment signoff.
Merging PR109 is not a mechanical prerequisite and no merge is authorized.

An authenticated read-only Home API snapshot found revision 97, sole Room
`kitchen`, sole active mapping `hey-missy` targeting `amanda`, and no Puck
Device. Neither those existing values, firmware wake defaults nor runbook
examples establish approved physical-Puck assignment. The exact remaining
household decision is **which Room and which wake mapping(s) this Puck may
use**; do not infer Kitchen from the existing iPhone gate.

No live Home deployment/enrollment or ops credential installation was
performed. No browser pairing/state, Standard token, running Puck bridge,
firmware, diagnostics switch, microphone or hardware session was touched.

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
