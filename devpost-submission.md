# HumanWire Agent Council

## One-line Summary

Gemini and Google ADK specialists turn one executive agenda into an evidence-backed decision brief while HumanWire keeps authority, provenance, and final approval explicitly human.

## Problem

Important decisions are slowed by coordination, not a lack of text. The right people are difficult to reach at the right moment, stakeholder concerns arrive late, evidence is mixed with assertion, and a polished AI answer can look like approval even when no accountable person has approved anything. Traditional meetings solve this by consuming everyone’s time at once.

## Solution

HumanWire turns one agenda into durable decision work. Seven bounded Gemini specialists analyze market, finance, product, technical, risk, synthesis, and red-team concerns. Eight named AI stakeholders contribute role-specific evidence and authority constraints inside a clearly labeled demo run. The final Council output is a review-ready decision brief with confirmed facts, model inferences, red-team challenges, source records, a digest, recommended action, required authority, and a visible **human approval required** state.

The workflow is saved in Firestore and survives refresh. The public demo sends no external messages. A separate connected-organization mode is implemented for activated members and consented routes, but it fails closed unless the required directory, identity, route, and transport are configured.

## Why This Matters

HumanWire reduces the coordination work around a decision without erasing the people accountable for it. Teams can collect distinct viewpoints asynchronously, challenge weak claims before a meeting, and enter the final review with the evidence and unresolved risks already organized. The intended outcome is fewer coordination meetings, shorter final meetings, and a decision record that can be defended later.

## How We Used AI

- **Gemini 3.5 Flash on Vertex AI** performs bounded specialist analysis and synthesis.
- **Google ADK 2.7** orchestrates the multi-agent Council and its handoffs.
- Every specialist has a named role, a bounded assignment, and a constrained output contract.
- HumanWire—not the model—owns identity, evidence provenance, durable state, authorization, and the final human approval gate.
- **Veo 3.1 Fast** produced the clearly labeled six-second visual guide in the demo film.
- **Lyria 3 Pro** produced the original instrumental score used in the demo film.
- Demo stakeholders and sample company records are labeled as AI/demo data and remain inside the run; no external message is claimed.

## How We Used Codex

Codex helped convert the product requirements into typed contracts, implement features test-first, run adversarial privacy and authority reviews, diagnose browser and deployment failures, compare the product against 60 official AI products, validate the deployed mission flow, prepare the architecture and submission film, and keep public claims bound to recorded evidence. The final implementation was repeatedly checked with focused tests, broad regression suites, static analysis, browser QA, cloud inspection, and media-quality gates.

## Key Features

- One agenda starts a durable mission instead of a chat session.
- Seven Gemini specialists work in parallel with visible progress and saved handoffs.
- Eight named stakeholder roles contribute different evidence, constraints, and authority conditions.
- The decision brief separates facts, inferences, and red-team challenges.
- Models cannot approve their own recommendation or silently send external messages.
- Firebase Google sign-in protects the workspace; App Check is configured in monitored rollout; exact host, origin, CSRF, and request-shape controls enforce mutations.
- Firestore preserves mission, evidence, Council, decision, and audit state across refresh.
- The completed Council, Decisions, and Evidence views expose a readable recommendation, source records, digest, required authority, and human approval gate.
- Connected-organization mode requires activated members and consented routes and fails closed when those prerequisites are absent.

## Architecture

The browser is served by Firebase Hosting. Firebase Authentication provides Google sign-in; App Check is configured and monitored. Same-origin CSRF, host, origin, body, and schema checks protect mutation routes. A digest-pinned Cloud Run DecisionOS service owns the authenticated workspace and mission API. Firestore stores tenant, organization, mission, evidence, Council, decision, and audit state. Google ADK orchestrates Gemini 3.5 Flash specialists on Vertex AI. A sanitized decision brief is projected from saved state, and the final transition remains an explicit human approval gate.

Architecture diagram: `submission/all-things-agentic-architecture.png`

Current production revision: `humanwire-decisionos-00040-g92`

## Testing Instructions

### Hosted judge flow

1. Open https://humanwire-agentic-2026.firebaseapp.com/workspace.
2. Sign in with Google.
3. Keep **Demo run** selected and start the prepared mission.
4. Observe seven specialist agents and eight named AI stakeholder contributions.
5. Wait for **Decision brief ready**.
6. Review **Council**, **Decisions**, and **Evidence**.
7. Refresh the mission URL and confirm the saved result returns with **human approval required**.

### Local test flow

```powershell
git clone https://github.com/marker2601/humanwire-agent-council.git
Set-Location humanwire-agent-council
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,google,decisionos]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
node --check src\humanwire\decisionos_static\decisionos-app.js
node tests\humanwire\decisionos_mission_harness.js
```

The default tests use fake or in-memory adapters and do not need Google credentials. Firestore emulator tests run only when `FIRESTORE_EMULATOR_HOST` is explicitly configured. Cloud deployment steps and required secret names are documented in `infra/google/README.md`.

## Public Demo Link

https://humanwire-agentic-2026.firebaseapp.com/workspace

Direct Cloud Run proof: https://humanwire-decisionos-wjjhjrgnyq-uc.a.run.app

## Public Repository Link

https://github.com/marker2601/humanwire-agent-council

## Demo Video

Final local master: `work/all-things-agentic-video/final/humanwire-agent-council-all-things-agentic-2026.mp4`

Public YouTube URL: https://youtu.be/5LDMzQU8oMM

The 1:52 film is 75.4% chronological footage from the authenticated deployed product. It names Gemini 3.5 Flash and Google ADK 2.7, shows the mission running, shows named stakeholder contributions and the saved decision brief, exposes the human-approval boundary, and shows the exact Cloud Run revision plus Firebase/Firestore/Vertex AI architecture. The Veo opening is labeled as a visual guide and does not replace real product proof.

## Screenshot Shot List

1. Signed-in mission composer with Demo run and no-external-message boundary.
2. Seven-specialist live progress rail.
3. Eight named stakeholder contributions with distinct roles.
4. Evidence-bound recommendation with authority and digest.
5. Decisions/Evidence view after refresh.
6. Google Cloud architecture diagram and exact Cloud Run revision.

## Submission Readiness Notes

- Official category: **Taskmaster**.
- Hosted product, public repository, README spin-up instructions, architecture diagram, and final video master exist.
- The live app and direct Cloud Run URL were verified against revision `humanwire-decisionos-00040-g92`.
- The final video is 112.000 seconds, 1920×1080, H.264/yuv420p at 30 fps with AAC stereo 48 kHz, burned English captions, faststart, and a full 3,360-frame decode.
- Final master SHA-256: `330CC378E57F3E2DF6B524A9C27DE28A7842E1E8F43D766EC40536F20BF1AF64`.
- The public video is published and passed signed-out title/readback verification; the final Devpost write is the remaining release action.

## Known Limitations

- The public product requires Google sign-in.
- App Check is currently in monitored rollout rather than full enforcement; authenticated mutation routes still enforce exact host, origin, CSRF, body, and schema boundaries.
- Public Demo run uses labeled AI stakeholders and sample company records and sends no external messages.
- Connected organization outreach requires private operator configuration, activated members, consented routes, and a ready transport; the public deployment does not claim provider delivery.
- The Council prepares a recommendation but does not grant itself human approval.
- Firestore emulator tests are skipped unless the emulator host is explicitly configured.

## Official Form Fields

- Submitter Type: `Individuals`
- Country of residence: `United States`
- Category: `Taskmaster`
- Organization name: `Not applicable — individual submission`
- Project start date: `08-11-26`
- Repository: `https://github.com/marker2601/humanwire-agent-council`
- Reproducible testing instructions in README: `Yes`
- Hosted project: `https://humanwire-agentic-2026.firebaseapp.com/workspace`
- Google SDK: `Agent Development Kit (ADK)`
- Google Cloud services: `Firebase Hosting, Firebase Authentication, Firebase App Check, Cloud Run, Firestore, Vertex AI`
- Google models: `Gemini 3.5 Flash, Veo 3.1 Fast, Lyria 3 Pro`
- Architecture upload: `submission/all-things-agentic-architecture.png`
- Public video URL: `https://youtu.be/5LDMzQU8oMM`
