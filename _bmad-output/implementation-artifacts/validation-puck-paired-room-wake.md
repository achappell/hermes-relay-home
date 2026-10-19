# Paired-room wake — local Home evidence and deployment gates

Date: 2026-10-19. Owner: existing Home NW-05 policy/grants, coordinated with TUI P4/3-P-5 consumer. This records the new owner-approved admission mode, not a second enrollment or proof of completed physical voice acceptance.

## Source and approved contract

- Isolated branch `feat/puck-paired-room-wake`, based on remote main `3b3f4064004749dce7d3ee409e5bb0fc74a35be2`.
- [Product spec](../specs/spec-puck-paired-room-wake/SPEC.md), [shared wire/recovery contract](../specs/spec-puck-paired-room-wake/admission-contract.md), [implementation plan](spec-puck-paired-room-wake.md).
- Amanda's explicit 2026-10-19 decision was recorded first in the private Hermes Home project hub: this paired Living Room Puck may admit Hey Missy without proximity qualification, accepting outside-Room hearing. This is not a general provider bypass or multi-device ranking permission.
- Intended existing binding: Device `c9dd86cfa9634330bf412e560969b7a9`, endpoint `ops-hermes-puck-bridge`, Room `living-room`, `hey-missy` → Amanda. These are operator configuration, not hard-coded authorization exceptions. The last observed deployed configuration was 99; deployment must refetch, not assume that revision.
- Paired grants require explicit policy, current Device scope/revision and household singleton eligibility for the same mapping. Another currently authorized configured same-mapping Device denies the mode, including credential activation without a revision change. Different-mapping strict Devices remain unchanged.

## Exercised local evidence

Python 3.14.8. All inputs, credentials and SQLite state below are temporary isolated local material, not production credentials.

1. `uv run --frozen --python 3.14 --extra dev pytest tests/test_paired_room_policy.py tests/test_paired_room_claim_store.py tests/test_paired_room_api.py -q`: **137 passed in 1.49s**.
   - Exact/omitted/malformed policy, active Profile/mapping and credential scope; cross-Room same-mapping and unrelated-mapping cases.
   - Durable paired kind, same-config-revision competitor activation after grant, revision-race denial, credential revocation and existing strict Room busy/cross-kind claim-ID uniqueness.
   - Idempotent known own paired close, foreign/unknown/non-paired/out-of-scope non-disclosure, current identity rejection and same-SQLite/root restart with no replay.
   - Actual production construction HTTP regression, not only an injected application.
2. Standalone `/tmp/hermes-home-paired-runtime-smoke.py`, run with the frozen Home environment: **exit 0**, actual `load_settings`/`create_runtime`, production durable claim store and real loopback HTTP.
   - Genuine enrollment offer/request/admin approval/one consume, exact discovery, unapproved/wrong-mapping `403`, stale `409`, fresh generated grant and duplicate `409`.
   - Foreign/unknown close `404` is not retirement proof. Runtime was closed/reopened with the same SQLite and root secret: claim kind/identity persisted; the existing restart boundary retired active work rather than replaying it. Exact known own cold-close receipt was idempotent; duplicate ID remained rejected after reopen.
   - A new fresh claim succeeded, normal policy removal retired it, owned close remained possible, unapproved admission became `403`, and revoked identity became `401` for discovery and retirement.
   - The configured loopback Standard address was deliberately never contacted; no upstream, audio, hardware or live host is claimed.
3. Identity owner independently exercised its actual Puck HTTP factory against HomeApplication + real server + SQLite credentials/claims: **seven scenarios passed**, observed receipt `hermes-puck-paired-api-smoke-o2sjowb9/observed.json` (session artifact 3399). This proved genuine consumer enrollment/discovery/paired grant, two idempotent owned closes, unknown non-proof without credential latch, duplicate rejection, strict calibration denial after policy removal, and known close after removal. It did **not** exercise Standard/audio/physical acceptance.

Initial local harness assumptions about the required `resolve(handle, device_id)` signature, credential `revoke` method and Standard `/api/ws` URL were corrected; no production workaround or relaxed assertion was used.

## Exact next gated cutover/recovery procedure

No operation in this section has been executed for the new paired-room mode. Independent spec/code review, exact source/artifact acceptance and parent approval remain required before any live mutation.

1. Preserve the currently deployed Home source/package, environment and every secret/provenance file, plus a fresh online SQLite backup and integrity receipt. Preserve existing ops enrollment/state/credential and browser config/process state. Use a reviewed compatible package-only swap procedure, no installer, dependency refresh, credential recreation or database restore. The prior reviewed helper's historical success does not prove compatibility of a new wheel.
2. Build the exact reviewed source; verify wheel RECORD, all Home modules plus top-level diagnostics module and metadata, dependency/schema compatibility and source revision. Rehearse the new/old package transitions against a private copied current database; prove old validation after policy removal and paired retirement. Do not rewrite signed historical provenance. Diagnostics/build claims must identify the actual installed source.
3. Start the new Home binary first with unchanged configuration and credentials. Preserve current Kitchen/iPhone/browser/Profile/mapping state. Do not publish paired policy while the bridge/token/service is unready.
4. Install the exact reviewed TUI bridge artifact and persistent private service state only after its own source/recovery approval. Use its own existing Home Device credential, not browser or Standard tokens. Transfer the distinct Puck token only through encrypted stdin/private files; private state directory `0700`, environment `0600`, no secrets in unit/argv/output. No operator-supplied conversation handle or bootstrap mapping is authority. Verify token/source match privately and the existing response-status authentication boundary without wake/audio actions.
5. Refetch current Home configuration, verify exact intended Device/endpoint/Room/mapping scope and same-mapping singleton, then publish only the explicit policy through the normal revision-checked admin API. Refetch/reconcile on conflict, never overwrite unrelated changes. Consumer discovers it, obtains a fresh per-wake grant, completes current ready validation and only then captures. Firmware actor alone owns physical Puck actions.
6. Before downgrading to the previous `4c3f621` binary, **under the new binary** remove every `wake_admission` field through normal revision-checked configuration and retire affected paired claims. Prove no active paired authority remains and old-code snapshot validation succeeds on the rehearsed database. The old binary neither validates the new field nor enforces its persisted claim-kind authorization; blindly installing it while policy/paired authority remains is not a safe downgrade.
7. Only after that compatibility proof may the reviewed old wheel be force-reinstalled with `--no-deps`, preserving the **current** database, root secret, endpoint credentials and browser state. Never rewind to a pre-enrollment database or old secret snapshot. If the new binary cannot perform safe policy removal/retirement, hold fail-closed and use reviewed forward repair rather than inventing a direct DB edit or blind old-code rollback.

The prior Home/ops actor explicitly handed off ownership and acknowledged no child/queued operations. This worker is the sole Home/ops coordinator/executor for this slice **after** the review/source/recovery gate; the firmware actor retains Puck custody. No additional live actor may mutate concurrently.

## Unexercised acceptance

Actual configured Standard gateway capability/response-audio format and a real authorized Amanda text/audio turn are still unproven for this slice. Read-only planning must identify the supported transport; a bounded approved operation must observe actual sample rate/channels/width/endianness, frames/bytes and terminal result rather than synthesize capabilities. Full acceptance requires the later awake-owner wake → capture → STT → Home → unmodified Standard Amanda → TTS → actual Puck fetch/drain and owner-confirmed coherent audible answer. Local HTTP success does not replace that evidence.

## Integrated regression checkpoint

After all production slices landed, `uv run --frozen --python 3.14 --extra dev pytest -q` completed with **1493 passed, 26 skipped, 7 existing WebSocket deprecation warnings in 30.53s** (observed artifact 3424). `uvx ruff check src tests` passed; `uvx ruff format --check src tests` reported **87 files already formatted**. The standalone smoke's observed exit-0 stdout is saved as nonsecret local receipt `/tmp/hermes-home-paired-runtime-smoke-receipt-20261019.json`; the temporary runner is not a deployment artifact.

## Final independent review and post-fix gate

One context-free full-capability review covered blind, changed-path edge and verification lenses. It found a real high-impact omission: paired resolution used a silent row-close helper, losing registered live bridge revocation callbacks. The patch uses the existing notifying closure after releasing the resolution lock. Three live bridge cases now prove same-revision competitor/checker-revision/snapshot-revision invalidation sends one upstream interrupt, tears down the binding, invokes callbacks outside the lock and does not notify again on subsequent owned close. The original reviewer independently confirmed the finding resolved. The firmware actor's independent full spec/code/recovery review found no additional substantiated issue. An earlier specialist's nonexistent in-memory-resolver finding was rejected rather than implemented.

Final observed command chain (artifact 3515):

- Focused new policy/store/API suites: **143 passed in 1.76s**.
- Full suite: **1499 passed, 26 skipped, 8 warnings in 29.24s**. Seven are the known WebSocket deprecations. One additional `PytestUnhandledThreadExceptionWarning` reports `Bad file descriptor` in WebSocket `serve_forever` startup during existing `test_create_runtime_recovers_paired_credentials_after_restart`; the runtime and that test were not changed. This was not suppressed or retried away. Its pre-existing startup/teardown race attribution is an inference from the unchanged path, not a new claimed fix.
- Ruff check passed; format check: **87 files already formatted**.
- Standalone real production runtime HTTP/reopen smoke: **exit 0 again after the notification fix**.
- Actual exact-old-wheel database compatibility rehearsal: **exit 0 after the fix**, receipt `/tmp/hermes-home-paired-rollback-fznabncz/rollback-receipt.json`.

The recovery rehearsal built the exact `4c3f62122fbb771f5ee20bacba8566274194143e` source wheel (SHA-256 `f96da7f5ce312dc92ff0aa0ab8c29b7c2eb6a344511e0e4c24684f06154827e7`, all 39 hashed RECORD entries verified, including top-level diagnostics and metadata). **Old wheel code** created an isolated database and its own credential; new code opened that same state, published paired policy and issued a grant. Exact old validation rejected the still-present new field. New code then removed policy by normal revision-checked API and retired paired work. Exact old wheel code reopened the **same current database/root/credential**, authenticated the unchanged credential, read the new revision, preserved the closed paired kind/reason, found no active paired authority or replay, and verified unchanged schema and SQLite integrity. No DB rewind or credential restore occurred.

This is a real package-code/database compatibility fixture, not an executed Windows reinstall, production rollback or live database rehearsal. The preserved reviewed installed 4c wheel remains the preferred actual rollback artifact; this locally rebuilt wheel proves source/schema compatibility. Existing Windows helper parameters accept a reviewed wheel/hash/full revision, but its historical 3a rollback example/error text is not authority for this slice: only the current-preserving 4c recovery plan above is approved, with policy removal first. No new updater or generic recovery framework was introduced.
