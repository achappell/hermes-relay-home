# Home bridge contract v1

> Status: the local endpoint-adapter slice is live in HOME-NW-03. Story 2
> implements the framework-independent `HomeBridge` seam described below, and
> HOME-NW-03 serves it on one explicitly configured local WebSocket route. Route
> discovery, identity proof, browser bootstrap, and roaming remain future work.

This page gives front ends the stable contract for the Home route adapter. The
local `/api/v1/bridge/ws` route is live for the HOME-NW-03 slice; a connection
refusal or `404` outside that configured listener is an implementation-status
signal, not proof that a device credential or conversation handle is invalid.

## Compatibility classification

The vanilla reference is the pinned local Hermes Agent `0.21.1` checkout at
the immutable commit recorded in
[`standard-baseline.md`](../../../_bmad-output/specs/spec-standard-hermes-compatibility-migration/standard-baseline.md).
The table separates that stock wire from Home-owned behavior:

| Layer | Vanilla Hermes Agent `0.21.1` | Home bridge behavior |
| --- | --- | --- |
| JSON/session socket | `/api/ws`, JSON-RPC 2.0; `gateway.ready`, `gateway.ping`, `session.create`, `session.resume`, `prompt.submit`, `session.interrupt`, `commands.catalog`, and advertised `command.dispatch` | `HomeBridge` opens this socket with a server-held token, discovers commands from `commands.catalog`, and maps the result into Home state. |
| Standard events | JSON-RPC notification `method: "event"` with `params.type` and `params.payload` | Home preserves the Standard `type` and semantic payload, then adds opaque Home conversation/turn correlation in its own envelope. |
| Response audio | Separate `/api/audio/speak-stream`; JSON `type: "start"`, raw signed-16 little-endian PCM, then `type: "end"` or `type: "fallback"` | Home associates the sidecar with a Home turn and exposes typed `AudioFrame` values; the local endpoint uses `audio.frame` only as a Home transport notification. |
| Authentication | Stock client path uses the Hermes bearer in the WebSocket URL as `?token=` | Home keeps that bearer server-side. Front ends use a Home Device credential or a short-lived browser upgrade ticket. |
| Timing | No authoritative `speech_timing` or word-offset event | Home reports `timing: "absent"` until a separately verified playback-clock/duration adapter exists. |
| Identity/recovery | Standard runtime and durable Session identifiers are part of the server protocol | Home resolves Profile/session context from an opaque conversation grant, hides runtime identity, and owns no-replay reconnect state. |

`conversation.open`, `conversation.reconnect`, `prompt.respond`,
`bridge.ping`, the Home `schema: 1` field, route objects, Device
authentication, browser tickets, `unresolved_turn`, and the `audio.frame`
notification are not vanilla Hermes methods or fields. A front end must send
those to the local Home adapter, never directly to the vanilla `/api/ws`
socket. Conversely, the Home adapter must translate its calls to the stock
Standard operations above rather than inventing a second Hermes protocol.

The pinned baseline records a real text-and-response-PCM check from the TUI
against the stock release. Story 2’s Home bridge evidence is currently
deterministic fake-gateway/fake-audio coverage; it is not a claim of a live
Home-to-vanilla integration run.

## Ownership and current boundary

Home owns endpoint authorization, conversation grants, Profile/session binding,
route selection, and the server-held Hermes bearer credential. Hermes remains
the authority for Session state, answers, Standard events, and response audio.
For binding/open, the front end supplies only its Home credential and an opaque
conversation handle. It may submit current user text through `prompt.submit`,
but it never supplies a Profile ID, Hermes Session ID, Hermes bearer token,
stored transcript, or audio stream identity.

The live Story 2 seam is the Python `HomeBridge` object. Its endpoint-facing
methods are:

| Operation | Current result or effect |
| --- | --- |
| `open(headers, conversation_handle)` | Authenticates the device, resolves the Home grant, opens Standard `/api/ws`, and returns `BridgeStatus`. |
| `reconnect(headers)` | Re-authenticates the existing binding and resumes the durable Standard Session without resubmitting an uncertain turn. Returns `BridgeStatus`. |
| `submit_prompt(text)` | Sends one non-empty prompt after Standard accepts it and returns a Home `BridgeTurn`. A transport failure after send is uncertain and is never retried by the bridge. |
| `next_event()` | Returns one `BridgeEvent` with the Standard event `type`, its semantic payload, and Home correlation fields. |
| `next_audio()` | Returns typed `AudioFrame` values from the separate Standard `/api/audio/speak-stream` sidecar. |
| `interrupt()` | Requests interruption; the matching Standard terminal event remains the completion authority. |
| `respond_prompt(event, response)` | Maps a validated approval, clarify, secret, or sudo response to the matching Standard operation. |
| `dispatch_command(name, arg)` | Dispatches only a command advertised by Standard `commands.catalog`. |
| `ping()` | Exercises the Standard liveness operation. It is not a substitute for a timing signal. |

`BridgeStatus.to_endpoint()`, `BridgeTurn.to_endpoint()`, and
`BridgeEvent.to_endpoint()` currently return Home domain dictionaries, not
JSON-RPC envelopes. The route adapter must wrap those domain values in the
wire contract below; a front end must not assume that calling the Python seam
means a public route already exists.

The current domain shapes are:

```json
{
  "schema": 1,
  "status": "ready",
  "conversation_handle": "opaque-home-handle",
  "capabilities": {
    "commands": ["status"],
    "heartbeat": true,
    "timing": "absent"
  }
}
```

```json
{
  "schema": 1,
  "conversation_handle": "opaque-home-handle",
  "turn_id": "home-turn-1",
  "status": "submitted"
}
```

```json
{
  "schema": 1,
  "conversation_handle": "opaque-home-handle",
  "turn_id": "home-turn-1",
  "correlation_id": "standard-request-id",
  "event": {
    "type": "message.delta",
    "payload": {"rendered": "hello"}
  }
}
```

`open()` can return `unavailable` with `reconnect_required` when the binding
has an unresolved turn. The caller must use the separate `reconnect()` method.
An explicit reconnect can return `ready` while still carrying
`unresolved_turn`; that older turn remains unresolved and is never silently
replayed.

## Planned endpoint transport

The target endpoint path is:

```text
wss://<selected-approved-route>/api/v1/bridge/ws
```

The route selector chooses only an approved route, proves the same Household
Identity, and reports the selected route separately from bridge and turn state.
The final discovery, identity-proof, TLS, and public reverse-proxy deployment
remain route-roaming work; they are not implemented by HOME-NW-03.

Native clients or a trusted client proxy send the Home credential on the
upgrade request:

```http
Authorization: Device <device-credential>
```

The credential is never placed in a URL, WebSocket subprotocol, JSON body,
snapshot, log, or diagnostic. A browser WebSocket cannot set an arbitrary
`Authorization` header, so a browser-facing deployment needs a short-lived
bootstrap capability. The planned shape is:

```http
POST /api/v1/bridge/ws-ticket
Authorization: Device <device-credential>
Content-Type: application/json

{}
```

```json
{
  "schema": 1,
  "websocket_ticket": "single-use-short-lived-value",
  "expires_at": "2026-09-14T20:00:00Z"
}
```

The browser then opens the WebSocket with the ticket:

```text
wss://<selected-approved-route>/api/v1/bridge/ws?ticket=<websocket-ticket>
```

The ticket is a one-use upgrade capability, not the device credential. The
adapter must expire it quickly (the target is 60 seconds), consume it
atomically, avoid logging or caching it, and bind it to the same approved route
and device authorization. If the deployment does not provide this bootstrap
route, a browser front end needs a same-origin trusted proxy that injects the
device header; it must not put the device credential in the WebSocket URL.

## JSON-RPC envelope

The local endpoint uses JSON-RPC 2.0 with Home `schema: 1` on every
Home-owned JSON frame. Requests use a unique client-generated `id`. Hermes
credentials and runtime Standard session identifiers never appear in a frame.

```json
{
  "jsonrpc": "2.0",
  "schema": 1,
  "id": "open-1",
  "method": "conversation.open",
  "params": {
    "conversation_handle": "opaque-home-handle"
  }
}
```

`conversation.open` and `conversation.reconnect` use a successful JSON-RPC
response for both `ready` and a typed `unavailable` state. This mirrors the
implemented `BridgeStatus`; an unavailable result is not a healthy session.

```json
{
  "jsonrpc": "2.0",
  "schema": 1,
  "id": "open-1",
  "result": {
    "schema": 1,
    "status": "ready",
    "conversation_handle": "opaque-home-handle",
    "route": {"class": "home", "id": "approved-route-label"},
    "capabilities": {
      "commands": ["status"],
      "heartbeat": true,
      "timing": "absent"
    }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "schema": 1,
  "id": "open-1",
  "result": {
    "schema": 1,
    "status": "unavailable",
    "conversation_handle": "opaque-home-handle",
    "reason": "stale_conversation"
  }
}
```

### Methods

| Method | Parameters | Success behavior |
| --- | --- | --- |
| `conversation.open` | `conversation_handle` | Returns `ready` or typed `unavailable`; the handle remains opaque. |
| `conversation.reconnect` | Same handle on a new binding; it may be omitted when the socket is already bound. | Re-authenticates and resumes the existing Session. It never resubmits a prompt or replays an old response. |
| `prompt.submit` | `conversation_handle`, non-empty `text` | Returns a Home `turn_id` only after Standard accepts the prompt. |
| `session.interrupt` | `conversation_handle`, active `turn_id` | Returns an acknowledgement; wait for the matching Standard terminal event before showing completion. |
| `prompt.respond` | `conversation_handle`, `turn_id`, `correlation_id`, `event_type`, and validated `response` | Resolves the matching structured prompt only while it is current. |
| `command.dispatch` | `conversation_handle`, advertised `name`, optional `arg` | Dispatches only when the name was advertised in `capabilities.commands`. |
| `bridge.ping` | `conversation_handle` | Returns the Standard liveness result. Do not gate this method on the automatic-heartbeat flag. |

The structured prompt event types and response keys are fixed at this boundary:

| Event type | Standard operation | Response key |
| --- | --- | --- |
| `approval.request` | `approval.respond` | Required `choice`; optional boolean `all` |
| `clarify.request` | `clarify.respond` | `answer` |
| `secret.request` | `secret.respond` | `value` |
| `sudo.request` | `sudo.respond` | `password` |

Prompt sensitivity, options, values, expiry, turn ownership, and correlation
IDs remain visible to the endpoint adapter. A prompt response is not ordinary
model input and must not be converted into a new `prompt.submit`.

### Events

The local adapter sends Standard events as JSON-RPC notifications. The
Standard `event.type`, semantic payload, order, cumulative-preview meaning,
turn ownership, and prompt correlation are preserved:

```json
{
  "jsonrpc": "2.0",
  "schema": 1,
  "method": "event",
  "params": {
    "schema": 1,
    "conversation_handle": "opaque-home-handle",
    "turn_id": "home-turn-1",
    "correlation_id": "standard-request-id",
    "event": {
      "type": "message.complete",
      "payload": {"rendered": "hello", "status": "completed"}
    }
  }
}
```

“Preserve the payload” does not mean “forward every key.” The current internal
sanitizer recursively removes `session_id`, `runtime_session_id`,
`stored_session_id`, and `session_key`; it does not provide a general public
credential/Profile scrubber. The route adapter must allowlist its Home
envelope and remove or reject Profile IDs, credentials, bearer tokens, runtime
session identities, and other server-only fields before serialization. A
front end must not treat `BridgeEvent.to_endpoint()` as a complete public
security boundary.

Global events may omit `turn_id`; they cannot complete or retarget the active
turn. A terminal event is the completion authority. `session.interrupt` being
accepted is not itself a terminal outcome.

### Errors

Malformed JSON-RPC uses the standard JSON-RPC error envelope. Method-level
domain failures use a JSON-RPC error with a stable Home code in
`error.data.code`:

```json
{
  "jsonrpc": "2.0",
  "schema": 1,
  "id": "prompt-2",
  "error": {
    "code": -32000,
    "message": "prompt delivery timed out",
    "data": {
      "schema": 1,
      "code": "transport_timeout",
      "delivery": "uncertain"
    }
  }
}
```

Stable codes are:

- `invalid_request` — the Home method or known field is malformed; delivery did
  not start;
- `authorization_unavailable` — Home could not safely authenticate or resolve
  the authorization dependency;
- `unauthorized` — the credential or device/handle binding is not authorized;
- `stale_conversation` — the opaque handle is no longer active;
- `conversation_mismatch` — Profile or durable Session binding changed;
- `request_rejected` — Standard explicitly rejected the operation;
- `transport_unavailable` — transport ended before delivery was known;
- `transport_timeout` — a bounded wait expired before delivery was known;
- `protocol_error` — Standard returned an invalid or unsupported wire shape;
- `capability_unavailable` — the requested optional command or prompt operation
  was not advertised or supported;
- `hermes_unavailable` — Home could not establish the Standard Session.

For operations that can send input, `request_rejected` means known
non-delivery; `transport_unavailable` and `transport_timeout` mean uncertain
delivery. The adapter must not automatically retry either transport outcome.
For `conversation.open` and `conversation.reconnect`, these same conditions
are represented as `result.status: "unavailable"` with `reason`, because those
operations return the bridge's readiness state rather than a submitted turn.

Route selection adds a separate, safe layer of reasons:
`route_unavailable`, `route_unauthorized`, `route_identity_mismatch`, and
`route_timeout`. A route failure must not be reported as a healthy bridge, and
a ready route must not be reported as proof that a prior turn was delivered.

## Response audio

The implemented bridge reads Standard response audio from the separate
`/api/audio/speak-stream` sidecar. The local endpoint adapter keeps the
Standard frame kinds rather than renaming them into new Hermes events. It uses
JSON `audio.frame` notifications for sidecar control frames and raw binary
frames for PCM:

```json
{
  "jsonrpc": "2.0",
  "schema": 1,
  "method": "audio.frame",
  "params": {
    "schema": 1,
    "conversation_handle": "opaque-home-handle",
    "turn_id": "home-turn-1",
    "frame": {
      "kind": "start",
      "sample_rate": 24000,
      "channels": 1,
      "sample_width": 2,
      "byte_order": "little"
    }
  }
}
```

After `kind: "start"`, binary frames are raw signed 16-bit little-endian
mono PCM. The adapter may split PCM at transport boundaries; clients append
binary data until the next JSON `audio.frame` notification:

- `kind: "end"` — the audio stream ended normally;
- `kind: "fallback"` — the sidecar fell back or produced no usable PCM;
- `kind: "unavailable"` — audio failed with a safe `reason` in the frame;
- `kind: "start"` — the metadata establishes the format for following bytes.

There are no public `audio.start`, `audio.end`, or `audio.fallback` Hermes
methods. Those names would rename Standard sidecar frame kinds. Text events
remain available when the optional audio sidecar fails. PCM before valid start,
invalid metadata, duplicate starts, and an incomplete final sample are typed
audio failures, not alternate text answers. The pinned baseline has no
authoritative `speech_timing` event; expose `timing: "absent"` unless a later
verified playback-clock adapter supplies evidence.

## Reconnect state machine

Front ends should implement the following policy:

1. Authenticate and send `conversation.open` with the opaque handle.
2. Accept new input only after `result.status` is `ready`.
3. Render events and optional audio by `conversation_handle` and `turn_id`.
4. If a prompt or control operation returns `transport_unavailable` or
   `transport_timeout`, show delivery as uncertain and do not retry it.
5. Send `conversation.reconnect` with fresh authorization. A successful
   response may include `unresolved_turn`; show that state separately.
6. Require a fresh user action for any new prompt. Never auto-resubmit the
   unresolved prompt and never treat reconnect readiness as completion.

This contract preserves the implemented Home/Standard ownership boundary while
leaving route discovery, identity proof, TLS deployment, and the production
endpoint adapter as later work.

## Front-end story handoff

Stories that consume this boundary must read the Home BMAD context before
planning or changing a client adapter. Read it in this order:

1. This `bridge-contract.md` for the endpoint-facing contract and its local
   live status versus future roaming work.
2. `SPEC.md` and `route-session-state.md` in this directory for route priority,
   Household Identity, connection state, and uncertain-turn rules.
3. `../spec-standard-hermes-compatibility-migration/standard-baseline.md` for
   the vanilla Hermes Agent `0.21.1` wire and capability boundary.
4. `../spec-standard-hermes-compatibility-migration/surface-migration-matrix.md`
   for the surface-specific evidence and ownership row.
5. `../spec-home-service-foundation/credential-lifecycle.md` for Device
   credential lifecycle and storage constraints.
6. `../spec-standard-bridge/transport-contract.md` for the Home seam's
   deterministic behavior and failure vocabulary.

The local story agent prompt can point at the same context explicitly:

```text
Home dependency context is in
~/Development/hermes-relay-home/_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md
and its linked companions. Read those files before planning. Treat the
vanilla Standard 0.21.1 rows as Hermes-owned and the Home envelope, Device
credential, route, reconnect, and redaction rows as Home-owned. The local Home
adapter is live in HOME-NW-03; do not claim route-roaming integration or invent
a second Hermes protocol. Keep this surface's existing session/presentation
boundary and record fake/live evidence in this repository.
```

The next-wave front-end stories consume the same contract with different local
evidence:

| Story | Consumer | Local adapter responsibility |
| --- | --- | --- |
| Story 3 | TUI | Keep `SessionProtocol` stable; add opt-in Home route/credential selection, normalized Standard events, audio/timing capability state, interrupt confirmation, reconnect, and rollback evidence. |
| Story 4 | iOS/macOS | Keep native presentation, local Profile/history identity, playback, and lifecycle boundaries; add Home credential/route/reconnect handling and XCTest/live evidence. |
| Story 5 | Android | Keep native Profile/history, audio, and lifecycle boundaries; add secure Home credential conversion, route/reconnect/unavailable handling, and JVM/instrumentation/live evidence. |

Each agent owns its repository's implementation and validation. A Home contract
reference is an upstream dependency, not permission to edit Home status records,
copy the server-held Hermes credential into a client, or mark another surface
complete. If the local endpoint is absent from a deployment, a front-end story
may build its adapter against a fake Home bridge and record the live route as a
blocked integration gate; it must not silently fall back to direct Hermes
bearer access as the Home implementation.
