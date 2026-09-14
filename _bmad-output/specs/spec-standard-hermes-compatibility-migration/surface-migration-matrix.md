# Surface Migration Matrix

This matrix is part of `SPEC.md`. It assigns migration work without claiming
that one surface's evidence closes another surface.

## Capability contract

The pinned release and the meaning of these labels are defined in
[standard-baseline.md](standard-baseline.md). Every cell is a requirement for
the named owner, not proof that the work is already implemented.

| Surface | JSON/session | PCM/audio | Timing | Prompts | Commands | Interrupt | Liveness | Authentication | Recovery |
|---|---|---|---|---|---|---|---|---|---|
| Hermes server | Required: Standard `/api/ws` JSON-RPC; preserve event order | Required: separate `/api/audio/speak-stream`; signed-16 LE PCM | Explicitly absent at baseline | Conditional: correlated request/resolution events | Conditional: advertised `command.dispatch` only | Required: `session.interrupt` plus terminal state | Required: `gateway.ready`/ping; one reader | Server-held credential only | Session resume; no uncertain-turn replay |
| Home bridge | Required: map Standard wire to `SessionProtocol` | Required: join audio to the turn; preserve metadata | Required: authoritative clock or typed absence | Conditional: preserve ID, sensitivity, and resolution | Conditional: forward only advertised commands | Required: preserve confirmation; no mid-turn switch | Required: one receive owner and fresh readiness | Device credential at Home; Hermes token stays server-side | Reconnect/resume; fresh action after uncertainty |
| TUI | Required: one Standard normalizer at the session seam | Required: playback consumes sidecar PCM | Conditional: verified playback clock/duration, otherwise visible absence | Conditional: typed unavailable state when UI is missing | Conditional: explicit Standard operation only | Required: interrupt then wait for terminal state | Required: ready/ping/reconnect state | Approved Home credential; no endpoint Hermes token | Preserve profile/history; no replay |
| iOS/macOS | Required: existing normalized session boundary | Required: native playback owns verified PCM | Conditional: playback clock/duration or visible absence | Conditional: native prompt UI or typed unavailable | Conditional: explicit operation only | Required: native cancel state is honest | Required: lifecycle-safe reconnect after ready | Device credential only | Preserve local identity/history; fresh action after uncertainty |
| Android | Required: existing normalized session boundary | Required: platform audio owns verified PCM | Conditional: playback clock/duration or visible absence | Conditional: native prompt UI or typed unavailable | Conditional: explicit operation only | Required: lifecycle-safe interrupt confirmation | Required: reconnect only after ready | Device credential only | Preserve profile/history; no automatic resend |
| ReSpeaker Puck | Required: adapter-owned; firmware does not parse Hermes JSON | Required: local capture/playback with verified PCM metadata | Conditional: measured playback clock or visible absence | Conditional: status-only or typed unavailable | Conditional: adapter forwards only advertised commands | Required: local cancellation state is honest | Required: hardware lifecycle and re-ready | Device credential; never a personal Hermes token | Reconnect; uncertain turn needs fresh action |
| ESP32 Touch | Required: adapter-owned; firmware does not become Hermes authority | Required: bounded audio adapter and local playback | Conditional: proven clock/duration or visible absence | Conditional: bounded UI or typed unavailable | Conditional: explicit operation only | Required: reducer records confirmed/unconfirmed cancel | Required: reconnect/re-ready state | Device credential only | No replay; fresh action after uncertainty |
| W/K browser/iPad | Required: one normalized browser event boundary | Required: browser playback owns sidecar PCM | Conditional: playback clock/duration or visible absence | Conditional: accessible prompt UI or typed unavailable | Conditional: explicit operation only | Required: UI reflects confirmed/unconfirmed interrupt | Required: permission/lifecycle-safe reconnect | Device credential through Home; no Hermes token in browser | Reconnect/resume; no automatic resend |

`Conditional` does not mean “silently omit it.” The adapter must expose the
missing capability before or during the affected interaction.

## Delivery ownership and evidence

| Boundary | Current shape | Migration work | Required evidence | Owner |
|---|---|---|---|---|
| Hermes server | Fork-dependent deployments and a stock Standard Hermes path with uneven capability evidence | Pin the supported Standard Hermes release and publish the capability matrix | Fake-server conformance plus live text/voice smoke for the pinned release | Hermes/Home integration |
| Home bridge | Foundation service exists; bridge/route contract is specified but not delivered | Authenticate the endpoint, map the opaque conversation handle, open Hermes with a server-held token, and preserve existing frames | JSON/PCM/timing/prompt/interrupt fixtures and no-token boundary checks | `hermes-relay-home` |
| TUI | `SessionProtocol` is stable; fork `voice-session` remains default; standard gateway proof is opt-in and text-first | Add target-path selection, Home credential/route handling, timing capability state, config migration, and rollback | TUI fake transport plus live stock text, voice, interrupt, reconnect, and sync checks | `hermes-relay-tui` |
| iOS/macOS | Native client consumes the voice-session boundary with local profile and history state | Add the Home/Standard adapter and migrate endpoint credentials without changing local history or presentation semantics | XCTest/fake bridge plus live text, voice, interruption, reconnect, and route evidence | `hermes-relay-ios` |
| Android | Native client consumes direct `/voice-session` profiles and owns platform audio/lifecycle | Add the Home/Standard adapter, secure credential conversion, route state, and rollback without losing profile history | JVM/instrumentation plus live transport, audio, interruption, reconnect, and unavailable-state evidence | `hermes-relay-android` |
| ReSpeaker Puck | Local capture and bridge path has its own service and hardware lifecycle | Replace fork-only authorization/session assumptions with Home credential and opaque-handle flow while retaining capture/playback ownership | Firmware/bridge fixtures, compile/OTA, full spoken round trip, failure and recovery evidence | TUI/Puck delivery |
| ESP32 Touch | Shared snapshot/action path exists; voice/audio adapter is still planned | Build the bounded audio/session adapter against Home/Standard semantics without putting Hermes authority in firmware | Native reducer, audio adapter, cancellation, reconnect, and no-replay evidence | TUI/firmware delivery |
| W/K browser/iPad | Browser display and voice bridge consume appliance/session events | Move endpoint auth/route selection behind Home, preserve normalized events and browser audio timing state | DOM/TypeScript tests plus Safari/iPad permission, audio, reconnect, and accessibility smoke | TUI/web delivery |

## Ownership rule

Home owns the server-side compatibility and bridge contract. Each endpoint
repository owns its adapter and evidence. The product hub and coverage index
record dependency and closure, but do not replace an owning repository's story
specification or validation record.
