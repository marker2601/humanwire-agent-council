# Hackathon Build Notes

## Guided onboarding — 2026-08-16

- Selected All Things Agentic and acknowledged the official rules.
- Chose the optional guided build path to adapt HumanWire rather than invent a disconnected product.
- Confirmed the initial workflow: executive request → targeted outreach → conflict/evidence → proposal revision → approval → availability → meeting package.
- Confirmed the initial Google architecture: Gemini 3.5+, Google ADK, Cloud Run, Firestore, and Pub/Sub.
- Preserved HumanWire's typed authority boundary and existing product experience.
- Chose Taskmaster as the primary category while using enterprise safety and architecture as differentiation.
- Chose an executive mission-control visual direction using the existing navy/cyan/amber design system.
- Active shaping moment: “whichever qualifies and ensures it will be better than others.” This set the priority order to official judging weights rather than personal aesthetic preference.
- Participant preference: begin with strong defaults, make changes later when evidence shows they are needed, and avoid repetitive approval prompts.
- Onboarding rounds completed: essentials, sharpening, and optional visual/positioning round.

## Scope — 2026-08-16

- Brain dump carried forward from the existing HumanWire product conversation and onboarding profile; no repeated interview was required.
- Research references selected: Palantir AIP for governed operations, Temporal for durable execution, and Linear for product clarity.
- Time budget approved: six focused build days, with the remaining calendar reserved for hardening and submission work.
- Cut scope to one exceptional launch-decision Taskmaster workflow.
- Kept Gemini 3.5+, Google ADK, Cloud Run, Firestore, and Pub/Sub as genuine runtime components.
- Explicitly cut real Telegram/email delivery, direct calendar writes, multi-tenant administration, optional Google media models, and multiple shallow workflows.
- Active shaping moment: participant replied “approved” to the recommended scope and asked to keep details changeable later.
- Deepening rounds: 0. The participant's speed-first preference and existing detailed product context made another optional scope interview lower value than writing the document.

## PRD — 2026-08-16

- Converted the approved scope into eight product epics covering request creation, live workspace, authoritative workflow, replay, recovery, completion/exports, accessibility, and Google proof.
- Defined strict visible chronology and prohibited early approval/availability.
- Required durable refresh, duplicate suppression, terminal truth, and isolated retry behavior.
- Preserved every important existing HumanWire interaction while making Google-runtime proof a first-class judge journey.
- Added product-level edge cases for delayed/malformed agents, redelivery, instance restart, replay selection, mobile sticky controls, failed terminal states, and final bindings.
- Active shaping moment: participant answered “next” after reviewing the recommended first-use, execution, failure, and wow-moment behaviors; this was treated as approval to write rather than request another interview.
- Deepening rounds: 0. The approved defaults, existing product evidence, and speed-first preference made the acceptance-criteria expansion more valuable than another optional question round.

## Technical specification — 2026-08-16

- Inspected the authoritative HumanWire checkout and traced the current run manager, public app, decision-engine factory, projection store, exports, controller, Vercel entry, and regression boundaries.
- Chose one image deployed as a public Cloud Run web service and a private Cloud Run worker service.
- Chose authenticated Pub/Sub push and Firestore transactional metadata plus immutable public timeline records.
- Kept the HumanWire gateway/workflow/repository as the sole authority; Google ADK and Gemini produce bounded typed candidate decisions only.
- Added an explicit `google_adk` runtime rather than changing Standard or model-assisted semantics.
- Chose ETag/saved-ordinal polling so refresh and instance replacement do not lose progress.
- Defined IAM, secret, prompt/output, idempotency, export, observability, testing, deployment, and rollback boundaries.
- Mapped every PRD epic to implementation components and verification.
- Active shaping moment: participant answered “approved” to Gemini 3.6 Flash, ADK 2.x, two Cloud Run services, Firestore, Pub/Sub, and adapter-preserved local behavior.
- Deepening rounds: 0. Explicit approval and the speed-first preference made another optional architecture interview unnecessary.

## Build checklist draft — 2026-08-16

- Reused the learner profile instead of re-asking plan-ownership and pause preferences: autonomous speed-run, no routine participant pauses, concise milestone updates, and one scoped verified commit per item.
- Selected the saved product wow moment: a visible objection triggers targeted interviews, evidence confirmation, proposal revision, approval, availability, and a meeting-ready package on one synchronized graph; refresh proves durability.
- Ordered the riskiest qualifying proof first: one real Google ADK/Gemini decision must traverse existing HumanWire authority before durable cloud infrastructure or submission polish expands.
- Drafted 12 dependency-ordered checklist items covering contracts, ADK/Gemini, Firestore, projection/exports, Pub/Sub worker, public app, browser polling, E2E authority, security/compatibility, deployment, live acceptance, and Devpost handoff.
- Build mode will lock as autonomous once the participant completes the required final gut-check.
- Deepening rounds: 0 on the hand-off path; the final gut-check is the participant review.

## Build checklist approved — 2026-08-16

- Participant replied “go ahead” to the required final gut-check.
- Locked the 12-item dependency order and autonomous speed-run mode.
- Participant-facing verification pauses remain disabled; Codex still runs the full test/review gates for every item.
- The first implementation target is the qualifying Google runtime contract and one real ADK/Gemini decision through existing HumanWire authority.

## Build item 1 — Google runtime contracts — 2026-08-16

- RED: `test_google_config.py` failed at collection because `humanwire.google_config` did not exist.
- GREEN: 13 focused tests cover the qualifying Gemini 3.5+ boundary, Vertex ADC project requirement, AI Studio key presence, secret-free runtime projection, explicit `google_adk` request mode, and the optional Google package extra.
- Added structural cloud repository/dispatcher/progress protocols while reusing the existing `PersonaDecisionEngineFactory` protocol.
- Compatibility gate for Google config, studio models, and the full synthetic test file exited 0 with two existing skips.
- Ruff, provider-free base imports, and `git diff --check` passed.
- Completion audit caught and fixed two issues before commit: eager evaluation of a type-only annotation and an incorrect projection type name.

## Build item 2 - Google ADK decision authority - 2026-08-16

- RED: the focused Google-agent suite failed during collection because `humanwire.google_agents` and `humanwire.google_decision_engine` did not exist.
- Added Google ADK 2.7.0 specialist agents and a frozen spawn-safe Gemini factory using typed `PersonaDecision` output.
- The real ADK `InMemoryRunner` is exercised at its model boundary; the captured request contains the delivered HumanWire assignment and permitted intent.
- A spawned Google decision traverses exactly one existing HumanWire gateway and a hostile hanging model is hard-terminated with one inert timeout action and no surviving child or persona thread.
- Unsafe authority or private-fact output remains centrally rejected by the existing HumanWire validator; provider exceptions are normalized to fixed safe reasons.
- Focused Google tests passed, and the Google-agent plus existing Pydantic persona and full synthetic compatibility gate exited 0 with two existing Windows skips.
- Ruff and `git diff --check` passed; the factory pickle and representation contain no API key.
- Live provider probe is intentionally deferred to Item 11: the current environment has no Google Cloud project, ADC, `gcloud`, or AI Studio key. No provider call or Google spend occurred.
