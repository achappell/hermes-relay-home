"""The self-contained HTML for the Home pairing page.

No external scripts, fonts, or images: the page is served from a tailnet-only
Home and must work without internet access. Every server value is inserted
with ``textContent``; only the server-generated QR SVG uses markup.
"""

from __future__ import annotations

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes Home Pairing</title>
<style nonce="__NONCE__">
:root {
  --bg: #f6f5f2; --panel: #ffffff; --text: #1c1b1a; --muted: #5f5b55;
  --line: #dedad3; --accent: #3a5bd9; --accent-text: #ffffff;
  --ok: #1f7a4d; --warn: #9a5b00; --danger: #b3261e;
  color-scheme: light dark;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161514; --panel: #201f1d; --text: #ecebe8; --muted: #a8a39b;
    --line: #34322e; --accent: #8ea4ff; --accent-text: #111111;
    --ok: #6fd3a0; --warn: #f0b35a; --danger: #ff8a80;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
h2 { font-size: 1.1rem; margin: 0 0 12px; }
p.lede { color: var(--muted); margin: 0 0 24px; }
section {
  background: var(--panel); border: 1px solid var(--line);
  border-radius: 12px; padding: 20px; margin-bottom: 16px;
}
button {
  font: inherit; border-radius: 8px; padding: 8px 14px; cursor: pointer;
  border: 1px solid var(--line); background: var(--panel); color: var(--text);
}
button.copied { border-color: var(--ok); color: var(--ok); }
button.primary { background: var(--accent); color: var(--accent-text); border-color: var(--accent); }
button.danger { color: var(--danger); }
button:disabled { opacity: .5; cursor: default; }
input[type=password] {
  font: inherit; width: 100%; padding: 8px 10px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--bg); color: var(--text);
}
label.inline { display: inline-flex; gap: 6px; align-items: center; margin: 4px 16px 4px 0; }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.muted { color: var(--muted); }
.code { font: 600 1.6rem/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .08em; }
.confirm { font: 600 1.2rem/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .1em; }
.offer { display: grid; grid-template-columns: minmax(0, 200px) minmax(0, 1fr); gap: 20px; align-items: center; }
.qr { width: min(200px, 100%); justify-self: center; }
.qr svg { display: block; width: 100%; height: auto; aspect-ratio: 1; background: #fff; border-radius: 8px; }
.link {
  display: block; width: 100%; min-height: 4.2em; margin: 0; padding: 0;
  border: 0; resize: none; background: transparent; color: var(--muted);
  font: .85rem/1.4 ui-monospace, Menlo, monospace;
  word-break: break-all; user-select: all;
}
.item { border-top: 1px solid var(--line); padding: 14px 0; }
.item:first-of-type { border-top: 0; padding-top: 0; }
.badge { display: inline-block; font-size: .8rem; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--line); margin-right: 6px; }
.badge.ok { color: var(--ok); } .badge.warn { color: var(--warn); } .badge.danger { color: var(--danger); }
.error { color: var(--danger); min-height: 1.5em; }
[hidden] { display: none !important; }
.spread { justify-content: space-between; }
.gap-top { margin-top: 16px; } .gap-small { margin-top: 8px; } .gap-medium { margin-top: 12px; }
fieldset.profiles { border: 0; padding: 8px 0; margin: 0; }
.dim { opacity: .4; }
.copy-status { min-height: 1.5em; margin: 4px 0 0; }
@media (max-width: 560px) {
  .offer { grid-template-columns: 1fr; }
  .qr svg { margin: 0 auto; }
  .link { font-size: 1rem; min-height: 5.6em; }
}
</style>
</head>
<body>
<main>
  <h1>Hermes Home pairing</h1>
  <p class="lede">Pair a TUI, iPhone, Mac, or Android client with this Home. It stays paired and renews itself.</p>

  <section id="signin">
    <h2>Sign in</h2>
    <form id="signin-form">
      <label for="token">Home admin token</label>
      <input id="token" type="password" autocomplete="current-password" required>
      <p class="error" id="signin-error" role="alert"></p>
      <button class="primary" type="submit">Sign in</button>
    </form>
  </section>

  <div id="app" hidden>
    <section>
      <div class="row spread">
        <h2>Pair a device</h2>
        <button id="signout" type="button">Sign out</button>
      </div>
      <p class="muted">On the device, run <code>hermes-relay pair</code> with the link, or type the code. Codes expire after 5 minutes.</p>
      <button class="primary" id="new-offer" type="button">Create pairing code</button>
      <div id="offer" hidden>
        <div class="offer gap-top">
          <div class="qr" id="qr" aria-label="Pairing QR code" role="img"></div>
          <div>
            <div class="muted">Code</div>
            <div class="code" id="code"></div>
            <div class="muted gap-small">Home</div>
            <div id="home"></div>
            <div class="muted gap-small">Link</div>
            <textarea class="link" id="link" aria-label="Pairing link" rows="3" readonly></textarea>
            <div class="row gap-medium">
              <button id="copy-link" type="button">Copy link</button>
              <span class="muted" id="countdown" aria-live="polite"></span>
            </div>
            <p class="muted copy-status" id="copy-status" role="status"></p>
          </div>
        </div>
      </div>
    </section>

    <section aria-live="polite">
      <h2>Waiting for approval</h2>
      <p class="muted" id="no-requests">No devices are waiting.</p>
      <div id="requests"></div>
    </section>

    <section>
      <h2>Paired devices</h2>
      <p class="muted" id="no-devices">No devices are paired yet.</p>
      <div id="devices"></div>
    </section>
    <section>
      <h2>Connection reports</h2>
      <p class="muted">Reports from devices with automatic diagnostics enabled. No conversations or audio. Kept for seven days.</p>
      <button id="load-reports" type="button">Refresh reports</button>
      <div id="client-reports" aria-live="polite"></div>
      <details><summary>Recent Home events</summary><div id="home-events"></div></details>
    </section>
    <p class="error" id="app-error" role="alert"></p>
  </div>
</main>
<script nonce="__NONCE__">
(() => {
  const $ = (id) => document.getElementById(id);
  let state = null, offerExpires = 0, poll = null, clockSkew = 0;
  let copyFeedbackTimer = null, reportReviewGeneration = 0;
  // Profile choices survive the 2-second refresh, keyed by request.
  const choicesByRequest = new Map();

  async function api(path, body) {
    const options = body === undefined
      ? { method: "GET", credentials: "same-origin" }
      : { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch (error) { data = {}; }
    if (!response.ok) {
      const code = (data.error && data.error.code) || String(response.status);
      const failure = new Error(code); failure.status = response.status; throw failure;
    }
    return data;
  }

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function when(seconds) {
    return new Date(seconds * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }

  function copyWithFallback(text) {
    if (typeof document.execCommand !== "function") return false;
    const active = document.activeElement;
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.setAttribute("aria-hidden", "true");
    field.style.position = "fixed";
    field.style.insetInlineStart = "-9999px";
    document.body.append(field);
    field.focus();
    field.select();
    field.setSelectionRange(0, field.value.length);
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (error) { copied = false; }
    field.remove();
    if (active && typeof active.focus === "function") active.focus();
    return copied;
  }

  function resetCopyFeedback() {
    clearTimeout(copyFeedbackTimer);
    copyFeedbackTimer = null;
    const button = $("copy-link");
    button.textContent = "Copy link";
    button.classList.remove("copied");
    $("copy-status").textContent = "";
  }

  function showCopySuccess() {
    const button = $("copy-link");
    button.textContent = "Copied";
    button.classList.add("copied");
    $("copy-status").textContent = "Pairing link copied.";
    copyFeedbackTimer = setTimeout(resetCopyFeedback, 3000);
  }

  async function copyPairingLink() {
    const link = $("link").value;
    const status = $("copy-status");
    resetCopyFeedback();
    try {
      if (!navigator.clipboard || !window.isSecureContext) {
        if (!copyWithFallback(link)) throw new Error("clipboard_unavailable");
      } else {
        await navigator.clipboard.writeText(link);
      }
      showCopySuccess();
    } catch (error) {
      if (copyWithFallback(link)) {
        showCopySuccess();
      } else {
        status.textContent = "Copy failed. Select the link above and copy it manually.";
      }
    }
  }

  function showApp(signedIn) {
    $("signin").hidden = signedIn;
    $("app").hidden = !signedIn;
    if (signedIn && !poll) { refresh(); poll = setInterval(refresh, 2000); }
    if (!signedIn && poll) { clearInterval(poll); poll = null; }
    if (!signedIn) { reportReviewGeneration += 1; $("client-reports").replaceChildren(); $("home-events").replaceChildren(); }
  }

  function fail(error) {
    if (error.status === 401) { showApp(false); return; }
    $("app-error").textContent = "Something went wrong: " + error.message;
  }

  async function refresh() {
    try {
      state = await api("/pair/api/state");
      clockSkew = state.now - Date.now() / 1000;
      $("app-error").textContent = "";
      render();
    } catch (error) { fail(error); }
  }

  function render() {
    const requests = $("requests"); requests.replaceChildren();
    $("no-requests").hidden = state.requests.length > 0;
    const waiting = new Set(state.requests.map((request) => request.request_id));
    for (const id of [...choicesByRequest.keys()]) if (!waiting.has(id)) choicesByRequest.delete(id);
    for (const request of state.requests) {
      if (!choicesByRequest.has(request.request_id)) {
        choicesByRequest.set(request.request_id, new Set(request.preselected_profiles || []));
      }
      const chosen = choicesByRequest.get(request.request_id);
      const item = el("div", undefined, "item");
      item.append(el("div", request.label + " (" + request.type + ")"));
      const asks = el("div", undefined, "muted");
      asks.append("Asks for: " + (request.requested_capabilities.join(", ") || "nothing"));
      if (request.requested_rooms.length) asks.append("; rooms: " + request.requested_rooms.join(", "));
      item.append(asks);
      const confirm = el("div", undefined, "muted");
      confirm.append("Confirm the device shows ", el("span", request.confirmation_code, "confirm"));
      item.append(confirm);
      const actions = el("div", undefined, "row");
      if (request.personal_client) {
        const choices = el("fieldset", undefined, "profiles");
        choices.append(el("legend", "Profiles this device may use", "muted"));
        for (const profile of state.profiles.filter((p) => p.available)) {
          const label = el("label", undefined, "inline");
          const box = document.createElement("input");
          box.type = "checkbox"; box.value = profile.id; box.checked = chosen.has(profile.id);
          box.addEventListener("change", () => { if (box.checked) chosen.add(profile.id); else chosen.delete(profile.id); });
          label.append(box, profile.name + (profile.shared ? " (shared)" : ""));
          choices.append(label);
        }
        item.append(choices);
        const approve = el("button", "Approve", "primary"); approve.type = "button";
        approve.addEventListener("click", async () => {
          const profiles = [...chosen];
          if (!profiles.length) { $("app-error").textContent = "Choose at least one Profile."; return; }
          approve.disabled = true;
          try { await api("/pair/api/requests/" + encodeURIComponent(request.request_id) + "/approve", { profiles }); refresh(); }
          catch (error) { approve.disabled = false; fail(error); }
        });
        actions.append(approve);
      } else {
        item.append(el("p", "Room devices are approved through the Home admin API.", "muted"));
      }
      const reject = el("button", "Reject", "danger"); reject.type = "button";
      reject.addEventListener("click", async () => {
        try { await api("/pair/api/requests/" + encodeURIComponent(request.request_id) + "/reject", {}); refresh(); }
        catch (error) { fail(error); }
      });
      actions.append(reject);
      item.append(actions);
      requests.append(item);
    }

    const devices = $("devices"); devices.replaceChildren();
    $("no-devices").hidden = state.devices.length > 0;
    for (const device of state.devices) {
      const item = el("div", undefined, "item");
      const title = el("div");
      title.append(el("strong", device.label), " ", el("span", device.type, "muted"));
      item.append(title);
      const status = el("div", undefined, "muted");
      const statusClass = device.status === "active" ? "ok" : "danger";
      if (device.status === "expired") {
        status.append(el("span", "expired", "badge danger"), "Expired " + when(device.expires_at) + ". Pair it again to reconnect.");
      } else {
        status.append(el("span", device.status, "badge " + statusClass), "Last renewed " + when(device.issued_at) + "; renews by itself before " + when(device.expires_at));
      }
      item.append(status);
      for (const grant of device.grants) {
        const line = el("div", undefined, "row");
        const grantClass = grant.status === "active" ? "ok" : "warn";
        const label = grant.status === "pending_owner" ? "waiting for owner" : grant.status;
        line.append(el("span", grant.profile), el("span", label, "badge " + grantClass));
        if (grant.bootstrap) line.append(el("span", "first device", "badge"));
        const revokeGrant = el("button", "Remove " + grant.profile, "danger"); revokeGrant.type = "button";
        revokeGrant.addEventListener("click", async () => {
          if (!window.confirm("Remove " + grant.profile + " from " + device.label + "?")) return;
          try { await api("/pair/api/grants/" + encodeURIComponent(grant.grant_id) + "/revoke", {}); refresh(); }
          catch (error) { fail(error); }
        });
        line.append(revokeGrant);
        item.append(line);
      }
      const revoke = el("button", "Unpair device", "danger"); revoke.type = "button";
      revoke.addEventListener("click", async () => {
        if (!window.confirm("Unpair " + device.label + "? It will need to pair again.")) return;
        try { await api("/pair/api/devices/" + encodeURIComponent(device.device_id) + "/revoke", {}); refresh(); }
        catch (error) { fail(error); }
      });
      const actions = el("div", undefined, "row gap-small");
      if (device.status === "expired") {
        const again = el("button", "Pair again", "primary"); again.type = "button";
        again.addEventListener("click", () => createOffer(device.grants.map((grant) => grant.profile_id)));
        actions.append(again);
      }
      actions.append(revoke); item.append(actions);
      devices.append(item);
    }
  }

  function tick() {
    if (!offerExpires) return;
    const left = Math.max(0, Math.round(offerExpires - (Date.now() / 1000 + clockSkew)));
    if (left === 0) { $("countdown").textContent = "Expired. Create a new code."; $("offer").classList.add("dim"); offerExpires = 0; return; }
    $("countdown").textContent = "Expires in " + Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0");
  }
  setInterval(tick, 1000);

  $("signin-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("signin-error").textContent = "";
    try { await api("/pair/api/session", { admin_token: $("token").value }); $("token").value = ""; showApp(true); }
    catch (error) { $("signin-error").textContent = error.status === 401 ? "That token was not accepted." : "Sign-in failed: " + error.message; }
  });
  $("signout").addEventListener("click", async () => { try { await api("/pair/api/logout", {}); } catch (error) {} showApp(false); });
  $("load-reports").addEventListener("click", async () => {
    const button = $("load-reports"); button.disabled = true;
    const generation = reportReviewGeneration;
    try {
      const data = await api("/pair/api/client-diagnostics", {});
      if (generation !== reportReviewGeneration) return;
      const reports = $("client-reports"); reports.replaceChildren();
      if (!data.reports.length) reports.append(el("p", "No connection reports yet.", "muted"));
      for (const item of data.reports) {
        const report = item.report;
        const device = (state?.devices || []).find((d) => d.device_id === item.device_id);
        const detail = el("details", undefined, "item");
        detail.append(el("summary", (device?.label || item.device_id) + " · " + new Date(report.created_at * 1000).toLocaleString()));
        detail.append(el("p", report.platform + " " + report.os_version + " · " + report.model + " · app " + report.app_version + " (" + report.build + ")", "muted"));
        for (const event of report.events) {
          detail.append(el("div", new Date(event.time * 1000).toLocaleString() + " · " + event.name + (event.phase ? " · " + event.phase : "") + (event.uncertain === true ? " · outcome uncertain" : "") + (event.code ? " · " + event.code : "") + (event.duration_ms !== undefined ? " · " + event.duration_ms + " ms" : "") + " · launch " + event.launch_id.slice(0, 8)));
        }
        reports.append(detail);
      }
      const home = $("home-events"); home.replaceChildren();
      if (!data.home_events.length) home.append(el("p", "No recent Home events.", "muted"));
      for (const event of data.home_events) {
        home.append(el("div", new Date(event.occurred_at * 1000).toLocaleString() + " · " + event.phase + " · " + event.outcome + (event.failure_code ? " · " + event.failure_code : "")));
      }
    } catch (error) { fail(error); }
    finally { button.disabled = false; }
  });
  $("copy-link").addEventListener("click", copyPairingLink);
  async function createOffer(profiles) {
    try {
      const offer = await api("/pair/api/offers", profiles && profiles.length ? { profiles } : {});
      $("qr").innerHTML = offer.qr_svg;
      $("code").textContent = offer.code;
      $("home").textContent = offer.home;
      $("link").value = offer.link;
      resetCopyFeedback();
      $("offer").hidden = false; $("offer").classList.remove("dim");
      offerExpires = offer.expires_at; tick();
      $("offer").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (error) {
      if (error.message === "https_required") {
        $("app-error").textContent = "Open this page through its https Home address; a pairing link must reach this Home from another device.";
      } else { fail(error); }
    }
  }
  $("new-offer").addEventListener("click", () => createOffer([]));

  api("/pair/api/state").then(() => showApp(true)).catch(() => showApp(false));
})();
</script>
</body>
</html>
"""


def render_pairing_page(nonce: str) -> str:
    """Return the page with this response's CSP nonce."""
    return _PAGE.replace("__NONCE__", nonce)
