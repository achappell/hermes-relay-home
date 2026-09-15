---
title: 'HOME-NW-03 — Serve the versioned Home bridge over one approved local route'
type: 'feature'
created: '2026-09-14'
status: 'done'
baseline_revision: 'acdf92d52c703b4d7b05293b4e5a306a16923d92'
route: 'dispatch'
review_loop_iteration: 0
followup_review_recommended: true
warnings: [oversized]
deferred:
  - Active-session revocation observation requires a credential/session lifecycle hook outside NW-03.
  - Windows installer/environment wiring for the sibling bridge listener remains deployment follow-up.
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/_bmad-output/specs/spec-home-bridge-route-roaming/SPEC.md'
  - '{project-root}/_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md'
  - '{project-root}/_bmad-output/specs/spec-standard-bridge/transport-contract.md'
---

<intent-contract>

## Intent

**Problem:** Home already has a deterministic `HomeBridge` that authorizes a
device and binds an opaque conversation to the pinned Standard Hermes gateway,
but endpoint clients have no versioned Home route through which to use it.

**Approach:** Add a Home-owned route adapter for one explicitly configured local
`home` route. It will validate the Home JSON-RPC envelope, translate endpoint
requests to the existing `HomeBridge`, and translate statuses, turns, Standard
events, prompt responses, controls, and response-audio frames into the safe
Home contract.

## Boundaries & Constraints

**Always:** Use JSON-RPC 2.0 with Home `schema: 1`; authenticate native upgrades
with `Authorization: Device`; keep Hermes bearer, Profile ID, runtime Session
ID, and credentials out of endpoint frames and logs; bind every operation to
one opaque conversation handle; preserve Standard event meaning and audio
metadata; distinguish typed rejection from uncertain transport; never retry an
uncertain prompt or claim that reconnect completed it.

**Never:** Add route discovery, Tailscale/public roaming, same-household
identity proof, TLS deployment, browser tickets, microphone ingress, a second
Hermes protocol, or a new Profile/conversation authority. The route adapter
must not manufacture grants; it consumes the injected Home-owned resolver.

**Decisions:** “Serve” means a live local WebSocket endpoint. It runs as a
sibling service using the dependency-backed synchronous WebSocket server; the
existing HTTP listener remains HTTP-only. The bridge factory and grant resolver
are injected runtime dependencies, so NW-03 can serve real endpoint traffic
without creating the NW-05 conversation authority.

The one route is represented by an injected safe descriptor with class `home`
and a non-secret operator label; NW-03 does not invent a route set or claim to
prove Household Identity. If no bridge factory is configured, the listener
remains safely available but returns `hermes_unavailable` rather than creating
an implicit grant or Session.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|----------------------------|----------------|
| OPEN | Valid Device credential and opaque handle on the local route | WebSocket upgrade succeeds and one JSON-RPC response contains the safe `ready` status and `home` route descriptor | Wrong path or malformed auth is rejected at upgrade; invalid, revoked, stale, or mismatched binding returns a safe typed failure; no turn is created |
| PROMPT | `prompt.submit` while ready with non-empty text | One opaque submitted turn response; later event/audio notifications retain correlation | Blank/malformed input is rejected before Hermes; transport loss is uncertain and is never retried |
| RECOVER | `conversation.reconnect` after transport loss | Fresh authorization and Standard Session resume; unresolved turn remains separate | No readiness claim when authorization, protocol, or Hermes binding fails |
| EVENT/AUDIO | Standard event or sidecar frame | Home notification preserves event/payload/order or typed PCM metadata | Server-only identity and credential fields are removed; malformed frames fail closed |

</intent-contract>

## Code Map

- `src/hermes_home/bridge/standard.py:146-221,705` — reusable
  `BridgeStatus`, `BridgeTurn`, `BridgeEvent`, `AudioFrame`, and `HomeBridge`;
  preserve its Standard gateway/audio ports, failure codes, and no-replay state;
  normalize internal `hermes_timeout` into the public route vocabulary here at
  the adapter boundary rather than exposing a new endpoint code.
- `src/hermes_home/bridge/__init__.py` — public bridge exports; extend with the
  route adapter's framework-independent types.
- `src/hermes_home/api/application.py:25` — current HTTP body bound and
  contract adapter; preserve its credential-authentication boundary, routes,
  and metrics.
- `src/hermes_home/api/server.py:15,64` — current loopback-by-default HTTP
  listener; preserve it unchanged because the bridge is a sibling service.
- `src/hermes_home/api/bridge_server.py` — new WebSocket listener using the
  dependency-backed synchronous server, route/path checks, bounded frames, and
  per-connection cleanup.
- `src/hermes_home/runtime.py:25-58,63-177` — runtime settings, resource
  ownership, construction, and shutdown; add bridge listener ownership and
  injected dependencies without inventing grant storage or changing existing
  HTTP settings.
- `pyproject.toml:1-18` and `uv.lock` — package metadata and locked
  dependencies; add the selected WebSocket runtime dependency; do not
  hand-roll RFC 6455 framing.
- `tests/test_standard_bridge.py` — deterministic fake gateway/audio fixtures
  and bridge lifecycle evidence to reuse, not rewrite.
- `tests/test_http_server.py` — existing server lifecycle conventions and
  regression coverage for the v1 HTTP contract.
- `tests/test_runtime.py` — runtime assembly and cleanup conventions to extend
  for the sibling listener.
- `tests/test_home_next_wave_contracts.py` — contract-presence assertions; add
  route assertions only for behavior actually implemented here.
- `_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md` —
  authoritative planned endpoint envelope, methods, events, audio, and safe
  failure vocabulary; NW-03 implements only its local-route slice.
- `_bmad-output/specs/spec-home-bridge-route-roaming/SPEC.md` — cross-story
  ownership and explicit NW-03 exclusions for roaming, identity proof, and
  browser bootstrap.
- `_bmad-output/specs/spec-standard-bridge/transport-contract.md` — pinned
  Standard gateway/audio semantics that the adapter must preserve.

## Tasks & Acceptance

**Execution:**
- [x] `src/hermes_home/bridge/endpoint.py` — implement one connection adapter
  that validates JSON-RPC/schema/request IDs/params, dispatches the contract's
  `conversation.open`, `conversation.reconnect`, `prompt.submit`,
  `prompt.respond`, `session.interrupt`, `command.dispatch`, and `bridge.ping`
  methods on one injected `HomeBridge`; wrap safe results, retain structured
  prompt events, and serialize event/audio notifications with an outbound lock.
  Map bridge failures to the stable Home error envelope and mark input
  transport failures uncertain without retrying; add the injected safe route
  descriptor to readiness results and normalize every internal failure to the
  documented public code set.
- [x] `src/hermes_home/api/bridge_server.py` — serve only
  `/api/v1/bridge/ws` on a sibling listener with the `websockets` synchronous
  server, require a `Device` upgrade header, create one endpoint/bridge per
  connection, enforce a 1 MiB message bound, and close the bridge on exit.
- [x] `src/hermes_home/bridge/__init__.py`, `src/hermes_home/api/__init__.py`,
  and `src/hermes_home/runtime.py` — export the adapter, add injected bridge
  factory and bridge-server lifecycle ownership, and leave existing HTTP
  routes/metrics and NW-05 grant ownership unchanged.
- [x] `pyproject.toml`, `uv.lock`, `tests/test_bridge_endpoint.py`,
  `tests/test_bridge_server.py`, and `tests/test_runtime.py` — add the locked
  WebSocket dependency and deterministic protocol, live-listener, cleanup,
  route-label, missing-factory, redaction, error-normalization, and regression
  coverage using fake `HomeBridge`/connection seams.
- [x] `_bmad-output/implementation-artifacts/validation-home-nw-03.md` — record
  the observed focused/full tests, package build, Ruff checks, and live local
  WebSocket smoke result; distinguish fake bridge evidence from live Hermes
  evidence.

**Acceptance Criteria:**
- Given an approved local route and valid endpoint authorization, when a client
  opens an opaque conversation, then the sibling endpoint returns a safe
  schema-1 readiness result containing the configured `home` route descriptor
  and never exposes the Hermes bearer or internal Session identity.
- Given a ready bridge, when a client submits text or a supported control,
  then `prompt.respond`, `session.interrupt`, `command.dispatch`, and
  `bridge.ping` follow the planned method contract while the adapter preserves
  Standard event/prompt/audio semantics and binds all output to the Home
  conversation/turn handles.
- Given a malformed, unauthorized, stale, mismatched, rejected, timed-out, or
  transport-lost request, then Home returns the contract's typed safe outcome
  and performs no unsafe retry or retargeting.
- Given a reconnect after uncertain delivery, when Standard readiness returns,
  then Home reports unresolved delivery separately and accepts a new prompt
  only after readiness; it never replays the old prompt automatically.

## Spec Change Log

## Review Triage Log

- Review layers: intent alignment, edge-case, blind, and verification-gap.
- Review result: 66 findings — 0 high, 28 medium, 25 low, and 13 false; 51
  patched, 2 deferred, and 13 rejected. No intent gap or bad-spec finding was
  identified. The medium findings make a follow-up review advisable after real
  HomeBridge/Hermes integration evidence exists.

### Intent alignment — Hooke

- [false] [reject] I1 — The full-operational reading expected production
  HomeBridge construction; NW-03 explicitly injects the bridge factory and
  does not create NW-05 grant or Session authority.
- [false] [reject] I2 — Upgrade authentication only checks the Device scheme;
  credential outcomes remain the injected HomeBridge's responsibility, while
  the listener rejects malformed upgrades before a connection is created.
- [false] [reject] I3 — Reconnect was not composed with live grant resolution
  and Standard resume; that behavior already belongs to HomeBridge and live
  Hermes validation is a separate gate from this adapter slice.
- [false] [reject] I4 — Handcrafted event/audio fixtures do not prove live
  Hermes semantics, but deterministic fake bridge coverage is the specified
  adapter-boundary evidence and the implementation preserves the domain types.
- [false] [reject] I5 — Log exclusion was not exercised because NW-03 adds no
  logging surface; endpoint serializers still scrub server-only fields before
  frames are emitted.
- [false] [reject] I6 — A safe `home/local` route default is not route
  discovery or an invented route set; runtime accepts the explicitly
  configured descriptor and the adapter tests the configured label.
- [false] [reject] I7 — Converting the draft to the workflow's
  `<intent-contract>`, marking it in review, and adding task/evidence sections
  are required build-auto governance changes, not scope drift.

### Edge-case hunter — Ptolemy

- [low] [patch] E1 — Added live handshake coverage for missing, empty, and
  duplicate Authorization headers; the listener rejects all before factory
  creation.
- [medium] [patch] E2 — Added post-bind handle mismatch coverage; every method
  uses the bound opaque handle and makes no bridge call on mismatch.
- [medium] [patch] E3 — Added terminal-event coverage proving audio cleanup and
  a completed turn release the connection for the next prompt.
- [medium] [patch] E4 — Extended reconnect coverage through a fresh prompt and
  reset of endpoint turn/audio state without replaying the old prompt.
- [low] [patch] E5 — Added runtime factory-wiring coverage for host, port,
  route descriptor, and injected bridge factory.
- [medium] [patch] E6 — Added nested mapping/list redaction coverage and
  normalized key matching for camelCase server-only names.
- [low] [patch] E7 — Added route-level timeout coverage and retained one shared
  `_raise_bridge_error` mapper for prompt responses, interrupts, and commands;
  all preserve typed delivery state without retry.
- [low] [patch] E8 — Added endpoint audio-timeout and malformed-frame tests;
  the audio worker now reports typed unavailability and always clears its
  ownership state.
- [low] [patch] E9 — Added public readiness normalization coverage for the
  internal `hermes_timeout` alias to `transport_timeout`.
- [medium] [patch] E10 — Unknown same-conversation turn events now fail closed
  as uncertain protocol errors; only a currently submitting turn may bridge
  the short binding race before its returned ID is known.

### Blind hunter — Nash

- [medium] [defer] B1 — An active-session revocation observer is outside the
  injected NW-03 boundary and needs a credential/session lifecycle hook; the
  existing HomeBridge revalidates before operations. Deferred to that owning
  slice.
- [false] [reject] B2 — The shipped entry point intentionally has no bridge
  factory until the Home-owned construction dependency is supplied; the
  explicit no-factory behavior is `hermes_unavailable`, as required.
- [medium] [patch] B3 — Bridge binding now defaults independently to loopback,
  including the programmatic runtime fallback, so an HTTP `0.0.0.0` setting
  does not silently expose the sibling bridge.
- [low] [patch] B4 — Startup failure cleanup now calls `server.server_close()`
  after bridge setup fails.
- [medium] [patch] B5 — Reconnect captures and joins the prior audio worker,
  clears ownership, and only starts the fresh event pump after cleanup.
- [medium] [patch] B6 — Audio send/worker failures now run through `finally`,
  mark transport loss, close the endpoint, and clear stale audio ownership.
- [medium] [patch] B7 — Event send failures now mark transport unavailable and
  close the bridge/socket instead of leaving a ready-looking dead stream.
- [medium] [patch] B8 — Built-in `TimeoutError` is classified before `OSError`
  as `transport_timeout` with uncertain delivery.
- [medium] [patch] B9 — Recursive outbound scrubbing now compares normalized
  alphanumeric key names, covering `profileId`, `runtimeSessionId`, and
  equivalent casing.
- [medium] [patch] B10 — Prompt-response, command, and ping results are
  allowlisted and wrapped with the bound conversation handle and turn where
  applicable.
- [medium] [patch] B11 — Same-conversation events with unknown turn IDs now
  fail closed; terminal state cannot be attributed to a foreign turn.
- [low] [patch] B12 — Prompt-expiry notifications now remove the matching
  retained prompt before a late response can resolve it.
- [low] [patch] B13 — Clarify responses validate question IDs against the
  retained batch question list and reject malformed values.
- [false] [reject] B14 — Advertised-command membership is already enforced by
  the existing HomeBridge boundary; the route adapter consumes that injected
  dependency and does not duplicate its authority.
- [medium] [patch] B15 — Invalid turn results now mark the endpoint unavailable
  with uncertain delivery, preventing a delivered prompt from being retried
  while the endpoint still claims readiness.
- [medium] [patch] B16 — Unsafe results after prompt/control execution now mark
  the endpoint unavailable before returning the typed protocol failure.
- [false] [reject] B17 — Request IDs are retained in method errors; missing or
  invalid IDs use `-32600`, while only malformed JSON uses `-32700`.
- [low] [patch] B18 — Schema validation now requires an exact integer, so
  boolean and floating-point lookalikes are rejected.
- [low] [patch] B19 — Deep JSON recursion is caught and returned as a typed
  invalid request rather than escaping from the parser.
- [low] [patch] B20 — The endpoint forces the unverified timing capability to
  `absent`, regardless of an injected future label.
- [medium] [patch] B21 — Outbound JSON is size-checked and PCM is emitted in
  bounded even-byte chunks under the one-MiB limit.
- [low] [patch] B22 — README's no-project verification command now includes
  the explicitly selected `websockets` dependency.
- [low] [defer] B23 — Windows installer/environment wiring for the sibling
  listener is deployment follow-up, not the local adapter contract; the
  runtime variables and loopback defaults are documented and tested here.
- [low] [patch] B24 — Canonical bridge and Standard transport docs now state
  that HOME-NW-03 serves the local route while roaming/browser deployment
  remains future work.
- [false] [reject] B25 — In-review status, unchecked tasks, and a not-yet-written
  validation artifact were expected intermediate workflow state; finalization
  supplies them before commit.
- [low] [patch] B26 — Finalization adds the HOME-NW-03 spec and validation links
  to the local story index and synchronizes formal status after verification.

### Verification-gap reviewer — Poincare

- [low] [patch] V1 — Exact integer schema checking and its regression test also
  cover the claimed JSON-RPC/schema validation boundary.
- [false] [reject] V2 — The Home contract explicitly requires approval
  `choice`; the Standard layer's deny default is not a valid Home endpoint
  response shape.
- [low] [patch] V3 — Clarify `question_id` type, batch presence, and membership
  are validated before the injected bridge is called.
- [medium] [patch] V4 — Unknown turn events are rejected as uncertain protocol
  failures, with a narrowly scoped submission race allowance.
- [medium] [patch] V5 — Reused IDs are rejected after bridge acceptance, and
  retired-ID protection is bounded to 1024 entries per connection.
- [low] [patch] V6 — Falsey non-None audio turn IDs are rejected instead of
  being attributed to the active turn.
- [medium] [patch] V7 — Terminal events observed during prompt submission are
  retained and applied when the returned turn ID binds.
- [medium] [patch] V8 — Invalid accepted-turn status marks delivery uncertain
  and readiness unavailable.
- [medium] [patch] V9 — Malformed results after prompt/control side effects
  mark the endpoint unavailable before exposing a typed protocol error.
- [medium] [patch] V10 — Built-in timeout classification is tested and mapped
  to uncertain `transport_timeout`.
- [medium] [patch] V11 — Hermes-unavailable/protocol failures now clear public
  readiness instead of leaving the endpoint ready after the bridge loses it.
- [medium] [patch] V12 — Event sanitization/send failures close the endpoint
  and bridge after marking the safe failure state.
- [medium] [patch] V13 — Audio send failures close the endpoint and the worker
  `finally` block clears ownership.
- [low] [patch] V14 — Invalid audio kind/metadata is typed as protocol error,
  reported through `audio.frame`, and cleaned up.
- [low] [patch] V15 — Terminal IDs are discarded on release; the added retired
  duplicate guard is bounded, so the connection does not accumulate an
  unbounded terminal-ID set.
- [low] [patch] V16 — HTTP server resources are closed on bridge setup failure.
- [low] [patch] V17 — Cleanup joins the bridge thread only after it has started;
  unstarted-thread failure cannot block shutdown.
- [low] [patch] V18 — This duplicate schema claim is covered by the exact-int
  fix and its test; no separate defect remains.
- [false] [reject] V19 — This duplicate approval-only claim conflicts with the
  explicit Home contract requirement for `choice`.
- [medium] [patch] V20 — This duplicate unknown-turn claim is covered by the
  fail-closed event binding patch.
- [medium] [patch] V21 — This duplicate malformed-turn claim is covered by
  uncertain-state marking after accepted prompt delivery.
- [low] [patch] V22 — This duplicate malformed-audio claim is covered by typed
  audio failure emission and worker cleanup.
- [low] [patch] V23 — This duplicate HTTP-cleanup claim is covered by startup
  `server_close()` cleanup.

## Design Notes

The shared route-roaming specification is intentionally broader than this
story. NW-03 therefore owns one local route and the endpoint adapter boundary;
route selection, identity proof, browser bootstrap, and roaming stay separate.
The existing bridge is a domain seam, not a wire adapter, so endpoint envelopes
must be built at the edge rather than changing Standard semantics. The bridge
domain serializer is not sufficient as a complete outbound scrubber; the route
adapter must allowlist endpoint fields and keep Profile/session/backend values
out even when a live transport is selected. The sibling listener owns only
WebSocket lifecycle; `HomeApplication` remains the ordinary HTTP adapter. Use
`websockets` 17.x synchronous server/client APIs rather than hand-implementing
RFC 6455; the bridge and Hermes network factories remain injectable so the
route can be tested without a live Hermes endpoint.

## Verification

**Commands:**
- `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q` — expected: all tests pass.
- `uvx --from ruff ruff check src tests` — expected: no diagnostics.
- `uvx --from ruff ruff format --check src tests` — expected: formatted.
- `uv run --no-cache --no-project --python 3.14 --with build -- python -m build --sdist --wheel` — expected: package build succeeds.

## Auto Run Result

- Outcome: `done`. The local Home bridge route, sibling listener, runtime
  lifecycle, dependency lock, deterministic coverage, and validation artifact
  are complete.
- Review: four context-free review layers returned 66 findings. The loop
  patched 51, deferred 2, and rejected 13 false or scope-conflicting claims;
  no intent gap or bad-spec finding was identified.
- Verification evidence is recorded in
  `_bmad-output/implementation-artifacts/validation-home-nw-03.md`: 41 focused
  tests, 203 full tests, Ruff check/format, `uv lock --check`, and a successful
  sdist/wheel build. The live listener smoke uses an injected fake bridge; no
  live Hermes or physical-device claim is made.
- Deferred risk: active-session revocation observation needs a credential/
  session lifecycle hook outside NW-03, and Windows installer wiring for the
  sibling listener remains deployment follow-up.
- Follow-up review: recommended once a real HomeBridge/Hermes integration
  fixture or environment is available; the current adapter-boundary evidence
  is complete and the route never retries uncertain input.
