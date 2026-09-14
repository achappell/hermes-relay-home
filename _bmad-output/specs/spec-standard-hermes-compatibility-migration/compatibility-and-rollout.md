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

1. **Inventory and freeze the boundary.** Pin the Standard Hermes release,
   record current fork-only behavior, and list every surface capability.
2. **Build conformance fixtures.** Prove the Home bridge and normalized event
   contract with fake Standard Hermes and fake endpoints before changing a
   surface default.
3. **Migrate the server boundary.** Run Home against the Standard Hermes path,
   with endpoint credentials and opaque conversation handles, while retaining
   the old direct path for rollback.
4. **Migrate surfaces independently.** Complete and validate TUI, iOS,
   Android, Puck, ESP32 Touch, and W/K in their owning repositories. A surface
   may move to the target path only after its local gate passes.
5. **Run the household pilot.** Exercise one text turn, one spoken turn,
   interruption, route loss, reconnect, prompt behavior, and timing behavior
   across the actual pilot surfaces.
6. **Switch the default deliberately.** Record the evidence and make the
   Standard path the default only after every required surface is green. Keep
   an explicit rollback setting until the pilot is accepted.
7. **Retire fork dependence.** Remove fork-only defaults and documentation only
   after rollback is no longer required and no surface reports a remaining
   fork-only dependency.

## Rollback rules

- Path selection happens before a turn. A route or transport failure during a
  turn never silently changes path and never resends the turn.
- Rollback restores the last verified path and requires a fresh user action;
  it does not claim that the uncertain turn succeeded.
- Capability absence is visible in diagnostics and surface state, with the
  same typed outcome used by the owning repository's tests.
- Configuration migration is idempotent and preserves the legacy source until
  the new Home-paired configuration is verified.
