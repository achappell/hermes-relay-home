---
id: standard-hermes-baseline-0.21.1
status: pinned
updated: 2026-09-13
source_commit: 2237be355906fbe6065ce1815711eee52b2d646e
---

> **Approved correction (2026-09-23):** The [delivery contract](../../implementation-artifacts/course-correction-2026-09-23.md) supersedes conflicting mode, authentication, rollback, timing and session clauses below. Both modes require unmodified Standard. Home mode uses Home credentials; direct personal mode uses supported Standard authentication. The old fork path is retired under HOME-MIG-09; direct Standard remains supported.

# Pinned Standard Hermes Baseline

This is the immutable Story 1 baseline for the Standard Hermes migration. Test
fixtures, bridge work, and surface evidence must use the release commit below;
the current moving source branch is not a release pin.

## Pin

| Item | Pinned value |
|---|---|
| Hermes release | `0.21.1` |
| Release date | `2026-09-07` |
| Immutable source commit | `2237be355906fbe6065ce1815711eee52b2d646e` |
| Source checkout | `~/Development/hermes-agent-relay-v0.21.0-minimal` |
| Standard JSON/session endpoint | `ws(s)://<hermes-host>/api/ws` |
| Standard response-audio endpoint | `ws(s)://<hermes-host>/api/audio/speak-stream` |
| Rollback-only endpoint | `/voice-session` from the fork plugin |

The commit is the authority. A branch or a later checkout that happens to
report version `0.21.1` is not sufficient evidence for compatibility.

## Observed stock contract at the pin

| Capability | Stock behavior at `0.21.1` | Migration contract |
|---|---|---|
| JSON/session | `/api/ws` is a JSON-RPC 2.0 gateway. It emits `gateway.ready`, supports gateway ping, and supports session create/resume and `prompt.submit`. The gateway has one receive owner. | Home maps this wire contract into the existing `SessionProtocol` without introducing a second Hermes parser or changing event meaning. Preserve event identity and order. |
| PCM/audio | Response audio is on the separate `/api/audio/speak-stream` socket. The stream announces sample rate and mono channels, then sends raw signed-16 little-endian PCM frames and a terminal end or fallback message. `/api/ws` is JSON-only. | Home owns the association between a turn and its audio socket. Surfaces consume verified PCM metadata; they do not infer audio format from arrival or guess missing metadata. |
| Timing | The pinned stock gateway/audio contract has no `speech_timing` or word-offset event. | Timing is explicitly absent until a surface proves an authoritative playback-clock or duration contract. Network arrival time is never timing authority. |
| Prompts | The gateway can emit correlated approval, clarification, secret, and sudo request events on the session stream. | Preserve request identity, correlation, sensitivity, and terminal resolution. A surface without the required UI reports typed unavailability and does not collect a value through an ordinary turn. |
| Commands | Command dispatch is an explicit gateway operation (`command.dispatch`) when the capability is advertised. The `commands.catalog` result contains `pairs` as two-item `[slash_name, description]` arrays; `gateway.ready` does not provide the command catalog. | Home derives bare command names only from valid `commands.catalog` `pairs`, ignores any ready command list, and exposes or dispatches no commands when the catalog is missing, rejected, or malformed. A command is never smuggled into model input as a prompt. |
| Interrupt | `session.interrupt` is the Standard control operation. A request or acknowledgement is not completion; the surface waits for the matching cancelled/interrupted terminal state. | Preserve interrupt correlation and honest cancellation state. No path switch or replay occurs mid-turn. |
| Liveness | `gateway.ready` advertises heartbeat support; the gateway also exposes ping. Reconnect requires fresh readiness before new work. | Each adapter has one owner for receive/liveness state and exposes disconnect/reconnect state before accepting work. |
| Authentication | The stock gateway client path uses a server credential in the WebSocket URL (`?token=`). | Home-facing clients use the approved device credential. The Hermes credential stays in Home or the server-side process and never reaches a paired endpoint, browser, display, or hardware surface. |
| Recovery | Standard sessions can resume with a session identifier, but the stock path does not provide a safe replay proof for an uncertain turn. | Reconnect or resume may restore session continuity; an uncertain submission requires a fresh user action and is never automatically replayed. |

## Capability labels

The matrix uses these meanings:

- **Required** — the target adapter must prove the behavior before it can be
  selected as the default for that surface.
- **Conditional** — preserve the behavior when Standard advertises and exposes
  it; otherwise report a typed unavailable state.
- **Explicitly absent** — the baseline does not provide the behavior, so a
  surface must not imply that it does.
- **Rollback-only** — behavior retained to recover the current deployment; it
  is not a Standard acceptance criterion.

## Rollback-only ledger

The current fork plugin exposes a direct `/voice-session` WebSocket with
`Authorization: Bearer` authentication, `hello`/`turn`/`interrupt`/`ping`
frames, fork-specific JSON events, binary raw PCM, and optional
`speech_timing`. It also has fork-only command and structured-prompt
capabilities. Those details remain frozen as rollback evidence so a failed
migration can be reversed deliberately.

They are not target requirements. In particular, Standard compatibility does
not require the `/voice-session` route, its `hello` or `turn` frames, its
fork-specific event names, its direct bearer-token boundary, or its timing
extension. A dependency tree built around those details is yesterday's wiring
kept under glass; it must not decide whether the Standard path is accepted.

## Evidence recorded for this checkpoint

- Source identity and endpoint behavior were inspected at the pinned release
  commit, not only at the moving `0.21.1` checkout.
- The TUI Standard gateway validation record on `2026-09-13` completed a real
  text turn against the pinned stock release and received response PCM from
  the separate audio sidecar. It recorded the absence of a speech-timing
  extension and kept the custom gateway untouched.
- A loopback capture on `2026-10-11` ran the pinned commit (`hermes serve
  --isolated`, throwaway state, scripted stub model, no credentials) and
  recorded its clarify wire behavior as fixtures in
  `tests/fixtures/standard/clarify-*.json`:
  - `clarify.request` is always a batch
    `{"questions":[{"qid","question","choices","multi_select"}],"request_id"}`
    with `choices: null` for free text. There are at most four choices, and
    with two or more the first carries a trailing `(Recommended)` on the wire.
    The event has no turn ID.
  - `clarify.respond` stores `answer` verbatim and does not validate it against
    `choices`; the clarify tool removes a trailing `(Recommended)` before the
    agent sees the answer. A batch answer without `question_id` is lost, an
    unknown `question_id` is error `4002`, and a resolved or unknown
    `request_id` answers `{"status":"expired"}` as an ordinary result.
  - The clarify waits `agent.clarify_timeout` (default 3600 seconds) and then
    emits `clarify.expire {request_id}`. `session.interrupt` emits no
    `clarify.expire`.
  - After a client socket drops, `session.resume` returns `pending_clarify`
    with the same `request_id` and the clarify is not re-emitted as an event.
  - `gateway.ready` carries no `capabilities`, so pinned Standard advertises no
    `prompt.choose` or `prompt.explore`.
- Home bridge and the remaining surface adapters are not claimed complete by
  this checkpoint. Their owning stories must prove the rows in the matrix.
