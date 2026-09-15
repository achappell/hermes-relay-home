---
title: 'HOME-NW-03 — Serve the versioned Home bridge over one approved local route'
type: 'feature'
created: '2026-09-14'
status: 'draft'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/_bmad-output/specs/spec-home-bridge-route-roaming/SPEC.md'
  - '{project-root}/_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md'
  - '{project-root}/_bmad-output/specs/spec-standard-bridge/transport-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

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

</frozen-after-approval>

## Code Map

- `src/hermes_home/bridge/standard.py` — reusable `HomeBridge`, safe domain
  records, Standard gateway/audio ports, failure codes, and no-replay state;
  normalize internal `hermes_timeout` into the public route vocabulary here at
  the adapter boundary rather than exposing a new endpoint code.
- `src/hermes_home/bridge/__init__.py` — public bridge exports to extend with
  the route adapter's framework-independent types.
- `src/hermes_home/api/application.py` — current HTTP contract adapter and
  credential-authentication boundary; preserve existing routes and metrics.
- `src/hermes_home/api/server.py` — current loopback-by-default HTTP listener;
  preserve it unchanged because the bridge is a sibling service.
- `src/hermes_home/api/bridge_server.py` — new WebSocket listener using the
  dependency-backed synchronous server, route/path checks, bounded frames, and
  per-connection cleanup.
- `src/hermes_home/runtime.py` — runtime resource assembly and shutdown; add
  bridge listener ownership and an injected bridge factory without inventing a
  grant store or changing existing HTTP settings.
- `pyproject.toml` and `uv.lock` — add and lock the selected WebSocket runtime
  dependency; do not hand-roll RFC 6455 framing.
- `tests/test_standard_bridge.py` — deterministic fake gateway/audio fixtures
  and bridge lifecycle evidence to reuse, not rewrite.
- `tests/test_http_server.py` — existing server lifecycle conventions and
  regression coverage for the v1 HTTP contract.
- `tests/test_home_next_wave_contracts.py` — contract-presence assertions;
  add route assertions only for behavior actually implemented here.

## Tasks & Acceptance

**Execution:**
- [ ] `src/hermes_home/bridge/endpoint.py` — implement one connection adapter
  that validates JSON-RPC/schema/request IDs/params, dispatches the contract's
  methods to one injected `HomeBridge`, wraps safe results, retains structured
  prompt events, and serializes event/audio notifications with an outbound
  lock. Map bridge failures to the stable Home error envelope and mark input
  transport failures uncertain without retrying; add the injected safe route
  descriptor to readiness results and normalize every internal failure to the
  documented public code set.
- [ ] `src/hermes_home/api/bridge_server.py` — serve only
  `/api/v1/bridge/ws` on a sibling listener with the `websockets` synchronous
  server, require a `Device` upgrade header, create one endpoint/bridge per
  connection, enforce a 1 MiB message bound, and close the bridge on exit.
- [ ] `src/hermes_home/bridge/__init__.py`, `src/hermes_home/api/__init__.py`,
  and `src/hermes_home/runtime.py` — export the adapter, add injected bridge
  factory and bridge-server lifecycle ownership, and leave existing HTTP
  routes/metrics and NW-05 grant ownership unchanged.
- [ ] `pyproject.toml`, `uv.lock`, `tests/test_bridge_endpoint.py`,
  `tests/test_bridge_server.py`, and `tests/test_runtime.py` — add the locked
  WebSocket dependency and deterministic protocol, live-listener, cleanup,
  route-label, missing-factory, redaction, error-normalization, and regression
  coverage using fake `HomeBridge`/connection seams.
- [ ] `_bmad-output/implementation-artifacts/validation-home-nw-03.md` — record
  the observed focused/full tests, package build, Ruff checks, and live local
  WebSocket smoke result; distinguish fake bridge evidence from live Hermes
  evidence.

**Acceptance Criteria:**
- Given an approved local route and valid endpoint authorization, when a client
  opens an opaque conversation, then the sibling endpoint returns a safe
  schema-1 readiness result containing the configured `home` route descriptor
  and never exposes the Hermes bearer or internal Session identity.
- Given a ready bridge, when a client submits text or a supported control,
  then the adapter preserves Standard event/prompt/audio semantics and binds
  all output to the Home conversation/turn handles.
- Given a malformed, unauthorized, stale, mismatched, rejected, timed-out, or
  transport-lost request, then Home returns the contract's typed safe outcome
  and performs no unsafe retry or retargeting.
- Given a reconnect after uncertain delivery, when Standard readiness returns,
  then Home reports unresolved delivery separately and accepts a new prompt
  only after readiness; it never replays the old prompt automatically.

## Implementation Notes

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
- `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography -- python -m pytest -q` — expected: all tests pass.
- `uvx --from ruff ruff check src tests` — expected: no diagnostics.
- `uvx --from ruff ruff format --check src tests` — expected: formatted.
- `uv run --no-cache --no-project --python 3.14 --with build -- python -m build --sdist --wheel` — expected: package build succeeds.
