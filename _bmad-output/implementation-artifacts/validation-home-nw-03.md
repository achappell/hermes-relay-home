# HOME-NW-03 Validation

Validated 2026-09-14 on Python 3.14 in the isolated
`feat/home-nw-03-approved-local-bridge-route` worktree.

## Observed checks

| Check | Command | Result |
| --- | --- | --- |
| Focused adapter/runtime suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_bridge_endpoint.py tests/test_bridge_server.py tests/test_runtime.py` | `41 passed in 1.55s` |
| Full Home suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q` | `203 passed in 3.47s` |
| Ruff lint | `uvx --from ruff ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx --from ruff ruff format --check src tests` | `38 files already formatted` |
| Package build | `uv run --no-cache --no-project --python 3.14 --with build -- python -m build --sdist --wheel` | Successfully built the sdist and wheel |

The live local WebSocket smoke is included in the full suite as
`test_live_bridge_server_accepts_only_the_versioned_route_and_closes_bridges`.
It binds a real loopback listener, performs a real WebSocket upgrade and
schema-1 open, verifies the configured route and Device-header pass-through,
and confirms bridge cleanup. Its bridge is an injected deterministic fixture;
this is live listener evidence, not a live Hermes gateway or physical-device
claim.

The first no-project test attempt omitted the newly selected `websockets`
dependency and failed collection with `ModuleNotFoundError`. The corrected
command above supplies `websockets>=17,<18`, and the README and companion
transport contract now document that exact environment.

## Boundary evidence

- The sibling listener accepts only `/api/v1/bridge/ws`, requires exactly one
  non-empty `Authorization: Device ...` header, and enforces a one-MiB bound.
- Endpoint frames retain only opaque Home conversation/turn handles and safe
  route/status/event/audio fields. Hermes bearer, Profile, runtime Session, and
  credential-shaped values are not emitted.
- Prompt, control, event, reconnect, and audio failure paths preserve typed
  known/uncertain outcomes and do not retry uncertain input.
- Route-roaming, Household Identity proof, browser tickets, and physical
  hardware remain outside this story's evidence. The deployment follow-up
  below is an operator-managed Sprint 1 pilot, not final roaming or claim
  authority.

## Deployment follow-up evidence

Validated 2026-09-15 from the deployment-wiring worktree. The Windows
installer was parsed by PowerShell and run on CaticornQueen with:

```powershell
.\install.ps1 -WheelPath .\hermes_relay_home-0.1.0-py3-none-any.whl `
  -BridgeBindHost 127.0.0.1 -BridgePort 8766 `
  -BridgeRouteId caticornqueen-tailnet
```

Observed results:

- The installed wheel matched SHA-256
  `1ea94ff95ac1ac956a9966f8202019a0fe6436e441c3bbdc124048f68480d2fc`.
- The `Hermes Home` scheduled task was running with listeners on
  `127.0.0.1:8780` and `127.0.0.1:8766`.
- Machine environment values were `127.0.0.1`, `8766`, and
  `caticornqueen-tailnet` for the bridge bind host, port, and route ID.
- The authenticated Prometheus `hermes-home` target reported `up = 1`.
- Tailscale Serve preserved `/` and proxied the versioned bridge path to
  `http://127.0.0.1:8766/api/v1/bridge/ws`.
- An external `wss` upgrade reached Home and returned the safe
  `hermes_unavailable` result for `conversation.open`; this proves deployment
  and transport reachability, not a ready Hermes turn. The console runtime has
  no injected bridge factory yet.

## Current Sprint 1 pilot evidence

Validated 2026-09-15 after the tailnet access rule was saved. The first direct
Tailscale mapping reached Standard but received HTTP 403 because Hermes's
loopback Host guard correctly rejected the public Serve hostname. The checked-
in loopback relay now keeps both sides bounded: it validates the existing
Standard token, binds only to `127.0.0.1:9121`, and opens the upstream socket to
the loopback Standard listener on `127.0.0.1:9120`.

Observed results:

- The media-server Standard LaunchAgent and relay LaunchAgent were both
  running for Profile `amanda`.
- Tailscale Serve remained tailnet-only and mapped `/api/ws` and the separate
  `/api/audio/speak-stream` route to the relay; the existing `/` mapping was
  unchanged.
- CaticornQueen reached the media server on TCP 8443 after the ACL change.
- The real CaticornQueen → Home → Tailscale → relay → Standard
  `conversation.open` returned `status: ready`, `unresolved_turn: false`,
  `interrupt: true`, `timing: absent`, and `audio: true`. Session identity and
  credential values were not emitted into the evidence.
- A direct external upgrade of Standard's response-audio WebSocket succeeded.
- No prompt, agent turn, physical-device action, or audio stream was sent
  during this readiness check. Text-turn, interrupt, reconnect/no-replay, and
  audio-failure evidence remain the next validation work for STD-3.
- Current Home verification after the relay change: `229 passed`; Ruff lint
  and format checks passed; both launchd plist files and shell wrappers
  parsed successfully.
