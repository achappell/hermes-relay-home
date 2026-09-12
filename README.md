# Hermes Relay Home

The local Home service for Hermes household devices.

This repository owns the household authority shared by iOS, Android, the TUI,
web/iPad Hands-Free Home, and wake-capable hardware:

- canonical Rooms, Devices, wake mappings, priorities, and Profile references;
- revisioned, atomic configuration publishing;
- authenticated wake claims and single-winner arbitration.

It is a local-LAN service, not a cloud backend. The first persistence target is
SQLite so the service can provide transactions and survive process restarts
without introducing a second database server.

The versioned HTTP contract lives in [`docs/contracts/v1/`](docs/contracts/v1/).
Clients must treat the Home service as the source of truth and must not create
their own household-wide configuration or arbitration authority.

## Project planning

This repository uses BMAD for local product and implementation planning. Start
with [`docs/project-context.md`](docs/project-context.md), then read the
[architecture spine](_bmad-output/planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md)
and [foundation spec](_bmad-output/specs/spec-home-service-foundation/SPEC.md)
before changing the service boundary. BMAD's installer-managed runtime lives
in `_bmad/` and the Claude Code skill surface in `.claude/skills/`.

Check the installed planning surface with:

```sh
npx --yes bmad-method@6.12.0 status
```

## Current status

This is the repository boundary and contract bootstrap. The HTTP adapter,
SQLite store, and arbitration engine are the next implementation slice.

## Development

The project targets Python 3.14. The contract fixtures use only the standard
library; HTTP framework and runtime dependencies will be added with the first
service implementation.
