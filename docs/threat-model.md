# HumanWire threat model

HumanWire coordinates decisions across people and channels. Its main risks are false authority, cross-mandate confusion, replay, delivery ambiguity, and disclosure of private human input.

## Authority and model boundary

Planner, extractor, alignment, and drafting model output is untrusted advisory data. Strict schemas, the organization directory, engagement policy, state machines, and repository transaction fences decide what can persist. A model cannot authenticate a sender, approve a decision, change a mandate state, select a transport destination, or create a meeting.

The initiating manager must match the registered sender, route, conversation, connection, and original mandate origin for preview release and optional override. Stakeholder acknowledgements, answers, confirmations, decisions, proposal responses, and availability are correlated to the exact token, person, active registered route, conversation, assignment, and mandate. `CONFIRM <token>` additionally requires a completed quick or structured session's persisted current route and conversation; its atomic write promotes only asserted evidence whose source has an exact persisted answer event. Ambiguous, stale, terminal, mismatched, replayed, unrelated-evidence, or cross-aggregate identities fail closed.

## Replay, concurrency, and stale snapshots

Incoming identity includes provider connection, channel, and message. Unique idempotency keys make exact replays inert. Compare-and-save transactions fence concurrent override/release, answer, decision, synthesis, proposal, availability, cancellation, expiration, and meeting transitions. Synthesis rechecks the complete contribution snapshot before commit. Terminal mandates reject late input and cannot be resurrected.

Initial deliveries use a durable outbox. Stable attempt identity and exact persisted route position survive restart. A lease owner must renew before and during provider I/O, and only that owner can complete the callback. A callback crash leaves recoverable claimed work; a stale worker cannot complete a superseding claim. Provider delivery is still at least once, so recipients may see a duplicate if a provider accepted a send before the process lost its callback.

## Privacy and public output

Raw request bodies, contact addresses, route IDs, conversation IDs, provider responses, credentials, private evidence, and raw `CHANGE` rationale stay outside public projections, event metadata, delivery metadata, analytics, and logs. Private evidence may affect a safe blocker count without exposing its text. Log records contain only bounded token/event/channel/reason fields.

Decision Room, Reach, Data JSON/CSV, and ICS rebuild safe projections from exact persisted records. Known private values are denied again at rendering. CSV cells neutralize formula-leading characters and strip line breaks. Internal analytics APIs require a separate read token. Unknown resource routes return bodyless 404 responses. Projection or repository failure returns a generic safe boundary rather than partial private output.

## Decision and meeting truth

Preview prevents silent outreach; only an authorized manager or configured deadline can release it. `REVIEW_APPROVAL` accepts only exact `APPROVE`, `REJECT`, or `CHANGE` commands from the assigned authority. A required `CHANGE` is a truthful partial/blocking contribution. It never implies alignment and never automatically enters meeting negotiation.

Negotiation is reserved for a fully ready primary aggregate with a real persisted blocking alignment issue. Every required respondent must answer, and two rounds are the hard cap. Scheduling accepts availability only from the exact required attendee set. A meeting package requires a current verified overlap; stale, missing, malformed, or mismatched proof fails closed. ICS generation is local and read-only, and HumanWire does not mutate calendars.

## Operations and secrets

Provider, model, analytics, and database credentials are separate deployment secrets. They must not be copied into the directory, database fixtures, browser output, logs, documentation, or smoke artifacts. The default smoke is fully offline and ignores ambient credentials. `--live --confirm-live` prints a manual operator checklist only: it does not connect, transmit, call a model, create a mandate, mutate a database, or prove live channels. Actual live-channel verification belongs to a later controlled deployment gate.
