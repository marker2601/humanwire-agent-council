# Title

HumanWire

## One-line Summary

HumanWire turns one executive objective into an evidence-backed, authority-approved, meeting-ready decision through durable Gemini and Google ADK specialists.

## Problem

Important decisions rarely fail because a team cannot generate more text. They fail because coordination is messy: the wrong people are asked for the wrong contribution, disagreement is discovered late, evidence is confused with assertion, approval is treated as implied, availability arrives before a decision exists, and progress disappears when a process restarts.

Managers compensate with broadcasts, spreadsheets, follow-up messages, and meetings that begin before the decision is ready. That creates delay without creating authority.

## Solution

HumanWire is an autonomous Taskmaster workflow for multi-stakeholder decisions. A decision owner submits one objective. Google ADK coordinates bounded Gemini specialists that perform outreach, identify conflict, conduct a targeted interview, confirm evidence, draft and revise a proposal, request explicit approval, collect post-approval availability, and produce a meeting-ready package.

HumanWire does not let a model declare that those steps happened. Every candidate decision crosses one typed gateway. Deterministic rules retain authority over identity, evidence confirmation, engagement contracts, approval, scheduling eligibility, privacy, and persistence. Firestore saves a monotonic public timeline; Pub/Sub dispatches asynchronous work to an IAM-private Cloud Run worker; a public Cloud Run service reconstructs the same saved prefix after refresh and generates digest-bound JSON and CSV evidence.

## Why This Matters

HumanWire removes the operational work between “we need a decision” and “the right people can make it.” The agent does more than answer a prompt: it manages a complete asynchronous workflow, distinguishes contribution types, resolves a real objection, protects explicit authority, and returns a usable outcome. The durable replay makes autonomy inspectable instead of theatrical.

## How We Used AI

- **Gemini 3.6 Flash through Vertex AI:** produces schema-constrained candidate stakeholder decisions for bounded assignments.
- **Google Agent Development Kit 2.x:** defines the coordinator and specialist roles and invokes Gemini with the exact delivered assignment and permitted intent.
- **HumanWire authority layer:** validates every `PersonaDecision` before it can cross the gateway or mutate workflow state. A model cannot authenticate a person, confirm evidence, reinterpret acknowledgement as approval, approve a decision, choose a destination, schedule a meeting, or write Firestore.
- **Failure isolation:** model execution is bounded in a killable child process. Malformed, unsafe, late, unauthorized, or timed-out output becomes inert rather than silently falling back to a different agent mode.

Live Vertex/Cloud proof is still pending a billing-enabled Google project. Provider-free tests use the real ADK/factory boundaries with deterministic fake model output; the final Devpost claim will be upgraded only after the recorded cloud run exists.

## How We Used Codex

Codex helped turn the existing HumanWire authority engine into this Google-native entry. It shaped the scope and PRD, wrote the technical specification and dependency-ordered checklist, implemented each layer test-first, reproduced adversarial failures before fixes, ran independent review rounds, exercised four browser viewports, verified privacy and exact replay behavior, built the two-role container, and maintained the evidence ledger.

The strongest Codex contribution was sustained verification across boundaries rather than one-shot code generation: it found and fixed early approval chronology, replay desynchronization, mobile control loss, graph label overlap, lease/finality gaps, Unicode privacy bypasses, same-origin encoding aliases, worker lifecycle leaks, and stale-state hydration. Build notes preserve the RED/GREEN evidence and the points where the original design had to change.

## Key Features

- One-click launch-decision workflow rather than a chat loop.
- Eight named stakeholder agents with explicit roles and engagement contracts.
- Visible request → outreach → conflict → targeted interview → confirmed evidence → proposal → revision → approval → availability → meeting-ready chronology.
- A truthful conflict-disabled branch that still engages the risk stakeholder and completes without manufacturing conflict.
- Strict identity, evidence, approval, and scheduling gates owned by HumanWire.
- Durable Firestore ownership, leases, immutable timeline records, exact terminal binding, and cold reconstruction.
- Authenticated Pub/Sub dispatch with idempotent redelivery and an IAM-private worker.
- Synchronized graph, conversation, data, lifecycle, replay, and bound JSON/CSV exports.
- Refresh-safe ETag polling and accessible desktop, tablet, and mobile layouts.
- Fixed safe logging, Unicode-normalized privacy checks, formula-safe CSV, no credentials in browser/state/export/error paths, and no Standard fallback in Google mode.

## Architecture

One digest-pinned image is deployed as two Cloud Run services:

1. **Public web:** validates an exact same-origin request, transactionally creates the queued run in Firestore, publishes only alias and idempotency data to Pub/Sub, and serves durable polling/replay/exports. It has only Datastore User and Pub/Sub Publisher.
2. **Private worker:** Pub/Sub invokes it with an OIDC token from a dedicated push identity. It claims a Firestore lease before constructing the model factory, runs the Google ADK/Gemini work, routes validated decisions through HumanWire, and appends immutable progress. It has Vertex AI User, Datastore User, and Logs Writer.
3. **Push identity:** has Cloud Run Invoker on the worker only.

Google Cloud uses Application Default Credentials through service identities. No API key is placed in the image, deployment scripts, browser, prompts, Firestore, exports, or logs.

Architecture upload: `submission/all-things-agentic-architecture.png`

## Testing Instructions

### Fast deterministic proof

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,google]"
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_google_e2e.py tests\humanwire\test_studio_e2e.py -q
```

This executes the full web → repository → dispatch → worker → gateway/workflow → durable publisher → cold-web path with deterministic model output and no external call. It asserts exact authority chronology, no early approver/availability action, one gateway handler, no duplicate execution on redelivery, and row-for-row JSON/CSV parity.

### Full verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
node --check src\humanwire\studio_static\coordination-studio.js
node --check tests\humanwire\studio_frontend_hostile_harness.js
```

### Container and cloud reproduction

```powershell
docker build --pull --tag humanwire-google:local .
.\infra\google\deploy.ps1 -ProjectId YOUR_BILLING_ENABLED_PROJECT -Region us-central1
```

See `infra/google/README.md` for exact IAM, role-local startup, deployed inspection, live acceptance, and history-preserving rollback steps.

## Public Demo Link

**Qualifying Google Cloud URL:** PENDING — blocked until hackathon credits or another billing-enabled Google project is available.

**Existing product continuity demo:** https://secondsignal.vercel.app/ — this is the credential-free Standard-agent product and is not claimed as Google/Vertex deployment proof.

## Public Repository Link

https://github.com/marker2601/humanwire/tree/codex/humanwire

Google adaptation branch: `codex/humanwire`

Reused-work disclosure: the Google adaptation began from base commit `b549b514a9abff0c4fd35150b6cc158b61f973c1` on 08-16-26. Earlier HumanWire workflow, gateway, repository, and product UI work is reused; Gemini/ADK mode, durable cloud adapters, cloud polling, deployment packaging, cloud E2E, and All Things Agentic materials were built during this submission period.

## Demo Video

**Required URL:** PENDING.

The locked four-minute outline is in `submission/all-things-agentic-video-script.md`. The final recording must include one continuous live run and visible proof of the actual Cloud Run revisions, Firestore timeline, Pub/Sub subscription, Vertex/ADK execution, and matching final digest. It must not reuse the Standard-agent Caspian video as Google proof.

## Screenshot Shot List

1. Composer showing Taskmaster objective, selected stakeholders, five-stage flow, and Gemini/ADK/Google Cloud disclosure.
2. Live conflict event with exactly one highlighted Caspian Gateway → Anika Rao path and synchronized conversation/data rows.
3. Confirmed evidence and “Proposal revised” event with lifecycle progress.
4. Explicit approval followed by availability and “Meeting package ready.”
5. Refresh-restored completed workspace plus JSON/CSV download controls.
6. Cloud Run revisions for public web and private worker, both pinned to the same image digest.
7. Safe Firestore run/timeline structure and authenticated Pub/Sub subscription without credentials or private data.
8. Architecture diagram.

## Submission Readiness Notes

- Devpost registration: verified for All Things Agentic.
- Category: Taskmaster.
- Official deadline: August 31, 2026 at 5:00 PM Pacific (September 1 at 00:00 UTC).
- Local implementation: checklist items 1–9 complete; Item 10 packaging complete.
- Container: pinned image builds; web and worker roles boot as non-root and pass health checks.
- Cloud deployment/live Gemini proof/video/screenshots: blocked because the authenticated Google account currently has no billing-enabled project or open billing account.
- Do not move to final Devpost submit until the qualifying cloud proof, video, architecture upload, country field, and final signed-out link checks are complete.

## Known Limitations

- A live Google deployment and Gemini invocation have not yet been recorded; current cloud claims are limited to implementation, deterministic integration tests, and local container proof.
- HumanWire prepares a meeting package but does not write to an external calendar.
- The launch-decision catalog is an intentionally fixed product scenario for a clear, auditable demo; it is not a general enterprise directory manager.
- The public projection excludes private evidence and contact routes by design.
- Cloud scaling is deliberately capped at one worker instance/concurrency one for bounded hackathon cost, not production throughput.

## TODO Official Form Fields

- **Submitter Type (28083):** Individuals
- **Submitter country of residence (28084):** TODO — participant must provide the exact Devpost country value
- **Category (28085):** Taskmaster
- **Organization name (28086):** Not applicable — individual submission
- **Project start date (28087):** 08-16-26
- **Repository (28141):** https://github.com/marker2601/humanwire/tree/codex/humanwire
- **Reproducible README instructions (28089):** Yes
- **Hosted URL (28088):** TODO — qualifying Cloud Run URL after live deployment
- **Private testing instructions (28090):** Use the deterministic E2E command above; then open the Cloud Run URL, start Launch decision, refresh during execution, and verify the restored prefix and terminal JSON/CSV digests.
- **Google SDK (28091):** Agent Development Kit (ADK)
- **Google Cloud services (28142):** Cloud Run; Firestore; Pub/Sub
- **Architecture file (28092):** `submission/all-things-agentic-architecture.png`
- **Google model (28143):** Gemini 3.6 Flash through Vertex AI — answer only after live proof
- **Startup Prize organization/email (28093/28101):** Not opting in
- **Bonus public content (28106):** PENDING / optional
- **Bonus social post (28107):** PENDING / optional; must include `#AllThingsAgenticHackathon`
- **Required video URL:** TODO — public YouTube or Vimeo URL after final cloud recording
