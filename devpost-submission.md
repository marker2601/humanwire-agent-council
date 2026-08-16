# Title

HumanWire

## One-line Summary

HumanWire is a decision-coordination agent that reaches the minimum necessary people across email and Telegram through one Caspian message boundary, resolves disagreement with evidence, and schedules only after approval and availability are real.

## Problem

Important decisions often stall because coordination tools broadcast the same request to everyone and then mistake delivery, silence, or an unverified answer for progress. Teams lose time in unnecessary interviews, decision authority becomes unclear, and meeting invitations appear before the evidence, approval, or required attendees are actually ready.

## Solution

HumanWire behaves more like a careful chief of staff. A manager describes the objective, timing, and stakeholders. HumanWire selects the minimum engagement contract each person needs—inform, acknowledge, quick response, structured interview, approval, or availability—and preserves one correlated workflow across email and Telegram through a single Caspian handler.

The workflow keeps assertions separate from confirmed evidence, treats a requested change as a real blocker, limits proposal revision to two rounds, and creates a meeting package only after the required authority and attendees have produced a current overlap.

## Why This Matters

An agent that can contact people needs stronger truth boundaries than a chat assistant. HumanWire is designed around the distinction between sent, acknowledged, asserted, confirmed, approved, and scheduled. That makes its output explainable, replayable, and safe to use as a coordination record.

## How We Used AI

The public product uses deterministic Standard agents so every judge can run the complete workflow without credentials or external messages. Those agents model different stakeholder roles and can acknowledge, disagree, answer an interview, confirm evidence, revise a proposal, approve, and provide availability.

The repository also includes an optional Pydantic AI / Featherless adapter. Model output is schema-constrained, privacy-scanned, deadline-bounded, and validated again at the central decision boundary. The model can suggest a bounded persona action; it cannot authenticate a sender, select a transport destination, confirm evidence, approve a decision, or schedule a meeting. The same deterministic policies remain the fallback.

## How We Used Codex

Codex helped turn the product specification into testable modules, write red tests before implementation, trace failures across the gateway/workflow/repository/viewer boundaries, perform independent review rounds, and run broad regression, privacy, replay, accessibility, responsive-layout, and media-quality gates. Codex also helped prepare the public repository, the 105-second demo, and this submission packet. Project documentation and executable tests remain the source of truth for every claim.

## Key Features

- One normalized Caspian `on_message` boundary for email and Telegram instead of duplicated per-channel agents.
- Minimum-necessary engagement planning across six explicit contracts.
- Exact sender, route, conversation, assignment, mandate, and replay correlation.
- Multi-person role behavior with conflict, targeted interview, evidence confirmation, proposal revision, approval, availability, and meeting preparation.
- Durable outbox, leases, retry/failover fences, restart-safe replay, and inert duplicate inbound handling.
- Live Decision Room graph plus synchronized Reach and Data panes.
- Previous, Next, Play, Pause, and Follow-live inspection of the saved path.
- Redacted JSON/CSV exports bound to the final saved run.
- Responsive 390 px through desktop product UI with keyboard-visible, 44 px controls.
- Credential-free public Standard-agent mode that makes no external calls.

## Architecture

```text
Manager request
  -> one Caspian email/Telegram message handler
  -> HumanWire planning and engagement policy
  -> authenticated workflow transitions
  -> SQLite event store + durable outbox + replay fences
  -> evidence, proposal, approval, availability
  -> meeting package
  -> redacted Decision Room / Reach / Data / JSON / CSV
```

Authority stays in the directory, contracts, state machines, transaction fences, and exact message correlation. Caspian is the cross-channel transport boundary. The product projection is rebuilt from saved truth and excludes routes, addresses, raw provider payloads, credentials, private facts, internal identifiers, and operational UUIDs.

## Testing Instructions

Requirements: Python 3.12.

```powershell
git clone https://github.com/marker2601/humanwire.git
cd humanwire
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe scripts\smoke_humanwire.py
.\.venv\Scripts\python.exe -m humanwire smoke
```

To run the private local studio without provider credentials:

```powershell
.\.venv\Scripts\python.exe -m humanwire studio --host 127.0.0.1 --port 8766 --workspace-root .\work\studio
```

Then open `http://127.0.0.1:8766/`, submit the launch-decision template, and inspect the live graph, synchronized panes, replay controls, and final downloads.

## Public Demo Link

https://secondsignal.vercel.app/

The public site runs the interactive Standard-agent workflow and sends no external messages.

## Public Repository Link

https://github.com/marker2601/humanwire

## Demo Video

https://youtu.be/FxzhLqoscSE

The independently reviewed master is 105 seconds, 1920×1080, H.264/AAC, and remains under the event's three-minute limit. Public signed-out availability was verified through YouTube's oEmbed endpoint.

The video deliberately distinguishes the working public Standard-agent product from the configured Caspian transport boundary. Live Telegram/email provider verification was not configured or recorded, and the video does not claim otherwise.

## Screenshot Shot List

1. Composer with the launch-decision request, seven stakeholders, five workflow stages, and the Standard-agent boundary.
2. Decision Room showing the full HumanWire → Caspian Gateway → stakeholder → artifact graph.
3. Conflict replay with the selected edge synchronized to Reach and Data.
4. Meeting-ready state with saved approval, availability, and final package.
5. Mobile Decision Room at 390×844 with replay/export controls visible.

## Submission Readiness Notes

- Devpost authentication: verified.
- Caspian registration: verified live on Devpost.
- Devpost project draft: https://devpost.com/software/humanwire
- Caspian submission: verified live as submission 1140539 at 2026-08-16T11:13:49.137-04:00.
- Project thumbnail: uploaded from the reviewed completed-workspace screenshot and processing on Devpost.
- Official deliverables: public GitHub repository and public demo video.
- Official custom submission questions: none.
- Public repository: live and signed-out reachable.
- Public product: live and signed-out reachable.
- Demo master: complete, independently approved, and public at https://youtu.be/FxzhLqoscSE.
- Latest repository-wide tests: exit 0.
- Secret/privacy/tracked-media scans: clean.

## Known Limitations

- The public product uses Standard agents and sends no external messages.
- Live Telegram/email Caspian-provider verification was not configured or recorded; no provider-proof claim is made.
- SQLite is the local transactional boundary; production needs a managed database with equivalent constraints and operational controls.
- Telegram outreach requires an existing bot conversation.
- Provider delivery is at least once; callbacks and replays are fenced, but exactly-once recipient delivery is not claimed.
- HumanWire creates a read-only meeting package/ICS artifact and does not write to a calendar.

## Official Form Fields

- Video URL: https://youtu.be/FxzhLqoscSE
- Devpost project URL: https://devpost.com/software/humanwire
- Submission receipt: 1140539
- No custom submission questions were returned by the official Caspian form.
