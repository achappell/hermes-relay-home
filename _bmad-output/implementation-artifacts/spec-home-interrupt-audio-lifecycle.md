---
title: 'Home interrupt after text terminal'
type: 'bugfix'
created: '2026-10-07'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: ['{project-root}/docs/contracts/v1/README.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A client can still be playing buffered speech after Home has observed the text-terminal event. Android currently stops that local tail without notifying Home, while Home retains turn admission until its audio worker exits; the next prompt is then rejected. Home must not infer that local action.

**Approach:** On a tail stop, Android stops local playback then sends one explicit existing `session.interrupt` for the same turn. Home treats it as an audio-only cancellation after text completion, stops the response-audio sidecar and releases prompt admission without sending a redundant upstream Hermes interrupt. A repeated signal for a recently completed turn is an accepted no-op; it cannot affect a newer turn. Preserve normal active-turn interrupt behavior, no-replay semantics, and no reconnect workaround. Do not deploy Home or use a device.

</frozen-after-approval>

## Implementation Notes

- `HomeBridge.interrupt()` (`bridge/standard.py`): when the active turn is already terminal and its response-audio sidecar is still owned, it stops only that sidecar (no upstream `session.interrupt`); otherwise a terminal turn still returns `False`.
- `BridgeEndpoint._session_interrupt` (`bridge/endpoint.py`): a retired turn id is an accepted no-op that never reaches the bridge, so it cannot affect a newer turn; the turn is marked interrupted before the stop is delivered so a user stop is not reported as an audio failure and the id is not left behind when the turn releases; a rejected/failed delivery unmarks it. `_interrupted_turn_ids` is also discarded on turn release.
- Release ordering (seam: `_session_interrupt`): for an audio tail (active turn already in `_terminal_turn_ids`), the acknowledgement is sent only after `_wait_for_stale_audio()` joins the audio worker (the same bounded 1 s join `prompt.submit` already uses) and `_maybe_release_turn` runs, so an accepted interrupt is the release point and the next prompt needs no wait or retry. A worker that does not exit within the bound marks the bridge unavailable, exactly as `prompt.submit` does today. Active (non-terminal) interrupts are unchanged. Note: `prompt.submit` already joined a stale audio worker before checking admission, so the Android client never needed to wait for the acknowledgement; the new ordering makes the reply itself the guarantee and bounds client-visible state.
- Test: `test_home_tail_interrupt_stops_audio_and_admits_the_next_prompt` drives the real bridge server over a websocket: text terminal, buffered audio, interrupt, the sidecar flushing for 0.2 s after `stop`, reply already after the audio `end`, immediately next prompt admitted, repeated interrupt for the retired turn a no-op that sends no upstream `session.interrupt` and never stops the new turn's sidecar. Session events are held until Home binds the turn so the terminal cannot race audio admission. Red without the fix (`request_rejected`), red for the reply-before-release ordering without the wait, 20/20 stable with it.
- Smoke (throwaway, not committed): the real `hermes-home` process (`uv run hermes-home`, real sockets, paired Device credential, client claim) against a local fake Standard Hermes (`/api/ws` + `/api/audio/speak-stream`, 0.2 s flush after stop). Result with the fix: interrupt acknowledged after the sidecar ended (0.24 s), next prompt admitted immediately as `home-turn-2`, no upstream `session.interrupt`, old-turn interrupt left turn 2's sidecar untouched (stops on connection 1 only). Without the wait the smoke fails on "acknowledged before the audio tail ended"; against `origin/main` the interrupt is rejected.
- Docs: `_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md` documents the audio-tail interrupt. Changelog: release-please generates it from the conventional commit/PR title (`changelog-type: github`; no `CHANGELOG.md`).

## Review Triage Log

- Interrupted-turn id could leak / user stop reported as audio failure (ordering of mark vs. stop): medium, real; patched by marking before delivery and unmarking on failure.
- Retired no-op untested against newer turn: medium, real; patched with an assertion that no upstream `session.interrupt` is sent.
- Failed stop send reported as success (`_stop_audio` closes the sidecar on send error, or ownership already changed): low; either way the sidecar is gone, so the turn is released. Rejected.
- `interrupt_requested` set before the stop is sent: low; no upstream events follow a terminal turn and a second interrupt correctly sees a stop in flight. Rejected.
- Missing error-path tests (terminal without audio, failed send): low; terminal-without-audio keeps the prior `False`/`request_rejected` behavior. Rejected.
