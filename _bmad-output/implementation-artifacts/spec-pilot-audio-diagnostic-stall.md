---
title: 'Pilot bridge — stop diagnostics from throttling spoken audio'
type: 'bugfix'
created: '2026-09-26'
status: 'in-review'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Spoken replies stuttered all the way through on iOS, on both Wi-Fi and cellular. Home forwarded response audio at about 0.63× real time: each PCM frame left roughly 200–250 ms after the previous one, whatever its size. Every upstream hop measured faster than real time on its own (Qwen 1.28×, Standard `speak-stream` 1.16× from media-server and from CaticornQueen through Tailscale Serve).

A `py-spy` profile of the live Home process during a spoken turn showed the audio thread spending 95% of its time in `SQLiteDiagnosticsStore.purge_expired`. The bridge recorded one `audio`/`accepted` diagnostic per PCM frame. Each record ran `purge_expired` three times (recorder, `append`, and the status refresh), and each run loaded every stored event (the table sits at its 4,096-event bound) and JSON-decoded and validated it in Python only to compare a deadline.

**Approach:**

1. Store each event's retention deadline in an indexed `expires_at` column and purge with one SQL `DELETE … WHERE expires_at <= ?`. Existing databases gain the column and are backfilled once at open.
2. Record audio diagnostics per stream, not per frame: `started`, then one terminal event. `completed` carries the total forwarded PCM byte count, so the timeline keeps the same fact without one event per frame.

No wire, protocol, retention, or privacy behavior changes. Diagnostics stay content-free.

</frozen-after-approval>

## Acceptance

- Purging no longer decodes stored events; expired events are removed by deadline, including events written before the column existed.
- A spoken turn records `audio`/`started` and one terminal audio event; `completed` carries the stream's total byte count.
- Focused tests, `ruff check src tests`, and `ruff format --check src tests` pass.
- Live check on CaticornQueen: Home's forwarded audio keeps pace with real time and iOS playback no longer stutters.
