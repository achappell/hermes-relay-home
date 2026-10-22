# Paired-room wake — Home implementation and owner-accepted physical evidence

Date: 2026-10-19. Owner: existing Home NW-05 policy/grants, coordinated with TUI P4/3-P-5 consumer. This records implementation, controlled transport proof and the first owner-accepted paired-room physical happy path, not a second enrollment or blanket migration/edge-matrix acceptance.

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

## Approved cutover and current-preserving recovery procedure

The parent accepted the independent review, exact replacement artifacts, compatibility fixture and recovery plan. Steps 1–5 were then executed on 2026-10-19 after both replacement-source CI runs succeeded, as recorded below. Steps 6–7 remain the required recovery procedure, not a claim that a production downgrade was exercised.

1. Preserve the currently deployed Home source/package, environment and every secret/provenance file, plus a fresh online SQLite backup and integrity receipt. Preserve existing ops enrollment/state/credential and browser config/process state. Use a reviewed compatible package-only swap procedure, no installer, dependency refresh, credential recreation or database restore. The prior reviewed helper's historical success does not prove compatibility of a new wheel.
2. Build the exact reviewed source; verify wheel RECORD, all Home modules plus top-level diagnostics module and metadata, dependency/schema compatibility and source revision. Rehearse the new/old package transitions against a private copied current database; prove old validation after policy removal and paired retirement. Do not rewrite signed historical provenance. Diagnostics/build claims must identify the actual installed source.
3. Start the new Home binary first with unchanged configuration and credentials. Preserve current Kitchen/iPhone/browser/Profile/mapping state. Do not publish paired policy while the bridge/token/service is unready.
4. Install the exact reviewed TUI bridge artifact and persistent private service state only after its own source/recovery approval. Use its own existing Home Device credential, not browser or Standard tokens. Transfer the distinct Puck token only through encrypted stdin/private files; private state directory `0700`, environment `0600`, no secrets in unit/argv/output. No operator-supplied conversation handle or bootstrap mapping is authority. Verify token/source match privately and the existing response-status authentication boundary without wake/audio actions.
5. Refetch current Home configuration, verify exact intended Device/endpoint/Room/mapping scope and same-mapping singleton, then publish only the explicit policy through the normal revision-checked admin API. Refetch/reconcile on conflict, never overwrite unrelated changes. Consumer discovers it, obtains a fresh per-wake grant, completes current ready validation and only then captures. Firmware actor alone owns physical Puck actions.
6. Before downgrading to the previous `4c3f621` binary, **under the new binary** remove every `wake_admission` field through normal revision-checked configuration and retire affected paired claims. Prove no active paired authority remains and old-code snapshot validation succeeds on the rehearsed database. The old binary neither validates the new field nor enforces its persisted claim-kind authorization; blindly installing it while policy/paired authority remains is not a safe downgrade.
7. Only after that compatibility proof may the reviewed old wheel be force-reinstalled with `--no-deps`, preserving the **current** database, root secret, endpoint credentials and browser state. Never rewind to a pre-enrollment database or old secret snapshot. If the new binary cannot perform safe policy removal/retirement, hold fail-closed and use reviewed forward repair rather than inventing a direct DB edit or blind old-code rollback.

The prior Home/ops actor explicitly handed off ownership and acknowledged no child/queued operations. This worker is the sole Home/ops coordinator/executor for this slice **after** the review/source/recovery gate; the firmware actor retains Puck custody. No additional live actor may mutate concurrently.

## Acceptance result and explicit limits

Amanda explicitly accepted the real Living Room paired-room happy path on 2026-10-19 after hearing a reasonable spoken answer: wake → Home authority before capture → upload → nonempty STT → unmodified Standard Amanda → TTS → Puck playback. The controlled probe below remains a separate earlier transport proof; the later physical windows, dependency correction, owner confirmation and authoritative post-turn cleanup are recorded at the end. This is not a claim of repeated-wake, follow-up, interruption/reconnect/revocation/shutdown-matrix, automatic-renewal or generic strict-provider acceptance.

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

## Upstream reconciliation after PR110 merged

Amanda reported PR111 conflicted after main advanced to `f6483c7` (merged PR110's reviewed Windows helper and actual Living Room enrollment evidence). `origin/main` was merged normally into the isolated feature branch: the sole conflict was two independent additive changelog bullets, both preserved. Upstream's seven files are recovery/evidence/status documentation and the already reviewed helper. `git diff --exit-code 2a2ab04a2276ce26d38b6b889d515b10abe30bb6 -- src tests` exited zero: **runtime and test source are unchanged** by reconciliation, so the existing independent code review and runtime/old-wheel compatibility proof remain applicable. No rebase, force push, PR merge or behavior change occurred.

Required post-merge checks passed again: **143 focused in 1.81s; 1499 full passed, 26 skipped, 7 known deprecation warnings in 29.15s** (artifact 3561); Ruff check passed and format check reported 87 files. This rerun was explicitly requested for the merge, not to erase the earlier separately recorded startup/close warning. The new merge SHA and rebuilt wheel supersede `2a2ab04`/`f0da...` for the deployment gate. TUI's new post-reconciliation PR254 source/wheel also supersedes `d5cb6c5`/`93dad...`; neither old artifact may be deployed. Both new exact-source CI runs must succeed before the unchanged approved live sequence.

## Actual reviewed deployment and controlled Standard speech proof

Both exact replacement-source CI runs completed successfully before any cutover:

| Component | Initial deployed immutable source | Exact wheel SHA-256 | Successful CI |
| --- | --- | --- | --- |
| Home 0.2.0 | `831e55e730ab49f3774ecc55a5bd111c993d2f1a` | `bcc6ea125aff1dd55de22b3f3a3a55a97cf7aeb16229056b684ff5215b9c7200` | [39285501187](https://github.com/achappell/hermes-relay-home/actions/runs/39285501187), completed 16:32:55 UTC |
| TUI bridge 0.13.1 | `acb1da678f125e0b90672c6248b04016fa8f834c` | `596424ef7df888b0039ba0626b39e208c81a8fe0ad86f27bfa78870ee671b1b8` | [39286682815](https://github.com/achappell/hermes-relay-tui/actions/runs/39286682815), completed 16:42:58 UTC |

The deployed Home runtime/test bytes are unchanged from independently reviewed `2a2ab04`; the normal main merge and final Serve-allowlist documentation produced the replacement source identity. All 36 packaged Python files (including top-level diagnostics), plus metadata, were checked against the exact source/wheel. The installed bridge matched all 51 source modules and all 69 hashed wheel RECORD entries (70 total rows including RECORD itself).

### Current-preserving Home swap and narrow Serve publication

- The fresh private backup is `C:\ProgramData\HermesHome\backups\home-paired-room-pre-831e55e-20261019-115056`. It preserves the current installed package, environment, all secrets, unchanged configuration, original Serve configuration, and a running-service online SQLite backup with integrity `ok` and zero active claims. All 39 hashed entries of the prior installed `4c3f621` package matched its preserved original wheel (`91a3a8f3ff01cfa6c3a7545e7948f5c7d2cb0ba3a4bf75e34320d694afdfca96`).
- Precision on historical provenance: the unused signed `provenance/deployment.json` was retained unchanged in place, not copied into that fresh directory. Its existing protected copy `backups\home-3a0eec0-pre-pr109-20261019-081824\provenance-deployment.json` was verified read-only to match the current file exactly: SHA-256 `22acdf407e64de396e24dc6bb503046533175b223292ceec22f70378b7c6d250`. Neither signature nor content was rewritten. The recovery record includes that matching prior private copy rather than claiming a new copy was made.
- The existing reviewed [package-only helper](../../deploy/windows/home-package-swap.ps1), SHA-256 `0103c3ea233e7abf55b9e661c5ada3e54ff43c96c495c3957a8bb496f38cece1`, ran **once**, at host-local 11:52:29. Its transcript is `cutover-20261019-115229.log` in that backup. A second current online database backup passed integrity; the exact new wheel installed with `--force-reinstall --no-deps`; installed RECORD mismatches were zero; the full machine deployment revision became `831e55e...`; both 8780 and 8766 listened and authenticated configuration remained revision 99.
- Post-swap semantic/configuration checks confirmed unchanged Kitchen, iPhone, Living Room, Profiles, mappings and optional client-device data. All **11 secret-file hashes** and every other machine environment value were unchanged. Historical signed provenance was preserved, not forged or rewritten. Python was 3.14.7; package metadata remains 0.2.0. Production old-code rollback was **not** run.
- The first post-swap PowerShell guard incorrectly wrapped a JSON array, producing a count/type error before any Serve update. Read-only reconciliation confirmed all 11 files unchanged and no owned reader processes. Later SSH authentication stalled at the local 1Password agent, not at a remote shell: the already configured disk identity worked with per-command `IdentityAgent=none` and `IdentitiesOnly=yes`, with no persistent SSH/key/host changes and no swap retry.
- During that hold, independent non-SSH checks at 17:03:21–38 UTC found the existing TLS `/pair` page **200** with actual Hermes pairing HTML and a tailnet pong via LAN in 3 ms. A single LAN TCP 8780 attempt failed, consistent with the existing documented tailnet-only firewall; no client-facing outage was observed. No restart or rollback was attempted to solve the authentication-agent issue.
- Actual Serve configuration was privately backed up before adding **only** `/api/v1/paired-room-claims` → `http://127.0.0.1:8780/api/v1/paired-room-claims`. Handler count changed **9 → 10**; every pre-existing handler was unchanged; Funnel remained off. No admin or generic `/api` route was published. An actual unauthenticated HTTPS request from ops returned **401**.

### Private bridge and explicit policy

- The exact TUI wheel was installed in its own release venv using the existing operator recipe (`python3.14 -m venv`, then that venv's pip with the wheel's voice extra). A one-shot preparation wrapper's unnecessary uv assumption was corrected before package installation; a subsequent model-launch PATH omission was corrected using the existing absolute `runuser`, without repeating installation or adding global tools. No apt/global dependency change was made. Actual WAV bridge/voice/faster-whisper imports succeeded without PortAudio, which this device-playback path does not use; the real `base` CPU/int8 STT model cache was warmed **without audio or transcription**.
- The distinct existing Puck token was selected privately from its recovery store and transferred only through encrypted stdin. Atomic environment replacement retained the existing Home Device credential and enrollment file unchanged, verified token/source equality privately, removed obsolete operator bootstrap values, and preserved owner `hermes-puck`, state mode `0700` and environment mode `0600`. No handle or browser/Standard credential was supplied. Source/file equality is not an independent claim about the board's embedded token.
- `hermes-puck-bridge.service` is enabled and active, serving LAN `192.168.0.4:8766` in Home transport from the exact release, without an operator conversation handle. Its observed PID was 2673983. The existing browser service remained active at **PID 229999** throughout. A real `/response-status?seq=0` boundary check returned **401** for missing/bad tokens and **404** for the valid token with an unknown sequence, without capture, claim or playback.
- Only then did normal authenticated revision-checked configuration publish **99 → 100**, adding the explicit paired policy to Device `c9dd86cfa9634330bf412e560969b7a9`, endpoint `ops-hermes-puck-bridge`, Room `living-room`, selected `hey-missy` → Amanda. The actual Puck's own credential subsequently obtained **200** discovery, exactly that paired Room/mapping policy, and only `hey-missy`. This also exercised the live authorization/same-mapping singleton intersection; no competing Device was disabled or reconfigured.

### One real Standard speech transport probe — not physical acceptance

At **2026-10-19T17:20:55.864563Z**, the independently reviewed probe (`37749330e08cd24a795c17d2c1e8a793d5cb82cc9f4e4b3ffb87fcd59a488cb2`) exited **0** after exactly one authorized deliberate text turn. It used only the Puck's existing Home credential, a fresh paired grant and a fresh private probe home; it did not reuse a browser token, Standard token or conversation handle.

Observed results:

- Fresh Home admission: **admitted / granted**, current configuration 100.
- Unmodified Standard Amanda returned actual `audio_start`: **24000 Hz, mono, 2-byte samples**.
- **7 audio chunks, 29696 PCM bytes**, followed by `audio_end` and terminal completion.
- `conversation.close` was acknowledged; durable recovery status was **retired**, not guessed from a 404 or network reachability.
- No failure, prompt replay, microphone/capture, audio file, Puck media fetch or playback occurred. Little-endian is the normalized Home PCM contract; raw wire byte order was **not separately observed**.

Nonsecret control-plane receipts are retained under `/tmp/hermes-home-paired-rollback-fznabncz/`, notably `actual-standard-speech-probe-summary.json`, `windows-post-swap-receipt.json`, `home-paired-policy-publication-receipt.json`, `ops-service-install-receipt.json` and `ops-bridge-auth-boundary-receipt.json`. The private ops recovery backup is `/var/backups/hermes-puck-bridge/paired-room-pre-acb1da6-20261019T171004Z`; production credentials, enrollment state and current databases remain in place.

Before physical testing, the firmware custodian's read-only installed-212 source/configuration check found the actual micro-decoder accepts dynamic 24 kHz mono/16-bit WAV, the configured resampler targets 48 kHz, and the existing reader/decoder handles HTTP chunked EOF plus the bridge's streaming `0xFFFFFFFF` RIFF/data sizes. The bridge does not resample. That was **static compatibility**, not by itself proof of network decode, speaker drain or audibility. Subsequent parent-GO physical evidence follows; no firmware/configuration/volume/flash change was made.

## Owner-accepted physical paired-room happy path — 2026-10-19

### First physical window found a real decoder incompatibility

The first owner-authorized window (17:53:43–17:55:13 UTC) proved wake, authenticated Home-mode admission, a fresh paired grant, a 350880-byte capture and successful upload. It did **not** produce an audible answer. Actual bridge transcription was invoked, but `faster-whisper 1.2.1` failed in `decode_audio` at `av.open`: **`TypeError: open() got an unexpected keyword argument 'metadata_errors'`**, with installed PyAV 19.0.1. This occurred before WAV decoding, VAD, language detection or inference; it was not an observed empty transcript and does not establish a channel/gain, calibrated-provider or P8 fragmentation cause. The transient WAV was removed by the existing `finally` cleanup; no actual clip RMS/peak/clipping/zero statistics were logged or recoverable without a new capture.

The installed 212 `HOME_GRANTED` path, matching fresh Home claim and accepted authenticated upload establish the actual Home transport, bridge target `192.168.0.4:8766` and operational board↔bridge token match, not merely matching source files. No token was read aloud or printed.

### Narrow reviewed bridge dependency correction

The owning TUI PR254 fixed the compatibility contract with `av<19` and a real decoder-boundary regression, independently reviewed before publication. Exact source **`6b288939adf9bfdc2954301f53c8fec68080b681`** passed [CI 39300603139](https://github.com/achappell/hermes-relay-tui/actions/runs/39300603139) before the separately approved bridge-only cutover:

- TUI 0.13.1 wheel SHA-256: `ac232c4dbedf39f1fd4be68a82ebb4445ade3487bbf39d50e3030ba5892efc4e`.
- Exact Linux PyAV **18.1.0** wheel SHA-256: `8a032e8d8ebc73dec079364b9b4a6837638a2d106e8472314e685ffbf163e700`; actual host tags were checked before installation.
- The old isolated venv was backed up privately at `/var/backups/hermes-puck-bridge/av181-pre-6b28893-20261019T182101Z`. Only the bridge service was stopped. The existing known paired claim received the exact authenticated owned-close **200** receipt and its recovery marker became confirmed **retired**; no row/marker deletion or 404-as-proof occurred. The next paired wake would also have retired it: this was cleanup, not an admission blocker.
- Only those two wheels were force-reinstalled with `--no-deps`. The only dependency-version delta was PyAV **19.0.1 → 18.1.0**; faster-whisper remained 1.2.1 and every other installed dependency version was unchanged. All **69 TUI + 259 PyAV hashed RECORD entries**, imports and pip dependency checks passed.
- An actual on-host, in-memory synthetic mono/16-bit/16 kHz WAV passed **the real `faster_whisper.audio.decode_audio`**, yielding 1600 finite float32 samples. This was a content-free decoder compatibility check, not synthetic wake/physical speech evidence; no audio file, microphone or playback was used.
- The same service, unit, private environment, Home credential, Puck token, enrollment and state were retained. The existing installation-slot directory still bears the original `acb1da6...` name; the unsigned operator deployment manifest correctly identifies installed source **6b288939...**, not that historical directory name. No path shim or broader installer was added.
- The restarted service was active/enabled at observed PID 2690895. Missing/bad-token response-status requests returned **401**, valid-token/unknown sequence **404**, recovery was retired and Home had **zero total active claims** before the second window. Home stayed at source **831e55e...**, configuration **100**; the browser stayed at PID 229999; firmware remained **212** and volume remained unmuted **0.40**.

### Accepted second physical window

After a new explicit parent GO, the custodian checked actual build/authentication/IDLE/volume read-only and opened **18:40:35.374134–18:42:05.375707 UTC**. Amanda supplied one real Hey Missy question. **She explicitly confirmed hearing a reasonable spoken response.** This owner confirmation establishes audible/coherent acceptance; PCM counts alone do not.

| Stage | Content-free observed evidence |
| --- | --- |
| Wake and authority before capture | Native `HOME_GRANTED` preceded capture; bridge Home ready at **18:40:49.786260**, fresh paired admission at **18:40:49.814838 UTC**, configuration 100. |
| Capture/upload | Native primary sequence **0**, **388960 raw bytes** (about **3.039 s** at declared stereo/32-bit/16 kHz), VAD end and successful upload. Existing channel index 1/gain 4 conversion was unchanged. |
| Nonempty STT → Standard | Established causally by the exclusive production path: the receiver calls the turn callback only after successful nonempty transcription, and that turn produced Standard audio. No transcript or positive-STT raw log was retained. |
| TTS production | **112 audio chunks**, turn complete at **18:41:17.728554 UTC**, reported turn processing **18.0 s**. |
| HTTP delivery/drain | At **18:41:17.729380 UTC**, **685568 source = 685568 delivered PCM bytes**, **24000 Hz / mono / 2-byte samples**, **14.283 s** audio, terminal/reason **complete**, **0.0 s** bridge stall; response duration **21.139 s**. No content hashes retained. |
| Actual Puck playback | Native media **PLAYING → IDLE**, response confirmed COMPLETE and wake detection resumed; Amanda's reasonable-audible-answer confirmation is separate human evidence. |
| Authoritative cleanup | After completed primary delivery, exact authenticated owned-paired close **200**, durable marker **retired**, then read-only Home count **0 total active claims**. No deletion, guessed 404 retirement or prompt replay. |

Native observer relative times were grant **15.015**, capture **15.186**, VAD end **18.242**, upload delivered **21.786**, PLAYING **31.186**, IDLE **47.472**, response COMPLETE **47.530** and wake detector resumed **48.034 seconds**. These are the native observer's reported relative clock, not an independently synchronized UTC conversion.

### Honest limits and quiescent finish

- The custodian counted **one unattributed speaker error** (raw message not retained) and **one capture warning** matching the bounded 8-second automatic follow-up limit. Do not claim zero warnings or infer an unobserved diagnosis from them.
- Automatic follow-up sequence **1** uploaded, then the bridge reported `NO_QUESTION_OR_AUDIO`; no second PLAYING/audio-producing turn was observed. That is not accepted follow-up reliability. Same-service repeated wake, interruption, reconnect, live revocation and shutdown/restart matrices remain separate, as do consumer renewal and the generic calibrated strict provider.
- Little-endian remains the normalized Home PCM contract, not a separately observed raw-byte assertion. The successful primary path does not prove bit-exact capture or eliminate every installed-212 P8 quality risk.
- No captured audio, transcript, credentials or content hashes were retained in observation artifacts. Both observers finished; the hardware custodian closed its tunnel/scaffolds and returned quiescent. After authoritative cleanup, no further live Home/ops/Puck actions were taken.

The content-free combined control-plane receipt is `/tmp/hermes-home-paired-rollback-fznabncz/second-physical-turn-accepted-receipt.json`, shared with the owning TUI identity worker. This durable repository record preserves its nonsecret stage/count/timing/acceptance facts. It closes the **first owner-approved paired-room physical happy path**, not broader migration, other Device approval or all P4/STD6 edge gates.
