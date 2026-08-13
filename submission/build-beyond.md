# HumanWire — Build Beyond submission

## Problem

Complex decisions fail between the mandate and the meeting: stakeholders receive the wrong ask, delivery state is mistaken for consent, context is lost across channels, and meetings are scheduled before the actual blocker is known.

## Solution

HumanWire is a persistent adaptive coordination agent. It selects the minimum necessary engagement, does not interview everyone, and assigns `INFORM`, `ACKNOWLEDGE`, `QUICK_RESPONSE`, `STRUCTURED_INTERVIEW`, `REVIEW_APPROVAL`, or `AVAILABILITY` according to the contribution each person actually owes.

## Live flow

One authenticated mandate becomes a previewed engagement graph. HumanWire releases outreach exactly once, maintains independent response ladders, continues an email interview over Telegram without losing session correlation, records answer-derived evidence only after `CONFIRM <token>`, and waits for every required contribution.

If verified public evidence is aligned, it produces an alignment brief. If a real conflict remains, it runs at most two proposal rounds. Only unresolved proposal conflict reaches scheduling; exact required availability is intersected locally before a meeting package and ICS download are created. A separate required approval `CHANGE` stays partial and never enters that meeting story.

## Technology

- Caspian email and Telegram through one normalized handler
- Featherless planning, evidence extraction, alignment suggestions, and proposal drafting
- Pydantic domain models and explicit state machines
- SQLAlchemy/SQLite transactions, compare-and-save fences, append-only events, and durable outbox recovery
- FastAPI/Jinja Decision Room, Propagation Lanes, Data, JSON, CSV, and verified ICS
- deterministic file-backed offline proof across restart and replay

## Responsible AI boundary

Human authority is explicit and source-bound. The agent never infers acknowledgement, approval, confirmation, availability, or agreement from delivery or silence. Raw private evidence, raw change text, routes, provider bodies, credentials, and operational identifiers do not enter public projections. Concurrent and replayed work is fenced against stale state.

## Setup and demo

The public deployment target is [secondsignal.vercel.app](https://secondsignal.vercel.app). It serves a deterministic read-only story with active, aligned, and meeting-ready mandates. The repository README provides local installation, organization seed, listener, web, smoke, analytics, and live-checklist instructions.

## Limitations

SQLite and the local directory are demonstration boundaries, provider delivery is at least once, and the public fixture does not prove a live provider run. HumanWire exports `METHOD:PUBLISH` calendar data but performs no external calendar mutation. Analytics are redacted snapshots; there is no Power BI certification, organizer endorsement, production security certification, or realtime guarantee.

## Proof checklist

- [x] One mandate persists several engagement types and exact completion rules.
- [x] Preview/override/release and the delivery outbox survive restart.
- [x] Required evidence confirmation recovers after a post-commit process failure.
- [x] Two proposal rounds are the hard cap.
- [x] Meeting readiness requires current exact attendee overlap.
- [x] Decision Room, Reach, Data, JSON, CSV, and ICS share persisted truth.
- [x] Offline smoke scans public output, logs, events, and exports for private sentinels.
- [ ] Complete three controlled live-channel flows before representing provider proof as live.
