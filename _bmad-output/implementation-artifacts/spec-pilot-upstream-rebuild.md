---
title: 'F2 — resume the bound Session after upstream loss'
type: 'bugfix'
created: '2026-09-27'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context:
  - _bmad-output/implementation-artifacts/spec-pilot-dead-upstream-reconnect-loop.md
  - _bmad-output/implementation-artifacts/spec-pilot-idle-upstream-retention.md
---

<frozen-after-approval reason="user explicitly requested F2 from the parent spec">

## Intent

**Problem:** Terminal retirement prevents reconnect loops but forces a new conversation even when the durable Standard Session remains resumable.

**Approach:** Suspend a failed upstream, retaining only its bound claim, durable Session and uncertain-turn metadata through the existing grace period. On authorized reconnect, perform exactly one fresh gateway connection and `session.resume`; never create another Session, replay the interrupted prompt, old audio, or buffered events from the lost transport. Revalidate the device credential/generation, active grant, handle, device, Profile, configuration and durable Session before recovery. Rejected candidates must not mutate or retire the original binding or extend its deadline. A resumed connection returns `ready` with any interrupted turn marked uncertain; if authorized resume fails, retire the claim and return `stale_conversation`. Healthy adoption remains unchanged. Given a completed turn and lost upstream, when authorized reconnect succeeds, then the same Session accepts and completes a follow-up prompt. Given an interrupted turn, then its ID and uncertain delivery survive without replay. Given an unresumable Session, then the terminal safeguard prevents a retry loop.

</frozen-after-approval>

## Implementation Notes

- Reuse `HomeBridge._reconnect`, which already performs `session.resume`, validates identity and retains uncertain turns. Add nonmutating candidate authorization before invoking it, because raw `_reconnect` can invalidate a binding on credential failure.
- Keep the endpoint event thread waiting while suspended, so fast reconnect cannot race event-pump restart. Stop old audio before returning readiness and discard lost-transport buffers. Endpoint parking retains suspended metadata, not a usable dead gateway.
- Scope: `standard.py`, `endpoint.py`, parking race handling in `bridge_server.py`, focused tests. No migration, deployment, client repository edits, or Standard modifications. Local fake-gateway context evidence is distinct from live pilot acceptance, which remains pending.

- Implemented `HomeBridge.suspend_failed_upstream` and `recover_upstream` around existing Session resume. Candidate preflight does not mutate authority; the actual attempt repeats configuration checks and any unsuccessful authorized exit retires the claim. Endpoint recovery is fenced against old event generations and event projection/delivery. Old audio must finish before readiness; its frames and notifications are dropped. Adoption buffers events and drops audio until authorization is acknowledged.
- Original parking expiry remains armed during recovery. Expiry captures the retirement obligation before asynchronous resume can change flags; a close requested during suspension completes when suspension unwinds. Healthy adoption and the F1 idle behavior remain covered.
- The existing ready envelope is retained. An interrupted recovered turn has its original ID, `status: uncertain`, and additive `delivery: uncertain`; no new failure code or client changes are required. Failed resume uses existing `stale_conversation`.
- Validation: `uv run --python 3.14 pytest -q tests/test_bridge_endpoint.py tests/test_bridge_server.py tests/test_standard_bridge.py tests/test_production_bridge.py tests/test_runtime.py` — 316 passed. `uvx ruff check src tests`, `uvx ruff format --check src tests` (68 files), and `git diff --check` passed.
- Live WebSocket/fake-gateway tests prove the same durable Session resumes once and a follow-up prompt uses the fake Session's retained context. They cover completed/interrupted first turns, wrong credentials, resume rejection/timeout/Session mismatch, five terminal retries without further gateway creation, configuration changes and unexpected callback failure. Barrier tests cover old reader failure/projection, held audio, suspended cleanup, original-deadline expiry and preauthorization output. This is not proof of deployed Hermes context continuity.
- Deployment, full two-minute pilot background/foreground reproduction, and live relay-socket kill acceptance remain pending. No remote services or configurations changed. These standalone pilot slices have no sprint tracker entry; issue/PR synchronization was not performed.

## Review Triage Log

- High, patched: an old reader failure could suspend the newly recovered gateway. Capture transport generations, pause the pump during rebuild and discard obsolete errors/events; deterministic old-error-after-ready test.
- High, patched: second grant resolution omitted configuration revision checks. Repeat the binding check inside actual resume; test revision change between preflight and resume.
- High, patched: unexpected authorized recovery exceptions bypassed retirement and allowed more attempts. Every unsuccessful authorized attempt now reaches terminal retirement; injected callback exception test.
- Medium, patched: taking a parked endpoint canceled expiry throughout a slow recovery. Arm original-deadline cleanup during the handshake; blocked-resume expiry test proves no ready after closure.
- Medium, patched: recovery tests lacked old audio concurrency. Hold an audio read across adoption, require no output before readiness, drop its old frame and verify a new audio pump starts.
- Medium, patched: tests omitted adoption and grace expiry during blocked suspension. Barrier test checks unchanged parking deadline and deferred closure without a live claim/endpoint afterward.
- High, patched on follow-up: generation check did not fence projection/delivery and associated errors. Shared event-dispatch lock serializes them against recovery; barrier projection test proves old output is discarded.
- High, patched on follow-up: expiry could lose its retirement obligation when resume cleared a flag. Capture the obligation under the state lock and do not clear it after shutdown begins.
- High, self-review patch: attached candidates could receive output before reauthorization completed. Gate event/audio delivery until the ready acknowledgment; targeted test proves the boundary.
- Final reviewer inspection verified the residual corrections and reported no further concrete blockers. No findings deferred.
