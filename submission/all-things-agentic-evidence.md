# All Things Agentic evidence ledger

Only checked items may become final Devpost claims.

## Official requirements

| Requirement | Evidence | Status |
|---|---|---|
| Taskmaster category | Fixed launch-decision workflow; strict end-to-end chronology | PASS locally |
| Gemini 3.5+ | Gemini 3.6 Flash Vertex/ADK runtime contract | PENDING live invocation |
| Google agent framework | Google ADK 2.7 coordinator/specialists and typed output | PASS implementation; PENDING live |
| Google Cloud infrastructure | Two Cloud Run services, Firestore, Pub/Sub deployment package | PASS locally; PENDING deployed proof |
| Repository + spin-up instructions | Public `codex/humanwire` branch and `infra/google/README.md` | PASS signed out |
| Architecture diagram | `submission/all-things-agentic-architecture.png` | PASS asset |
| ~4-minute video | Locked script requires continuous live run and cloud console evidence | PENDING |

## Judging criteria

### Innovation & Operational Utility — 40%

- One objective triggers autonomous outreach, conflict handling, targeted interview, evidence confirmation, revision, approval, availability, and a meeting package.
- Conflict-disabled mode stays truthful and still reaches the outcome.
- Models cannot manufacture identity, evidence, approval, or readiness.

### Architectural Discipline & Tech Stack — 30%

- Public web and IAM-private worker are separate Cloud Run identities from one digest-pinned image.
- Firestore transactionally owns one active run, leases, immutable event order, and terminal binding.
- Pub/Sub uses an OIDC-authenticated dedicated push identity and idempotent delivery.
- Vertex AI uses ADC only in the worker; web cannot invoke it.
- Safe fixed logging, Unicode-normalized privacy checks, exact origin/path/body limits, failure isolation, and history-preserving rollback are covered by tests.

### Demo & Production Readiness — 30%

- Browser QA passed 1680×950, 1280×720, 600×900, and 390×844 locally with no graph collision, clipping, sub-44px control, or console error.
- Refresh, manual replay, selected-row synchronization, terminal hydration, downloads, and reset are covered by hostile controller tests.
- Local Docker build and non-root web/worker health checks pass.
- Live cloud deployment, continuous recorded run, Cloud Console evidence, and signed-out link checks remain mandatory.

## Reused-work disclosure

- Reused base: HumanWire gateway, workflow, repository, product UI, Standard agents, and prior adapters at commit `b549b514a9abff0c4fd35150b6cc158b61f973c1`.
- Submission-period work from 08-16-26: Gemini/Google ADK mode, cloud repository/dispatch/progress/web/worker adapters, durable browser mode, cloud E2E and hardening, deployment package, diagram, and All Things Agentic submission materials.

## Current blocker

- Google Cloud SDK 580.0.0: installed.
- Google account: authenticated.
- Accessible projects: four.
- Billing-enabled projects: zero.
- Open billing accounts visible to the authenticated account: zero.
- Cloud resources created or provider calls made: none.

The blocker must be cleared with hackathon credits or another billing-enabled project. Do not claim deployment, Gemini use, or Google Cloud proof until the resulting live evidence is recorded.
