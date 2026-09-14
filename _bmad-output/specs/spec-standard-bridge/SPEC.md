---
id: SPEC-standard-bridge
companions:
  - transport-contract.md
  - ../../../docs/project-context.md
  - ../../planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md
  - ../spec-home-bridge-route-roaming/SPEC.md
  - ../spec-home-bridge-route-roaming/route-session-state.md
  - ../spec-standard-hermes-compatibility-migration/SPEC.md
  - ../spec-standard-hermes-compatibility-migration/standard-baseline.md
  - ../spec-standard-hermes-compatibility-migration/surface-migration-matrix.md
  - ../../../docs/contracts/v1/bridge.md
sources:
  - ../../../docs/standard-bridge.md
---

> **Canonical contract.** This SPEC and the files in `companions:` define the
> Story 2 bridge seam that Home must build, test, and validate. The route-roaming
> and Standard-migration companions retain ownership of the public Home route
> and the pinned cross-surface rollout.

# Story 2: Standard Hermes Bridge for Home

## Why

Home is the household authority, while Hermes remains authoritative for
sessions, answers, and speech. Story 2 closes the gap between those boundaries:
an authorized Home endpoint needs a tested Standard Hermes bridge that carries
text, optional response audio, prompts, commands, interruption, and recovery
without exposing the Hermes credential or inventing fork-only behavior.

## Capabilities

- **CAP-1**
  - **intent:** Authorized Home callers can open or resume a Standard Hermes
    conversation while retaining the correct Profile and session identity.
  - **success:** Deterministic fixtures prove gateway readiness, session create
    and resume, safe unavailable states, fail-closed Profile/session mismatches,
    and no Hermes bearer credential or direct Profile/session input at the Home
    endpoint boundary.

- **CAP-2**
  - **intent:** Home can submit a non-empty prompt and expose ordered,
    correlated Standard text updates with cumulative previews and an explicit
    terminal state.
  - **success:** Fixtures prove JSON-RPC acceptance and rejection, prompt and
    session correlation, filtering of unrelated events, preserved event names
    and order, whitespace rejection, and terminal completion semantics.

- **CAP-3**
  - **intent:** Home can deliver response audio alongside text without
    corrupting or duplicating PCM frames or making readable text depend on
    optional audio.
  - **success:** Separate audio fixtures prove signed-16 little-endian PCM,
    start metadata, reassembly across transport frames, cumulative-text suffix
    handling, and typed audio failure with text still available.

- **CAP-4**
  - **intent:** Home can interrupt a live turn, answer structured prompts, and
    expose and dispatch advertised commands with honest outcomes.
  - **success:** Fixtures prove that interrupt acknowledgement is not treated
    as completion, the matching terminal event is required, prompt identity and
    sensitivity survive response mapping, and unsupported or rejected commands
    remain typed outcomes.

- **CAP-5**
  - **intent:** Home can recover a lost Standard connection and resume the
    existing conversation without replaying prior input or binding to a
    different Profile or session.
  - **success:** Transport-loss fixtures prove fresh authorization and
    readiness, session resume, bounded waits, no automatic prompt replay, no
    path switch, and honest unavailable or ready state after recovery.

## Constraints

- The compatibility target is Standard Hermes `0.21.1` at `/api/ws` for
  JSON-RPC session traffic and `/api/audio/speak-stream` for response audio;
  the fork-only `/voice-session` path is rollback evidence, not a target.
- Home owns endpoint authorization and keeps the Hermes bearer credential
  server-side. Credentials must not enter endpoint payloads, URLs, logs,
  snapshots, prompts, transcripts, audio, or diagnostics.
- The bridge remains framework-independent and must be testable through fake
  ports and deterministic fixtures without a live Hermes endpoint or physical
  hardware.
- One reader owns gateway receive state and demultiplexes RPC responses from
  events; writes are serialized; readiness, RPC, event, and audio waits are
  bounded; ping is used only for the advertised liveness operation.
- Standard event identity, meaning, order, cumulative text-preview semantics,
  append-only audio-suffix semantics, and signed-16 little-endian PCM metadata
  remain intact.
- Profile and session mismatches fail closed, conversation handles remain
  opaque to endpoint callers, and unsupported optional capabilities are
  reported rather than guessed.
- Reconnect resumes an existing session without replaying prompts or switching
  transport paths automatically. An interrupted turn reaches an honest
  terminal state, and a transport uncertainty remains distinguishable from an
  explicit rejection.
- A response-audio sidecar may fail independently; readable text survives and
  the audio failure is typed and observable.
- The production Home route adapter and roaming identity proof belong to the
  route-roaming specification; this slice must not implement them.

## Non-goals

- Implement the production Home endpoint adapter, route path, or roaming
  identity proof. The endpoint contract is pinned by the route-roaming
  specification and `docs/contracts/v1/bridge.md`.
- Implement live Hermes deployment validation, physical hardware adapters,
  client integrations, production framework wiring, persistence, or pairing.
- Add timing or latency authority that Standard Hermes does not explicitly
  provide.
- Create a second conversation authority or change Hermes session, answer, or
  speech ownership beyond this adapter seam.
- Replay a pending prompt or switch transport paths automatically after
  reconnect.

## Success signal

The deterministic fake-gateway and fake-audio suite proves authorized session
create/resume, ordered text, separate PCM audio, interruption, structured
prompts, advertised commands, bounded reconnect, no replay, typed failures,
and credential isolation at the Home boundary. The bridge is ready for the
later route-envelope slice without changing Standard event meaning or making a
second assistant authority.

## Assumptions

- The existing Home authorization and `ConversationGrant` seam supplies
  endpoint authorization while the endpoint-facing v1 envelope is pinned by
  the route-roaming specification and `docs/contracts/v1/bridge.md`.
- The pinned Standard Hermes `0.21.1` baseline and migration artifacts are the
  accepted compatibility authority for this slice.
- Unit-level deterministic fixtures are sufficient for Story 2; live Hermes
  and hardware validation are later gates.

## Open Questions

- Which production Home route adapter and roaming identity proof will consume
  this bridge seam?
- Which live Standard Hermes smoke test, if any, becomes a release gate after
  deterministic compatibility passes?
- Should the bridge integrate with the existing `SessionProtocol` in the route
  slice, or remain a framework-independent seam?

## Review Findings

- [x] [Review][Patch] Preserve an endpoint-visible unresolved prior turn after reconnect [src/hermes_home/bridge/standard.py:734-827]

- [x] [Review][Patch] Accept Standard events whose optional `payload` is omitted; the pinned `message.start` event has no payload [src/hermes_home/bridge/standard.py:1562-1574]
- [x] [Review][Patch] Strip the runtime Hermes Session ID from endpoint event payloads [src/hermes_home/bridge/standard.py:141-164]
- [x] [Review][Patch] Bound socket setup and writes, and make optional audio setup independent from text submission [src/hermes_home/bridge/standard.py:247-337,990-1012,1390-1462]
- [x] [Review][Patch] Enforce the pinned `/api/ws` and `/api/audio/speak-stream` transport paths [src/hermes_home/bridge/standard.py:247-249,1552-1560]
- [x] [Review][Patch] Revalidate the Home grant before ready-state prompt, command, prompt-response, and ping operations [src/hermes_home/bridge/standard.py:834-995]
- [x] [Review][Patch] Fence Standard events to the active Home turn and reject or discard stale event generations [src/hermes_home/bridge/standard.py:1088-1116]
- [x] [Review][Patch] Preserve protocol errors and explicit Standard RPC rejection as typed outcomes [src/hermes_home/bridge/standard.py:680-691,799-808,1056-1074]
- [x] [Review][Patch] Never use a runtime Session ID as a durable resume identity and validate returned Profile/session binding [src/hermes_home/bridge/standard.py:673-706,783-821]
- [x] [Review][Patch] Normalize resolver status values to the documented safe reason vocabulary [src/hermes_home/bridge/standard.py:648-652,756-760]
- [x] [Review][Patch] Convert result-level command rejection into `BridgeRequestRejected` [src/hermes_home/bridge/standard.py:834-868]
- [x] [Review][Patch] Enforce structured-prompt expiry, active-turn ownership, and accepted terminal resolution [src/hermes_home/bridge/standard.py:921-967,1139-1150]
- [x] [Review][Patch] Validate structured response types and pinned batch-clarify/approval-all semantics [src/hermes_home/bridge/standard.py:13-19,908-940]
- [x] [Review][Patch] Reject late command, ping, and response results after the bridge binding changes [src/hermes_home/bridge/standard.py:834-888,941-967,1492-1515]
- [x] [Review][Patch] Authenticate before revealing state and preserve the existing binding until a replacement open succeeds [src/hermes_home/bridge/standard.py:603-629]
- [x] [Review][Patch] Reject duplicate in-flight JSON-RPC request IDs [src/hermes_home/bridge/standard.py:319-325]
- [x] [Review][Patch] Convert EOF and unexpected reader exceptions into a recorded transport failure [src/hermes_home/bridge/standard.py:413-431]
- [x] [Review][Patch] Reject non-finite timeout values [src/hermes_home/bridge/standard.py:491-496]
- [x] [Review][Patch] Enforce pinned audio frame types and mono PCM metadata before interpreting sidecar frames [src/hermes_home/bridge/standard.py:1304-1373]
- [x] [Review][Patch] Feed audio from raw append-only text and validate cumulative/interim state before emitting suffixes [src/hermes_home/bridge/standard.py:1124-1175,1642-1663]
- [x] [Review][Patch] Validate explicit `message.complete` status values before changing turn state [src/hermes_home/bridge/standard.py:1124-1138,1674-1686]
- [x] [Review][Patch] Add an initial unauthorized-open fixture proving no resolver or Hermes call occurs [tests/test_standard_bridge.py:149-216,1577-1616]
- [x] [Review][Patch] Parameterize structured-prompt mapping and stale-response coverage for clarify, secret, and sudo [tests/test_standard_bridge.py:967-1107]
- [x] [Review][Patch] Add a JSON-RPC command rejection fixture [tests/test_standard_bridge.py:495-547]
- [x] [Review][Patch] Add a reconnect fixture for a mismatched returned durable Session ID [tests/test_standard_bridge.py:1671-1757]

- [x] [Review][Defer] Decide whether Home consumes Standard event sequence/cursor replay after reconnect [src/hermes_home/bridge/standard.py:783-791,1562-1599] — deferred: the pinned Standard release supports `session.events.since` and monotonic event sequence numbers, while this story explicitly forbids replaying uncertain prompts and old responses; the route-level contract must settle whether missed non-response events are intentionally discarded or replayed.

### Rejected

- false — `gateway.ping` is a Standard operation independent of the optional automatic-heartbeat advertisement; the pinned baseline explicitly exposes both.
- false — `interrupt()` returns request acceptance while `next_event()` carries the required terminal wait; the existing interrupt fixture proves that acknowledgement is not completion.
- false — identity-less global events are emitted without a turn ID and cannot complete the active turn; the bridge owns one gateway/session, so this does not retarget another Home conversation.
- false — concrete Home route/runtime wiring is an explicit non-goal of Story 2 and belongs to the route-roaming slice.
- false — a terminal `end`/`fallback` frame without PCM does not expose usable audio; the contract requires start metadata before PCM, not before a no-audio terminal.
- false — deterministic fake ports are the stated Story 2 verification boundary; live Hermes validation is a later gate.
- low — duplicate terminal frames are an uncommon sidecar race, and preventing them would add synchronization machinery beyond a direct correction.
