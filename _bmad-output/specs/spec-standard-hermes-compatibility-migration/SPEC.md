---
id: SPEC-standard-hermes-compatibility-migration
companions:
  - standard-baseline.md
  - surface-migration-matrix.md
  - compatibility-and-rollout.md
  - ../spec-home-bridge-route-roaming/bridge-contract.md
sources:
  - ../../../docs/project-context.md
  - ../../planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md
  - ../../../../hermes-relay-tui/_bmad-output/planning-artifacts/architecture/architecture-hermes-relay-tui-2026-09-08/ARCHITECTURE-SPINE.md
  - ~/Documents/Vaults/Personal Vault/projects/hermes-home/sources/prds/prd-hermes-home-next-wave-2026-09-13/prd.md
  - ~/Development/hermes-agent-relay-v0.21.0-minimal/plugins/platforms/voice_session/README.md
---

> **Canonical contract.** This SPEC and the files in `companions:` define the
> compatibility and rollout work required before the household can retire its
> fork-dependent default. The surface matrix assigns ownership; the rollout
> companion defines the gates and rollback rules.

# Standard Hermes Compatibility and Cross-Surface Migration

## Why

The household currently has several surfaces that reach a fork-specific Hermes
path, while the product direction is to use the Standard Hermes Channel and
keep fork changes exceptional. That is not a one-line endpoint change: audio,
timing, prompts, interruption, credentials, route selection, session recovery,
and local configuration must remain honest on every surface. This slice makes
the migration a deliberate, reversible program before the remaining Home
features depend on an unproved compatibility assumption.

## Capabilities

- **CAP-1**
  - **intent:** The project can state which Standard Hermes capabilities each
    surface requires and what evidence proves them.
  - **success:** A versioned compatibility matrix records the required JSON,
    PCM, timing, prompt, command, interrupt, liveness, authentication, and
    recovery behavior for Home, TUI, iOS, Android, Puck, ESP32 Touch, and W/K.

- **CAP-2**
  - **intent:** The Standard Hermes server path can be exercised through Home
    without requiring a fork-only wire event or assistant semantic.
  - **success:** Deterministic fixtures prove `gateway.ready`, session
    create/resume, streamed text, response PCM, audio metadata, interruption,
    reconnect, structured prompts, and advertised commands through the Home
    bridge with preserved event meaning and ordering.

- **CAP-3**
  - **intent:** A surface can adopt the target path without changing its
    presentation or answer authority.
  - **success:** Each surface uses its existing session or normalized-event
    boundary; Hermes remains the source of answers and the surface does not
    parse a second wire dialect.

- **CAP-4**
  - **intent:** A surface can move from direct fork access to the approved Home
    or Standard Hermes boundary without losing its local identity or continuity.
  - **success:** Configuration migration preserves the selected Profile,
    device identity, deliberate local history, and safe recovery behavior while
    replacing personal bearer-token access with the approved credential path.

- **CAP-5**
  - **intent:** Every in-scope surface can prove the target path with its own
    audio, lifecycle, and failure behavior.
  - **success:** TUI, iOS, Android, Puck, ESP32 Touch, and W/K each record
    focused evidence for text, voice response, interruption, reconnect,
    timing, and honest unavailable states in their owning repository.

- **CAP-6**
  - **intent:** The household can roll the migration out without changing an
    active turn or losing a safe recovery path.
  - **success:** The selected path and capability state are visible before a
    turn, the legacy path remains an explicit rollback option until the final
    gate, and no route or transport change resubmits an uncertain turn.

- **CAP-7**
  - **intent:** The project can retire fork-only dependencies after all
    surfaces pass the same contract gates.
  - **success:** The default path no longer requires the fork, the retirement
    decision cites per-surface evidence, and a failed capability remains a
    typed compatibility failure rather than an invisible fallback.

## Constraints

- The Standard Hermes Channel is the target. No migration acceptance criterion
  may require a fork-only event, assistant semantic, or server behavior.
- Home remains the household authority and may terminate endpoint credentials
  before opening Hermes with a server-held bearer token. Personal Hermes tokens
  never reach paired endpoints, browsers, displays, or hardware.
- The surface-facing SessionProtocol and normalized event meanings remain
  stable unless a separately reviewed contract change proves they are not
  sufficient.
- Existing surface event meanings and ordering, binary little-endian response
  PCM, audio metadata, interruption behavior, structured-prompt correlation,
  explicit commands when advertised, and cumulative text-preview replacement
  semantics remain part of the compatibility contract. Standard wire names and
  endpoint details are pinned in `standard-baseline.md`; fork-only names are
  rollback evidence, not acceptance criteria.
- Text/audio synchronization is valid only when authoritative timing metadata
  or a verified playback-clock contract exists. Network arrival time is never
  synchronization authority.
- Legacy and Standard paths are selected before a turn begins. Recovery after
  a turn may have reached Hermes is reconnect-only and requires fresh user
  initiation.
- Each surface owns its migration evidence and remains independently
  deployable. One surface passing never closes another surface's work.
- The migration cannot introduce a second household authority, shared
  transcript store, broker, or new Hermes WebSocket channel.

## Non-goals

- Rewriting Hermes or extracting a shared platform UI/core package.
- Completing pairing, Profile mapping, route roaming, diagnostics retention,
  Watch, notifications, typed choices, artifacts, or arbitrary files.
- Inventing feature parity for unsupported prompts, timing, commands, audio, or
  attachments; unsupported capability must be visible and bounded.
- Automatically switching transports during an uncertain active turn or
  replaying a prompt to repair a migration failure.

## Success signal

The Home bridge and every in-scope surface can complete the same representative
text and voice scenarios against the Standard Hermes path, including response
audio, interruption, reconnect, timing capability reporting, prompts, and
failure states. The household can select the old path for rollback, then switch
the default only after the recorded evidence shows that no surface depends on
the fork.

## Assumptions

- The pinned Standard release uses the stock gateway WebSocket at `/api/ws` for
  JSON-RPC session traffic and the separate `/api/audio/speak-stream` socket
  for response PCM. The fork's `/voice-session` route is rollback-only.
- The pinned Standard release has no authoritative speech-timing extension;
  timing remains explicitly absent until a surface proves a playback-clock or
  duration contract.
- Existing surface session abstractions are usable migration seams; a shared
  `hermes-relay-core` package is not required to begin this work.
- Home bridge, endpoint credential, Profile-claim, and route-roaming work can
  be delivered as separate adapters around the compatibility boundary.

## Open Questions

- Which capabilities are mandatory before the first default switch, and which
  may remain explicitly unavailable in the pilot?
- Which surfaces move behind Home first, and which may temporarily remain
  direct Standard Hermes clients during rollout?
- What exact configuration conversion preserves profiles, local history,
  device identity, and rollback when a direct fork endpoint becomes a
  Home-paired endpoint?
- What operational signal authorizes retirement of the legacy fork path?
