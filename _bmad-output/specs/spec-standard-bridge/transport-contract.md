# Standard Bridge Transport Contract

This companion carries the load-bearing wire, state, fixture, and failure
details for Story 2. It preserves the pinned Standard boundary; it does not
define the public Home route envelope.

## Boundary records

| Record | Required meaning |
| --- | --- |
| `ConversationGrant` | Home-owned binding of an opaque conversation handle to one authorized device, Profile, and optional durable Hermes Session ID. Endpoint callers do not supply the Profile or Session ID directly. |
| `BridgeStatus` | Endpoint-safe readiness result containing schema version, opaque conversation handle, safe reason code when unavailable, advertised commands, heartbeat support, and explicit `timing: absent` capability when timing is not provided. |
| `BridgeTurn` | Home-owned opaque turn ID bound to the conversation handle and submission status. |
| `BridgeEvent` | Standard event type and payload plus Home turn and prompt-correlation fields; the bridge does not rename or reinterpret the Standard event. |
| `AudioFrame` | Typed output from the separate response-audio socket, including stream kind, turn ownership, raw PCM bytes, and verified sample metadata. |

Endpoint payloads may contain the opaque conversation and turn handles, safe
state, Standard event payloads, and capability results. They must not contain
the server-held Hermes bearer, the runtime Hermes Session ID, or the internal
Profile ID.

## Standard boundary

| Flow | Pinned shape | Bridge rule |
| --- | --- | --- |
| Gateway connection | `/api/ws`, JSON-RPC 2.0 | Add the server-held token only while Home opens the gateway socket. Require `gateway.ready` before creating or resuming a Session. |
| Session lifecycle | `session.create` or `session.resume` | Bind the operation to the grant's Profile and durable Session identity; reject a returned durable ID that conflicts with the grant. |
| Text turn | `prompt.submit` and JSON event stream | Reject blank input; preserve Standard event names, correlation, order, and cumulative preview replacement semantics; require a terminal event for completion. |
| Response audio | Separate `/api/audio/speak-stream` socket | Send only newly appendable text, then use the Standard `done` or `stop` controls; require start metadata before PCM is usable; preserve signed-16 little-endian bytes, sample rate, channels, and stream boundaries. |
| Liveness | Advertised heartbeat and `gateway.ping` | Ping exercises the Standard operation but does not create timing or playback authority. |
| Interrupt | `session.interrupt` | An accepted request or acknowledgement is not completion; wait for the matching interrupted or cancelled terminal state. |
| Structured prompts | Correlated approval, clarify, secret, and sudo request events | Preserve prompt type, sensitivity, options, and correlation; map only a validated response to its matching `approval.respond`, `clarify.respond`, `secret.respond`, or `sudo.respond` operation. |
| Commands | Advertised `command.dispatch` | Dispatch only an advertised command; preserve command identity and correlation; report unavailable or rejected outcomes explicitly. |

## State and recovery

| State or condition | Required behavior |
| --- | --- |
| `connecting` | Authenticate the endpoint, resolve the opaque grant, open Standard transport, and wait within bounded deadlines for readiness and Session binding. |
| `ready` | Accept a fresh prompt only when the grant, Profile, and Session binding remain valid. Expose safe capability state, not internal credentials or identifiers. |
| `turn_active` | Keep one active turn bound to one conversation. Do not open a replacement conversation or accept a second turn that could clobber it. |
| `turn_uncertain` | Preserve uncertainty when transport loss may follow prompt delivery. Require reconnect before another submission and never resubmit automatically. |
| `reconnecting` | Perform fresh endpoint authorization and Standard readiness, then resume the existing Session. Do not replay the uncertain prompt or an old response. |
| `unavailable` | Return a safe reason for authorization, stale grant, mismatch, timeout, transport, protocol, or capability failure. Do not claim readiness. |
| Revoked or mismatched grant | Stop reachable work, reject new activity, and refuse Profile, Session, or conversation retargeting. |
| Audio sidecar loss | Close the audio sidecar, release the terminal turn, preserve readable text, and surface a typed audio-unavailable result. |

Recovery restores transport and session continuity only. Route readiness never
proves that an earlier turn completed, and a reconnect never authorizes a new
Profile or a second Hermes Session.

## Deterministic fixture matrix

| Fixture | Acceptance evidence |
| --- | --- |
| Valid device credential and opaque handle | Home reaches `ready`; gateway URL receives the server-held token only in Home-owned setup; endpoint-safe output contains no token, Profile ID, or runtime Session ID. |
| `gateway.ready` with commands and heartbeat | Status advertises the commands and heartbeat and reports timing as explicitly absent. |
| New grant with no durable Session | `session.create` is sent with Home source and the grant Profile; the runtime Session is retained internally. |
| Grant with durable Session | `session.resume` is sent with the granted Session ID; no new Session is created. |
| Ordered text stream | RPC response and gateway events remain ordered under concurrent demand; unrelated or identity-less session events cannot complete the active turn. |
| Blank or malformed prompt | No prompt is sent; the result is a typed invalid-request or protocol failure. |
| Cumulative rendered text | Only the new text suffix is sent to audio; repeated cumulative prefixes do not duplicate spoken text. |
| PCM split across frames | Bytes are reassembled without loss; non-signed-16-bit or metadata-late audio is not exposed as usable PCM. |
| Audio sidecar failure | The terminal turn is released, text remains readable, and the audio failure is not misreported as a second answer. |
| Interrupt | Explicit rejection remains rejection; an accepted interrupt waits for a matching terminal interrupted/cancelled event. |
| Structured prompt | Prompt type, sensitivity, options, and correlation survive; stale or uncorrelated responses are rejected. |
| Advertised command | Only an advertised command is dispatched; transport loss, explicit rejection, and capability absence remain distinguishable. |
| Transport loss before or after acceptance | Reconnect performs fresh readiness and Session resume, sends no replacement prompt, and does not replay the old response. |
| Profile or durable-Session mismatch | Reconnect or resume fails closed and cannot retarget the bound conversation. |

## Failure vocabulary

| Outcome | Meaning |
| --- | --- |
| `request_rejected` | Standard explicitly rejected the operation; delivery is known not to have succeeded. |
| `transport_unavailable` / `transport_timeout` | The transport ended or exceeded its bound before delivery or response could be known. |
| `protocol_error` | A peer violated the JSON-RPC, event, result, identity, or audio metadata shape. |
| `capability_unavailable` | An optional command or feature was not advertised and was not guessed. |
| `authorization_unavailable` / `unauthorized` | Home could not validate the endpoint credential or the endpoint lacks access. |
| `stale_conversation` / `conversation_mismatch` | The grant is inactive, the handle is not bound to the caller, or the resumed Profile/Session identity conflicts. |

## Verification boundary

The bridge contract is verified with injected JSON and audio socket ports and
deterministic fixtures. The focused and full Home checks use:

```sh
uv run --no-project --with pytest -- python -m pytest -q
```

Live Hermes, physical hardware, and the final Home route envelope are separate
validation gates owned by later slices.
