# HumanWire 3–5 minute demo

The seeded story is fictional. No real person is contacted, no provider is connected, and no calendar is mutated.

## 1. Prove the connected product offline

Run:

```powershell
python scripts/smoke_humanwire.py
```

The command creates a temporary file-backed database, uses a deterministic fake Caspian client around the real gateway and workflow, restarts the repository, exercises real answer and `CONFIRM <token>` messages through the normalized handler, inspects the demo routes, and prints eleven safe `PASS` lines. It uses no network, ambient credentials, or model.

## 2. Open the fictional public app

Run the deterministic demo server and open `/`. Select `HW-2411` to show the active mixed plan. Explain why not everyone is interviewed: HumanWire chooses the smallest contract needed. An information recipient gets delivery only, an acknowledgement recipient only authenticates receipt, quick respondents answer one question each, and only the structured contributor enters a multi-question session.

## 3. Walk the product surfaces

In Decision Room, point out the mandate state, typed stakeholder ladders, safe evidence summary, and pending approval. Open Reach to show exact outreach progression and the email-to-Telegram continuation. Open Data to show the canonical event table, filters, and CSV export; JSON and parsed CSV use the same field order and values.

Use `HW-2412` for an approved aligned result. Use `HW-2413` to demonstrate a prepared meeting view and local ICS download. Downloading the ICS does not create or update a calendar event.

## 4. Explain the safety story

The connected proof uses a primary approved mandate for bounded proposal rounds and verified meeting readiness. A separate required approval receives `CHANGE` and stops at partial/blocking. HumanWire never forces that blocker into a meeting. Raw private rationale, contact routes, provider bodies, and credentials are absent from the UI, APIs, CSV, ICS, events, logs, and smoke output.

For a later deployment rehearsal, `python scripts/smoke_humanwire.py --live --confirm-live` prints the exact manual inspection sequence. The live checklist does not transmit, connect to providers, call a model, or mutate data, and it is not an automated live-channel pass.
