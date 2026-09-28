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
