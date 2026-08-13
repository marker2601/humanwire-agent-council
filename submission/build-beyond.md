# HumanWire — Build Beyond submission packet

## Title

HumanWire

## One-line summary

HumanWire turns one authenticated manager mandate into a persistent, minimum-necessary coordination lifecycle—from preview and cross-channel outreach to explicit human decisions, verified meeting readiness, and safe replayable exports.

## Inspiration, problem, and why it matters

HumanWire uses minimum necessary engagement: it does not interview everyone.

Complex decisions routinely break between the mandate and the meeting. A broadcast can look like consent; delivery or silence can be mistaken for progress; context is lost when an interview moves channels; and meetings get scheduled before the real blocker is known. The result is more interruption with less trustworthy coordination.

HumanWire is inspired by the deliberate work of a good chief of staff: give context to people who only need context, request a focused contribution only when it is necessary, retain an explicit authority boundary, and schedule only when asynchronous work reveals a verified unresolved conflict. This matters because an agentic product must preserve the difference between delivery, an asserted answer, confirmed evidence, a decision, and an unresolved blocker. A required `CHANGE` is therefore a truthful partial result—not agreement and not a meeting trigger.

## Persistent mandate-to-meeting lifecycle

An authenticated manager begins with a destination-free preview of a bounded engagement graph. The manager may make a permitted `ENGAGE` override or explicitly release it with `GO`; a configured preview deadline may also release the plan. HumanWire then selects the smallest of six contracts for each required contribution:

- `INFORM`
- `ACKNOWLEDGE`
- `QUICK_RESPONSE`
- `STRUCTURED_INTERVIEW`
- `REVIEW_APPROVAL`
- `AVAILABILITY`

HumanWire does not interview everyone: only quick and structured contracts create interviews. Email and Telegram enter one normalized Caspian `on_message` handler and continue through one persisted workflow, so a structured interview can continue from email to Telegram without losing its coordination boundary. Delivery is not a response, and provider delivery remains at least once rather than exactly once.

Quick and structured answers first become asserted, source-bound evidence. They become answer-derived evidence only when the exact assigned participant sends `CONFIRM <token>` on that session’s persisted current route and conversation. `REVIEW_APPROVAL` accepts only exact `APPROVE`, `REJECT`, or `CHANGE` from the assigned authority. A required `CHANGE` remains partial/blocking: it never implies alignment and never enters proposal negotiation or scheduling.

Once every required contribution is ready, deterministic policy evaluates alignment. A real persisted blocking alignment issue may receive at most two proposal rounds. If the second round remains unresolved, HumanWire moves to scheduling—never a third round. Only the exact required attendees may submit availability, and a meeting package plus downloadable local ICS is created only from their current verified overlap. HumanWire does not write to an external calendar.

## Product surfaces: persisted truth made inspectable

HumanWire’s Decision Room, Reach, and Data surfaces rebuild safe, read-only views from persisted truth. Reach is a GET-only replay of a frozen scenario: selecting an event explains its causal chain as:

```text
From [source]  ->  To [destination]  ->  Generated [safe data point]
```

The replay uses allowlisted labels for the mandate, plan, outreach, response, evidence, decision, proposal, scheduling, and outcome stages. It does not contact people, run a model, create a mandate, mutate persisted state, or create telemetry. Decision Room shows the coordination state and safe contribution progress; Data presents the canonical redacted outreach-event projection.

The inline JSON API, downloadable JSON attachment, and CSV download use the same filtered 16-field redacted projection as the Data table. JSON and CSV preserve the exact active filter query; the ICS is a local read-only artifact. Public projections exclude private evidence, raw `CHANGE` rationale, contact routes, provider bodies, message identifiers, credentials, operational UUIDs, and availability windows. In non-demo deployments, `/api/v1/*` is read-only and separately protected by a read token; the public fixture contains no API credential.

The deterministic public replay and the offline simulation visibly retain this provenance:

```text
proof_class=synthetic_multi_persona
actor_type=simulated_persona
identity_source=synthetic_fixture
transport=fake_caspian
human_attested=false
live_provider_verified=false
```

## Verified architecture and agent boundary

```text
Registered manager (email or Telegram)
  → Caspian: one normalized on_message handler
  → HumanWire: directory policy, explicit state transitions, exact correlation
  → Featherless: constrained advisory JSON with deterministic fallback
  → SQLite aggregates + append-only events + durable outbox and recovery fences
  → minimum-necessary engagements + read-only Decision Room, Reach, Data, JSON/CSV, ICS
```

Featherless suggestions are advisory only. Strict schemas, the trusted directory, engagement policy, state machines, and transaction fences decide what may persist. The model cannot authenticate a sender, choose a route or destination, weaken a required contribution, confirm evidence, approve a decision, mutate a calendar, or create a meeting.

Durable outbox identity, leases, and transaction fences support recovery across restart; exact inbound replay and duplicate callback handling are inert. The workflow’s persisted boundaries cover preview/release, answers, confirmations, decisions, proposals, availability, synthesis, and meeting preparation. These are implemented local behaviors with deterministic synthetic/offline proof, not a claim of production deployment or live provider transport.

## How Codex was used

Codex assisted HumanWire’s implementation and testing workflow under the documented red-green-refactor process. Repository documentation, tests, and reviewed implementation are the basis for the claims in this packet. This does not claim that Codex independently authored HumanWire or establish an unrecorded productivity or performance result.

## Setup and testing

HumanWire requires Python 3.12. Create an ignored local `.env` from `.env.example`; do not commit secrets or the private organization directory.

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

The implemented offline proof uses a temporary file-backed SQLite database, deterministic fake-Caspian and fake-model adapters, and the real gateway, workflow, repository, and web boundaries. It makes no network call and prints eleven safe `PASS` lines. Tests cover the six contracts, exact correlation and confirmation, explicit approval/partial `CHANGE`, bounded proposal/scheduling behavior, verified overlap and local ICS, replay/restart fencing, canonical JSON/CSV projections, and privacy-safe public output.

This is deterministic offline and synthetic proof. A controlled private live-provider recording remains pending until an operator-owned configuration, consenting test identities, and the documented three-flow checklist are completed without publishing private content.

## Screenshot shot list

Capture only the synthetic, read-only fixture after final QA. Never capture addresses, routes, tokens, private answers, provider bodies, conversation/message IDs, credentials, or database content.

1. Desktop Decision Room: destination-free preview/release and six-contract mix.
2. Desktop Reach: a selected persisted event with the From → To → Generated strip and synthetic provenance labels.
3. Desktop Data: a filtered redacted event table with JSON-attachment and CSV-download controls.
4. Desktop meeting-ready state: verified-overlap outcome and local ICS download.
5. Mobile (390 px) Decision Room: readable synthetic/read-only labeling and contract progress.
6. Mobile (390 px) Reach or Data: visible replay or export controls with safe content.

## 75–90 second video beats

1. **0–8s — Problem:** explain why broadcast, delivery, and silence are not a trustworthy coordination record.
2. **8–20s — One persistent mandate:** show preview/release and the six minimum-necessary contracts.
3. **20–32s — Cross-channel continuity:** show one normalized handler and a structured interview continuing from email to Telegram.
4. **32–44s — Human proof:** distinguish asserted input from exact `CONFIRM <token>` on the persisted route and conversation.
5. **44–55s — Truthful decisions:** show exact approval commands and a required `CHANGE` staying partial, outside the meeting path.
6. **55–66s — Bounded resolution:** show the two-round cap, then exact attendee availability and verified meeting overlap.
7. **66–78s — Inspectable product:** show Decision Room, From → To → Generated replay, redacted Data, JSON/CSV, and local ICS.
8. **78–90s — Proof boundary:** show synthetic provenance labels and state that fake-Caspian/offline proof is implemented and tested while private live-provider proof is pending.

## Submission links and official-form items

- Public repository URL: **[entrant-provided after publication and signed-out verification]**
- Public demo URL: **[entrant-provided after signed-out synthetic/read-only verification; current continuity target: https://secondsignal.vercel.app]**
- Public video URL: **[entrant-provided after upload and signed-out playback verification]**
- Build Beyond registration evidence: **[entrant-provided privacy-safe confirmation]**
- Build Beyond eligibility, team, reuse/originality, deadline, judging, and required form/media evidence: **[official organizer evidence pending]**
- Final submission receipt, timestamp, and submitted links: **[entrant-provided after submission]**

Organizer eligibility is external pending. This packet does not assert official event facts, organizer approval or endorsement, registration state, deadline, judging criteria, or reuse terms until the entrant retains the event-specific official evidence.

## Limitations and readiness

SQLite is HumanWire’s local transactional and offline-proof boundary; production requires a managed relational database with equivalent constraints, migrations, operations, backups, retention, and separately verified cutover. Provider delivery is at least once. Telegram outreach requires an existing bot conversation. HumanWire does not claim production-security certification, Power BI certification, realtime analytics, organizer endorsement, a hosted production database, or an external calendar write.

The public demo is deterministic, synthetic, GET-only, read-only, and isolated from ambient configuration. Fake-Caspian and fake-model proof are not live-provider, live-model, or real-human proof. This local packet is ready to pair with safe screenshots, a 75–90 second recording, signed-out repository/demo/video links, Build Beyond organizer evidence, and privately retained live-provider proof when those items exist.
