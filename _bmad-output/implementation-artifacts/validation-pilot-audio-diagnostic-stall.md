# Validation — Pilot bridge audio diagnostic stall

Spec: [spec-pilot-audio-diagnostic-stall.md](spec-pilot-audio-diagnostic-stall.md)

## Diagnosis evidence (2026-09-26, CaticornQueen pilot)

| Hop | Measured speed |
|---|---|
| Qwen3-TTS direct (Mac and media-server) | 1.28× real time |
| Qwen3-TTS sentence by sentence | 1.11× |
| Standard `speak-stream` on media-server loopback | 1.16× |
| Standard `speak-stream` from CaticornQueen via Tailscale Serve | 1.16× |
| Home forwarding during a spoken turn (Home's own frame timestamps) | 0.63× |
| iOS arrival | 0.63× |

- Home's per-frame forward gaps were 205–290 ms regardless of frame size (8,192 and 1,792 bytes alike).
- `py-spy record` of the live Home process for one spoken turn: the audio thread spent 95% of samples in `SQLiteDiagnosticsStore.purge_expired`, 2% waiting for audio, and under 1% sending.

## Automated checks (worktree `fix/home-audio-diagnostic-stall`)

- `pytest -q` (full suite): 705 passed.
- `ruff check src tests`: all checks passed.
- `ruff format --check src tests`: 68 files already formatted.
- New tests: purge by deadline without decoding stored events; backfill of `expires_at` for a pre-column database, including an undecodable legacy row. Updated test: audio timeline records `started` and `completed` with the total byte count.

## Micro-benchmark

`DiagnosticsRecorder.record()` into a store at its 4,096-event bound, 50 samples, same Mac:

- Before: median 117.1 ms, max 166.3 ms.
- After: median 1.41 ms, max 1.73 ms.

Per spoken reply, audio diagnostics drop from one record per PCM frame (about 50 for a 7 s reply) to two.

## Live check

Pending deployment to CaticornQueen: confirm Home's forwarded audio keeps pace with real time and iOS playback no longer stutters.
