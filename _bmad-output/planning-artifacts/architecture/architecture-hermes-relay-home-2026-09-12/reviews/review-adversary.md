# Reviewer Gate — Adversarial Divergence Review

Date: 2026-09-13

## Verdict

PASS. Independent endpoint, observation, diagnostics, and artifact units do not
have a remaining high-severity ownership escape while obeying the spine.

## Hypothetical independent units

### Unit A — a paired voice endpoint

It presents an opaque device credential and Wake Mapping ID, claims in its Room,
waits for `ready`, then uses the ordinary Hermes session stream. AD-4, AD-7,
AD-8, AD-9, and AD-10 prevent it from choosing an arbitrary Profile, receiving
the Hermes token, promoting a loser, retargeting an active conversation, or
retaining access after revocation.

### Unit B — a Watch, health, or notification surface

It asks Home for a current display/session view or a device health result. AD-10
and AD-11 prevent it from turning Watch into remote control, receiving raw
audio, exposing credentials, claiming a cached green result is a live Session,
or making a private notification speak in a shared Room.

### Unit C — an automatic telemetry collector and incident reviewer

It receives events, metrics, or an explicitly selected incident bundle. AD-4
and AD-12 prevent automatic content export, missing correlation across the
endpoint/Home/Hermes path, retention rules being silently shared, or a collector
outage holding a live turn hostage.

### Unit D — a choice or artifact backend

It proposes a choice or file change and later applies it. AD-10 and AD-13 keep
the action tied to current Session/turn/object/freshness or artifact revision,
require explicit Apply, and keep Home from becoming the file owner.

## Findings

No critical or high findings. Exact wire schemas, notification delivery,
crypto/store choices, and backend-specific details remain correctly deferred to
their own specifications.
