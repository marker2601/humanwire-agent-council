# HumanWire — Caspian submission

## Problem

Decision coordination is usually treated as broadcasting: send the same request to everyone and chase silence manually. That wastes attention, loses channel context, and makes delivery look like agreement.

## Solution

HumanWire is an AI chief of staff that selects the minimum necessary engagement for each person touched by a mandate. It can inform, request acknowledgement, collect a quick response, conduct a structured interview, request an explicit approval review, or collect availability. HumanWire does not interview everyone.

## Live flow

An authenticated manager sends one `/mandate` through email or Telegram. HumanWire returns a destination-free preview, accepts a constrained optional override, and releases the plan once. One Caspian handler then routes `INFORM`, `ACKNOWLEDGE`, `QUICK_RESPONSE`, `STRUCTURED_INTERVIEW`, `REVIEW_APPROVAL`, and `AVAILABILITY` contracts.

The proof flow includes delivery-only context, authenticated acknowledgement, one-question input, a structured interview begun by email and continued in the same persisted session over Telegram, `CONFIRM <token>` for answer-derived evidence, and an explicit authority decision. Provider failure advances the saved response ladder; silence never becomes agreement.

## Why Caspian

Caspian is the channel boundary, not a pair of duplicated bots. HumanWire connects email and Telegram, registers exactly one `on_message` handler, normalizes inbound messages once, and dispatches through reply, initiate-email, or existing-conversation delivery according to the stored route. Assignment identity, channel position, attempt, callback, outbox lease, and restart recovery remain in one workflow.

Automated tests and the offline product proof exercise the real gateway against a deterministic fake Caspian client. Actual provider transmission remains a separate operator-controlled live gate; this document does not represent the fake transport as a live provider run.

## Technology

- Python 3.12 and `caspian-sdk==0.6.1`
- one Caspian email/Telegram handler and a durable at-least-once outbox
- Pydantic validation, SQLAlchemy transactions, and SQLite persistence
- FastAPI/Jinja read-only product views
- Featherless JSON suggestions with deterministic fallback

## Responsible AI boundary

Models suggest plans and summaries; they cannot authenticate a sender, select a destination, weaken required engagement, confirm evidence, approve a decision, or create a meeting. Exact sender, route, conversation, token, assignment, and mandate correlation controls every human action. Private answers and raw change rationale stay out of public views, exports, events, and logs.

## Setup and demo

The public deployment target is [secondsignal.vercel.app](https://secondsignal.vercel.app). It is a deterministic read-only fixture and contains no provider credentials or real messages. Local setup, the eleven-line offline smoke proof, and the no-side-effect live checklist are documented in the repository README.

## Limitations

Provider delivery is at least once, Telegram requires an existing bot conversation, and SQLite is the local proof boundary. The public fixture is not evidence of a live channel run. HumanWire produces a local ICS artifact but does not write to a calendar. It claims no organizer endorsement, production security certification, Power BI certification, or realtime analytics.

## Proof checklist

- [x] One handler accepts both email and Telegram messages.
- [x] The real workflow persists channel continuation and callback truth.
- [x] INFORM and ACKNOWLEDGE do not create interview evidence.
- [x] Quick and structured answers require authenticated confirmation.
- [x] Replay, restart, delivery failure, and concurrent synthesis fail closed.
- [x] Offline smoke makes no network call.
- [ ] Complete the controlled live-provider run and retain only privacy-safe evidence.
