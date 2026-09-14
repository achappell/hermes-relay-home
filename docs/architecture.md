# Hermes Relay Home architecture

## Boundary

`hermes-relay-home` is an independently deployable local-LAN process. It is
the household authority for shared device configuration and wake arbitration.
It does not own Hermes sessions, prompt text, transcripts, audio, or surface
presentation.

| Concern | Owner |
| --- | --- |
| Rooms, Devices, wake mappings, priorities, Profile references | Home service |
| Configuration revision and atomic publish | Home service |
| Wake claim validation and winner selection | Home service |
| iOS/Android administration UI and Keychain storage | Native clients |
| Terminal interaction, Hermes session, local audio | TUI |
| Wake detection, local capture, and grant handling | Claimant device/surface |
| Display rendering and display-only state | Display surfaces |
| Hermes model, speech, and supported session protocols | Hermes relay |
| Operational metrics and dashboard definitions | Home service / observability artifacts |

## Runtime shape

The first runtime is deliberately one local service with two internal roles:

1. a durable configuration store backed by SQLite;
2. a low-latency arbitration engine using the service's monotonic receive
   clock.

The HTTP layer is an adapter over those roles. The contract is independent of
the HTTP framework so clients do not acquire a Python implementation detail.
The admin-authenticated `/metrics` endpoint is a separate operational adapter;
its low-cardinality Prometheus output is not household state and is not part of
the versioned client contract.

```text
                         local LAN
 iOS / Android ───────┐
 TUI ─────────────────┼──> hermes-relay-home ──> SQLite
 web / iPad ──────────┤          │
 ESP32 / Puck ───────┘          └── wake arbitration
```

The service binds to loopback by default during development. LAN binding is an
explicit deployment choice and must remain behind the private-household trust
boundary.

## Ownership rules

- Clients may cache a verified snapshot for presentation, but the server
  revision is authoritative.
- A publish supplies an expected revision. A stale write returns a typed
  conflict; it never overwrites the newer snapshot.
- A claimant submits a `WakeClaim`; it never selects the household winner.
- The service grants or denies each claim. A failed winner does not promote a
  loser; a new wake is required.
- Hermes credentials, device credentials, and Home admin credentials remain
  in their owning process. They never enter snapshots, URLs, display payloads,
  transcripts, or ordinary logs.

## Migration from `hermes-relay-tui`

The TUI repository currently contains the household display/appliance code and
the shared wake-arbitration contract. During migration:

1. this repository becomes the canonical owner of the Home contract;
2. the TUI keeps a thin client/compatibility adapter while service code moves;
3. iOS, Android, web, and hardware clients consume the versioned HTTP contract;
4. display and firmware code remain in the TUI until their independent build,
   hardware CI, or release cadence justifies separate repositories.

No generic `hermes-relay-core` repository is created at this stage. The Python
Hermes session core remains with the TUI until multiple consumers demonstrate
that a shared package is cheaper than an adapter.
