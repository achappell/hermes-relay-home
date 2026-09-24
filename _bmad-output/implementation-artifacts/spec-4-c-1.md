---
id: 4-C-1
status: backlog
product_epic: 5
created: 2026-09-23
---

# 4-C-1 — Evaluate shared departure reminders

## Approved scope

Implement the existing shared departure evaluator contract: qualify location and live/configured travel estimates, apply thresholds, recompute/cancel/clear, and publish one visual-only household result. TUI owns renderer integration. Preserve the original 4-C-1 identity.

## Acceptance

- Deliver the owner-specific behavior above using unmodified Standard Hermes and the approved [delivery contract](course-correction-2026-09-23.md).
- Preserve existing story evidence and supported adapters; no automatic mode switch or replay of an uncertain turn.
- Record applicable setup, capability limits, privacy, failure and recovery behavior against the actual supported baseline.
- Record implementation, merge and physical/live acceptance separately; do not declare an unexercised gate complete.

## Dependencies

No additional story prerequisite; refine the implementation contract before development.

## Readiness

Approved backlog scope. Owning BMAD specification/readiness review must settle API details and a bounded execution plan before implementation. No implementation or runtime acceptance is claimed.
