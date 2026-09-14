# Standard Hermes bridge

`hermes_home.bridge.HomeBridge` is the framework-independent Home-to-Standard
Hermes adapter. It uses the pinned Standard Hermes `0.21.1` boundary:

- JSON-RPC 2.0 session traffic on `/api/ws`;
- response speech on the separate `/api/audio/speak-stream` socket;
- the server-held Hermes token only in Home-owned socket setup;
- an opaque Home conversation handle at the endpoint boundary.

## Boundary

`HomeBridge.open()` authenticates the endpoint, resolves its opaque handle to a
Home-owned `ConversationGrant`, waits for `gateway.ready`, and creates or
resumes the one Standard Session for that grant. The grant supplies the Profile
and optional durable Session ID; callers cannot supply either value directly.

The bridge owns endpoint identity and recovery state while leaving Standard
event meaning intact:

- `BridgeStatus.to_endpoint()` returns readiness, the opaque conversation
  handle, safe reason codes, advertised commands, heartbeat support, and the
  explicit `timing: "absent"` capability;
- `BridgeTurn.to_endpoint()` returns the opaque conversation handle and Home
  turn ID;
- `BridgeEvent.to_endpoint()` preserves the Standard event type and payload,
  with Home turn and prompt-correlation fields alongside it.

Text updates retain their Standard event names and cumulative preview payloads.
The audio sidecar receives only newly appendable text, returns start metadata
and raw signed 16-bit little-endian PCM, and is never confused with the JSON
gateway stream. PCM samples split across transport frames are reassembled
before they are exposed.

The gateway has one background reader. It demultiplexes JSON-RPC responses from
events, serializes writes, and bounds readiness, request, event, and audio
waits. `ping()` exercises the advertised Standard liveness operation; it does
not invent a timing signal.

## Recovery rules

`reconnect()` performs fresh endpoint authorization and Standard readiness,
then resumes the existing Session. It never resubmits a prompt whose delivery
was uncertain. An active turn cannot be rebound to another conversation, and a
reconnect that resolves the handle to a different Profile fails closed.

An interrupt request is not treated as completion. The bridge waits for the
Standard terminal event, and explicit request rejection remains distinguishable
from uncertain transport delivery. Audio failure leaves readable text alive;
it does not turn the text submission into a second answer authority.

Structured prompts retain their Standard event name, sensitivity, options, and
correlation ID. `respond_prompt()` maps a validated response to the matching
Standard operation (`approval.respond`, `clarify.respond`, `secret.respond`,
or `sudo.respond`) and refuses stale or uncorrelated prompts.

## Scope

The endpoint-facing v1 WebSocket envelope and public Home bridge path are
defined in [`docs/contracts/v1/bridge.md`](contracts/v1/bridge.md). This module
remains the tested Standard transport seam; it does not implement the public
Home route or its endpoint adapter. Its deterministic fixtures are validated
with:

```sh
uv run --python 3.14 --no-project --with pytest -- python -m pytest -q
```
