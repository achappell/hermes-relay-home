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

The first service foundation slice is implemented. It includes validated,
revisioned SQLite configuration storage, deterministic bounded wake arbitration,
credential-bound contract translation, a loopback-by-default threaded HTTP
server, an executable Python 3.14 runtime, and admin-authenticated Prometheus
metrics with provisionable Grafana dashboards. Device credential provisioning,
LAN binding policy, and hardware acoustic calibration remain follow-up work.

## BMAD ownership

This repository owns the Home service foundation story record in
`_bmad-output/implementation-artifacts/story-index.yaml` and its formal local
status in `sprint-status.yaml`. The product hub owns durable household intent
and cross-repository decisions; the TUI repository is not the Home service's
status authority.

## Development

The project targets Python 3.14 and uses the standard library for SQLite and
the initial HTTP server. Run the focused checks with:

```sh
uv run --no-project --python 3.14 --with pytest -- python -m pytest -q
uvx --from ruff ruff check src tests
uvx --from ruff ruff format --check src tests
```

Monitoring setup is documented in [`observability/README.md`](observability/README.md).
The native Windows deployment path is documented in
[`deploy/windows/README.md`](deploy/windows/README.md).
