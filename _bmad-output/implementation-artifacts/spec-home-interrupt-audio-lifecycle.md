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
- Test: `test_home_tail_interrupt_stops_audio_and_admits_the_next_prompt` drives the real bridge server over a websocket: text terminal, buffered audio, interrupt, `end` audio frame, next prompt admitted, repeated interrupt a no-op with no upstream `session.interrupt`. Session events are held until Home binds the turn so the terminal cannot race audio admission. Red without the fix (`request_rejected`), 25/25 stable with it.
- No contract doc change: `docs/contracts/v1/README.md` does not document the client `session.interrupt` method.

## Review Triage Log

- Interrupted-turn id could leak / user stop reported as audio failure (ordering of mark vs. stop): medium, real; patched by marking before delivery and unmarking on failure.
- Retired no-op untested against newer turn: medium, real; patched with an assertion that no upstream `session.interrupt` is sent.
- Failed stop send reported as success (`_stop_audio` closes the sidecar on send error, or ownership already changed): low; either way the sidecar is gone, so the turn is released. Rejected.
- `interrupt_requested` set before the stop is sent: low; no upstream events follow a terminal turn and a second interrupt correctly sees a stop in flight. Rejected.
- Missing error-path tests (terminal without audio, failed send): low; terminal-without-audio keeps the prior `False`/`request_rejected` behavior. Rejected.
