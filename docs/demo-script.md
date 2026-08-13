# HumanWire 3–5 minute demo

The seeded story is fictional. No real person is contacted, no provider is connected, and no calendar is mutated.

## 1. Prove the connected product offline

Run:

```powershell
python scripts/smoke_humanwire.py
```

The command creates a temporary file-backed database, uses a deterministic fake Caspian client around the real gateway and workflow, restarts the repository, exercises real answer and `CONFIRM <token>` messages through the normalized handler, inspects the demo routes, and prints eleven safe `PASS` lines. It uses no network, ambient credentials, or model.

## 2. Reproduce the synthetic multi-persona proof

Use explicit single-owner run-root paths that do not yet exist. HumanWire atomically claims each path, so two cooperative harness runs cannot share one root:

```powershell
$generateRoot = Join-Path $PWD "work/synthetic-generate-01"
python -m humanwire synthetic generate --output (Join-Path $generateRoot "transcript.json") --run-root $generateRoot

$replayRoot = Join-Path $PWD "work/synthetic-replay-01"
python -m humanwire synthetic replay --transcript tests/fixtures/humanwire/synthetic_launch_v1.json --run-root $replayRoot
```

Point out the exact visible provenance: `proof_class=synthetic_multi_persona`, `actor_type=simulated_persona`, `identity_source=synthetic_fixture`, `transport=fake_caspian`, `human_attested=false`, and `live_provider_verified=false`. Both commands print only safe identifiers, counts, terminal state, and trace hash. They never print routes, destinations, response content, UUIDs, or local paths.

The claim fails closed if the root already exists or a competing output appears. HumanWire does not overwrite or recursively delete preexisting data and writes no artifact outside the claimed root. Atomic ownership coordinates well-behaved harness runs; it is not a security boundary against a malicious same-account process with direct filesystem control.

**Non-live disclaimer:** This deterministic synthetic proof uses simulated personas, injected fake-Caspian transport, deterministic local policy, and fresh local SQLite. It does not contact real people, call Caspian or Featherless, verify a live provider or model, or constitute real-human testing.

## 3. Open the fictional public app

Run the deterministic demo server and open `/`. Select `HW-2411` to show the active mixed plan. Explain why not everyone is interviewed: HumanWire chooses the smallest contract needed. An information recipient gets delivery only, an acknowledgement recipient only authenticates receipt, quick respondents answer one question each, and only the structured contributor enters a multi-question session.

## 4. Walk the product surfaces

In Decision Room, point out the mandate state, typed stakeholder ladders, safe evidence summary, and pending approval. Open Reach to show exact outreach progression and the email-to-Telegram continuation. Open Data to show the canonical event table, filters, and CSV export; JSON and parsed CSV use the same field order and values.

Use `HW-2412` for an approved aligned result. Use `HW-2413` to demonstrate a prepared meeting view and local ICS download. Downloading the ICS does not create or update a calendar event.

## 5. Explain the safety story

The connected proof uses a primary approved mandate for bounded proposal rounds and verified meeting readiness. A separate required approval receives `CHANGE` and stops at partial/blocking. HumanWire never forces that blocker into a meeting. Raw private rationale, contact routes, provider bodies, and credentials are absent from the UI, APIs, CSV, ICS, events, logs, and smoke output.

For a later deployment rehearsal, `python scripts/smoke_humanwire.py --live --confirm-live` prints the exact manual inspection sequence. The live checklist does not transmit, connect to providers, call a model, or mutate data, and it is not an automated live-channel pass.
