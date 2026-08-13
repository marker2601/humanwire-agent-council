# HumanWire — Caspian submission packet

## Title

HumanWire

## One-line summary

HumanWire uses one Caspian message boundary for email and Telegram to turn an authenticated manager mandate into the smallest truthful set of human engagements needed to reach a decision.

## The problem, inspiration, and why it matters

Most coordination tools broadcast the same request to everyone, then treat delivery or silence as progress. That creates unnecessary interviews, obscures who has authority, and makes it easy to confuse an uncorrelated reply with evidence or approval.

HumanWire is inspired by the more careful coordination a good chief of staff provides: ask only the person who can contribute the needed fact, decision, or availability, preserve the context of that request across channels, and surface what is still genuinely unresolved. The result matters because a coordination system must be truthful about what it knows. Delivered outreach is not acknowledgement; an asserted answer is not confirmed evidence; and a required approval `CHANGE` is a blocker, not permission to manufacture consensus.

## What we built

An authenticated manager sends `/mandate` from a registered email or Telegram route. HumanWire persists a destination-free preview. The manager may make a permitted `ENGAGE` override or use `GO` to release it; a configured preview deadline may also release the plan. The workflow then assigns the minimum necessary contract for each contribution:

- `INFORM`
- `ACKNOWLEDGE`
- `QUICK_RESPONSE`
- `STRUCTURED_INTERVIEW`
- `REVIEW_APPROVAL`
- `AVAILABILITY`

Only quick and structured contributions create interview sessions. Quick and structured answers begin as asserted evidence. They become answer-derived evidence only when the exact participant sends `CONFIRM <token>` through that session’s persisted current route and conversation. `REVIEW_APPROVAL` accepts only exact `APPROVE`, `REJECT`, or `CHANGE` commands from its assigned authority. A required `CHANGE` produces a truthful partial/blocking result; it does not start a proposal round or schedule a meeting.

For a real persisted blocking alignment issue, HumanWire permits at most two proposal rounds. If the second round remains unresolved, the workflow moves to scheduling. It creates a meeting package and read-only downloadable ICS only from a current verified overlap among the exact required attendees; it never writes to a calendar.

## Verified local features

- One normalized Caspian `on_message` handler accepts both email and Telegram shapes while one persisted workflow coordinates them.
- Exact authenticated manager, participant, route, conversation, token, assignment, and mandate correlation fail closed on stale, mismatched, unrelated, terminal, or replayed input.
- Preview/release, answer confirmation, decision, proposal, availability, synthesis, and meeting transitions use persisted replay and concurrency fences.
- A durable outbox, claim leases, callback fences, and recovery preserve the at-least-once delivery boundary through restart; exact inbound replays are inert.
- Decision Room, Reach, Data, JSON, CSV, and ICS rebuild redacted, read-only views from persisted truth.
- The reproducible offline proof runs with deterministic fake-Caspian transport and synthetic personas; it makes no network call and is explicitly not live-provider proof.

## Why Caspian

Caspian is the single cross-channel boundary in HumanWire, not two duplicated bots. Email and Telegram enter exactly one normalized `on_message` handler. That handler and the persisted workflow retain the sender, route, conversation, token, mandate, assignment, and replay correlation needed to decide whether an inbound action belongs to the active state.

The handler replies to the current message when appropriate, starts new email conversations when appropriate, and continues Telegram only in an existing bot conversation. An email structured interview can continue over Telegram without losing its one persisted session, question index, route order, or conversation correlation.

Delivery is intentionally a durable, at-least-once boundary. Initial outreach is committed to a durable outbox with stable identity. Claims, leases, callback completion, and recovery are fenced by the persisted outbox, assignment, route position, attempt, and owner identities. A crash can recover expired work with the same stable message identity; exact inbound replays and duplicate callbacks are inert. Provider failure follows the saved response/failover ladder rather than being mistaken for a human response. Because an external provider can accept a send before its callback is lost, HumanWire does not claim exactly-once provider delivery or that a recipient can never see a duplicate.

## Architecture and safety boundary

HumanWire combines the Caspian gateway, deterministic workflow policy, persisted state, and read-only views:

```text
Registered manager (email or Telegram)
  → Caspian: one normalized on_message handler
  → HumanWire workflow: policy, exact correlation, state transitions
  → SQLite aggregates, events, durable outbox, and recovery fences
  → minimum-necessary engagements across email and Telegram
  → Decision Room, Reach, Data, redacted JSON/CSV, and local ICS
```

Featherless suggestions are constrained advisory JSON for planning, evidence extraction, alignment analysis, and proposal drafting. Schemas, the directory, engagement policy, state machines, and transaction fences retain authority. A model cannot authenticate a sender, select a transport destination, weaken required engagement, confirm evidence, approve a decision, or create a meeting. Deterministic fallbacks preserve this boundary when a model is unavailable or fails.

Public projections are rebuilt from persisted truth and exclude private evidence, raw change rationale, contact routes, provider bodies, message identifiers, credentials, and operational UUIDs. Decision Room, Reach, Data, JSON/CSV exports, and ICS are safe read-only views. JSON and CSV share the same redacted 16-field projection and exact filter contract.

## How Codex was used

Codex assisted the project’s implementation and testing workflow under the approved red-green-refactor process. HumanWire’s repository documentation, tests, and submission materials remain the source for the behavior described here. This packet makes no claim that Codex independently authored the project or that its use produced an unrecorded productivity or performance result.

## Setup and testing

Requirements include Python 3.12. For a local development environment, create a virtual environment, install the development extras, and copy `.env.example` to ignored `.env`; secrets and the private organization directory must never be committed.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m humanwire init-db
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe scripts\smoke_humanwire.py
.\.venv\Scripts\python.exe -m humanwire smoke
```

The offline smoke uses a temporary file-backed SQLite database, deterministic fake channel/model adapters, and the real gateway, workflow, repository, and web boundaries. It makes no network call and prints eleven safe `PASS` lines. The deterministic public replay is also synthetic and read-only.

Visible provenance for the public replay and offline simulation must remain:

```text
proof_class=synthetic_multi_persona
actor_type=simulated_persona
identity_source=synthetic_fixture
transport=fake_caspian
human_attested=false
live_provider_verified=false
```

The opt-in `humanwire smoke --live --confirm-live` command prints a manual checklist only. It does not connect to providers, transmit messages, call a model, or mutate a database. Controlled live-provider proof remains pending: it requires an operator-owned deployment and provider setup, consenting operator-owned test identities, and the documented three-flow checklist. Offline fake-Caspian proof is not live-provider or real-human proof.

## Screenshot shot list

Capture only the synthetic, read-only fixture after final QA; never capture routes, addresses, tokens, private answers, provider bodies, credentials, conversation/message IDs, or database content.

1. Desktop Decision Room: mandate state with safe destination-free preview/release story.
2. Desktop Reach: selected persisted event, flow strip, and synthetic provenance labels.
3. Desktop Data: filtered redacted JSON/CSV controls and safe event table.
4. Desktop meeting-ready state: verified-overlap outcome and ICS download.
5. Mobile (390 px) Decision Room: readable, unclipped synthetic/read-only labeling.
6. Mobile (390 px) Reach or Data: replay/export controls and safe content visible.

## 75–90 second video beats

1. **0–8s — Problem:** broadcasting makes silence look like progress; HumanWire asks for the minimum necessary contribution.
2. **8–18s — Preview:** authenticated manager mandate, destination-free preview, permitted override or `GO` release.
3. **18–31s — One handler/two channels:** email and Telegram enter one Caspian `on_message` handler; show an email structured interview continuing on Telegram.
4. **31–43s — Truthful evidence:** distinguish delivery, acknowledgement, asserted answer, and exact `CONFIRM <token>` evidence confirmation.
5. **43–55s — Decisions and bounded resolution:** explicit authority approval commands, a required `CHANGE` as a partial blocker, and the two-round cap.
6. **55–66s — Meeting proof:** exact attendee availability produces a meeting package and downloadable local ICS only after verified overlap.
7. **66–78s — Durable operations:** persisted outbox, callback/replay fencing, failure ladder, and restart recovery; provider delivery remains at least once.
8. **78–90s — Safe proof and limits:** Decision Room/Reach/Data exports, synthetic provenance labels, and explicit statement that live-provider proof is pending.

## Submission links and official-form items

- Public repository URL: **[entrant-provided after publication and signed-out verification]**
- Public demo URL: **[entrant-provided after signed-out synthetic/read-only verification]**
- Public video URL: **[entrant-provided after upload and signed-out playback verification]**
- Caspian registration evidence: **[entrant-provided privacy-safe confirmation]**
- Caspian eligibility, team, reuse/originality, deadline, judging, and required form/media evidence: **[official organizer evidence pending]**
- Final submission receipt, timestamp, and submitted links: **[entrant-provided after submission]**

No official event facts, eligibility, deadline, judging criterion, organizer approval, registration state, or endorsement is asserted here until supported by retained official evidence.

## Limitations and readiness

SQLite is HumanWire’s local transactional and offline-proof boundary; a production deployment needs a managed relational database with equivalent constraints, migrations, operations, backups, retention, and a separately verified cutover. Telegram outreach requires an existing bot conversation. Provider delivery is at least once. The public demo is deterministic, synthetic, read-only, and isolated; it does not prove a live Caspian/provider run or real-human testing. HumanWire creates a local read-only ICS artifact and does not write calendars. It claims neither Power BI certification, real-time analytics, production security certification, nor organizer endorsement.

The local submission packet is ready to be paired with the required safe screenshots, recorded video, signed-out links, official organizer evidence, and separately retained private live-provider proof. Until those external or private items exist, they remain explicitly pending rather than inferred from the synthetic replay or fake transport.
