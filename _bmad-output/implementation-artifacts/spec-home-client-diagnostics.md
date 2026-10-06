---
id: HOME-NW-06-client-reports
parent: HOME-NW-06
status: review
product_epic: 6
---

# Automatic client connection reports

Explicitly requested by Amanda on 2026-09-28 for an opted-in personal Apple client, including durable reports across app launches. This is a new delivery slice of HOME-NW-06, not a reopening or expansion of its historical implementation evidence. It does not authorize content capture.

## Acceptance

- `POST /api/v1/client-diagnostics` authenticates the existing active Device credential and assigns device identity server-side. Administrator tokens do not substitute for Device credentials. No device read endpoint.
- Strict schema 1, 64 KiB request limit, at most 100 events, closed event/code vocabulary and bounded numeric metadata. Reject unknown fields and malformed values without recording their contents.
- Durable SQLite storage, report ID deduplication per authenticated device, one new report per device per 30 seconds, 100 retained reports per device and 1,000 total. Expire after seven days.
- Signed-in same-origin `/pair` viewer shows the latest 50 reports with device labels and recent content-safe Home events; no new publicly readable diagnostics route. Browser output uses textContent.
- Review is explicit refresh, with no effect on conversation transport or per-frame audio processing. Report ingestion does not recursively generate diagnostic events.
- Tests cover auth/revocation, device isolation, browser authorization/origin, content rejection, restart/deduplication, retention/rate/storage bounds and runtime ownership. Deployment and real-device acceptance remain distinct gates.

## Android platform (HOME-NW-06-android-platform amendment, 2026-10-06)

Requested by the Android client tickets ANDROID-DIAG-02 (schema 1 upload) and ANDROID-DIAG-03 (schema 2 correlation), which could not ship because Home accepted only `platform` `ios`/`macos`. This amendment changes the envelope vocabulary only; it adds no field, endpoint, event name, code, size, rate, retention or storage change, and it does not authorize content capture. Device credentials already allow endpoint type `android`.

### Vocabulary

- `platform` is exactly one of `ios`, `macos`, `android` (lowercase, no trimming; `Android`, ` android` and non-strings are rejected).
- `model` is validated against the vocabulary of the report's `platform`:
  - `ios`/`macos`: unchanged — `(iPhone|iPad|Mac)N,N`, `arm64`, `x86_64`, `unknown`. Android-style models are rejected on these platforms.
  - `android`: `[A-Za-z0-9](?:[A-Za-z0-9 _.+()-]{0,38}[A-Za-z0-9_.+()-])?` — 1–40 ASCII characters from letters, digits, space and `_ . + ( ) -`; first character alphanumeric, last character not a space. `unknown` matches and is the client fallback. Apple-only syntax such as `iPhone18,1` does not match.
- `os_version` keeps the existing numeric dotted regex; an Android release like `16` or `15.0` already matches. `app_version`, `build` (the numeric `versionCode`), schema 1/2 field sets, event names, `CODES`, 100-event/64 KiB bounds, rate and retention are unchanged.
- There is no manufacturer field and the exact key set is enforced, so a `manufacturer` key is rejected. Android sends `Build.MODEL` alone.

### Why a pattern instead of an enumeration

Apple identifiers are a small machine-generated grammar, so the Apple check was already a grammar. Android `Build.MODEL` is OEM-defined and open-ended (`Pixel 9 Pro XL`, `SM-S928B`, `moto g(60)`, `Redmi Note 11 Pro+`, `ASUS_I006D`, `sdk_gphone64_arm64`); an enumeration would reject real household phones and need a Home release per new device. The pattern keeps the closed-vocabulary property that matters for privacy: ASCII only, a restricted punctuation set (no `@ : , ; / ' " < > \`, control characters, tabs or newlines, so emails, URLs, sentences with punctuation and Unicode/bidi tricks fail), 40-character maximum, no normalization.

Residual risk, stated plainly: any short letters/digits/space string, including a 40-character phrase, fits the pattern. The pattern bounds shape and size; it cannot prove the string came from `Build.MODEL`. The Android client therefore must send `Build.MODEL` only (never `Settings.Global.DEVICE_NAME`, an account name or any user-editable text), which the ANDROID-DIAG-02 content-safety test is required to cover. Home also keeps the existing exact-key-set, event, code and size bounds, so this field is the only free-ish string in a report and is capped at 40 bytes. The `/pair` viewer renders it via `textContent`, so even a hostile value cannot inject markup.

### Wire notes for the Android client

- Optional schema-2 identifier fields (`connection_id`, `home_connection_id`, `request_id`, `correlation_id`) are validated as strings when present; omit the key rather than sending `null`. Close codes and origin fields may be `null` as before.
- Receipt remains `{"schema": 1, "report_id": …}` for schema 2.
- Deployment is a package-only Home upgrade (no schema/config migration, like `0effbf9`) and is a separate step requiring the owner's go-ahead.

### Acceptance

- Android schema 1 and schema 2 (with correlation) reports validate unchanged; mixed case/whitespace platform and model values, unknown platforms, oversize (41+) or out-of-charset models, and extra keys are rejected; Apple vocabularies are unchanged and cross-platform models are rejected.
- Android reports persist across restart, appear in the `/pair` review payload and device HTTP path, and share the existing rate, cap and expiry accounting; rejected content never reaches storage or review output.

