# HumanWire — ML Empowerment submission packet

## Title

HumanWire

## One-line summary

HumanWire uses constrained Featherless JSON suggestions and deterministic local policy to ask each person for the smallest truthful contribution a decision needs—without giving a model human authority.

## Inspiration, problem, and why it matters

Most AI coordination products optimize for more messages and more model autonomy. That can turn delivery, silence, or plausible text into apparent progress, while obscuring who is authorized to decide. HumanWire takes the opposite approach: it minimizes interruption and makes every human contribution, confirmation, and unresolved blocker explicit.

The inspiration is the careful work of a good chief of staff: inform people who only need context, ask a focused question only where evidence is needed, route a decision to its assigned authority, and call for availability only after an actual unresolved conflict. This matters because a system that helps coordinate humans must remain honest about its limits. An asserted answer is not confirmed evidence, and a required `CHANGE` is a partial blocker—not manufactured agreement or a meeting request.

## Human empowerment, with AI kept advisory

An authenticated manager starts with a destination-free plan preview, can make a permitted `ENGAGE` override, and can use `GO` to release it; a configured deadline may release the preview. HumanWire then selects one bounded engagement contract per contribution:

- `INFORM`
- `ACKNOWLEDGE`
- `QUICK_RESPONSE`
- `STRUCTURED_INTERVIEW`
- `REVIEW_APPROVAL`
- `AVAILABILITY`

Only quick and structured contracts create interviews. `REVIEW_APPROVAL` accepts only exact `APPROVE`, `REJECT`, or `CHANGE` from the assigned authority. A required `CHANGE` remains a truthful partial result and does not begin proposal negotiation or scheduling. Where a real persisted blocking issue exists, the workflow allows at most two proposal rounds; an unresolved second round moves to scheduling. A meeting package and local downloadable ICS appear only after current verified overlap among the exact required attendees. HumanWire does not write to external calendars.

## Four Featherless advisory jobs

Featherless is integrated through a narrow OpenAI-compatible JSON client for four bounded advisory tasks:

1. Propose a stakeholder roster and engagement plan from an allowlisted projection.
2. Extract structured, shareable evidence drafts from a response.
3. Suggest non-authoritative alignment issues from an allowlisted public projection.
4. Draft bounded proposal language from verified issues.

The model is implemented as an advisor, never as the decision maker. Its JSON is strict-schema validated; the trusted directory, engagement policy, state machines, and transaction fences resolve what may persist. Failed, missing, malformed, coerced, extra-field, or unsafe output takes a conservative deterministic fallback. The model cannot authenticate identity, choose a route or destination, weaken a required contribution, record a confirmation, accept an approval, mutate a calendar, or create a meeting.

Untrusted message text is delimited. Direct contact values, credentials, private evidence, provider bodies, message identifiers, and operational identifiers are excluded from public model inputs and public projections. The model may add a bounded suggestion, but cannot turn unfinished work into agreement or claim human authority.

## Exact human confirmation and authority provenance

Quick and structured responses first become asserted, source-bound evidence. They promote only when the exact assigned participant sends `CONFIRM <token>` on that session’s persisted current route and conversation; the atomic confirmation promotes only answer-derived evidence. Delivery, silence, an unrelated reply, a stale route, or a mismatched token cannot confirm it.

Likewise, decision authority is exact and assignment-bound: the designated authority must submit the allowed decision command for that active contribution. Required alignment evaluates confirmed evidence, exact persisted decisions, and exact authenticated availability—not model text. Inbound actions correlate to the registered person, route, conversation, token, mandate, assignment, and active state, and replay/concurrency fences keep terminal or duplicate input inert.

## Verified architecture and product surfaces

```text
Registered manager (email or Telegram)
  → Caspian: one normalized on_message handler
  → HumanWire: deterministic directory policy, state transitions, exact correlation
  → Featherless: constrained advisory JSON with schema/fallback boundary
  → SQLite aggregates, append-only events, durable outbox, and recovery fences
  → minimum-necessary engagements and read-only Decision Room, Reach, Data, JSON/CSV, ICS
```

Email and Telegram enter one normalized Caspian `on_message` handler and one persisted workflow. Durable outbox identity, leases, and transaction fences support restart recovery; exact inbound replays and duplicate callbacks are inert. Provider delivery remains at least once, not exactly once.

Decision Room, Reach, Data, inline JSON, JSON attachment, CSV, and ICS rebuild safe read-only views from persisted truth. JSON and CSV share a redacted 16-field projection and filter contract. Those public views exclude private evidence, raw change rationale, contact routes, provider bodies, message identifiers, credentials, and operational UUIDs.

## How Codex was used

Codex assisted the project’s implementation and testing workflow under the documented red-green-refactor process. The repository documentation, tests, and reviewed implementation remain the basis for the claims in this packet. This does not claim that Codex independently authored HumanWire or produced any unrecorded productivity or performance result.

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

The implemented offline proof uses a temporary file-backed SQLite database, deterministic fake-Caspian and fake-model adapters, and the real gateway, workflow, repository, and web boundaries. It makes no network call and prints eleven safe `PASS` lines. Tests cover strict model schemas, deterministic fallbacks, allowlisted model inputs, authority preservation, exact evidence confirmation, and the model’s inability to create agreement or claim authority.

Featherless integration is therefore implemented and tested with deterministic fake-model/fallback evidence. It is not live-recorded Featherless proof. A controlled private live-provider/model recording remains pending until an operator-owned configuration and its checklist are completed without exposing private content.

The deterministic public replay and offline simulation must visibly retain:

```text
proof_class=synthetic_multi_persona
actor_type=simulated_persona
identity_source=synthetic_fixture
transport=fake_caspian
human_attested=false
live_provider_verified=false
```

## Screenshot shot list

Capture only the synthetic, read-only fixture after final QA. Never capture addresses, routes, tokens, private answers, provider bodies, conversation/message IDs, credentials, or database content.

1. Desktop Decision Room: destination-free preview/release and contract mix.
2. Desktop Reach: selected persisted event, flow strip, and synthetic provenance labels.
3. Desktop Data: filtered redacted event table with JSON/CSV controls.
4. Desktop meeting-ready state: verified-overlap outcome and local ICS download.
5. Mobile (390 px) Decision Room: readable synthetic/read-only labeling.
6. Mobile (390 px) Reach or Data: visible replay/export controls and safe content.

## 75–90 second video beats

1. **0–8s — Problem:** AI coordination should not turn silence or model text into authority.
2. **8–18s — Minimum necessary engagement:** show the six contracts and why HumanWire does not interview everyone.
3. **18–30s — Advisory Featherless:** show the four constrained jobs and strict schema/deterministic fallback boundary.
4. **30–42s — Human confirmation:** distinguish asserted evidence from exact `CONFIRM <token>` on the persisted route and conversation.
5. **42–54s — Authority and truthful blockers:** show exact approval commands and a required `CHANGE` staying partial.
6. **54–65s — Bounded resolution:** show the two-round cap, then verified availability and the local ICS outcome.
7. **65–77s — Durable, private-by-design system:** one handler/two channels, recovery/replay fences, redacted read-only views and exports.
8. **77–90s — Proof boundary:** display synthetic provenance labels; state that deterministic fake-model/fake-provider proof is implemented and tested, while private live Featherless/provider recording is pending.

## Submission links and official-form items

- Public repository URL: **[entrant-provided after publication and signed-out verification]**
- Public demo URL: **[entrant-provided after signed-out synthetic/read-only verification; current continuity target: https://secondsignal.vercel.app]**
- Public video URL: **[entrant-provided after upload and signed-out playback verification]**
- ML Empowerment registration evidence: **[entrant-provided privacy-safe confirmation]**
- ML Empowerment eligibility, team, reuse/originality, deadline, judging, and required form/media evidence: **[official organizer evidence pending]**
- Final submission receipt, timestamp, and submitted links: **[entrant-provided after submission]**

Organizer eligibility is external pending. This packet does not assert official event facts, organizer approval or endorsement, registration state, deadline, judging criteria, or reuse terms until the entrant retains the event-specific official evidence.

## Limitations and readiness

SQLite is HumanWire’s local transactional and offline-proof boundary; production requires a managed relational database with equivalent constraints, migrations, operations, backups, retention, and separately verified cutover. Telegram outreach requires an existing bot conversation. Provider delivery is at least once. The public demo is deterministic, synthetic, read-only, and isolated; fake-Caspian and fake-model proof are not live-provider, live-Featherless, or real-human proof. HumanWire does not claim production-security certification, Power BI certification, real-time analytics, organizer endorsement, or an external calendar write.

This local packet is ready to pair with the safe screenshots, 75–90 second recording, signed-out repository/demo/video links, event-specific organizer evidence, and privately retained live-provider/model proof when those items exist. Until then, the only claimed ML evidence is the implemented, tested deterministic fake-model/fallback integration.
