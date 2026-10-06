# Standard speak-stream timeout patch

A local edit to the media-server's Hermes Agent checkout
(`~/.hermes/hermes-agent`, base `f97608f178d1`), made 2026-10-06 and kept here
so a `hermes update` cannot silently revert it. It is **not** part of upstream
Hermes; the live files carry it as uncommitted changes.

## What and why

Home's response-audio socket (`/api/audio/speak-stream`) is served by the
Standard pilot on port 9120. When the speech provider stalled mid-sentence, the
OpenAI SDK default (600 s timeout, two retries) held `speak-stream` silent far
past Home's 30 s audio timeout, so the client gave up and the stall looked like
a Home bug. The patch bounds each synthesis request so a stalled sentence ends
the stream promptly:

| File | Change |
| --- | --- |
| `tools/tts_streaming.py` | `OpenAIStreamer.stream` builds the client with `timeout=_stream_timeout_seconds(self.section)` and `max_retries=0`. The timeout bounds connect and each read (the gap between PCM chunks). |
| `hermes_cli/web_routers/audio.py` | The `speak-stream synthesis failed` warning logs only the exception type. Provider errors can echo the synthesized text; the `finally` still ends the stream. |

Only the `.patch` (the exact `git diff` of the live host) is checked in. The
original sketch also had a `tests/test_speak_stream_stall.py`; it is not part of
the live edit and is not shipped.

### Configuration

`tts.openai.stream_timeout_seconds` in the Hermes profile config
(`~/.hermes/profiles/<profile>/config.yaml`, section `tts.openai`):

- default `15.0` when unset (it is unset in the `amanda` profile on 2026-10-06);
- accepted range `0 < value <= 120`;
- anything else (non-number, zero, negative, over 120, a boolean) logs a
  warning and falls back to `15.0`.

### Risk

The 15 s bound covers connect plus **each read**, so it includes the
time-to-first-audio gap, not just gaps between chunks. A sentence that the TTS
server legitimately takes longer than 15 s to start (cold model, GPU busy with
another request, very long first sentence) is cut: that sentence's audio ends
the stream early and one `speak-stream synthesis failed: ReadTimeout` warning
is logged. If slow sentences are being cut, raise
`tts.openai.stream_timeout_seconds` (max 120) rather than removing the patch;
Home gives up on the audio socket at 30 s, so values above ~25 s stop helping
Home.

## Files

| File | Purpose |
| --- | --- |
| `standard-speak-stream-timeout.patch` | Exact `git diff` of the two live files against HEAD `f97608f178d1` (`git apply` format). |
| `apply.sh` | Idempotent, safe apply. Never restarts. |
| `verify.sh` | Read-only post-update check. |
| `rollback.sh` | Reverse-applies the patch. Never restarts. |

SHA-256, recorded 2026-10-06:

```text
8567c67b9e58099aafca4c55056e35abeaff2f00e898fe4ced5edba26c9d950f  standard-speak-stream-timeout.patch
# media-server, original (HEAD f97608f178d1) -> patched (live)
099a7b03d4ddbb14e5b01464ab4b8546171d2dd04e081c127dc7d58dd01f4e96 -> 32ec6f4020c9bb3c4a53e658d7ab9148db46dfb44dbcd21feb7a7e1493d36024  hermes_cli/web_routers/audio.py
81062ebd58091aecebd922cb33a05bf060810a4e23c712ae367b1761ec614d5d -> eca3a1dbe370d64899d6afdf28b5828bdfe215f39c55712d443dc2172b4a66c8  tools/tts_streaming.py
```

## Install on the media server

Copy this directory to the Hermes service user's account (it is not run from
the repository checkout):

```sh
scp -r deploy/ops/standard-speak-stream-timeout media-server:hermes-ops/
ssh media-server 'sh ~/hermes-ops/standard-speak-stream-timeout/verify.sh'
```

The 2026-10-06 edit is already live; `apply.sh` reports `already applied`.

## Apply

```sh
sh ~/hermes-ops/standard-speak-stream-timeout/apply.sh           # apply
sh ~/hermes-ops/standard-speak-stream-timeout/apply.sh --check   # report only
```

`apply.sh` runs from the checkout root (`HERMES_AGENT_DIR`, default
`~/.hermes/hermes-agent`) and:

1. `git apply --check --reverse` succeeds -> prints `already applied` and exits 0
   without touching anything;
2. `git apply --check` fails -> prints `CONFLICT`, exits 2, changes nothing;
3. otherwise backs up both files to
   `~/hermes-backups/standard-speak-timeout-<timestamp>/` (with
   `original.sha256`), applies, and prints the restart command.

It does not commit, stash, or restart anything.

## After `hermes update`

`hermes update` stashes local changes (`git stash push --include-untracked`,
subject `hermes-update-autostash-<UTC stamp>`), fast-forwards, then
`git stash apply`-es them. Outcomes:

- **Clean restore** - the patch is back, but the running pilot still holds the
  old code until it is restarted.
- **Conflict** (upstream changed the same lines) - the updater resets the tree
  hard and **leaves the patch in the stash**; the files are then unpatched. It
  also stops re-applying with `--keep-stash`, which is what the desktop updater
  uses.

So after every update run:

```sh
sh ~/hermes-ops/standard-speak-stream-timeout/verify.sh
```

It greps the loaded files for `stream_timeout_seconds`, `max_retries=0` and the
type-only warning, checks the checked-in patch is fully present, lists
`hermes-update-autostash-*` entries and unmerged paths, and compares the pilot's
start time with the files' mtimes. Exit `0` = patched and running it, `1` = the
patch is missing/partial/conflicted (it prints the exact re-apply command),
`3` = patched on disk but the pilot predates the edit.

Re-apply when `verify.sh` reports it missing:

```sh
sh ~/hermes-ops/standard-speak-stream-timeout/apply.sh
# or by hand:
cd ~/.hermes/hermes-agent && git apply ~/hermes-ops/standard-speak-stream-timeout/standard-speak-stream-timeout.patch
```

### Conflicts

`apply.sh` exits 2 and changes nothing. Check, in order:

1. Did upstream fix the stall itself (a timeout on `OpenAIStreamer`)? If so,
   drop this patch and delete the leftover autostash.
2. Otherwise re-derive the two small edits against the new code by hand: pass
   `timeout=` and `max_retries=0` to `OpenAI(...)`, and log only
   `type(exc).__name__` in the speak-stream warning. Then regenerate the
   `.patch` with `git diff -- hermes_cli/web_routers/audio.py tools/tts_streaming.py`
   and update the hashes above.
3. The stash that `hermes update` left (`git stash list`) still holds the
   original edit; inspect it with `git stash show -p stash@{N}` and drop it
   only after the new patch is in place.

## Restart

Applying changes only files on disk. The running Standard pilot loads the code
at start, so restart it - **only when no one is using speech** - with:

```sh
launchctl kickstart -k gui/$(id -u)/com.hermes.home-standard-pilot
```

This restarts the pilot on port 9120 only; the relay proxy
(`com.hermes.home-standard-pilot-proxy`) is untouched.

## Rollback

```sh
sh ~/hermes-ops/standard-speak-stream-timeout/rollback.sh   # reverse-apply
```

If the files have drifted so the reverse apply is refused, restore the
`apply.sh` backup directory instead (`cp -p` both files back), then restart the
pilot as above.

The original save from 2026-10-06 lives on the media server at
`~/hermes-backups/standard-speak-timeout-20261006/` with its own
`rollback.sh`, which restores the saved originals and restarts the pilot:

```sh
#!/bin/sh
# Rollback: restore originals and restart the Standard pilot (port 9120) only.
cp -p /Users/jensen/hermes-backups/standard-speak-timeout-20261006/hermes_cli/web_routers/audio.py ~/.hermes/hermes-agent/hermes_cli/web_routers/audio.py && cp -p /Users/jensen/hermes-backups/standard-speak-timeout-20261006/tools/tts_streaming.py ~/.hermes/hermes-agent/tools/tts_streaming.py && launchctl kickstart -k gui/$(id -u)/com.hermes.home-standard-pilot
```
