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

Point out the exact visible provenance: `proof_class=synthetic_multi_persona`, `actor_type=simulated_persona`, `identity_source=synthetic_fixture`, `transport=fake_caspian`, `human_attested=false`, and `live_provider_verified=false`. Both commands print only safe identifiers, counts, `terminal_state=partial`, `terminal_states=meeting_ready,partial`, and the same trace hash. They never print routes, destinations, response content, UUIDs, or local paths.

Walk the semantic milestones rather than only the counts: the primary mandate includes all six contracts, two independent quick responses, saved email-to-Telegram structured continuation plus confirmation, approval, availability, proposal rounds one and two, and a verified meeting-ready package. The second mandate records a required approval `CHANGE`, ends `PARTIAL`, and creates neither a proposal nor a meeting.

The claim fails closed if the root already exists or a competing output appears. HumanWire does not overwrite or recursively delete preexisting data and writes no artifact outside the claimed root. Atomic ownership coordinates well-behaved harness runs; it is not a security boundary against a malicious same-account process with direct filesystem control.

**Non-live disclaimer:** This deterministic synthetic proof uses simulated personas, injected fake-Caspian transport, deterministic local policy, and fresh local SQLite. It does not contact real people, call Caspian or Featherless, verify a live provider or model, or constitute real-human testing.

## 3. Watch the local persisted run

For a local operator walkthrough, start the deterministic command in the [synthetic agent runtime guide](synthetic-agent-runtime.md) with a run root that does not exist, then open `http://127.0.0.1:8766`. Follow Live advances only through persisted events. Pause it and use Previous and Next to show that one persona or origin highlight and `From -> To -> Generated` describe the selected event.

After the viewer reports Complete, download JSON and CSV as attachments and confirm that the JSON evidence records `meeting_ready,partial`. Then stop the viewer and replay the validated transcript into a second fresh root. Stopping the viewer does not mutate persisted workflow state. The public Vercel product cannot start this local synthetic-watch viewer.

The deterministic watch is local synthetic proof, not live-provider or human proof. Featherless mode is a separate, explicit private exploratory check and must pass validation, privacy, and replay before its output may be frozen; it does not replace the deterministic fixture.

## 4. Open the public interactive product

Open the deployed product and confirm that it starts on **Start a coordination** with the visible **Standard agents · no external messages** boundary. Submit the launch-decision template. The page must move into the workspace and progressively render the saved Request → HumanWire → Caspian Gateway path without contacting external people or providers.

## 5. Walk the product surfaces

In Decision Room, follow the graph through outreach, conflict, targeted interview, evidence confirmation, revised proposal, approval, availability, and the meeting package. Open Reach to inspect the saved conversation at the selected event, then open Data to inspect the synchronized saved result. Use Previous, Next, Play, Pause visuals, and Follow live to show that the graph, conversation, data row, and From → To → Generated strip all describe the same selected event.

After completion, download JSON and CSV from the validated final evidence already carried by the event stream, then choose **New coordination** and confirm that the prior presentation is cleared before another run starts. The public product does not expose the legacy ICS or private operational APIs.

## 6. Explain the safety story

The connected proof uses a primary approved mandate for bounded proposal rounds and verified meeting readiness. A separate required approval receives `CHANGE` and stops at partial/blocking. HumanWire never forces that blocker into a meeting. Raw private rationale, contact routes, provider bodies, and credentials are absent from the UI, APIs, CSV, ICS, events, logs, and smoke output.

For a later deployment rehearsal, `python scripts/smoke_humanwire.py --live --confirm-live` prints the exact manual inspection sequence. The live checklist does not transmit, connect to providers, call a model, or mutate data, and it is not an automated live-channel pass.
