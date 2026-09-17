---
title: 'Pilot bridge — preserve an accepted turn across a brief endpoint disconnect'
type: 'bugfix'
created: '2026-09-16'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

## Intent

**Problem:** The Home WebSocket endpoint owned the `HomeBridge`, its Standard
event reader, and its response-audio reader. When an endpoint transport ended,
`BridgeEndpoint.run()` closed all three. A mobile client that briefly lost its
socket after `prompt.submit` therefore lost the accepted turn's remaining text
and terminal event even though the Hermes gateway was still reachable.

**Approach:** When a socket drops during an accepted turn, detach it and park
the complete endpoint for 90 seconds. A new socket may adopt that endpoint only
through `conversation.reconnect` for the same opaque handle. Home reauthenticates
the device and refreshes the grant without reconnecting the Standard gateway,
resuming a Session, or submitting another prompt. Text events produced while
detached are bounded and delivered after the successful reconnect response.
Response audio is not replayed; it may finish and release independently while
the text turn continues. A plain `conversation.open` for a parked handle returns
`reconnect_required`.

The submit response's `correlation_id` identifies the accepted turn for
diagnostics and turn-scoped side channels. Standard events retain their own
event correlation IDs, which can identify a structured prompt request and need
not match the submit response. Clients scope response events by conversation
handle and turn ID, then preserve an event correlation ID when answering a
structured prompt.

## Safety and lifecycle

- The parking lot holds at most one endpoint per opaque conversation handle.
- A parked endpoint expires and closes after 90 seconds by default.
- Adoption rechecks the device credential, active grant, device, handle,
  Profile, and durable Session binding before releasing buffered text.
- Failed adoption closes only the candidate socket and reparks the original
  endpoint for the remaining recovery path.
- An unauthorized reconnect attempt does not restart the original grace timer.
- Buffered endpoint events are capped at 8 MiB. Overflow closes the endpoint
  and bridge rather than retaining an unbounded turn.
- Binary PCM and `audio.frame` notifications are not replayed after a drop.
- A second transport failure removes a buffered event only after its send
  succeeds.

## Verification

- Focused bridge, listener, and Standard suites: 128 passed.
- Full suite: 351 passed.
- `ruff check src tests`: passed.
- `ruff format --check src tests`: passed.
