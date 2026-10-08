# HOME-MIG-09 — pilot cutover and retirement: readiness review

Date: 2026-10-08. Story status: **backlog** (unchanged). Document status: **draft, paper work only**.

Basis: `origin/main` at `6859c08`. Read-only: no source change, no host, SSH, ops, Tailscale or Grafana access, no deployment, no commit to `main`. This is not a verification record. It claims no implementation, no merge of cutover work and no physical or live acceptance (spec Acceptance, fourth bullet). Every inventory fact cites a file and line. Anything the repository does not show is `UNKNOWN`. A recommended default that needs the owner is `PROPOSED`. A conclusion drawn from the cited text rather than stated by it is `[INFERENCE]`.

Spec: [spec-home-mig-09-pilot-cutover-and-retirement.md](spec-home-mig-09-pilot-cutover-and-retirement.md). Issue: [#55](https://github.com/achappell/hermes-relay-home/issues/55). Delivery contract: [course-correction-2026-09-23.md](course-correction-2026-09-23.md) (cited as CC).

Abbreviations: **VX** = `validation-home-nw-06-diagnostics-exporter.md`; **V06** = `validation-home-nw-06.md`; **V18** = `validation-home-nw-18.md`; **WIN** = `deploy/windows/README.md`; **OPS** = `deploy/ops/README.md`; **SS** = `sprint-status.yaml`; **SI** = `story-index.yaml`; **BASE** = `_bmad-output/specs/spec-standard-hermes-compatibility-migration/standard-baseline.md`; **COMPAT** = the `SPEC.md` in that same directory. Paths without a directory are under `_bmad-output/implementation-artifacts/`.

## 1. Verdict

**Not ready to execute. Ready to plan.** The spec is an approved scope statement with no execution plan, no inventory, no recovery procedure and no evidence list. Its own Readiness section says so, and so does `course-correction-readiness-2026-09-23.md:12`: "record the actual deployment inventory and compatible recovery procedure before cutover". This document supplies the paper work that does not need hardware. It leaves 16 owner decisions (section 7) and a list of steps that need hardware or Ops (section 8).

Three findings change what "cutover" means for this household:

1. **The Home service itself is already on the Standard path.** Home speaks only `/api/ws` and the separate audio socket, and refuses other paths (`tests/test_standard_bridge.py:4274-4285`: a `/voice-session` URL is rejected with a `/api/ws` error). `src/` has no `/voice-session` client. The live route is CaticornQueen, Home, Tailscale, relay proxy, Standard (`validation-home-nw-03.md:70-95`). What is left to retire is therefore mostly **outside this repository** (clients, the fork plugin on the agent host) plus a small set of Home-side legacy residues (section 2).
2. **The "unmodified Standard" claim is not currently true of the pilot Standard.** The media-server checkout is Hermes Agent HEAD `f97608f178d1` with a live uncommitted two-file patch (`deploy/ops/standard-speak-stream-timeout/README.md:3-5`, `OPS:145-148`). The pinned baseline is 0.21.1 at `2237be3` (`BASE:18-22`). The relation between the two commits is `UNKNOWN`. CC:7 says a feature must not require an agent patch. The patch is a robustness fix rather than a feature, but the owner has to rule on it (OD-4, OD-5).
3. **The recorded rollback pattern has never been exercised.** Every `rollback-package.ps1` since 2026-10-03 was parsed and not run (`V18:266-282`, `VX:75`). It deliberately does not restore the database. Compatibility of the older code with the migrated database is unverified (`V18:278-281`). Rehearsal is a precondition of cutover (CC:71) and is blocked on host access.

## 2. Inventory

Legend for **Action**: RETIRE = removed by the cutover (CC:65-69); KEEP = reusable Standard adapter or current Home machinery that CC:65 says to distinguish from legacy; DECIDE = needs an owner decision; UNKNOWN = the repository does not show state.

### 2.1 Home repository (code and deploy bundle)

| ID | Item | What it is and where | Evidence | Action |
| --- | --- | --- | --- | --- |
| H1 | Static device-credentials mode | `HERMES_HOME_DEVICE_CREDENTIALS_FILE`; runtime reports `auth_mode == "legacy"`; `StaticCredentialAuthenticator`; installer `-DeviceCredentialsFile`. Pre-NW-02 mode, "an explicit legacy mode" | `src/hermes_home/runtime.py:53,75-76,165-175`; `src/hermes_home/auth/static.py:8-28`; `deploy/windows/install.ps1:21,551-559`; `README.md:53-55`; `WIN:70-72` | DECIDE (OD-7). Live use `UNKNOWN`. [INFERENCE] not set on CaticornQueen: runtime refuses it together with the root secret (`runtime.py:188-191`) and a paired physical iOS client has been used live (`validation-home-nw-17.md:83`) |
| H2 | Static-credential tests | Tests that exercise the legacy mode | `tests/test_runtime.py:22,36,112,122,181,191,841` | Removed with H1 |
| H3 | Installer rerun clears credential settings | Omitting `-CredentialRootSecretFile` clears the root-secret variable; omitting the Standard settings clears the Standard variables. A rerun is destructive unless every setting is repeated | `deploy/windows/install.ps1:545-550,578-584`; `WIN:209-211` | KEEP, but hazard for the runbook (section 5.4 B) |
| H4 | Conversation-grants file residue | Current code stores claims in SQLite (`ConversationGrantStore`, `production.py:63`) and the README says there is no conversation-grant file. The host still has `secrets\conversation-grants.json` and two `.bak` copies, from the earlier operator-supplied handle flow. Contents not read | `WIN:176-179`; `VX:174`; no reference in `src/` (grep) | RETIRE (archive, hash, delete) per OD-13. Contents `UNKNOWN` |
| H5 | Operator-supplied disposable handles | Retired flow: `HOME_CONVERSATION_HANDLE`, minted handles that expire in 90 s unopened or 8 s after playback. Replaced by client claims | `spec-home-nw-17-client-pairing-and-direct-admission.md:27`; CC:27 | RETIRE in clients; Home side has nothing left to remove [INFERENCE: no source reference] |
| H6 | `/voice-session` references | Rollback-only ledger; fork route "obsolete and removed at the HOME-MIG-09 cutover"; Home test asserting the ledger text | `BASE:26,58-71`; `COMPAT:139`; `tests/test_standard_compatibility_artifacts.py:73-76` | KEEP as historical evidence (CC:69 "keep historical source and evidence"). Stale rollback language needs errata (G6) |
| H7 | Pilot relay proxy | Loopback relay on `127.0.0.1:9121` in front of Standard `9120`, because Standard's loopback Host guard rejects the Tailscale Serve host. Named "pilot", loads `hermes_home_diagnostics.py` from the Home checkout. A Home adapter around Standard, not a fork | `deploy/ops/hermes-standard-home-pilot-proxy.py:1-14,42-44,88`; `OPS:55-136`; `validation-home-nw-03.md:70-76` | DECIDE (OD-6). Not fork |
| H8 | Pilot Standard launch agent | LaunchAgent `com.hermes.home-standard-pilot`, Profile `amanda`, port 9120, `serve --isolated`. Home mapping still names it "the Sprint 1 pilot target" | `deploy/ops/com.hermes.home-standard-pilot.plist:5-7,12-14`; `deploy/ops/hermes-standard-home-pilot.sh:30-34`; `OPS:55-60,134-137` | DECIDE (OD-6). Profile mapping beyond `amanda` is NW-05 machinery, deployed: configuration lists `amanda`, `jensen`, `spark` (`V06:451`) |
| H9 | Live local patch on the agent checkout | `tts.openai.stream_timeout_seconds` + `max_retries=0` and a type-only warning, uncommitted on `f97608f178d1`. Not upstream. Survives `hermes update` only by autostash | `deploy/ops/standard-speak-stream-timeout/README.md:3-5,17-20,36-46,128-150`; `OPS:138-163` | DECIDE (OD-5). Conflicts with CC:7 |
| H10 | Qwen3 speech-server edits | Persistent content-free log and archiving start script in `D:\Qwen3-TTS` on CaticornQueen. The start script sets a `streaming-fork` TTS fork path. A TTS server, not the Hermes agent | `OPS:10,145-149`; `deploy/ops/qwen3-streaming-logging/start_qwen3_streaming_optimized.ps1:17,39` | KEEP [INFERENCE: out of CC:65 scope]. Confirm in OD-5 |
| H11 | Static fixtures and docs naming the fork | `standard-baseline.md` rollback-only ledger; COMPAT CAP-6 "legacy path remains an explicit rollback option" | `BASE:58-71`; `COMPAT:76-80,108-110,126-133,157` | KEEP as history, add errata (G6) |

### 2.2 CaticornQueen host (Windows) — state recorded 2026-10-08

All values are from validation records. The host was not read for this document.

| ID | Item | Recorded state | Evidence |
| --- | --- | --- | --- |
| W1 | Home package | Revision `d3d816a0ee40c5d44778864daacf6f510291d006`, 35 source hashes equal to the wheel; wheel SHA-256 `b8d1bb83…daa5d` | `VX:50-54,90` |
| W2 | Task | `Hermes Home`, SYSTEM, started 2026-10-08 10:18:58 after the bind change (pid `32808`); runner SHA-256 `52b9316a…cc55`, task XML `f61e73a2…d495` | `VX:61-64,120` |
| W3 | Bind / ports | `HERMES_HOME_BIND_HOST=0.0.0.0` (was `127.0.0.1`, saved in `bind-host-before.json`); `0.0.0.0:8780`, bridge `127.0.0.1:8766` | `VX:118-127`; `WIN:76-78` |
| W4 | Firewall | Inbound Allow, TCP 8780, remote `100.64.0.0/10`, Private; display name `Hermes Home metrics from Tailscale`. Only allow for 8780; LAN access times out | `VX:118,134`; `WIN:117-132` |
| W5 | Machine variables | 17 `HERMES_HOME*` variables plus `HERMES_HOME_DEPLOYMENT_REVISION` before the 2026-10-08 cutover, then `HERMES_HOME_EXPORT_DIR` added; earlier records count 18. Names and values of the full list are not in the repository | `VX:62,83`; `V06:138,251` |
| W6 | Credentials on disk (names only) | `admin-token`, `credential-root`, `standard-token`, `live-gate-signing.key` (purpose `UNKNOWN`), all SYSTEM and Administrators only. Contents not recorded | `VX:174` |
| W7 | Database | `home.sqlite3` under `C:\ProgramData\HermesHome`; schema migrations are forward only (`conversation_claims` rebuilt and extended, `diagnostic_state` column added). Old-code compatibility with the migrated schema `UNKNOWN` | `src/hermes_home/bridge/production.py:1259-1292`; `src/hermes_home/storage/diagnostics.py:94-120`; `V18:278-281` |
| W8 | HermesHome ACL | Inheritance removed 10:45 2026-10-08; root has SYSTEM, Administrators, CREATOR OWNER only; no Users, Authenticated Users or Everyone on 3,311 objects. Saved ACL at `backups\home-acl-lockdown-20261008\HermesHome-acl.txt` | `VX:174-188` |
| W9 | Backups | `backups\` holds one directory per deployment: `nw18-082e593-20261003`, `home-nw06-a45f7ef-20261004`, `home-0effbf9-20261005`, `home-pr80-20261006`, `home-pr83-20261007`, `home-pr85-20261007`, `home-pr91-20261008`, `home-acl-lockdown-20261008`; also `repairs\title-event-20260923` | `V18:205-210`; `V06:125,206,338,415,518`; `VX:70,178`; `validation-home-title-event-fix.md:41` |
| W10 | Legacy diagnostics | `diagnostics-legacy\` (the original user-writable logs directory) and `logs\` | `V06:139-141`; `WIN:225-232` |
| W11 | Diagnostics and export | `diagnostics\` (SYSTEM Modify, Administrators Read), `diagnostics\export\` (JSONL, 14-day and 7-day retention) | `WIN:225-260`; `VX:81,98` |
| W12 | Alloy shipper | Grafana Alloy v1.20.1 as `NT SERVICE\Alloy`, Automatic; config SHA-256 `c017c282…8828`; read-only on the export directory, Modify on its own data directory; service environment set in the registry (the installer truncates `//`); UI `127.0.0.1:12345` | `VX:140-170`; `WIN:262-304` |
| W13 | Local Prometheus | Job `hermes-home` targets `127.0.0.1:8780`; config backed up with a timestamp by the installer; service Running | `WIN:90-93,215-218`; `VX:130` |
| W14 | Tailscale Serve (CaticornQueen) | `/` preserved; `/api/v1/bridge/ws`; `/pair`, `/api/v1/enrollment/requests`, `/api/v1/client-claims`, `/api/v1/client-sessions`, `/api/v1/profile-grants`, `/api/v1/devices`, `/api/v1/client-diagnostics`. Live Serve config is **not** backed up anywhere in the repository | `WIN:47-61,306-343`; `validation-home-nw-03.md:63-65` |
| W15 | Standard gateway setting | `HERMES_HOME_STANDARD_GATEWAY_URL` is a `wss://media-server…:8443/api/ws` target (media-server, port 8443 reachable). Exact live value `UNKNOWN` (values not recorded) | `WIN:185-189`; `V06:451` |
| W16 | Installed `run.ps1` | Still differs from the repository runner in the `logs` to `diagnostics` path (recorded 2026-10-04) | `VX:106` |

### 2.3 Media server (macOS), ops host, other hosts

| ID | Item | Recorded state | Evidence | Action |
| --- | --- | --- | --- | --- |
| M1 | Standard pilot and relay | LaunchAgents `com.hermes.home-standard-pilot` (9120) and `…-proxy` (9121); Serve on `https=8443` maps `/api/ws` and `/api/audio/speak-stream` to 9121; Profile `amanda`; token at `~/.hermes/hermes-home-standard-pilot/standard-token` (0600) | `OPS:55-136`; `validation-home-nw-03.md:70-95` | KEEP / OD-6 |
| M2 | Agent checkout | `~/.hermes/hermes-agent` at `f97608f178d1` plus patch H9; Python `…/venv/bin/python` | `deploy/ops/standard-speak-stream-timeout/README.md:3-5`; `deploy/ops/hermes-standard-home-pilot.sh:9` | DECIDE (OD-4, OD-5) |
| M3 | Fork agent / `voice_session` plugin | The fork checkout is named in the pin as `~/Development/hermes-agent-relay-v0.21.0-minimal` (also the source of the 0.21.1 pin). Where, if anywhere, a fork agent is **running** (host, process, launch service, port, config, tokens) is not recorded | `BASE:23`; `COMPAT:10-16` | UNKNOWN. RETIRE (OD-8) |
| M4 | Pilot token handling | `standard-token` copied by hand to CaticornQueen's token file; same value on both | `OPS:132-136` | KEEP; rotation = OD-9 |
| M5 | Ops Alloy scrape | `hermes-home.alloy` scrapes `100.78.105.19:8780/metrics` with the admin token read from `/srv/ops/alloy/secrets/hermes-home-admin-token` on the ops host | `deploy/ops/hermes-home.alloy:1-20`; `OPS:17-29` | KEEP. Token is an Ops-held copy of a Home credential: include in any rotation |
| M6 | Loki push | `http://ops.taila59979.ts.net:3100/loki/api/v1/push`; no authentication or tenant; needs the `tag:home` to `tag:ops` `tcp:3100` grant | `WIN:280-283`; `VX:140` | KEEP |

### 2.4 Client surfaces and agent (outside this repository)

Home cannot see these. They are listed because CC:65-69 makes each delivery repository own its removal and because the spec's evidence depends on them. Everything here is `UNKNOWN` to Home except what the specs say.

| ID | Item | What the Home-side documents say | Evidence |
| --- | --- | --- | --- |
| C1 | TUI | Fork `voice-session` default; opt-in Standard gateway proof; consumes `HOME_CONVERSATION_HANDLE`; "pair from a link", `/approvals`, `--continue`/`--resume` named as consuming stories | `COMPAT` surface matrix (`surface-migration-matrix.md:39`, TUI row); `spec-home-nw-17…md:286` |
| C2 | iOS and macOS | Consume the voice-session boundary; legacy pairing UI and operator handles to be replaced by client claims | surface matrix iOS/macOS row; `spec-home-nw-17…md:287` |
| C3 | Android | Direct `/voice-session` profiles; operator-supplied disposable handles used for the 5-A-4 live gate | surface matrix Android row; `spec-home-nw-17…md:27` |
| C4 | Puck, Touch, W/K | Puck and W/K migration stories 6 and 8 are TUI/web delivery; Touch is Home NW-16 (Home half) plus firmware | `stories.yaml:1-125` (source stories 6-8); `spec-home-nw-16…md:148-150` |
| C5 | Direct-Standard personal mode (Epic 2) | No Home story; owned by the client repositories. Home must stay out of it: "Standard setup … does not request a Home pairing code" | CC:15, 19; `SI` has no Epic-2 entry; `SS` has no `epic-2` key |
| C6 | Signed macOS build (`MACOS-DIST`) | Required by Epic 4, but no Home story identity exists | CC:73; `SI` has no `MACOS-DIST` entry (grep) |

## 3. Gate state on `main` (`6859c08`, 2026-10-08)

MIG-09 depends on `epic:1`, `epic:2`, `epic:3` (`SI:144-147`; spec Dependencies). CC:41 says Epics 1-4 close only when their required end-to-end capabilities work on the accepted surfaces; a historical `done` label alone does not close new acceptance (CC:67).

| Gate | Tracker state | Evidence and what it does not show |
| --- | --- | --- |
| Epic 1 | `epic-1: in-progress` (`SS:10`). All seven Home records under it are `done` (`SS:11-17`): foundation, NW-01, NW-02, NW-03, NW-04, NW-17, NW-18 | Per-story evidence is local or Home-only. NW-17 `passed-local` plus owner acceptance, "not a new test run" (`validation-home-nw-17.md:3,91`); NW-18 `deployed-verified-source`, live Standard interrupt, WebSocket close, iOS consumption and Serve routing not exercised (`V18:113-119,287-289`). What keeps the epic `in-progress` (client repositories) is `UNKNOWN` to Home |
| Epic 2 | Not tracked in Home: no `epic-2` key (`SS:9-41`), no Home story (`SI`) | `UNKNOWN`. The gate can only be a link to client-repository evidence (OD-1) |
| Epic 3 | `epic-3: in-progress` (`SS:18`). NW-05 `done`, NW-16 `done`, **NW-13 `backlog`** (`SS:19-21`) | NW-05 `verified`: no live Standard server, no physical device (`validation-home-nw-05.md:3,39`). NW-16 `passed`: no physical Touch, no endpoint audio, no live Standard, no deployment (`validation-home-nw-16.md:3,22-23`). NW-13 is `release_scope: scope-decision`; custom wake phrases remain an explicit decision, built-in wake admission required (`SI:123-135`) (OD-3). Puck and W/K evidence `UNKNOWN` |
| Epic 4 siblings | `epic-4: backlog`; NW-15 `backlog`, `release_scope: later` (`SS:22-23`, `SI:151-161`) | Doesn't block household cutover (CC:73). Split acceptance already described in `SI:160` |
| Epic 6 (evidence tooling, not a gate) | NW-06 `done`; `client-reports`, `connection-diagnostics`, `diagnostics-exporter` `review` (`SS:28-31`) | Real-device captures and the owner's Grafana review pending (`VX:3,44,190`) |

**Reading:** none of the three epic gates is met on the tracker. Every Home-owned story under Epic 1 and Epic 3 except NW-13 is `done`, but the epics are `in-progress`, Epic 2 has no Home record, and the physical and live evidence that CC:41 and the spec's fourth Acceptance bullet require is explicitly absent from the Home validation records. Step R3 below makes each gate checkable.

## 4. Retirement plan

Order follows the rollout order in `compatibility-and-rollout.md:26-32` and CC:63-73. Each step lists its **preconditions** (tied to stories and decisions), the **evidence** that closes it, and its **reversibility**. "Evidence" is recorded in a new validation section, with implementation, merge and physical/live acceptance recorded separately (spec Acceptance, fourth bullet). No step is performed by this document.

| Step | What | Preconditions | Evidence required | Reversible? |
| --- | --- | --- | --- | --- |
| R0 | **Record the baseline.** Deployed Home revision and wheel hash; the Standard commit actually running, patched or not; Serve configuration on both hosts; bind, firewall, ACL, Alloy state; machine-variable names (values redacted) | OD-4 (which pin). Read-only host access (Ops) | A provenance record: Home `d3d816a` (or successor), Standard commit and tree state, patch hashes, Serve status dumps. Closes CC:67 "exact unmodified upstream baseline and deployed code provenance" | n/a (read-only) |
| R1 | **Finish the inventory.** Fill every `UNKNOWN` in section 2: where the fork agent and `voice_session` plugin run; launch services; credentials and tokens by name, path, ACL, holder; client legacy artifacts (from each repository); `live-gate-signing.key` purpose | R0. Client repositories (C1-C6) supply their own lists (CC:65) | Sanitized inventory table with owner per row; no secret contents | n/a |
| R2 | **Prepare and verify recovery before cutover** (CC:71). Create the cutover backup directory, parse the rollback script, rehearse it | OD-10 (what a compatible recovery build is), OD-11. Host access. A rehearsal target: a copy of the pre-cutover database restored with old code (CaticornQueen or a scratch host) | Section 5 checklist completed; rehearsal result including old-code-on-new-database outcome (W7) recorded. If no compatible recovery build exists: pause rollout and mark the surface unavailable (CC:71) | n/a |
| R3 | **Gate check, epics 1-3.** For each surface in CC:29-39, link exact-build evidence for: setup, text, voice (required for migration), stop, disconnect/recovery without replay, renewal and revocation (Home mode), honest unsupported states | Epic 1: NW-01..04, NW-17, NW-18 `done` (met) **and** per-surface client evidence (UNKNOWN). Epic 2: client evidence only (OD-1). Epic 3: NW-05, NW-16 `done` (met), NW-13 decided (OD-3), Puck/Touch/W/K physical evidence (UNKNOWN). Owner moves `epic-1` and `epic-3` out of `in-progress` | A per-surface matrix (extend this file) of links to client-repository validation records, each naming build and date. Owner decision recorded in `sprint-status.yaml` | n/a |
| R4 | **Controlled pilot** across surfaces in the order of OD-12. Re-pair each client through `/pair` (no credential conversion by default, OD-9). Preserve intentional history (CC:69) | R0-R3; OD-9, OD-12; NW-17/NW-18 routes published (they are, `WIN:306-338`). NW-06 diagnostics available as support, not a gate (OD-14) | Per surface: dated live capture of CC:67 behaviors; Home-side corroboration (claims list, client reports, safe-event export); any unsupported capability reported as deferred or scope decision, never `done` (CC:41) | Yes: stop pilot, remaining surfaces stay on the old path until step R6 |
| R5 | **Household install, upgrade and recovery instructions** for the actual deployment (CC:73), merged into one repeatable runbook | R2 (recovery verified). OD-15 (macOS signing story location) and OD-16 (media-server scope) | A runbook that covers install, start/restart, upgrade, recovery for CaticornQueen and the media server; run once end to end, or each unrun step marked unexercised | n/a |
| R6 | **Switch the default.** Clients and room devices use the replacement; no surface depends on the fork | R3 green for every required surface; R5; owner approval | Recorded owner approval; per-surface "no fork dependence" statement | Yes, within the bake window (OD-12) via section 5 |
| R7 | **Retire.** In this order: (a) stop obsolete services (fork agent/plugin, launch entries); (b) invalidate obsolete credentials: fork bearer tokens, operator handles, static device credentials, superseded Home device credentials after each surface is accepted, `conversation-grants.json` residue; (c) remove supported code and configuration paths in a separate source PR (H1/H2, installer `-DeviceCredentialsFile`, README, COMPAT errata); (d) archive the fork checkout read-only | R6; end of bake window (OD-12); inventory R1 complete; OD-5, OD-6, OD-7, OD-8, OD-13 | Per item: before/after state, hashes, who and when. A scan showing no supported install path requires the fork or the old pairing (CC:69: "no supported installation may still require…") | **No** for credential invalidation (re-pair needed). Stop services: restart possible but not a supported state (CC:71) |
| R8 | **Close.** Update `sprint-status.yaml` and the spec status; close issue #55. NW-15 and `MACOS-DIST` stay later (split) | R7; owner acceptance | Owner acceptance recorded; implementation, merge and live acceptance listed separately | n/a |

## 5. Rollback runbook (cutover)

Governing rules (CC:71, `compatibility-and-rollout.md:34-36`): recovery restores a verified compatible Home/client/Standard build and recoverable configuration and data. It never silently switches a user's mode, never replays a turn and never restores the fork as a supported product. If no compatible recovery build exists, pause the rollout and leave the affected surface explicitly unavailable.

Every command in this section is taken from a cited repository document. The assembled sequence for the cutover is `PROPOSED` and **not exercised**. Commands run elevated or over the trusted `caticornqueen` alias; a non-elevated desktop session can no longer read `HermesHome` (`VX:188`).

### 5.1 State as of 2026-10-08 (what a rollback must land on)

Use W1-W16 above. Operationally: Home `d3d816a`, bind `0.0.0.0`, tailnet-scoped firewall rule in place, local Prometheus on `127.0.0.1:8780`, ops Alloy scrape `up`, Alloy v1.20.1 shipping the export to Loki, HermesHome ACL locked down. Existing rollback assets and their scope:

| Asset | Where | Restores | Does **not** touch | Status |
| --- | --- | --- | --- | --- |
| `rollback-package.ps1` (SHA-256 `FEFAF353…8059`) | `backups\home-pr91-20261008` | Package `d803994` from `installed-package.zip` with per-file hash check; clears `HERMES_HOME_EXPORT_DIR`; restores the previous `HERMES_HOME_DEPLOYMENT_REVISION`; takes `home-before-rollback.sqlite3`; starts the task | Database, runner, task definition, export files, bind, firewall, ACL, Alloy | Parsed, **not run** (`VX:75`) |
| `rollback-part1.ps1` | same directory | Removes the firewall rule, restores saved bind, restarts the task | Everything else | Not run (`VX:121`) |
| `rollback-acl.ps1` / `icacls C:\ProgramData /restore …\HermesHome-acl.txt /C` | `backups\home-acl-lockdown-20261008` | The pre-lockdown ACLs, **including** `BUILTIN\Users` read and the stale `achap`, `CodexSandboxUsers` and unresolvable-SID entries | Home, Alloy (no restart needed) | Verified on a scratch tree only (`VX:186`) |
| Alloy rollback | `WIN:301-304`; `VX:168` | `Stop-Service Alloy`; `& "$env:ProgramFiles\GrafanaLabs\Alloy\uninstall.exe" /S`; remove ACL entries | Home export (local JSONL stays the source of truth) | Not run |

Older rollback directories (W9) restore older packages in the same pattern. They are valid only for their own revision and must not be reused for this cutover without the old-code-on-new-database check (W7).

### 5.2 Before the cutover (preparation, one cutover directory)

`PROPOSED` directory name, following the existing pattern: `C:\ProgramData\HermesHome\backups\home-mig09-<revision>-<YYYYMMDD>`, protected to SYSTEM and Administrators. Contents, each from an existing precedent unless marked NEW:

1. `installed-package.zip` plus manifest, each file hashed (`VX:72`).
2. `home-preupgrade.sqlite3`: online `sqlite3.Connection.backup` (not a file copy), `PRAGMA integrity_check` = `ok`, size and SHA-256 recorded (`VX:73`, `V18:213-216`).
3. `run.ps1`, `task.xml`, `machine-settings.json`, `deployment-revision-before.json`, `bind-host-before.json` (`VX:74,119`).
4. The reviewed wheel and its SHA-256, and the previous wheel (`V18:135`, `V18:208-220`).
5. ACL save of `HermesHome` (`icacls … /save … /T /C`) (`VX:178`).
6. `rollback-package.ps1` for this revision, parsed with 0 errors; execution of a rehearsal (R2).
7. NEW: Tailscale Serve configuration dump from CaticornQueen and the media server (W14 / M1: not backed up in any document). Command not specified in the repository; `UNKNOWN` which `tailscale serve` subcommand output is the supported export.
8. NEW: firewall rule export, Prometheus configuration copy (the installer already saves a timestamped one, `WIN:216-218`), Alloy `config.alloy` and the registry `Environment` value (`VX:148`).
9. NEW: media-server backup of `~/.hermes/hermes-home-standard-pilot/` (scripts and plists; the token **not** copied into the backup, only its path and mode), the agent checkout identity and `git stash list`, and the patch H9 state (`verify.sh` result).
10. NEW: sanitized list of credentials that retirement will invalidate (names, holders, created date; no values) so that "what needs re-pairing" is known before it happens.

### 5.3 Rollback triggers and decision

`PROPOSED` triggers (OD-12): a required surface fails its R6 acceptance check; Home or Standard unhealthy after cutover; credential or pairing flow broken for a paired client; unexplained data loss. Decision owner: the owner (Amanda). Rule from CC:71: if rollback cannot produce a compatible build, **pause and mark the surface unavailable** instead of restoring the fork.

### 5.4 Rollback sequences

Pick the smallest sequence that fits. Stop after each step and verify (5.5).

**A. Package-only rollback (Home code).** Pattern from `V18:229-236` and `VX:77`:

```powershell
Stop-ScheduledTask -TaskName 'Hermes Home'
# confirm 127.0.0.1:8780/0.0.0.0:8780 and 127.0.0.1:8766 listeners are released
# restore package from <cutover dir>\installed-package.zip, verify hashes (rollback-package.ps1 does this)
[Environment]::SetEnvironmentVariable('HERMES_HOME_DEPLOYMENT_REVISION', '<value from deployment-revision-before.json>', 'Machine')
Start-ScheduledTask -TaskName 'Hermes Home'
```

Run the prepared `rollback-package.ps1` rather than typing this by hand. It keeps the live database and takes `home-before-rollback.sqlite3` first (`VX:75`; `V18:278-281`). **Do not restore `home-preupgrade.sqlite3` over the live database** unless the owner accepts losing everything written since the backup (claims, enrollments, grants, reports). `V18:278-281` is explicit that "post-deployment user data must not be discarded".

**B. Configuration rollback** (bind, firewall, export). From `WIN:156-168`:

```powershell
Remove-NetFirewallRule -DisplayName 'Hermes Home metrics from Tailscale'
[Environment]::SetEnvironmentVariable('HERMES_HOME_BIND_HOST', '127.0.0.1', 'Machine')
Stop-ScheduledTask -TaskName 'Hermes Home'
Start-ScheduledTask -TaskName 'Hermes Home'
```

Ops Alloy then reports the target down until its scrape is removed. Disabling only the export: clear `HERMES_HOME_EXPORT_DIR` and restart the task (`VX:77`). Installer reruns hazard H3: do **not** rerun `install.ps1` as a rollback unless every setting (`-CredentialRootSecretFile`, `-StandardGatewayUrl`, `-StandardTokenFile`, `-BridgeBindHost`, `-BridgeRouteId`, `-ClientClaimsPerDevice`, `-ClientReconnectGraceSeconds`, `-ConversationIdleTimeoutSeconds`) is passed again (`install.ps1:545-584`); a package-only cutover touches none of them (`WIN:170-172`).

**C. ACL rollback.** `icacls C:\ProgramData /restore C:\ProgramData\HermesHome\backups\home-acl-lockdown-20261008\HermesHome-acl.txt /C` (`VX:186`). Reopens `Users` read on secrets and databases. Use only if the lockdown itself breaks something; it needs no restart.

**D. Observability rollback.** Alloy uninstall as in 5.1; ops-side scrape removal is an Ops action. Neither affects serving.

**E. Media-server rollback (Standard pilot and patch).** Pilot services: `launchctl kickstart -k gui/$(id -u)/com.hermes.home-standard-pilot` restarts only the agent on 9120; the relay is `com.hermes.home-standard-pilot-proxy` (`OPS:82-83,160`; patch README restart section). Patch H9: `sh ~/hermes-ops/standard-speak-stream-timeout/rollback.sh` (reverse apply, no restart) or the saved `~/hermes-backups/standard-speak-timeout-20261006/rollback.sh` (restores originals and restarts the pilot) (patch README rollback section). Restart only when no one is using speech (`OPS:162-163`). A rollback of the agent to an older upstream commit is `UNKNOWN`: no recovery build is recorded for the agent (OD-10).

**F. What cannot be rolled back.** (1) Invalidated credentials: a revoked Home device credential gets 401 afterwards (observed for revoked QA devices, `V18:229-262` region of the 2026-10-03 deployment) and the device must re-pair; fork bearer tokens revoked in R7 stay revoked. (2) Claims: a Home restart closes active claims by design (`V18:139,239`). (3) Database migrations are forward only (W7). (4) Any fork service stopped in R7 is not a supported recovery target (CC:27, 71). (5) Replacing the credential root secret would invalidate all derived credentials ([INFERENCE] from `_bmad-output/specs/spec-home-service-foundation/credential-lifecycle.md:119-127`: digests are keyed by the external root secret). Avoid it as a rollback tool.

### 5.5 Verification after any rollback

From `WIN:134-154`, `VX:86-99,123-135`:

- Task `Running` as SYSTEM, both listeners present on the expected addresses, new pid.
- `/pair` 200 on loopback, tailnet IP and the Serve URL; `/api/v1/client-claims` 401 through Serve.
- Authenticated `/metrics` 401 without and 200 with the admin token; authenticated `/api/v1/diagnostics/status` 200 and `/api/v1/configuration` 200 with the configuration SHA-256 unchanged.
- Local Prometheus `up{job="hermes-home"}` 1; ops `up{job="hermes-home",host="caticornqueen"}` 1 if bind and rule are in place.
- Installed source hashes equal the restored wheel; runner and task XML hashes unchanged.
- LAN access to 8780 still times out.
- Re-pair one client and open one conversation if the credential store was touched.
- Never submit a prompt to test. No replay (CC:71).

### 5.6 Gaps in the recovery procedure that this document cannot close

(i) No recorded recovery build for the Standard agent. (ii) `rollback-package.ps1` never run, and the old code on the migrated database is untested (W7). (iii) No Serve backup procedure. (iv) Media-server recovery relies on a local patch that `hermes update` can silently drop (`OPS:147`). (v) No recorded procedure for rolling back client builds (client repositories). (vi) No rollback record for the macOS signed build.

## 6. Readiness review of the spec

The spec (`spec-home-mig-09-pilot-cutover-and-retirement.md`, as on `main`) is a one-paragraph scope, four generic acceptance bullets and a dependency list. Its Readiness section says implementation requires "API details and a bounded execution plan". Findings, each with a pointer to the decision or step that resolves it:

| Gap | Finding | Evidence | Resolves in |
| --- | --- | --- | --- |
| G1 | Acceptance is generic. The four bullets restate the contract; none is checkable. No named evidence per surface, no named hosts, no rollback criterion. | spec Acceptance | Proposed ACs in the spec update; section 4 evidence column |
| G2 | The dependency `epic:2` has no Home record and the epic gate for `epic:1` and `epic:3` is not defined. | `SI:144-147`; `SS:9-41` | OD-1, OD-2 |
| G3 | "Record deployed upstream provenance" has no target: the running Standard is not the pinned commit and carries a patch. | `BASE:18-22`; patch README:3-5 | OD-4, OD-5, R0 |
| G4 | No inventory. Home-side items are knowable (section 2); fork, launch services, credentials and client artifacts are not recorded anywhere. | `course-correction-readiness-2026-09-23.md:12` | R1; section 2 `UNKNOWN` rows |
| G5 | No compatible-recovery definition. The recorded rollbacks are package-only, never run, database not restored, Standard and clients not covered. | `VX:75`, `V18:266-282`, CC:71 | OD-10, section 5 |
| G6 | Stale rollback language in older specs contradicts the contract: COMPAT CAP-6 and constraints keep the legacy path as an "explicit rollback option until the final gate"; BASE keeps `/voice-session` as "rollback-only"; NW-17 once kept it "as a supported rollback route". CC:27 supersedes. The correction banners cover mode, auth and rollback generally, but the clauses stay in place. | `COMPAT:76-80,108-110,126-133`; `BASE:55-71`; CC:27 | R7 errata step; no change in this PR (frozen history) |
| G7 | NW-13 (custom wake phrases) still blocks a clean reading of epic 3. | `SI:123-135` | OD-3 |
| G8 | `MACOS-DIST` and the macOS signed build are required by Epic 4 but have no Home story and no stated owner. | CC:73; `SI` | OD-15 |
| G9 | Epic 4 requires repeatable instructions "for the actual household deployment". CaticornQueen is documented; the media server is a copy-and-`launchctl` recipe plus a local patch; no single runbook. | `WIN`; `OPS:55-137` | OD-16, R5 |
| G10 | The pilot relay and pilot Standard are named "pilot" and "Sprint 1", and Home's own README says "HOME-NW-05 still owns the eventual dynamic Profile and claim authority". That is stale (NW-05 is `done`). Whether these remain supported components after cutover is not stated. | `OPS:134-137`; `SS:19` | OD-6 |
| G11 | No definition of when the pilot ends, which surfaces go first, or the bake window. | none | OD-12 |
| G12 | The legacy static-credential mode and its tests are still supported code. | H1, H2 | OD-7 |
| G13 | Data-preservation scope is undefined: "intentional history and configuration data" spans Home SQLite, Standard sessions and client-local history. | CC:69 | OD-9, OD-10 |
| G14 | Credential-invalidation list is not defined and there is no record of which credentials exist or who holds them (including the Ops-held admin-token copy and the media-server token copy). | M4, M5, W6 | R1, OD-9 |
| G15 | On `main` the spec has no `validation` pointer and `SI` has no `validation:` key for MIG-09, unlike the other stories. | `SI:136-149` on `main` | Added by this PR |
| G16 | NW-17/NW-18 and NW-05/NW-16 evidence caveats (live Standard, physical devices, deployment routes) all point forward to a live gate; MIG-09 is that gate but does not list them. | `validation-home-nw-17.md:27`; `V18:113-119`; `validation-home-nw-05.md:39`; `validation-home-nw-16.md:22-23` | R3 matrix |

## 7. Owner decisions

Each is a question with a `PROPOSED` default. None has been decided.

**OD-1. What is the Epic 2 gate for Home?** Epic 2 (direct Standard on personal clients) has no Home story and no tracker key. `PROPOSED`: Home records links to each client repository's validation for direct mode in the R3 matrix and never marks `epic-2` itself; Epic 2 is "met" when the four personal clients each have a linked record. Alternative: add an `epic-2` placeholder key to `sprint-status.yaml`.

**OD-2. What makes `epic-1` and `epic-3` leave `in-progress`?** All Home stories are `done` except NW-13. `PROPOSED`: the owner flips an epic only after the R3 matrix has exact-build, dated evidence for every required surface (and a recorded NW-13 decision for Epic 3); a historical `done` does not count (CC:67).

**OD-3. Are custom wake phrases (NW-13) in the migration release?** `PROPOSED`: no. Built-in wake admission is what Epic 3 requires (`SI:123-135`), and MIG-09 does not wait on NW-13. Record the deferral in `SI` as an explicit disposition.

**OD-4. Which Standard commit is the supported baseline?** The pin is 0.21.1 `2237be3` (`BASE:18-22`); the media server runs `f97608f178d1` (patch README:3-5). `PROPOSED`: R0 records the commit actually deployed and confirms it is upstream, not the fork; if it is a later upstream commit, add a new baseline record next to `standard-baseline.md` (do not rewrite the 0.21.1 pin), re-run the Home Standard fixtures against it, and cite it in R6. Open sub-question: is `~/Development/hermes-agent-relay-v0.21.0-minimal` (the pin's "source checkout") a fork? `UNKNOWN`.

**OD-5. How is the speak-stream patch (H9) treated against "unmodified Standard"?** Options: (a) remove it at cutover and rely on Home giving up on the audio socket at 30 s, accepting a possible return of the stall the patch addresses (`deploy/ops/standard-speak-stream-timeout/README.md:10-15,36-46`); (b) replace it with a supported configuration or an upstream fix; (c) record it as a named, owner-signed, time-boxed exception. `PROPOSED`: (c) until (b) is available, with acceptance wording "unmodified except the recorded exception" and an exit criterion. It must not be silently called unmodified. Also confirm the Qwen3 logging edits (H10) are out of scope.

**OD-6. Do the pilot relay proxy (H7) and pilot Standard service (H8) remain supported after cutover?** `PROPOSED`: yes, as supported components of the household deployment, renamed and documented without "pilot" or "Sprint 1" wording; they are Home adapters around unmodified Standard. Confirm whether Standard has a supported way to accept the Serve host header that would make the relay unnecessary (`UNKNOWN`).

**OD-7. Retire the static device-credentials mode (H1, H2)?** `PROPOSED`: yes, in a separate source PR after R6, once the machine variables show it unset. It is "an explicit legacy mode" (`README.md:54`) and pre-dates Home enrollment.

**OD-8. What does "retire the fork" mean on the agent host?** `PROPOSED`: stop and unload every fork service and launch entry, invalidate its bearer tokens, leave the checkout archived read-only for 30 days for traceability, then remove its supported configuration. No fork rollback target (CC:71). Needs R1 to learn where it runs.

**OD-9. Re-pair or convert credentials, and what is invalidated?** `PROPOSED`: no conversion. Each client re-pairs through `/pair`; each superseded credential is revoked only after its surface passes acceptance. The credential root secret is **not** rotated (it would invalidate every credential, W7/5.4F). Include the Ops-held admin-token copy and the Standard token copies in the R1 credential list, and decide separately whether to rotate them.

**OD-10. What is the "compatible recovery build" and how long is it kept?** `PROPOSED`: the last Home wheel with a validation record that passes section 5.5 against the current database, plus its pre-cutover SQLite online backup; the two previous wheels are kept for 30 days. For Standard: the commit recorded in R0. For clients: whatever each repository names. Old-code-on-new-database must be proven in R2, otherwise rollback = pause (CC:71).

**OD-11. Rehearse rollback where?** `PROPOSED`: on a scratch copy of the pre-cutover database with the previous wheel (CaticornQueen under a different port and data directory, or a scratch Windows host). Needs Ops.

**OD-12. Pilot order, duration, bake window and rollback authority?** `PROPOSED`: Home-mode personal clients first (TUI, then macOS, iOS, Android), then room devices (Touch, Puck, W/K), then direct-Standard mode as the client repositories report ready; one full day of normal household use per stage; a 24-hour bake window after R6 before any irreversible R7 step; the owner decides every rollback.

**OD-13. What happens to host residues (H4, W10, `repairs\`, old backup directories, `home-before-*.sqlite3` copies in `secrets\`)?** `PROPOSED`: list names, sizes and hashes (not contents), archive under a protected directory for 30 days, then delete; `conversation-grants.json` and its `.bak` copies are removed from `secrets\` after the owner confirms no process reads them (no source reference exists). `diagnostics-legacy\` and `logs\` content is `UNKNOWN`: the owner reviews the listing first.

**OD-14. Does NW-06 diagnostics gate the pilot?** `PROPOSED`: no. Use the exporter, client reports and Grafana as pilot evidence where available. AC-8 real-device captures and AC-10 Grafana review (`VX:190`) are not MIG-09 gates.

**OD-15. Where is the signed macOS build tracked?** CC:73 requires it, Home has no story. `PROPOSED`: it lives in the macOS client repository under a `MACOS-DIST` identity; MIG-09 links the result and does not own the work.

**OD-16. Scope of the household runbook?** `PROPOSED`: one document for CaticornQueen and the media server (install, start/restart, upgrade, recover), built from `WIN` and `OPS`; NW-15 (other homes, Linux, macOS Home host, updater) stays `later` and out of MIG-09 (`SI:160`; CC:73).

## 8. Blocked on hardware or Ops

Nothing below was attempted.

- **R0, R1:** read-only host inspection of CaticornQueen, the media server, the host running the fork agent, and the ops host (Serve status, machine variables, launch services, credential inventory, running Standard commit, `live-gate-signing.key` purpose).
- **R2:** backup directory creation, rollback script parse and rehearsal, old-code-on-migrated-database check, Serve and firewall exports, media-server backups.
- **R3, R4:** physical devices and real clients: iOS, macOS, Android, TUI sessions, Puck, ESP32 Touch, W/K browser or iPad; real Standard text and voice turns; stop, disconnect and no-replay checks; renewal and revocation on live Home. Client-repository evidence is outside this repository.
- **R5:** one end-to-end run of install, upgrade and recovery on the household hosts.
- **R6, R7:** the cutover itself, stopping fork services, credential invalidation and re-pairing, archive and cleanup on every host; the later source PR for H1/H2 and errata.
- **Ops-held items:** Tailscale ACL grants, ops Alloy scrape and secret file, Loki and Grafana review.
- **Distribution:** signed macOS build (needs the Apple signing identity, client repository).

## 9. Docs-only checks run for this PR

Recorded in the PR body: `git diff --check`, and the repository's issue-tracking, contract-pack and Standard-compatibility test files. No source, deploy script or tracker status changed.
