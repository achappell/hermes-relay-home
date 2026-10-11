# Typed-choice enablement: sequencing, production risk, and the B3 live proof

Companion to `spec-home-tc-b1-client-device-interactive-choice.md` and `spec-home-tc-b2-clarify-choice-projection.md`. Drafted 2026-10-11 against Home `1f70524`; TUI `origin/main` `638e1bc`. Planning only: nothing here is implemented, merged, deployed, or accepted. `HOME-TC-B1`, `HOME-TC-B2`, and "B3" are placeholder names; the owner assigns story IDs, issues, and tracker rows (none exist in `story-index.yaml` or `sprint-status.yaml`, and this note adds none).

Owner decisions applied (2026-10-11): D1 = P1 (project a single-select Standard clarify as a typed choice; `choose` only), D2 = A (per-client-device opt-in in Home configuration, default off), D3 = A (`choose`-only accepted), D4 = A (household browser only, harmless-only, approvals unchanged), D5 = A (browser appliance first, mechanism per Device ID).

## 1. Order and why

1. **B1 first.** B2 needs a grant that can be interactive without a Room. B1 is safe to merge dark: with B1 alone nothing new is advertised (the `prompt.choose` flags still come only from Standard's `gateway.ready`, which pinned Standard never sends), so it is provable by unit tests alone.
2. **B2 second.** It consumes B1's grant flag and is the only slice that creates a Standard-facing behavior. It is blocked until the Standard capture in B2 Open Question 1 exists (answer value; unexpired-clarify and reconnect behavior).
3. **B3 last.** A real Home with B1+B2, a controlled Standard fixture, the real browser appliance, then the owner on iPad Safari. B3 closes nothing by itself; it is the evidence readiness note Q2 requires before 2-WK-5 can close and 2-WK-6 can start.

Implementation, merge, live smoke, and iPad confirmation are recorded as four separate evidence items.

## 2. Production risk during the WK-HOME-01 bake (to 2026-10-12 17:08 CDT)

**What a merge does.** Merging B1/B2 to Home `main` changes no running service; the Windows Home deploys from a separate step (`deploy/windows`). Readiness Q11 allows merges "to `home_display/`, `home_display/web/`, or `puck_bridge/`" (TUI repo); its wording does not name this repository, so treating a Home-repo merge as bake-safe is an assumption (`[INFERENCE]`) the owner should confirm.

**What a deploy does.** `ConversationGrantStore.__init__` closes every active claim as `service_restart` (`production.py` L138), so a Home restart ends the production browser's open conversation. Q11 says nobody uses the production browser live, but a restart during the bake may warrant a fresh spot check (`[INFERENCE]`, as in the note's bake section). Recommendation: merge freely, deploy after the bake closes, owner's call.

**Why default-off keeps production safe.**
- With no `client_devices` entry, a client claim resolves `interactive_choice=False` exactly as today; `_capabilities` copies no choice flags; B2's projection gate requires the grant flag, so every clarify takes today's path. Existing clarify tests stay unmodified as the proof.
- The schema change is an optional key omitted from normalized output when empty, so current admin tooling's GET -> PUT round-trip and `tests/fixtures/configuration.json` are unchanged.
- The new grant lookup is isolated and fails closed to `False`, never to `None` (which would close every browser claim as `stale_conversation`).
- The credential-service addition is read-only; the claim store, `conversation_claims` schema, routes, and credential scope are untouched.
- The only action that changes production behavior is the owner's admin PUT adding a `client_devices` entry. It is a deliberate later step and one key to revert.

**Rollback hazard (owner decision needed before any opt-in).** A pre-B1 Home build rejects a stored snapshot containing `client_devices` (`validate_snapshot` -> `_require_keys` unknown field). `GET /api/v1/configuration`, device configuration, and every wake claim would then answer 503. Rules: (a) no opt-in until B3 passes; (b) before any rollback of the Home build, publish a configuration without `client_devices`; (c) a PUT carrying `client_devices` to a pre-B1 Home is rejected `400`, so a premature PUT cannot corrupt state.

**Opt-in procedure (later, owner-run; not performed by this work).** `GET /api/v1/configuration`; copy the snapshot; add `client_devices: [{"id": "<browser Device ID>", "capabilities": {"interactive_choice": true}}]`; `PUT` with `expected_revision` equal to the GET revision. `PUT` replaces the whole configuration, so a stripped copy would delete Rooms, Profiles, mappings, and Devices. The publish bumps the revision: existing browser claims stay open but stop being advertised, and the browser must refetch before its next claim (`409 stale_configuration` until then, as for any publish today). Pre-deploy check: confirm no `devices[].id` equals a client-claim Device ID; B1 makes client claims ignore `devices[]`.

## 3. B3 live proof (separate TUI-repo change after B1+B2 merge)

**What exists.** `hermes-relay-tui/scripts/smoke_home_browser.py` (337 lines at the local checkout). It starts a real Home in-process (`hermes_home.runtime.create_runtime`) from whatever Home checkout is installed in the TUI venv, with a controlled aiohttp Standard fixture, pairs the browser appliance through the real pairing client, launches the appliance, and drives `/state` snapshots. It touches only a temp directory and ephemeral ports. `--serve` keeps the UI up until Ctrl-C.

**What it lacks for typed choices** (read from the script; all are TUI-repo changes):
1. The Standard fixture handles only `commands.catalog`, `session.create`, `prompt.submit`, interrupts, and `prompt.respond`. It has no `clarify.request` emission and no `clarify.respond` handler. Add triggers by prompt text (for example `choose`, `replace`, `freetext`, `multiselect`, `expire`) that emit the batch shape from the B2 capture, and a `clarify.respond` handler that records its params and answers in the captured result shape, then streams a reply naming the received answer.
2. It publishes configuration once at revision 0 with `devices: []`, before the Device exists. The opt-in needs the Device ID, so add a second admin PUT (`expected_revision` 1) after pairing and **before** launching the appliance, adding the `client_devices` entry.
3. It binds the appliance to `127.0.0.1`. An iPad cannot reach that. B3 must add an explicit opt-in flag to bind the temporary UI to a LAN or tailnet address (Open Question B3-2).
4. Install the Home checkout under test in the same venv (the script's docstring says so) and print its path and `git rev-parse HEAD` in the ready line (it already prints `home_implementation`).

**Headless run (assertions, no iPad):** from the TUI checkout, with the B1+B2 Home installed in the venv and the web bundle built, `python scripts/smoke_home_browser.py`. Required assertions:
- With the opt-in: the snapshot advertises `prompt.choose` and not `prompt.explore`; the prompt carries a choice object with `operations: ["choose"]`; one `choose` yields exactly one fixture `clarify.respond` with the expected `answer` and `question_id`; a second `choose` is rejected with visible feedback and does not reach the fixture; the reply shows the answer.
- Stale and replaced: a second clarify replaces the first; the first object's action is rejected; the fixture sees zero extra `clarify.respond`.
- `explore` is never offered; a hand-sent `explore` is rejected and the fixture sees nothing.
- Free-text and multi-select clarify take the legacy path (TUI B2b/B2c behavior), with no `prompt.choose` on those prompts.
- Without the opt-in (run the script's first phase, or a flag): no `prompt.choose`; a single-select clarify takes the legacy path.
- Remove the opt-in by PUT while a choice is pending: the response is refused and the fixture sees zero `clarify.respond`.
- Drop the Standard socket with a choice pending: no replay (`automatic_replays: 0`), the next turn is fresh.
- `expire`: the fixture emits `clarify.expire`; the choice becomes unavailable, not actionable.

**Owner on iPad Safari (after the headless run passes, with `--serve` and the LAN/tailnet bind):**
1. Run the script with `--serve`; open the printed UI URL in iPad Safari. Confirm it shows connected with the temporary Profile.
2. Use the typed Send form to send `choose`. Expect a dialog with the question and the listed options, with `Choose` and **no** `Explore`.
3. Select an option and tap `Choose`. Expect one transient "Choose requested: <label>" line, disabled controls, then a reply naming the answer.
4. Tap `Choose` again, then reload the page. Expect no second answer and no resubmission.
5. Send `replace`; confirm the earlier option set cannot be answered once the new one appears.
6. Send `freetext`; confirm today's clarify behavior (not a typed choice).
7. Report what you saw. 44 px targets, focus, and VoiceOver belong to 2-WK-6, not B3.
The owner touches only the temporary page. No Pixel, hardware, production browser, or Tailscale configuration is involved; Guided Access is irrelevant.

## 4. Where the readiness note's claims did not match the code or repos

1. **Revision check (material).** The note's B1 row says a capability or revision change leaves the claim open but unadvertised "as for Room devices", and (1)(a) treats the missing `devices[]` entry as the whole gap. In code, `resolve` compares the global `snapshot["revision"]` with the claim's stored `configuration_revision`. For a client claim that value is the Device's projected revision from `CredentialService.client_configuration`, which runs ahead of the global revision permanently after a grant change (reproduced: 5 -> 6 -> 7 -> 8 against snapshots 5, 5, 6, 7). Reusing the check would leave the capability dead for a browser that has gained a grant. B1 compares against the projection (B1 Open Question 1).
2. **B2 file list is incomplete.** Beyond event validation and `_STRUCTURED_PROMPT_OPERATIONS`, B2 must also: rewrite before the pending/revision logic in `_next_event`; map `clarify.expire` (the expiry table is keyed to `clarify.request`, so it would be dropped for a projected prompt); validate the `clarify.respond` result with clarify-batch rules (`remaining` is rejected for a `prompt.request` event); and change `_capabilities`, which copies choice flags only from Standard's payload. `choice_authority.py` and `endpoint.py _choice_respond` need no change.
3. **"Fixtures captured from the pinned Standard."** The repo has no choice-bearing clarify capture; only a free-text one (`a460f17`). The Event Compatibility Matrix keeps raw frames in a scratch directory outside the repo. The answer value is unknown (B2 Open Question 1).
4. **TUI has moved.** TUI `origin/main` includes #243 (sequential multi-question single-select clarify), newer than the note's "optional B2c". The browser already answers single-select clarify through the legacy path, so P1 adds no user capability; it exists to exercise the typed-choice contract. The local TUI checkout is at `8821bfd`, behind `origin/main`.
5. **`smoke_home_browser.py`** exists in the TUI repo but does not emit clarify, handle `clarify.respond`, know the Device ID at publish time, or bind beyond loopback (section 3).
6. **Anchors.** Every Home line anchor in the note matched the code (`production.py` L586-618, `standard.py` L2005-2027, L2530-2540, L3425-3460, L3841, `endpoint.py` L1396, L1990-1995, `bridge-contract.md` L302-308, README "Client admission" L357).
7. **Q11 scope.** It names TUI paths only; Home-repo merge and deploy are not covered (section 2).

## 5. Open questions

- **B3-1. Who writes the B3 change and where is it recorded?** Provisional: a TUI-repo PR after B2 merges; evidence in a Home validation record per slice and in the TUI tracker for 2-WK-5.
- **B3-2. How does the iPad reach the temporary UI?** Options: (A, provisional) a new opt-in `--display-host` bind flag on a LAN or tailnet address, temporary only; (B) the owner uses an existing tailnet serve. B touches the owner's Tailscale configuration, which the script currently promises not to do. Provisional: A.
- **B3-3. Deploy timing against the bake.** Provisional: merge B1/B2 whenever reviewed; deploy Home after 2026-10-12 17:08 CDT; opt-in PUT only after B3 passes.
- **B3-4. Story IDs, issues, tracker rows.** Owner assigns; none created here.
- **B3-5. Upstream ask (P2).** The note recommended pairing P1 with a separate upstream request for native typed choices. D1 chose P1; whether to file the upstream ask is unrecorded. Provisional: file it separately; B2's "native advertisement disables projection" rule makes a later re-pin a clean switch.
- **B3-6. FR-45 / Story 2.6 wording (D3).** Amending to "Explore is advertised only when Standard supports it" is a vault/TUI change, not this repo. Provisional: owner or TUI session does it with the B3 result.
