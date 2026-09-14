# Home bridge WebSocket contract v1

This is the endpoint-facing contract for a paired session-bearing surface. It
keeps Home as the household authority and Hermes as the authority for session,
answer, and speech meaning. It is distinct from the Standard Hermes sockets:
Home speaks Standard Hermes on its server side and exposes this bounded bridge
to an approved endpoint.

## Boundary

The endpoint opens:

```text
wss://<approved-home-route>/api/v1/bridge/ws
```

The WebSocket upgrade must carry:

```http
Authorization: Device <device-credential>
```

The device credential is never placed in the URL. Missing or invalid
credentials fail the upgrade with HTTP `401`; Home does not reveal route,
conversation, Profile, or Session state before authentication succeeds.

Each WebSocket text frame is one UTF-8 JSON object. Session and control frames
use JSON-RPC 2.0 with an additional top-level `schema: 1` member. The `id` is
an endpoint request correlation value; it is not a Hermes Session ID or a
Home turn ID. A client may use string IDs only, which keeps logging and test
fixtures easy to redact.

The endpoint path is versioned independently of the Standard Hermes paths:

| Boundary | v1 path | Credential owner |
| --- | --- | --- |
| Endpoint ↔ Home bridge | `/api/v1/bridge/ws` | Home device credential |
| Home ↔ Standard JSON gateway | `/api/ws` | Home-held Hermes credential |
| Home ↔ Standard response audio | `/api/audio/speak-stream` | Home-held Hermes credential |

The endpoint never receives the Hermes credential, runtime Hermes Session ID,
internal Profile ID, or Home database record. Opaque conversation and turn
handles are safe endpoint identifiers, not authority.

## Conversation lifecycle

The Home claim flow gives the endpoint an opaque `conversation_handle`. The
endpoint does not derive one from a local Profile, submit a Profile ID, or
invent a Session ID. It sends:

```json
{
  "schema": 1,
  "jsonrpc": "2.0",
  "id": "open-1",
  "method": "conversation.open",
  "params": {
    "conversation_handle": "opaque-conversation-1"
  }
}
```

Home validates the device credential and handle together, resolves the Home
grant, waits for Standard `gateway.ready`, and creates or resumes the one
bound Standard Session. A successful result is:

```json
{
  "schema": 1,
  "jsonrpc": "2.0",
  "id": "open-1",
  "result": {
    "schema": 1,
    "status": "ready",
    "conversation_handle": "opaque-conversation-1",
    "route": {"class": "home", "id": "route-home"},
    "capabilities": {
      "heartbeat": true,
      "timing": "absent",
      "commands": [],
      "interrupt": true,
      "audio": true
    }
  }
}
```

`route.class` is one of `home`, `tailscale`, or explicitly enabled `public`.
`route.id` is an opaque, safe route label. Neither field is identity proof by
itself; route selection must already have accepted the approved Household
Identity contract.

When the bridge cannot safely bind the conversation, the response remains a
successful JSON-RPC result so the endpoint can render a typed unavailable
state:

```json
{
  "schema": 1,
  "jsonrpc": "2.0",
  "id": "open-1",
  "result": {
    "schema": 1,
    "status": "unavailable",
    "conversation_handle": "opaque-conversation-1",
    "reason": "stale_conversation"
  }
}
```

Safe `reason` values are `invalid_request`, `authorization_unavailable`,
`unauthorized`, `stale_conversation`, `conversation_mismatch`,
`hermes_timeout`, `hermes_unavailable`, `request_rejected`,
`protocol_error`, and `capability_unavailable`. A reason never includes a
credential, prompt, transcript, audio, or internal identifier.

## Turns and events

After `ready`, an endpoint may submit one fresh non-empty prompt:

```json
{
  "schema": 1,
  "jsonrpc": "2.0",
  "id": "prompt-1",
  "method": "prompt.submit",
  "params": {
    "conversation_handle": "opaque-conversation-1",
    "text": "What is the weather?"
  }
}
```

Home owns the turn ID and returns it only after Standard accepts the prompt:

```json
{
  "schema": 1,
  "jsonrpc": "2.0",
  "id": "prompt-1",
  "result": {
    "schema": 1,
    "conversation_handle": "opaque-conversation-1",
    "turn_id": "home-turn-1",
    "status": "submitted"
  }
}
```

The endpoint must not submit another turn while one is active. A transport
failure before the result is known makes delivery uncertain; the client may
reconnect and resume the bound Session, but it never sends the same prompt
again automatically.

Home forwards Standard events as JSON-RPC `event` notifications. The nested
event keeps the Standard event name and payload intact:

```json
{
  "schema": 1,
  "jsonrpc": "2.0",
  "method": "event",
  "params": {
    "schema": 1,
    "conversation_handle": "opaque-conversation-1",
    "turn_id": "home-turn-1",
    "event": {
      "type": "message.delta",
      "payload": {"text": "Rain later"}
    }
  }
}
```

The bridge may add `correlation_id` for a structured prompt. It must not rename
Standard events, replace cumulative previews with deltas, or expose runtime
Session identity. A `message.complete` event with a terminal status remains
the completion authority. Endpoint reducers replace cumulative previews or
append only a verified new suffix; they never render each cumulative frame as
a separate message.

The endpoint may use the following requests, subject to the advertised
capabilities:

| Method | Required params | Result rule |
| --- | --- | --- |
| `conversation.open` | `conversation_handle` | `ready` or typed `unavailable`; no turn is created on failure |
| `prompt.submit` | `conversation_handle`, non-empty `text` | Home-owned `turn_id` and `submitted` status |
| `session.interrupt` | `conversation_handle`, active `turn_id` | `accepted` is not completion; wait for the terminal event |
| `prompt.respond` | handle, active turn, prompt type, correlation, validated response | Matching Standard prompt operation only |
| `command.dispatch` | handle, active turn, advertised command, bounded argument | Advertised commands only |
| `bridge.ping` | `conversation_handle` | Current bridge readiness and safe capabilities |

The endpoint sends `session.interrupt` as:

```json
{
  "schema": 1,
  "jsonrpc": "2.0",
  "id": "interrupt-1",
  "method": "session.interrupt",
  "params": {
    "conversation_handle": "opaque-conversation-1",
    "turn_id": "home-turn-1"
  }
}
```

An accepted response does not settle the turn. The endpoint waits for the
matching Standard terminal event (`interrupted`, `cancelled`, or the pinned
equivalent) and reports an uncertain state if the connection ends first.

Malformed JSON-RPC uses the standard `-32700`, `-32600`, `-32601`, or `-32602`
errors. Domain failures use a JSON-RPC error with a safe `error.data.code`:
`request_rejected`, `transport_unavailable`, `transport_timeout`,
`protocol_error`, `authorization_unavailable`, `unauthorized`,
`stale_conversation`, `conversation_mismatch`, or
`capability_unavailable`. A `prompt.submit` error is known non-delivery only
when its code is `request_rejected`; transport and timeout codes are
uncertain and must not be retried automatically.

## Response audio

The endpoint uses the same `/api/v1/bridge/ws` connection for the optional
endpoint-facing audio stream. Home still uses the separate Standard audio
sidecar internally. This keeps Android, TUI, and native clients on one
authenticated Home boundary without making them parse Standard's two-socket
association.

Home sends an `audio.start` notification before any binary frame:

```json
{
  "schema": 1,
  "jsonrpc": "2.0",
  "method": "audio.start",
  "params": {
    "conversation_handle": "opaque-conversation-1",
    "turn_id": "home-turn-1",
    "sample_rate": 24000,
    "channels": 1,
    "sample_width": 2,
    "byte_order": "little"
  }
}
```

It then sends zero or more binary WebSocket frames containing raw signed
16-bit little-endian PCM for that turn, followed by one JSON `audio.end` or
`audio.fallback` notification. Binary frames before `audio.start`, after the
terminal frame, or for a different turn are a protocol error. Missing or
unsupported metadata produces `audio.fallback`; readable text and turn state
remain usable.

An endpoint stops local playback immediately after its own interrupt request,
but does not claim the turn is complete until the matching terminal event.

## Reconnect and redaction

Reconnect repeats upgrade authentication and `conversation.open` for the same
opaque handle. Home resumes the existing Standard Session when its grant and
Session identity still match. It does not create a second conversation, switch
Profile, or replay an uncertain prompt or old response.

Every endpoint-facing frame is scrubbed before serialization. It may contain
opaque endpoint-safe handles, route labels, safe capabilities, Standard event
names, bounded event payloads, and typed reason codes. It must not contain:

- a Hermes bearer token or Home admin credential;
- a runtime Hermes Session ID or internal Profile ID;
- raw device-credential values;
- prompts, transcripts, or audio in diagnostics or error text;
- an unadvertised command or arbitrary Profile/Session selector.

The bridge path is therefore a transport boundary, not a second answer
authority. The server-side `HomeBridge` remains responsible for Standard
session and audio association, grant revalidation, single-reader behavior,
and no-replay recovery.
