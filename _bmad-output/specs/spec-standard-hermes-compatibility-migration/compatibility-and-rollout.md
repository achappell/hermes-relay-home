> **Approved correction (2026-09-23):** The [delivery contract](../../implementation-artifacts/course-correction-2026-09-23.md) supersedes conflicting mode, authentication, rollback, timing and session clauses below. Both modes require unmodified Standard. Home mode uses Home credentials; direct personal mode uses supported Standard authentication. The old fork path is retired under HOME-MIG-09; direct Standard remains supported.

# Compatibility and Rollout Gates

## Baseline used by these gates

All fixtures and rollout evidence use the pinned stock Hermes `0.21.1` release
at commit `2237be355906fbe6065ce1815711eee52b2d646e`. The Standard JSON/session
boundary is `/api/ws`; response PCM comes from the separate
`/api/audio/speak-stream` socket. See [standard-baseline.md](standard-baseline.md)
for the observed wire contract and the rollback-only fork ledger.

## Capability matrix

| Capability | Must preserve | Missing capability behavior |
|---|---|---|
| Handshake and identity | `gateway.ready`, session/profile context, explicit capability advertisement | Report unavailable or incompatible before capture; do not guess a Profile or token scope. |
| Text turn | One accepted turn, cumulative preview/replacement semantics, terminal outcome | Preserve the typed failure and never resubmit after uncertain delivery. |
| Response audio | Separate audio sidecar start metadata, raw signed-16 little-endian PCM, terminal end/fallback, byte counts, isolated turn ownership | Keep usable text and label audio unavailable; never claim Speaking or completion falsely. |
| Interruption | Advertised interrupt request and confirmed interruption where supported | Use the existing close/reconnect boundary and describe cancellation as unconfirmed when it is. |
| Prompts and commands | Structured-prompt identity/correlation and explicitly advertised commands | Reject or expose unsupported capability; never collect, log, or invent a replacement payload. |
| Timing | Authoritative segment/word timing or a verified playback-clock contract; the pinned Standard baseline has explicit absence | Show a compatibility state; never synchronize from network arrival time. |
| Liveness and reconnect | One receive owner, honest disconnect, fresh readiness, no replay | Leave the uncertain turn unresolved and require a fresh user action. |
| Authentication and routes | Home device credential, approved route, server-held Hermes token, same-household identity | Fail closed before capture or Hermes submission. |

## Rollout order

1. Record the unmodified upstream baseline, deployed provenance and per-surface capabilities.
2. Complete required Epics 1–3: personal Home setup, explicit direct mode, and Home room devices. Preserve existing valid evidence and close new acceptance separately.
3. Run the required household surface pilot using the approved capability matrix. A required unsupported capability remains a blocker until scope is explicitly revised.
4. Verify repeatable household installation, upgrades and recovery; preserve intentional history and require re-pairing where migration cannot be supported.
5. Complete HOME-MIG-09 (existing source Story 9): remove obsolete pairing, fork adapters, configuration, services and credentials after replacement acceptance. Keep the supported direct Standard mode.

## Recovery rules

Restore a verified compatible Home/client/Standard build and recoverable configuration/data. Never automatically switch mode, replay an uncertain turn, or restore fork dependence as a supported configuration. If no compatible recovery build exists, pause rollout and report the affected surface unavailable. Verify recovery before cutover. Broader other-home distribution and automatic updater scope remain later work.
