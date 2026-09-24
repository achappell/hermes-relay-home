---
status: deployed-verified-source
validated: 2026-09-23
baseline_commit: d2447f684a143817f7b5688b597c11aa053bb672
---

# Home title event and upstream-loss repair

The personal-client live trial accepted a turn but never displayed its completed response. A read-only inspection found Standard's title event uses a runtime session ID in the envelope and a durable session ID in its payload. Home treated that valid pair as conflicting identities, stopped its reader, and closed the upstream connection. Its endpoint event pump marked itself unavailable without closing the downstream socket, leaving the client waiting.

## Changes

- Accept the distinct durable payload identity only for `session.title`, requiring its runtime envelope identity. Other event types retain conflicting-identity rejection.
- On genuine upstream transport failure, escaping timeout, or unavailable runtime, close the downstream connection with WebSocket code 1011. Preserve the bridge and uncertain turn for authenticated reconnect; do not cancel or replay the turn.
- Keep the existing quiet idle-timeout behavior separate from transport failure.

## Evidence

`uv run --python 3.14 --locked --extra dev pytest -q tests/test_standard_bridge.py tests/test_bridge_endpoint.py`: 216 passed. The new regressions cover title events followed by a complete streamed reply, rejection of title events without a runtime envelope, downstream closure with preserved adoptable uncertain state and no prompt replay, while the existing real HomeBridge idle-poll regression proves the healthy gateway remains ready after a quiet poll. The existing conflicting turn-event identity test still passes.

`uv run --python 3.14 --locked --extra dev pytest -q`: 683 passed in 8.11s before review additions; final complete suite after review additions: 684 passed in 7.46s.

`uvx ruff check src tests`: passed. `uvx ruff format --check src tests`: 68 files already formatted. `git diff --check`: passed.

Home repair commit `2dc6ee7bde443d8fb9cd4dba7c17f8e22eb4921b` was deployed during the authorized live trial. The candidate wheel SHA-256 is `6f08944bf8b9ae38311a678083d514968c9feadfaa629ed7c3182a1d6c7ddd4a`; all 32 installed Python source files match the reviewed tree. Only the Home scheduled task was restarted, with dependencies, settings, credentials and database preserved. The pairing page passed readiness. A fresh paired TUI adapter probe received the assistant text and message completion, confirming the original title-event stall is repaired. The probe then exposed a separate TUI assumption that audio.end must precede message.complete; that client correction is tracked in TUI-HOME-01. No Standard Hermes changes were made.

## Deployment boundary

The Windows installer can clear omitted Standard connection settings and also updates Prometheus. A narrowly scoped candidate deployment should preserve existing machine settings, credentials, and database; back up the installed Home package; install a verified wheel without changing dependencies; restart only the Home scheduled task; and verify health plus the personal-client conversation path. Retain the previous package for rollback. Do not use an installer invocation that omits the existing settings.

## Review triage

- Foreign-runtime title isolation: tolerable verification gap, now closed. `test_title_durable_identity_does_not_discard_remaining_turn_events` first injects a foreign runtime envelope with the current durable payload ID and proves it is filtered; the accepted title and following completion remain correctly attributed.
- Accepted title privacy projection: tolerable verification gap, now closed. The same test asserts the public title payload contains only the title and that its durable ID is absent from the serialized endpoint event. Existing conflicting turn-identity rejection is unchanged.
- Combined disconnect/parking uncertainty: tolerable verification gap, now closed. `test_real_bridge_upstream_loss_parks_endpoint_and_retains_original_uncertain_turn` runs a real HomeBridge and BridgeEndpoint with a failing synthetic gateway through `run(park_on_disconnect=True)`. It verifies close code 1011, a parked recoverable endpoint, real bridge state `turn_uncertain`, authenticated adoption exposing the original uncertain turn ID, and exactly one upstream prompt. `test_bridge_reconnects_by_resuming_without_replaying_uncertain_prompt` separately verifies upstream resume; `test_live_server_requires_reconnect_and_adopts_the_parked_turn` covers the live server's parking path.

Expanded focused check after review: `uv run --python 3.14 --locked --extra dev pytest -q tests/test_standard_bridge.py tests/test_bridge_endpoint.py tests/test_bridge_server.py`: **226 passed in 2.86s**. These additions required no production-code changes.

## Deployment evidence

The target retains the previous wheel and installed-package backup under `C:\ProgramData\HermesHome\repairs\title-event-20260923`. Rollback wheel SHA-256: `89cf88c4422fbb8faa932123fd03499c0edca2562dcd8c88a4396dfa671cd133`. The additive `repair.json` records this verified revision. The existing signed deployment manifest predates NW-17 and was not rewritten without its signing workflow; runtime provenance consumers of that older manifest remain a separate operational limitation.

## Follow-up: explicit close acknowledgment race

The live trial completed text and audio, then exposed a race in the new transport-loss closure: an explicit `conversation.close` shuts down the upstream gateway while the event pump is reading, and the resulting expected error could close the client socket before the close RPC acknowledgment was sent. The endpoint now marks explicit conversation shutdown before closing the bridge and suppresses event-pump teardown of the downstream socket for that expected shutdown. A later ready binding resets the guard; genuine unexpected failures retain the 1011 close path.

`test_explicit_close_ack_survives_concurrent_real_bridge_event_failure` uses a real HomeBridge and forces the event pump to process gateway shutdown before allowing the close RPC to return. It verifies the `closed` acknowledgment reaches the client while the downstream connection remains open. No Standard changes were needed.

After this repair: focused Standard bridge, endpoint, and server suites **227 passed in 2.92s**; full Home suite **685 passed in 7.13s**; Ruff lint/format and whitespace checks passed. The follow-up repair has not been deployed by this agent.

## Final live repair check

Deployed revision `e4838793943b2820c62cb8f68ecfb52cabfee196` includes the close-acknowledgment race fix. Wheel SHA-256: `ffb8d4dc99c3a6559a7ca293979355c176ce64f3ac3238d7bed211d99b56d9b0`. All 32 installed Python sources match. A fresh synthetic paired-client turn observed audio start at 0.64s, text completion at 1.58s, audio end at 2.21s, 25,856 PCM bytes and a confirmed claim close. The original user prompt was never replayed. The previous wheel/package backup remains available; existing pairing and configuration were preserved.

## Content-free reader diagnostics

Added bounded exception/cause class names and an allowlist of fixed parser failure descriptions to the Standard reader failure log and unexpected endpoint upstream-loss branches. Remote error messages, payloads, IDs, credentials, and tracebacks are not logged by these additions. Three regressions verify a recognized parser reason is recorded while arbitrary protocol messages and transport URLs containing secrets remain absent.

Focused bridge/endpoint/server suites: **230 passed in 3.08s**. Ruff lint/format and whitespace checks passed. Diagnostic changes preserve connection behavior and have not been deployed by this agent.

## Follow-up: concurrent authorization snapshots

A deterministic barrier test proved the failure: event polling and prompt admission can both capture one grant, independently resolve fresh equal grant objects, and race to replace the cached grant. The previous object-identity check rejected the second equally authorized result with `BridgeTransportError('bridge changed during Home authorization')`. `test_concurrent_equal_claim_refreshes_do_not_invalidate_binding` failed with that exact error before the repair.

The final check now compares authority values, including protected capability fields excluded from dataclass equality. It permits only a concurrent session promotion to the already validated expected durable ID, and prevents a stale resolver result from undoing accepted-turn persistence. Changed gateway/runtime/readiness or changed authority remains rejected. No broader locking or Standard modifications were introduced.

The seven-case `test_refresh_preserves_persistence_but_rejects_changed_authority` exercises the real persistence helper and rejects different session, Profile, revoked status, credential generation, protected capability, and capability revision updates.

After repair: focused bridge/endpoint/server suites **238 passed in 3.07s**; full Home suite **696 passed in 7.08s**; Ruff lint/format and whitespace checks passed. Live two-client and delayed-input validation remains with the main agent.

## Final review outcomes

- Authority comparison review: one low-severity direct correction. Normalize absent session IDs (`None` and empty string) in the session-promotion comparison, matching the preceding authorization checks. A two-direction narrow regression covers equivalent absent IDs during refresh.
- Other review layer: no defects found; no change required.
- Verification-gap review layer: no gaps found; no change required.

After this correction, only the edited Standard bridge test file was run: **131 passed**. Ruff checks for the edited Python files passed. Full verification is delegated to the main agent.

## Concurrent-client deployment and actual TUI verification

Final Home suite after review correction: **698 passed in 7.46s**. Deployed runtime revision `e2a8521aa100cd5c6ea58be2b5ce263f8a34eda7`, wheel SHA-256 `9227cfd1bdb4c687ff0fbdd14214a33e9de78937e71632e24de233a46198bcc2`. All 32 installed Python sources match and the target repair record was updated. The original two-client reproduction now completes a text/audio turn with both connections open and both claims close successfully.

A separate live `HermesStreamingApp.run_test()` check used the real paired Home transport and normal app event path, held a second connection open, waited 30 seconds, then submitted a fresh synthetic message. At 10, 20 and 30 seconds the app remained connected/ready. The turn returned true with connection connected, voice ready and prompt completed; both claims closed successfully. This covers the actual app/concurrent-connection condition missed by the earlier isolated adapter probe. Amanda was asked to retry her terminal after the Home restart; human confirmation remains pending. Standard Hermes was not modified.
