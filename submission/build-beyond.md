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

HumanWire’s public Decision Room, Reach, and Data surfaces render safe presentation views from one bounded stream of saved coordination progress. Selecting an event explains its causal chain as:

```text
From [source]  ->  To [destination]  ->  Generated [safe data point]
```

The interactive product uses allowlisted labels for request, outreach, response, evidence, decision, proposal, scheduling, and outcome stages. It starts an isolated Standard-agent run but does not contact external people or providers. Decision Room shows the live saved path; Reach and Data remain synchronized to the selected saved event.

The public JSON and CSV downloads are created from the same validated final evidence delivered by the event stream. The separate legacy/local analytics API, JSON attachment, CSV, and ICS keep their 16-field filter contract. Public projections exclude private evidence, raw `CHANGE` rationale, contact routes, provider bodies, message identifiers, credentials, operational UUIDs, and availability windows.

The legacy frozen replay and offline simulation visibly retain this provenance:

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

Capture the interactive Standard-agent public product only after final QA. Never capture addresses, routes, tokens, private answers, provider bodies, conversation/message IDs, credentials, or database content.

1. Desktop composer: launch-decision request, selected stakeholders, and visible Standard-agent boundary.
2. Desktop Decision Room: selected saved event, graph, and From → To → Generated strip.
3. Desktop Reach/Data: synchronized conversation and saved result at the same event.
4. Desktop meeting-ready state: verified outcome plus final JSON/CSV downloads.
5. Mobile (390 px) Decision Room: readable controls, contract progress, and the visible **Standard agents · no external messages** boundary.
6. Mobile (390 px) Reach or Data: visible replay or export controls with safe content.

## 75–90 second video beats

1. **0–8s — Problem:** explain why broadcast, delivery, and silence are not a trustworthy coordination record.
2. **8–20s — Start one coordination:** submit the launch request and show the bounded graph begin.
3. **20–32s — Minimum path:** show named stakeholders receiving different contracts through one channel-neutral gateway boundary.
4. **32–44s — Evidence:** follow conflict, targeted interview, and confirmed evidence in synchronized panes.
5. **44–55s — Truthful decision:** show the revised proposal and saved approval only after evidence.
6. **55–66s — Meeting path:** show availability and verified meeting readiness only after approval.
7. **66–78s — Inspectable product:** use Decision Room, Reach, Data, replay, and final JSON/CSV downloads.
8. **78–90s — Proof boundary:** show **Standard agents · no external messages** and state that private live-provider proof remains pending.

## Submission links and official-form items

- Public repository URL: **[entrant-provided after publication and signed-out verification]**
- Public product URL: **[entrant-provided after signed-out interactive-run, replay, download, and Standard-agent-boundary verification; current continuity target: https://secondsignal.vercel.app]**
- Public video URL: **[entrant-provided after upload and signed-out playback verification]**
- Build Beyond registration evidence: **[entrant-provided privacy-safe confirmation]**
- Build Beyond eligibility, team, reuse/originality, deadline, judging, and required form/media evidence: **[official organizer evidence pending]**
- Final submission receipt, timestamp, and submitted links: **[entrant-provided after submission]**

Organizer eligibility is external pending. This packet does not assert official event facts, organizer approval or endorsement, registration state, deadline, judging criteria, or reuse terms until the entrant retains the event-specific official evidence.

## Limitations and readiness

SQLite is HumanWire’s local transactional and offline-proof boundary; production requires a managed relational database with equivalent constraints, migrations, operations, backups, retention, and separately verified cutover. Provider delivery is at least once. Telegram outreach requires an existing bot conversation. HumanWire does not claim production-security certification, Power BI certification, realtime analytics, organizer endorsement, a hosted production database, or an external calendar write.

The public product is an interactive, credential-free Standard-agent workflow with one guarded creation request and a bounded same-origin event stream. It sends no external messages and remains isolated from ambient provider/model configuration. Separate fake-Caspian and fake-model proof are not live-provider, live-model, or real-human proof. This local packet is ready to pair with safe screenshots, a 75–90 second recording, signed-out repository/product/video links, Build Beyond organizer evidence, and privately retained live-provider proof when those items exist.
