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

## Build item 3 - Durable Firestore run repository - 2026-08-16

- RED: `test_cloud_store.py` failed during collection because `humanwire.cloud_store` did not exist.
- Added matching thread-safe in-memory and transactional Firestore repositories behind the expanded cloud repository contract.
- Concurrent creation has one global owner and returns only fixed `active_run` on conflict; the dispatch key is stored only as a SHA-256 binding.
- Claims are lease-fenced, owner-idempotent, and explicit about healthy conflict, terminal redelivery, renewal, and expired recovery.
- Immutable records use eight-digit padded document IDs, canonical record hashes, exact ordinal/persisted-ordinal progression, and same-hash idempotence with divergent duplicates rejected.
- Refresh reconstructs the synchronized public prefix from normalized request metadata plus timeline documents, never from worker memory.
- Complete/failed binding and active-owner release share one transaction; failed runs keep the prefix without enabling exports, while complete runs require all evidence digests.
- Focused gate passed 11 tests with one documented emulator skip; the emulator-only transaction test is registered and will run when `FIRESTORE_EMULATOR_HOST` is explicit.
- Adjacent cloud/studio models/projection/manager compatibility passed; Ruff, compile, privacy-boundary checks, and `git diff --check` passed.
- No Firestore service, live credential, or billable Google resource was used.
- Completion audit caught and fixed active-control drift: every Firestore claim, renewal, recovery, and append now validates the same global owner and advances its safe owner version transactionally.

## Build item 4 - Durable projection and bound exports - 2026-08-16

- RED: `test_cloud_progress.py` failed during collection because `humanwire.cloud_progress` did not exist.
- Added an optional validated snapshot publisher to `StudioProgressStore`; `publisher=None` preserves the existing in-memory behavior and frozen generation path.
- Added `CloudProgressPublisher`, which converts each stable product ordinal into one hash-bound event/conversation/data/lifecycle record through the claimed repository.
- The first real-run integration failed closed because events 11-15 were published before their batched presentation callbacks arrived. Evidence tracing showed a later callback can attach to the newest saved event on the next capture.
- Fixed the root cause with a durable stabilization watermark: early conversation-free batches stay staged, the newest conversation-bearing ordinal remains staged for one capture, and terminal snapshots flush the exact remaining prefix without rewriting history.
- A real launch run now persists 52 authoritative records plus 3 inert attempts, survives cold reconstruction, and binds complete evidence exactly once.
- Canonical JSON and CSV are regenerated from the immutable prefix on any instance; their row order, ordinal, effect, provenance, and digests match, while failed runs never expose downloads.
- Focused cloud progress passed 5 tests; the broader projection/viewer/studio/public-app/frozen-hash gate passed with one documented emulator skip.
- Ruff, compile, privacy/formula defenses, and `git diff --check` passed. No provider or billable cloud call occurred.

## Build item 5 - Pub/Sub dispatch and private worker - 2026-08-16

- RED: focused dispatch/worker tests failed during collection because `humanwire.cloud_dispatch` and `humanwire.cloud_worker` did not exist.
- Added one strict versioned message containing only run alias and opaque idempotency key, plus inline and bounded Pub/Sub dispatchers with fixed exception-graph-safe failure.
- Added the IAM-private worker app boundary: exact host/path/query/method/content-length/type/encoding rules, strict Pub/Sub envelope/base64/message validation, fixed responses, and no docs or provider details.
- The execution service transactionally claims before building the Google ADK factory, heartbeats a bounded lease on one joined non-daemon thread, runs in an isolated temporary root, and binds terminal state only after cleanup.
- A real deterministic workflow under the worker orchestration completed through the durable publisher while receiving the actual frozen Google ADK factory type; no provider call was made.
- Healthy duplicate/terminal delivery is accepted without rerun; competing claims return conflict; malformed or binding-mismatched messages are irreparable; unexpected pre-progress infrastructure failure remains retryable.
- Expired ownership appends one visible inert recovery record and fails the saved workspace rather than replaying already-persisted authority. Ten concurrent claim races each produced one executor and one fenced conflict.
- Added atomic queued-dispatch failure so a Pub/Sub publish error releases active ownership and produces a fixed failed workspace with no exports.
- Focused dispatch/worker/store tests, worker/manager/Google compatibility, Ruff, compile, privacy scan, and diff checks passed with only the explicit Firestore emulator test skipped.
- Platform authentication is deliberately enforced by Cloud Run IAM rather than trusting an application header; Item 10 will verify the invoker binding. No cloud resource or spend was created.

## Build item 6 - Hardened public Cloud Run application - 2026-08-16

- Added a separate durable Cloud web factory with composer, catalog, queued creation, workspace, snapshot, cold JSON/CSV export, and fixed health routes.
- The public service stores Google ADK runs before dispatch, returns `202`, never invokes the coordination worker or reads model credentials, and atomically fails/releases a queued run when publication fails.
- Snapshot polling is reconstructed from the repository with stable ETags and saved-ordinal headers; terminal exports are regenerated and digest-checked from immutable records on any cold instance.
- Exact Host/same-origin/action/raw-path/query/method/content-length/type/encoding/body guards and fixed security headers apply to success and failure paths. Active conflicts, provider failures, and repository failures expose no alias or private detail.
- Focused coverage includes simultaneous starts, malformed/duplicate/non-ASCII headers, encoded paths, no-run GETs, cold workspaces, `304`, pending/final exports, real inline queue-to-worker completion, and public/private route separation. No Gemini, Pub/Sub, Firestore, or other live cloud call was made.

## Build item 7 - Durable cloud browser workspace - 2026-08-16

- RED: cloud-page and controller contracts failed because the durable app still rendered local-poll copy and did not send or consume the saved-ordinal protocol.
- Added a distinct cloud delivery mode that submits only `google_adk`, hydrates any non-stream workspace URL immediately, polls with both ETag and saved ordinal, and uses immutable terminal JSON plus evidence CSV export routes.
- Preserved stream and local-poll behavior while extending the hostile controller harness across cloud start, cold refresh, alias-free active conflict, unchanged snapshots, terminal hydration, replay queues, failed state, mobile tabs, and full reset.
- The rendered cloud surface now identifies Google ADK, Gemini 3.6 Flash, HumanWire authority gates, and the no-external-message boundary without exposing Standard/model mode controls.
- In-app Browser verification passed at exact 1680x950, 1280x720, 600x900, and 390x844 viewports: 17 nodes and 57 edges, no graph collisions or page overflow, no visible control below 44x44, no meaningful text below 14px, and no console warnings or errors.
- A completed 55-event durable run survived reload at Event 55, replay moved 55 to 54, and both JSON and CSV downloads fired without navigating away. The mobile composer and completed workspace retained all required controls and truthful runtime copy.
- Automated frontend/cloud-page gates, Node syntax, Ruff, privacy scan, and diff checks passed. Browser QA used only the local in-memory repository and deterministic worker; no Gemini or live Google Cloud call occurred.

## Build item 8 - Deterministic cloud authority E2E - 2026-08-16

- RED: `test_google_e2e.py` failed during collection because the reusable `humanwire.cloud_e2e` authority-proof verifier did not exist.
- Added a fail-closed verifier that consumes the cold public snapshot plus bound JSON/CSV together and rejects schema, request, outcome, graph, conversation, event, chronology, or byte-level export divergence.
- The E2E test drives the actual public create boundary, records the safe dispatch message, claims it in the private worker, observes the explicit Google ADK factory, executes the deterministic fake-model path through one gateway, publishes the durable timeline, redelivers the queue message, and reads the result through a new cold web instance.
- The default story is locked at exact ordinals 1, 4, 25, 31, 35, 36, 43, 49, 51, and 55 for request, outreach, conflict, interview, confirmed evidence, proposal, revision, approval, availability, and meeting readiness.
- Sofia has no conversation before the proposal and Daniel has none before approval. The conflict-disabled branch still receives Anika's risk acknowledgement, contains no rollback, rejection, conflict, or targeted-interview transition, and reaches meeting-ready.
- Terminal duplicate delivery is byte-stable and does not rebuild the factory or rerun authority. Cold ETag refresh returns `304`; JSON and CSV contain one row per event with exact ordinal, persisted ordinal, effect, and data-point parity.
- Hostile tests prove that reordered approval/evidence or any CSV drift fails the shared verifier. The 286-test cloud E2E, studio E2E, workflow, and Caspian gateway gate exited 0; the checklist's legacy `test_gateway.py` path is named `test_caspian_gateway.py` in this repository.
- Scoped Ruff and diff checks passed. All Google/ADK output was deterministic and fake; no provider credential, live Gemini call, or billable cloud resource was used.
