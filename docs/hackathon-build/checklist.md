# HumanWire All Things Agentic Build Checklist

## Checklist Status

- **Status:** Approved and locked for autonomous execution
- **Time budget:** Six focused build days, followed by hardening and submission buffer
- **Ordering principle:** prove the riskiest qualifying Google-agent path first, then durability, product integration, deployment, and submission proof

## Build Preferences

- **Plan design:** Handed off to Codex using the participant's approved defaults.
- **Build mode:** Autonomous speed-run. Locked on 2026-08-16.
- **Comprehension checks:** N/A; explain only decisions that materially affect product or submission risk.
- **Git:** One scoped commit per completed checklist item after tests and independent review; never mix unrelated work.
- **Verification:** Codex verifies every item. No routine participant look-at-it pauses; stop only for a genuine credential/billing/legal blocker, an irreversible out-of-scope action, or a material product decision not answered by the PRD/spec.
- **Check-in cadence:** Concise milestone updates at RED, first GREEN, broad GREEN, review verdict, and commit.
- **Compatibility:** Standard/local/Vercel behavior is frozen unless a test explicitly selects `google_adk`.
- **External cost:** Prefer emulator, fake adapters, scale-to-zero, event credits, and bounded live probes. Never create open-ended spend.

## Submission Wow Moment

The judge starts one executive launch decision and sees an objection become a targeted agent interview, confirmed evidence, a justified proposal revision, explicit approval, post-approval availability, and **Meeting package ready**—all on one synchronized decision graph. A refresh during the run restores the same saved prefix, proving this is durable agentic work rather than an animated script.

Submission story assets to capture during the build:

- Composer with qualifying Gemini/ADK/Google Cloud disclosure.
- Live conflict-to-evidence active path.
- Proposal revision and authority gate.
- Completed meeting package and synchronized replay.
- Cloud Run revisions, Pub/Sub subscription, Firestore timeline, and matching run digest.
- Architecture diagram, public repository, reproducible commands, and reused-work disclosure.

## Checklist

- [x] **1. Establish Google runtime contracts and dependency boundary**
  Spec ref: `spec.md > 3.3 Explicit runtime modes; 9. Configuration; 10. File Plan; 11. Dependencies And Official References`
  What to build: Add the optional Google dependency group, explicit `google_adk` mode, strict safe Google settings, and protocols for decision engine, run repository, dispatcher, and progress publisher. Keep Standard/model-assisted defaults unchanged and add tests before production edits.
  Acceptance: Google mode cannot initialize without explicit qualifying configuration; configuration retains no credential value; Standard generation preserves existing frozen transcript/semantic behavior; importing the base package does not require Google credentials.
  Verify: `.venv\Scripts\python.exe -m pytest tests\humanwire\test_google_config.py tests\humanwire\test_studio_models.py tests\humanwire\test_synthetic.py -q` and `.venv\Scripts\ruff.exe check src\humanwire tests\humanwire`.

- [x] **2. Prove one real ADK/Gemini decision through HumanWire authority**
  Spec ref: `spec.md > 4.3 Google ADK coordinator; 4.4 Specialist agents; 4.5 Gemini decision-engine factory; 4.6 HumanWire authority layer`
  What to build: Implement the frozen spawn-safe Google factory, ADK coordinator, initial specialist definitions, Pydantic structured decision output, and conversion into the current `PersonaDecision`. Start with fake-runner tests, then perform one explicit bounded live Gemini probe when credentials are ready.
  Acceptance: A qualifying Gemini decision is produced through ADK, centrally validated, and delivered through exactly one existing gateway handler; malformed, mismatched, late, unsafe, or unauthorized output is inert; a timed-out child is terminated with no surviving worker; no Standard fallback occurs.
  Verify: `.venv\Scripts\python.exe -m pytest tests\humanwire\test_google_agents.py tests\humanwire\test_google_decision_engine.py tests\humanwire\test_pydantic_persona.py tests\humanwire\test_synthetic.py -q`, then run the documented opt-in live probe and record only safe model/project/result metadata.

- [x] **3. Implement the durable run repository and immutable timeline**
  Spec ref: `spec.md > 4.8 Firestore run repository; 5. Firestore Data Model`
  What to build: Implement in-memory and Firestore repositories for active ownership, normalized run creation, transactional claims/leases, monotonic timeline appends, safe reconstruction, exact terminal idempotence, and ownership release. Add emulator-compatible tests for races and redelivery.
  Acceptance: Two concurrent creates yield one owner; same ordinal/hash is idempotent; divergent duplicate rejects; no ordinal is skipped or rewritten; expired recovery is explicit; terminal binding and active-owner release are atomic; only the safe public request/projection is stored.
  Verify: `.venv\Scripts\python.exe -m pytest tests\humanwire\test_cloud_store.py -q`; with emulator configured, `.venv\Scripts\python.exe -m pytest tests\humanwire\test_cloud_store.py -m firestore_emulator -q` repeated for the concurrency slice.

- [x] **4. Persist synchronized progress and bind truthful exports**
  Spec ref: `spec.md > 4.7 Durable progress publisher; 4.11 Exports; 5.5 Final binding`
  What to build: Connect `StudioProgressObserver` to an optional Firestore publisher, append synchronized event/conversation/data/transition records, reconstruct snapshots, and generate bound JSON/CSV from the immutable timeline. Preserve byte-identical `publisher=None` behavior.
  Acceptance: Visible panes share the selected ordinal; inert and persisted records retain unique timeline provenance/effect; complete/failed publication is exact-idempotent; exports remain disabled until both bindings are valid; JSON/CSV parity and privacy/formula defenses pass.
  Verify: `.venv\Scripts\python.exe -m pytest tests\humanwire\test_cloud_progress.py tests\humanwire\test_studio_projection.py tests\humanwire\test_synthetic_viewer.py -q` plus the existing frozen transcript/semantic/restart hash probes.

- [x] **5. Build authenticated Pub/Sub dispatch and private worker execution**
  Spec ref: `spec.md > 4.2 Private worker service; 4.9 Pub/Sub dispatcher; 6.2 Private worker route; 7.2 Claim and execute`
  What to build: Implement the safe versioned message, publisher adapter, private FastAPI worker route, envelope validation, transactional claim, Google execution, progress publication, cleanup, retry classification, and duplicate-delivery behavior.
  Acceptance: Messages contain only schema/alias/idempotency data; duplicate push cannot execute twice; terminal duplicates return safely; transient infrastructure errors retry; malformed envelopes fail closed; terminal response follows cleanup; no request, provider detail, credential, stack, or exception graph escapes.
  Verify: `.venv\Scripts\python.exe -m pytest tests\humanwire\test_cloud_dispatch.py tests\humanwire\test_cloud_worker.py tests\humanwire\test_studio_run.py -q` and repeat the claim/redelivery concurrency selection ten times.

- [x] **6. Create the hardened public Cloud Run application**
  Spec ref: `spec.md > 4.1 Public web service; 6.1 Public routes; 8.5 Public boundary`
  What to build: Add the separate cloud FastAPI factory and entry point for composer/catalog/create/workspace/snapshot/export/health routes. Create queued Firestore state before dispatch and return `202` plus the dedicated workspace URL.
  Acceptance: No run is created on GET; exact same-origin/raw-path/content-length/type/body limits hold; simultaneous starts yield one safe winner; active conflict discloses no alias; web service never runs coordination or reads model credentials; all fixed errors include required headers.
  Verify: `.venv\Scripts\python.exe -m pytest tests\humanwire\test_cloud_web.py tests\humanwire\test_studio_app.py tests\humanwire\test_submission_app.py tests\humanwire\test_web.py -q` and `.venv\Scripts\ruff.exe check src\humanwire tests\humanwire`.

- [x] **7. Add durable browser polling without weakening replay**
  Spec ref: `spec.md > 4.10 Browser controller; 6.1 Public routes; 12.6 Browser verification`
  What to build: Add the ETag/saved-ordinal polling adapter, Cloud workspace hydration, terminal export URLs, and truthful Gemini/ADK/Cloud disclosure while retaining stream and local-poll modes. Extend the hostile async harness for unchanged snapshots, `304`, refresh, stale queues, failed terminal state, mobile tabs, and New coordination.
  Acceptance: First progress appears automatically; `304` does not erase graph/selection; manual replay stays selected; Follow Live cancels stale work; refresh restores running/complete/failed truth; all panes share one selected event; mobile retains every required control; Standard product copy remains truthful.
  Verify: `.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_frontend.py tests\humanwire\test_cloud_web.py tests\humanwire\test_studio_e2e.py -q`, `node --check src\humanwire\studio_static\coordination-studio.js`, and computed-browser geometry/interaction checks at four approved viewports.

- [x] **8. Complete the deterministic cloud end-to-end authority story**
  Spec ref: `spec.md > 7. End-To-End Flow; 12.4 Deterministic cloud E2E; PRD Epic 3`
  What to build: Wire the fake ADK runner through web → repository → dispatch → worker → gateway/workflow → projection → polling and encode the launch-decision chronology as strict ordered assertions, including the conflict-disabled path.
  Acceptance: Default order is request → outreach → conflict → targeted interview → confirmed evidence → proposal → revision → approval → availability → meeting package; Sofia/Daniel never act early; conflict-disabled still engages Anika truthfully and reaches meeting-ready; refresh/redelivery produce no duplicate or reordered event.
  Verify: `.venv\Scripts\python.exe -m pytest tests\humanwire\test_google_e2e.py tests\humanwire\test_studio_e2e.py tests\humanwire\test_workflow.py tests\humanwire\test_gateway.py -q` and compare the final snapshot, JSON, and CSV row-for-row.

- [x] **9. Harden privacy, IAM, failure recovery, and compatibility**
  Spec ref: `spec.md > 8. Security, Privacy, And Authority; 13. Failure And Recovery; 12.7 Compatibility gates`
  What to build: Add deployment-contract/IAM tests, safe structured logging, Unicode-normalized hostile privacy coverage, credential/exception graph checks, lease-recovery failures, missing-config truth, and comprehensive regression gates. Resolve only defects attributable to the Google adaptation.
  Acceptance: No secret/private/provider/path/command/internal identifier reaches Firestore, public JSON/CSV, logs, DOM, or errors; Gemini cannot cross authority gates; missing config never falls back; failed stages remain non-complete; Standard transcript bytes/hashes and existing Vercel/local product behavior remain intact.
  Verify: `.venv\Scripts\python.exe -m pytest tests\humanwire -q`, `.venv\Scripts\python.exe -m pytest -q`, `.venv\Scripts\ruff.exe check .`, both Node syntax checks, smoke commands, `git diff --check`, and tracked-diff/public-artifact privacy scans.

- [ ] **10. Package and deploy the two-service Google Cloud stack**
  Spec ref: `spec.md > 14. Deployment; 9. Configuration; 11. Dependencies And Official References`
  What to build: Add Dockerfile, `.dockerignore`, Cloud Build/deployment files, Firestore indexes, dedicated service-account/IAM instructions, authenticated push subscription, scale-to-zero/bounded runtime settings, rollback steps, and clean-environment reproduction. Deploy one image as public web and private worker.
  Acceptance: Worker rejects unauthenticated access; Pub/Sub identity alone can invoke it; web identity cannot invoke Vertex AI; cloud uses ADC, not browser/API-key transport; revision/image are pinned; exact public origin is configured; rollback does not delete history; no open-ended spend setting is introduced.
  Verify: `.venv\Scripts\python.exe -m pytest tests\humanwire\test_google_deployment_contract.py -q`, build the container locally, run both entry points, then execute documented `gcloud` inspection/smoke commands against deployed revisions.
  Current evidence: local packaging, deployment-contract tests, pinned Docker build, and non-root web/worker startup checks are complete. Live deployment remains pending an authenticated Google account, a selected billing-enabled project, and the requested hackathon access/credits.

- [ ] **11. Run live Gemini, durability, and judge-view browser acceptance**
  Spec ref: `spec.md > 12.5 Live Gemini proof; 12.6 Browser verification; 18. Definition Of Done`
  What to build: Execute one fresh live launch-decision run on the deployed stack, refresh during execution, replay a historical event, verify final exports/digests, inspect safe Google proof, and capture desktop/mobile evidence. Fix any Critical/Important issue test-first and rerun affected gates.
  Acceptance: ADK/Gemini invocation is real and visible through saved work; Cloud Run/Pub/Sub/Firestore are essential; strict chronology completes; refresh restores the same prefix; exactly one graph path and synchronized rows render; 1680×950/1280×720/600×900/390×844 pass accessibility/geometry/console checks; public proof reveals no secrets.
  Verify: Follow the live acceptance script in `infra/google/README.md`; record run/revision/digest evidence; download and compare JSON/CSV; independently review browser screenshots and the complete code/deployment diff; require READY YES with no Critical/Important findings.

- [ ] **12. Prepare the Devpost handoff**
  Spec ref: `spec.md > 4.12 Observability and proof; 15 Phase 7 — submission hardening; prd.md > Submission Proof Points`
  What to build: Finalize public README/reproduction, Google architecture diagram, Taskmaster positioning, reused-work/base-commit disclosure, safe proof ledger, four-minute story/script, screenshots, repository link, deployed URL, video plan, test evidence, and required Devpost answers. Keep claims limited to recorded proof.
  Acceptance: A first-time judge can understand the problem, autonomous arc, Google architecture, HumanWire authority boundary, durable refresh, outcome, and reproduction path; every rule/judging criterion has evidence; links are public; no unrecorded provider/human action is claimed; materials are ready for `$prepare-submission`.
  Verify: Run the submission checklist and all referenced links in a signed-out browser, verify repository/deployed URL/video accessibility, compare architecture and claims to the live run, and confirm the next command is `$prepare-submission`.

## Dependency Chain

```text
1 contracts
  → 2 real ADK/Gemini authority proof
  → 3 Firestore repository
  → 4 progress/final binding
  → 5 Pub/Sub worker
  → 6 public cloud app
  → 7 browser polling
  → 8 deterministic E2E
  → 9 hardening/compatibility
  → 10 deploy
  → 11 live acceptance
  → 12 Devpost handoff
```

The live-model risk is intentionally proven in Item 2. Durable infrastructure follows only after the qualifying agent path is real. Submission assets are collected throughout, then finalized in Item 12.
