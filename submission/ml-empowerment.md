# HumanWire — ML Empowerment submission

## Problem

AI coordination systems often optimize for more conversation while quietly assigning authority to model output. Organizations need the opposite: minimum interruption, explicit human authority, and evidence that remains truthful when a model is unavailable or wrong.

## Solution

HumanWire selects the minimum necessary engagement for every stakeholder in a manager mandate. It applies one of `INFORM`, `ACKNOWLEDGE`, `QUICK_RESPONSE`, `STRUCTURED_INTERVIEW`, `REVIEW_APPROVAL`, or `AVAILABILITY`. HumanWire does not interview everyone, and a model suggestion cannot downgrade a contribution required by policy.

## Live flow

The manager receives a safe plan preview before outreach. Inform recipients get context only; acknowledgement recipients authenticate receipt; quick respondents answer one question; only selected contributors enter a structured interview; the authority replies with an explicit decision; and availability is requested only if a verified conflict survives two proposal rounds.

Quick and structured responses are initially asserted. `CONFIRM <token>` from the exact session route and conversation promotes only evidence tied to persisted answer events. A required `CHANGE` remains a separate partial blocker and never becomes synthetic alignment or a forced meeting.

## Why Featherless

Featherless supplies constrained OpenAI-compatible JSON for four advisory jobs:

- proposing a bounded stakeholder and engagement plan;
- extracting structured evidence from a response;
- suggesting alignment issues over an allowlisted public projection;
- drafting a bounded proposal from verified issues.

Every response is schema-validated and policy-resolved locally. Directory membership, direction, authority, response requirement, state transition, contribution readiness, meeting proof, and transport destination remain deterministic. Safe rules provide a usable fallback when Featherless is absent, times out, returns malformed JSON, or suggests an unsafe roster.

## Technology

- Featherless JSON completions behind a narrow typed client
- Pydantic strict schemas and bounded model input/output
- deterministic planning, evidence, alignment, and proposal fallbacks
- SQLAlchemy transaction fences and append-only provenance
- Caspian email/Telegram delivery through one handler
- read-only FastAPI views and canonical redacted analytics export

## Responsible AI boundary

Untrusted message text is delimited, direct contact values and credentials are removed, and only allowlisted public fields reach model analysis. The model cannot authenticate identity, record a confirmation, accept an approval, mutate the calendar, or determine a transport route. Missing evidence, silence, failure, rejection, and raw `CHANGE` never become agreement.

## Setup and demo

The public deployment target is [secondsignal.vercel.app](https://secondsignal.vercel.app). The deterministic fixture never invokes Featherless. The offline smoke uses deterministic fakes around the real workflow; live Featherless/provider verification remains an explicit operator gate described in the README.

## Limitations

Model suggestions are not autonomous authority and the deterministic fallback is intentionally conservative. SQLite is a local demonstration boundary. Public analytics are snapshots rather than realtime feeds. The ICS download does not create or update an external calendar. HumanWire claims no organizer endorsement or production security/Power BI certification.

## Proof checklist

- [x] Invalid or failed model output falls back without weakening policy.
- [x] Model input excludes destinations, credentials, and private evidence.
- [x] Model output cannot alter deterministic authority or persistence rules.
- [x] Confirmed answer provenance is required before required synthesis.
- [x] Required `CHANGE` remains partial and separate from meeting negotiation.
- [x] Offline smoke proves the complete adaptive product without network access.
- [ ] Record the controlled live Featherless/provider proof without private content.
