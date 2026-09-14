## Deferred from: code review of SPEC.md (2026-09-14)

- Decide whether Home consumes Standard event sequence/cursor replay after reconnect. The pinned Standard release supports `session.events.since` and monotonic event sequence numbers, while Story 2 forbids replaying uncertain prompts and old responses; the route-level contract must settle whether missed non-response events are intentionally discarded or replayed.
