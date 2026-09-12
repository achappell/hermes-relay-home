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

## Current status

This is the repository boundary and contract bootstrap. The HTTP adapter,
SQLite store, and arbitration engine are the next implementation slice.

## Development

The project targets Python 3.14. The contract fixtures use only the standard
library; HTTP framework and runtime dependencies will be added with the first
service implementation.
