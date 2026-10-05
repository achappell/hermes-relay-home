---
title: Response-audio timeout must not run before speech is requested
status: implemented-not-deployed
validated: 2026-10-05
baseline_commit: 26ed833
---

# Response-audio timeout must not run before speech is requested

## Incident evidence (2026-10-05, UTC)

Read-only evidence from the CaticornQueen Home diagnostics store and the
media-server agent log. No prompt or response content was read.

| Time | Source | Observation |
| --- | --- | --- |
| 13:45:49.115 | Home | Turn accepted. |
| 13:46:19.126 | Home | `audio unavailable`, `failure_code=transport_timeout`: 30.0 s after acceptance. |
| 13:46:24.752 | Agent | Model call finished after 35.8 s; turn ended normally. |
| 13:46:25.114 | Home | Turn completed. |
| 13:48:40.043 → 13:49:10.055 | Home | Same pattern for a later turn: audio unavailable 30.0 s after acceptance; the turn completed at 13:49:48. |

`BridgeEndpoint._audio_loop` calls `HomeBridge.next_audio()` as soon as a turn
is accepted. `_next_audio` gave each read a full `audio_timeout` (30 s),
counted from the call, so the timeout ran while the agent was still thinking,
before any text had reached Standard's speak-stream. Home then sent
`{kind: unavailable, reason: transport_timeout}` and the phone showed
"Audio playback failed".

A separate incident in the same window (13:48:07–13:48:38) is a speech stall
after the first sentence. That is a Standard speak-stream/TTS issue; this fix
keeps Home's 30 s timeout for it.

## Change

- `HomeBridge` records when speech is first requested from the sidecar: first
  text appended, `done`, or `stop` (`_ActiveTurn.audio_requested_at`).
- `_next_audio` counts `audio_timeout` only from
  `max(call start, speech requested)`. Before that, it polls the sidecar in
  1-second slices with no overall limit. The wait still ends when the sidecar
  is closed or replaced (upstream loss, interrupt, endpoint close), and the
  turn's terminal sends `done`/`stop`, which starts the 30 s clock.
- After speech is requested, behaviour is unchanged: 30 s with no frame raises
  `BridgeTimeoutError`, and the endpoint sends `unavailable`/`transport_timeout`.
- `HomeBridge` takes an injectable `monotonic` clock for tests. The production
  factory and the client wire contract are unchanged.

## Verification

| Check | Command | Result |
| --- | --- | --- |
| New behaviour tests | `.venv/bin/python -m pytest -q tests/test_standard_bridge.py -k "thinking or never_sends or stalls_after or once_speech"` | 4 passed. Against the baseline source, the two new pre-speech tests failed (`audio wait did not keep polling`). |
| Bridge suites | `.venv/bin/python -m pytest -q tests/test_standard_bridge.py tests/test_bridge_endpoint.py tests/test_bridge_server.py tests/test_production_bridge.py` | 310 passed |
| Full suite | `.venv/bin/python -m pytest -q` | 832 passed |
| Lint/format | `ruff check src tests`; `ruff format --check src tests` | Passed |

Tests use a fake speak-stream socket that answers only after Home sends text,
`done`, or `stop`, and a manual clock:

1. No text for 40 s, then text and PCM: no timeout; audio is forwarded.
2. After the first PCM, 30 s with no frame: `BridgeTimeoutError` (the endpoint
   maps it to `unavailable`/`transport_timeout`).
3. The turn completes with no text: `done` is sent, Standard's `start`/`end`
   is forwarded, no timeout, the sidecar closes, and the turn is released.

`test_bridge_bounds_response_audio_wait` now covers the bound after speech is
requested (renamed `..._once_speech_is_requested`).

## Not verified / deployment

Not deployed. CaticornQueen runs `hermes_home.runtime` from
`C:\ProgramData\HermesHome` (deployment revision `376583d…`, which is not in this
clone). The phone benefits only after this change is deployed there and the
Hermes Home scheduled task restarts. No live Standard or device test was run.
