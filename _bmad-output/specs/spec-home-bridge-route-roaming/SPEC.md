---
id: SPEC-home-bridge-route-roaming
updated: 2026-09-13
companions:
  - route-session-state.md
  - ../../planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md
  - ../../../docs/contracts/v1/README.md
  - bridge-contract.md
  - ../../../docs/contracts/v1/configuration.schema.json
  - ../../../docs/contracts/v1/wake-claim.schema.json
  - '~/Documents/Vaults/Personal Vault/projects/hermes-home/sources/prds/prd-hermes-home-next-wave-2026-09-13/prd.md'
  - '~/Documents/Vaults/Personal Vault/projects/hermes-home/slices/next-feature-slate-2026-09-13.md'
  - '~/Development/hermes-relay-tui/_bmad-output/planning-artifacts/architecture/architecture-hermes-relay-tui-2026-09-08/ARCHITECTURE-SPINE.md'
  - ../spec-standard-hermes-compatibility-migration/standard-baseline.md
sources:
  - ../../../docs/project-context.md
  - ../../../docs/architecture.md
  - '~/Development/hermes-agent-relay-v0.21.0-minimal/plugins/platforms/voice_session/README.md'
---

> **Canonical contract.** This SPEC and `route-session-state.md` define the
> Home-owned bridge and approved-route behavior. Slice A owns endpoint
> credentials, Slice B owns Profile mapping and conversation claims, and the
> pinned Standard baseline owns the target gateway/audio transport. The fork
> voice-session README is rollback evidence only.

The front-end wire shape is recorded in `bridge-contract.md`
as a planned route-adapter contract. Story 2 currently provides only the
internal `HomeBridge` seam; route discovery, identity proof, TLS deployment,
browser bootstrap, and the public WebSocket adapter are not live merely because
that contract is documented.

## Endpoint contract decision

Session-bearing endpoint migrations use the Home bridge first. A paired client
does not become a direct Standard Hermes client during this wave: it reaches
Home through the planned `/api/v1/bridge/ws` contract in `bridge-contract.md`,
authenticates with its limited Device credential, and receives only opaque
conversation/turn handles and safe capability state. Home alone opens Standard
`/api/ws` and `/api/audio/speak-stream` with its server-held Hermes credential.

# Home Bridge and Route Roaming

## Why

This is a reliability and boundary problem: a laptop or phone may leave the
home network, but the household should still have one Hermes relationship, one
Home authority, and one honest turn history. The endpoint needs to move between
approved home, Tailscale, and optionally public routes without manual editing,
while the bridge keeps personal Hermes credentials server-side and preserves
the pinned Standard gateway and audio-sidecar behavior. Recovery must repair
connectivity without quietly duplicating a turn whose delivery is uncertain.

## Capabilities

- **CAP-1**
  - **intent:** A paired endpoint can select the best currently reachable
    approved route to the one Household Server and show the route it is using.
  - **success:** Deterministic route fixtures choose home over Tailscale and
    Tailscale over an explicitly enabled public route; an unconfigured public
    route is never attempted, and the endpoint exposes the selected route.

- **CAP-2**
  - **intent:** A paired endpoint can verify that an approved route reaches the
    same Household Server before using it for Home or Hermes work.
  - **success:** Two approved routes for one Household Server are accepted;
    an unknown or mismatched route is rejected without creating another
    household authority, session, or credential scope.

- **CAP-3**
  - **intent:** An authorized endpoint can use an active Home conversation
    through the pinned Standard gateway and audio boundary without receiving a
    personal Hermes bearer credential.
  - **success:** A valid endpoint credential and opaque conversation handle
    produce `ready` and a bridged Session; revoked, stale, unauthorized, or
    unavailable requests produce a safe typed failure and create no Hermes
    turn.

- **CAP-4**
  - **intent:** A bridged endpoint can exchange Standard session events and
    response media without changing their meaning.
  - **success:** Fixtures preserve Standard JSON-RPC event identity and
    ordering, separate-sidecar PCM bytes and start metadata, interrupt and
    structured-prompt correlation across the bridge, and the baseline's
    explicit timing absence. A verified playback-clock or duration adapter may
    add timing evidence; no endpoint-specific Hermes dialect is required.

- **CAP-5**
  - **intent:** A paired endpoint can recover through another approved route
    after transport loss without automatically duplicating an uncertain turn.
  - **success:** Recovery reaches an honest ready or unavailable state, sends
    no replacement turn automatically, does not replay the old response, and
    accepts a later fresh user turn only after readiness is verified.

- **CAP-6**
  - **intent:** A paired endpoint can distinguish route, Home bridge,
    authorization, and Hermes-session readiness or failure.
  - **success:** Deterministic failures identify the failing boundary without
    exposing credentials, claiming that a disconnected Session is healthy, or
    silently switching Profiles or household authorities.

## Constraints

- One Household Server remains the sole household authority. Route roaming
  must not create a second configuration, credential, Profile, or Hermes
  session authority.
- Approved route priority is home network, then Tailscale, then public
  internet only when the household explicitly enables it.
- A route is eligible only after it proves the same Household Server identity.
  Route labels, URLs, and credentials are not identity proof by themselves.
- The endpoint Home credential and the server-held Hermes bearer credential are
  distinct. Neither is exposed through route status, session payloads,
  snapshots, logs, diagnostics, transcripts, or audio.
- The bridge accepts an already authorized opaque conversation handle from the
  Home claim flow. The endpoint cannot submit an arbitrary Profile ID or
  Hermes Session identity, prompt, transcript, or audio to widen its
  authority; Home may overwrite untrusted identity fields before opening
  Hermes.
- The bridge uses the pinned Standard `/api/ws` JSON-RPC gateway and separate
  `/api/audio/speak-stream` response-audio socket. No new Hermes WebSocket
  channel, fork-only event, assistant semantic, or endpoint-specific Hermes
  dialect is introduced for roaming.
- Standard JSON-RPC event identity and ordering, separate-sidecar binary
  little-endian PCM, audio metadata, interrupt behavior, and structured-prompt
  correlation remain stable. The pinned baseline has no `speech_timing` event;
  if timing is absent, the endpoint reports that compatibility condition or
  uses a verified playback-clock/duration adapter instead of guessing from
  network arrival.
- Live microphone capture and speech-to-text remain endpoint responsibilities.
  Hermes v1 does not gain binary microphone ingress through this slice.
- Route or transport failure is visible and fail-closed. Home does not promote
  another endpoint, switch Profiles, replay an uncertain turn, or create a
  second Session to repair the failure.
- Route status and turn-delivery status remain separate. A reconnect may become
  ready without claiming that an interrupted or uncertain turn succeeded.
- Route policy and bridge behavior are testable through injected route,
  identity, clock, credential, and WebSocket ports without a live public
  network or Hermes endpoint.

## Non-goals

- Choosing the final cryptographic or challenge-based same-Household identity
  proof, route-discovery mechanism, public reverse proxy, or TLS deployment.
- Implementing pairing or credential issuance, Profile mappings and wake
  arbitration, Watch/notifications, diagnostics retention, or artifact
  behavior.
- Adding binary microphone ingress, server-side speech recognition, a new
  Hermes channel, or a second assistant protocol.
- Replaying an old response, resolving uncertain delivery automatically, or
  providing multiple household servers for one endpoint.
- Defining the final audio/text alignment algorithm; this slice preserves the
  timing data supplied by Standard when present and makes baseline timing
  absence explicit.

## Success signal

A fake endpoint, Home bridge, and Hermes server can use two approved routes to
the same Household Server. A text turn and a voice turn pass through the
pinned Standard gateway and audio sidecar with JSON, PCM, prompt, and timing
capability behavior preserved; a route loss produces honest recovery without
replaying an uncertain turn; and a fresh user turn succeeds only after the
endpoint is ready again.

## Assumptions

- Slice A issues an opaque limited Home credential, and Slice B returns
  `ready`/`unavailable` plus an opaque conversation handle before endpoint
  capture begins.
- The pinned Standard release remains available at the Home server boundary:
  `/api/ws` provides `gateway.ready`, session create/resume, `prompt.submit`,
  and `session.interrupt` JSON-RPC traffic, while
  `/api/audio/speak-stream` provides response PCM. The stock baseline has no
  `speech_timing` event; timing is explicit absence unless a verified
  playback-clock or duration adapter supplies it.
- The endpoint can retain its logical Home and Hermes Session identifiers
  across a transport reconnect, subject to the standard channel's cursor
  rules.

## Open Questions

- What exact cryptographic or challenge-based proof establishes that local,
  Tailscale, and optional public routes are the same Household Server?
- Where are Approved Routes configured and refreshed, and how does an endpoint
  learn them without treating a stale route list as authority?
- Which production route-discovery, identity-proof, TLS, and deployment choices
  will serve the planned bridge envelope and endpoint WebSocket path while
  preserving the safe errors and browser credential boundary?
- When a route changes during active response audio, should the endpoint drain
  local playback, stop immediately, or show a distinct interrupted state
  before reconnecting?
- How should Home retain or re-establish the Hermes Session identity and
  last-turn cursor when the old transport ends during an active turn?
- Which health and liveness probes belong to this bridge slice versus the
  later single-device health slice, and what probe budget is safe without
  opening a microphone or speaker?
