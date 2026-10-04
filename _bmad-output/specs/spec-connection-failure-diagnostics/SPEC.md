---
id: SPEC-connection-failure-diagnostics
companions:
  - event-contract.md
  - implementation-acceptance.md
  - ../spec-household-diagnostics-incident-review/SPEC.md
  - ../spec-household-diagnostics-incident-review/diagnostics-contract.md
  - ../../implementation-artifacts/spec-home-client-diagnostics.md
sources: []
---

# Connection Failure Diagnostics

> Proposed implementation supplement to **HOME-NW-06**, issue [16](https://github.com/achappell/hermes-relay-home/issues/16), Epic 6 / later. The adopted household-diagnostics contract remains authoritative; this supplement does not create a story, promote priority, change status, or complete that broader feature. The [approved course correction](../../implementation-artifacts/course-correction-2026-09-23.md) governs delivery order and unmodified Standard requirements.

## Why

The September 28 iOS → Home → proxy → Standard incident could be narrowed but not causally reconstructed: client reports lack request joins, service logs lack reliable time/build identity, and a generated rejection does not establish client receipt. Add the smallest content-free evidence needed to distinguish submission failure, transport closure and real recovery. Durable evidence and limitations are in `implementation-acceptance.md`; no root cause or behavioral fix is asserted.

## Capabilities

- **CAP-1**
  - **intent:** Operators can order operational evidence and identify the running code that emitted it.
  - **success:** Every operational diagnostic carries UTC time, process-local ordering and a provenance reference identifying loaded code or explicitly unavailable/unverified provenance.
- **CAP-2**
  - **intent:** Operators can trace a submission through Home and proxy to its observed upstream outcome and client-write boundary.
  - **success:** Opaque joins distinguish submission, rejection generation, write attempt/result and closure; write success never claims client receipt.
- **CAP-3**
  - **intent:** Operators can distinguish which transport leg closed, who initiated it and in which phase.
  - **success:** Typed observations distinguish submit unavailable, pong timeout, normal shutdown, timeout during closing and unknown, with pending state and numeric sent/received close codes in both close orderings.
- **CAP-4**
  - **intent:** Operators can separate readiness from end-to-end recovery.
  - **success:** Ready/reconnect evidence links to the next explicitly submitted request and its actual outcome, including a local unavailable-latch rejection, without replay.
- **CAP-5**
  - **intent:** Client evidence can distinguish a received and decoded response from transport loss while a request remains pending.
  - **success:** Approved iOS/report evolution exposes correlated receive/resolve/close ordering and uncertainty while preserving old-client/report compatibility and per-Home upload opt-in.
- **CAP-6**
  - **intent:** Households retain bounded useful evidence without content leakage or dependence of live turns on diagnostics.
  - **success:** Content-canary, retention/rotation/load, interruption and logging-failure acceptance passes with visible evidence-loss counts and unchanged conversation behavior.

## Constraints

- Reuse Home correlation/redaction authority and the existing recorder, report boundary and deployment runners; shared wire/report changes require the approval gates in `implementation-acceptance.md`.
- Allowlisted fields only: no prompts, responses, transcripts, audio, credentials, raw payloads, arbitrary exception/close-reason strings, upstream session handles, URLs or private names. Do not hash private values into new diagnostic identifiers.
- Standard remains unmodified. Home/proxy evidence observes its supported interface, not unseen internal execution. No diagnostic token reaches Standard.
- Detailed events expire within 14 days and may be evicted earlier by explicit capacity bounds. Existing automatic iOS reports retain their separate 7-day policy and opt-in. Canonical metrics/incident-bundle policies are not replaced.
- Logging failure never blocks, retries, replays, suppresses errors or otherwise changes a live turn. Unknown/missing/lost evidence stays explicit.

## Non-goals

- Fixing this incident, changing readiness/uncertainty semantics, replay/retry, error suppression, keepalive/timeout tuning, mode fallback or agent modifications.
- A metrics backend, fleet observability overhaul, new export UI, or encrypted/content-bearing incident capture. Broader HOME-NW-06 acceptance remains outside this supplement.

## Success signal

A reviewer can reconstruct the acceptance timelines using only safe records: identify time/build, follow one explicit submission, separate upstream outcome from write/receipt, identify both proxy legs and close ordering, and see whether the next explicit request actually succeeds. Home/proxy-only delivery is labeled partial until approved iOS receipt/report evidence passes.

## Assumptions

- Proposed operational defaults: JSONL records ≤2 KiB, a nonblocking 1,024-record queue, drop-newest with visible counters, active 10 MiB plus four backups per Home/proxy process, and 14-day expiry (earlier capacity eviction allowed).
- Optional negotiated correlation metadata and schema-2 client reports are the proposed compatibility strategy, not approved shared contracts. Exact proposed fields and fallback behavior are in `event-contract.md`.

## Open Questions

- Will Home, proxy and iOS owners approve the proposed shared metadata and report-schema evolution before enabling those additions?
- Will the privacy/operations owner approve the proposed local quotas, loss policy and restricted-reader deployment settings? Existing retention and upload-consent authority is unchanged.
