# Hermes Relay Home project context

## Purpose

`hermes-relay-home` is the independently deployable local-LAN authority for
shared household state used by Hermes voice surfaces. It exists to give iOS,
Android, the TUI, web/iPad Hands-Free Home, and wake-capable hardware one
canonical place to read configuration and resolve simultaneous wake claims.

The service is deliberately not a second assistant. Hermes remains authoritative
for sessions, model routing, answer content, speech generation, and the
voice-session protocol.

## Ownership boundary

The Home service owns:

- Rooms, Devices, wake mappings, priorities, and Profile references;
- durable configuration revision and atomic publication;
- authenticated wake-claim validation and single-winner arbitration.

Clients own presentation, local caches, microphone permission, audio I/O, and
their own transport lifecycle. Clients may cache a verified snapshot for
display, but the service revision is authoritative and clients must not create a
second household-wide authority.

Credentials remain in their owning process. They do not enter configuration
snapshots, URLs, display payloads, transcripts, or ordinary diagnostics.

## Current state

The repository contains the service boundary, architecture notes, a versioned
HTTP contract, JSON Schemas, fixtures, tests, packaging, BMAD runtime
configuration, CI/release automation, and the first runtime foundation slice:

1. SQLite-backed configuration store with atomic revisioned replacement;
2. pure wake-claim validation and bounded arbitration engine;
3. thin HTTP adapter over those roles;
4. admin-authenticated Prometheus metrics and provisionable Grafana dashboards;
5. deterministic tests using fakes, concurrent requests, and contract fixtures.

The next work is integration and deployment policy: device credential
provisioning and revocation, hardware acoustic-evidence calibration, and client
adapters consuming the v1 contract.

## Contract anchors

- `docs/contracts/v1/README.md` — wire behavior and security boundary;
- `docs/contracts/v1/configuration.schema.json` — configuration snapshot shape;
- `docs/contracts/v1/wake-claim.schema.json` — wake claim shape;
- `docs/architecture.md` — repository and ownership boundary;
- `_bmad-output/planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md` — concise design invariants;
- `_bmad-output/specs/spec-home-service-foundation/SPEC.md` — first implementation slice.

## Working rules

- Keep the HTTP contract independent of the eventual Python HTTP framework.
- Use the Home service's monotonic receive clock for arbitration; device wall
  clocks are metadata only.
- A stale configuration revision returns a typed conflict and never overwrites
  newer state.
- A failed arbitration winner does not promote a loser; a new wake is needed.
- Wake claims contain no Profile ID, wake phrase, prompt, transcript, or audio.
- Do not add Hermes upload, remote undo, usage, compression, or other server
  operations until Hermes exposes them.

## Validation baseline

The repository targets Python 3.14. Before a change is handed off, run the
focused tests, the complete test/package checks, and the BMAD artifact lints
relevant to the slice. Live Hermes access is not required for unit tests.
