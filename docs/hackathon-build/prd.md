# Product Requirements Document

## Product Summary

HumanWire is an autonomous decision-coordination product for executives and managers. A user gives HumanWire a consequential objective, identifies the relevant stakeholders, and starts one bounded workflow. Specialist agents then gather the minimum necessary input, surface and resolve disagreement, confirm evidence, revise the proposal when justified, obtain explicit approval, collect availability, and produce a meeting-ready package.

The experience must feel like an executive mission-control room rather than a chatbot. The user should always understand who is acting, why the workflow advanced, which facts are merely asserted versus confirmed, what authority remains missing, and whether the final outcome is safe to rely on.

Gemini and Google ADK provide adaptive planning and specialist reasoning. HumanWire's deterministic policy and authority rules remain responsible for identity, evidence confirmation, approval, scheduling eligibility, privacy, and irreversible workflow transitions.

## Target User

### Primary user: decision owner

An executive, manager, program lead, or other decision owner who needs a multi-stakeholder decision to reach a meeting-ready state without manually chasing participants.

Their needs:

- Start from a clear, editable objective rather than an open-ended prompt.
- See the right stakeholders engaged for the right reason.
- Trust that disagreement and missing evidence are not hidden.
- Know that approval and availability were requested only at the correct stage.
- Understand the current state without reading every message.
- Recover from refreshes, delays, and partial failures without losing truthful progress.

### Secondary user: stakeholder

A participant contributing an acknowledgement, evidence, objection, proposal response, approval, or availability. The stakeholder should receive only the engagement necessary for their role and should never be represented as having agreed when they did not.

### Secondary user: reviewer or judge

A person evaluating the system who needs a fast, inspectable proof that the agent performed real work, used the required Google stack, maintained authority boundaries, and produced a reproducible outcome.

## Product Principles

1. **Outcome over chat:** every agent interaction must advance, challenge, or safely stop the workflow.
2. **Minimum necessary interruption:** engage only stakeholders whose contribution is required at the current stage.
3. **Authority is explicit:** a model suggestion is never treated as identity, evidence confirmation, approval, or availability.
4. **Truth survives presentation:** live views, replay views, exports, and final outcomes must describe the same saved history.
5. **Failure is visible:** the product preserves completed work and explains when coordination stops; it never fabricates success or silently substitutes a different execution mode.
6. **The demo is the product:** the strongest four-minute path must also be a credible everyday product flow.

## Core User Journey

### 1. Compose the objective

The user opens HumanWire and sees a launch-decision template already populated with a realistic objective, requester role, timing, stakeholder selection, and conflict-handling choice. Nothing runs automatically.

The user may edit the objective, choose their role, select stakeholders, change timing, and decide whether disagreement should be resolved before approval. The interface explains the resulting five-stage workflow before the user starts.

### 2. Start coordination

The user selects **Start coordination** once. The product prevents duplicate starts, validates the request, and moves to a dedicated run workspace. The workspace immediately identifies that the run uses Gemini, Google ADK, and Google Cloud without showing credentials or internal secrets.

### 3. Follow autonomous execution

The live workspace shows:

- The current lifecycle stage and saved-event count.
- A graph of the executive, coordinator, specialist agents, stakeholders, evidence, proposal, approval, availability, and meeting package.
- The currently active path between the responsible participants or artifacts.
- Human-readable conversations appropriate for the selected event.
- Saved data labels and effects.
- A synchronized explanation of **From → To → Generated**.

The user may pause visual progression without stopping backend work, follow the latest event, or select a historical event while the run continues.

### 4. Resolve the decision

The default demonstration surfaces a legitimate objection before approval. HumanWire gathers targeted answers, confirms the required evidence, creates a proposal, records a justified revision, and requests approval only after those gates pass.

Availability is requested only after approval. The workflow becomes meeting-ready only when all required attendees have valid overlap. Every stage remains attributable and replayable.

### 5. Review the outcome

On completion, the user sees **Meeting package ready**, the final approved proposal, the relevant attendees and time, the completed lifecycle, and enabled JSON/CSV exports. The user can replay any event, download the public result, or start a new coordination without stale state carrying over.

## Epics And User Stories

### Epic 1: Clear, bounded request creation

- As a decision owner, I want a realistic template and editable fields so that I can start quickly without learning prompt syntax.
- As a decision owner, I want to see the planned stages before starting so that I understand what the system will do.
- As a decision owner, I want my conflict choice to change the actual workflow so that controls never make false promises.

Acceptance criteria:

- The initial page visibly contains an objective, requester role, timing, stakeholders, conflict option, and workflow preview.
- No run root or workspace is created before the user selects **Start coordination**.
- Required fields show specific, recoverable validation messages.
- Changing the template updates stakeholder selection and the visible count consistently.
- Disabling conflict resolution produces a completed workflow with no conflict, targeted-interview, rollback, or rejection events while still engaging the risk stakeholder truthfully.
- A second click or simultaneous request cannot create a second active run.

### Epic 2: Immediate, understandable live workspace

- As a decision owner, I want to see autonomous progress immediately so that I know the system is working.
- As a reviewer, I want each visible transition to identify its source, destination, and generated result so that agent action is inspectable.

Acceptance criteria:

- Starting a valid request navigates to a dedicated run URL and shows a non-empty lifecycle, graph, progress label, and live-status message.
- The first saved events appear without a manual page refresh.
- Exactly one active graph path is emphasized for a selected event that represents a connection.
- The selected graph path, conversation row, data row, lifecycle stage, and **From → To → Generated** explanation refer to the same saved event.
- The interface visibly distinguishes running, complete, and failed states.
- The runtime disclosure truthfully identifies Gemini/ADK/Google Cloud execution and does not imply external messages or real people when those are not part of this entry.

### Epic 3: Autonomous, authoritative decision workflow

- As a decision owner, I want agents to handle the coordination details so that I intervene only when genuine human authority is required.
- As a stakeholder, I want my contribution interpreted within my assigned role so that an acknowledgement is not mistaken for approval.
- As a reviewer, I want strict chronology so that the outcome cannot be produced before its evidence and authority gates.

Acceptance criteria:

- The coordinator creates and exposes a bounded plan before specialist work is shown.
- Outreach occurs before conflict or evidence resolution.
- The default story shows conflict before targeted interviews, confirmed evidence before the revised proposal, approval after proposal readiness, availability after approval, and the meeting package last.
- No early approval or availability conversation appears before the applicable gate.
- Asserted evidence is visibly different from confirmed evidence.
- A requested proposal change cannot be displayed as approval.
- Meeting readiness is impossible without current approval and valid required-attendee overlap.
- Model output that is late, malformed, unauthorized, or inconsistent becomes a visible rejected/no-response/error result and cannot mutate authority state.

### Epic 4: Controlled live following and historical replay

- As a decision owner, I want to pause visuals or inspect history without stopping autonomous work.
- As a reviewer, I want replay to preserve the exact saved chronology so that I can verify the final story.

Acceptance criteria:

- **Pause visuals** stops automatic visual advancement while backend progress may continue.
- **Follow Live** returns selection to the latest saved event.
- Previous, Next, Play, and Pause operate within the available ordinal range and expose accurate enabled/disabled states.
- Incoming snapshots do not erase or unexpectedly advance a manual historical selection.
- Returning to live mode cancels stale queued visual updates.
- A completed refresh selects the final event; a failed refresh preserves the last valid stage as failed, not completed.
- The selected conversation or data row is visibly highlighted and scrolled above sticky controls on desktop and mobile.

### Epic 5: Durable refresh and truthful recovery

- As a decision owner, I want to refresh or return to an active run so that ordinary browser behavior does not lose coordination.
- As a decision owner, I want failures to preserve completed work so that I can understand and retry safely.

Acceptance criteria:

- Refreshing a running or completed run URL restores the same public event prefix and current state.
- Repeated polling or delivery of the same saved event does not duplicate rows or advance the lifecycle twice.
- A delayed or failed agent cannot overwrite a newer accepted result.
- When execution stops, the workspace remains visible, the final status is **Failed** or **Coordination stopped**, and no incomplete stage is labeled completed.
- A retry action is offered only when it can create a new isolated run; it never rewrites the failed history.
- Fixed public error messages reveal no credentials, contact routes, private facts, identifiers, filesystem paths, or provider bodies.

### Epic 6: Meeting-ready result and portable proof

- As a decision owner, I want a concise meeting package so that the coordination work produces an actionable outcome.
- As a reviewer, I want exports to match the visible replay so that the evidence is portable and falsifiable.

Acceptance criteria:

- The final outcome shows the approved decision, selected attendees, meeting timing, and **Meeting package ready**.
- JSON and CSV remain disabled until the run is complete and both final bindings are valid.
- JSON and CSV include the same event rows, timeline ordinals, persisted ordinals, effects, and safe provenance.
- Spreadsheet-formula prefixes and control characters are neutralized in CSV.
- Exports contain no credentials, private facts, email addresses, provider message bodies, internal keys, commands, operational UUIDs, or file paths.
- Starting **New coordination** clears graph nodes, rows, outcome, progress, lifecycle, selection, download state, and queued playback before showing the composer.

### Epic 7: Accessible, responsive executive product

- As a user on desktop, tablet, or mobile, I want every required control and data view to remain usable.
- As a keyboard or reduced-motion user, I want equivalent control without animation dependence.

Acceptance criteria:

- The product has no page-level horizontal overflow at 1680×950, 1280×720, 600×900, or 390×844.
- All visible interactive targets have an effective hit area of at least 44×44 pixels.
- Meaningful text is at least 14 pixels and focus indicators remain visible.
- The full 17-node decision graph contains its labels and avoids material node/content collisions at desktop and mobile viewports.
- Mobile users retain Pause, replay, JSON, CSV, and New coordination controls; required actions are never hidden with `display: none`.
- Reduced-motion preference removes nonessential animation without changing functionality.
- Conversation and Data tabs preserve and reveal the selected event.

### Epic 8: Judge-visible Google proof

- As a judge, I want to see that Gemini, ADK, and Google Cloud are central to the working product rather than decorative claims.
- As a maintainer, I want proof that does not expose sensitive operational data.

Acceptance criteria:

- The product and repository name the actual Gemini model family used and the specialist responsibilities delegated through ADK.
- A complete run can be tied to a Cloud Run revision and durable cloud state without revealing project secrets.
- The demo visibly shows the deployed product plus concise Cloud Run, Firestore, Pub/Sub, or logging evidence.
- The architecture diagram matches the observed request, execution, persistence, projection, and UI flow.
- Local reproducibility instructions allow a reviewer to run the product with documented credentials or a safe test mode.
- No public page claims that an unconfigured provider, external message, or human interaction occurred.

## Edge Cases

### Request and ownership

- Empty or whitespace-only objective.
- Invalid timing or unsupported requester role.
- No selected stakeholders or a template/manual-selection mismatch.
- Duplicate clicks, concurrent starts, or a partially started worker.
- New request while another run owns the workspace.

### Model and agent behavior

- Gemini credentials missing or rejected.
- Agent deadline exceeded, cancellation ignored, or process terminated.
- Malformed structured output, unknown intent, unsafe content, or mismatched persona identity.
- One agent responds on time while another hangs.
- Late result arrives after a timeout result has already been committed.
- All stakeholders agree and the workflow must still advance to scheduling without inventing conflict.

### Cloud and persistence

- Pub/Sub redelivery or out-of-order delivery.
- Firestore transaction conflict or temporarily unavailable read.
- Cloud Run instance restart between start and completion.
- User refreshes before the first event, during queued visual playback, or immediately after terminal publication.
- Final snapshot visible before worker cleanup is complete.

### Replay and presentation

- Unchanged snapshot or not-modified response while a historical event is selected.
- Selected row would be hidden behind sticky controls.
- Mobile tab changes while a row in the other tab is selected.
- Failed terminal state after several successful stages.
- Event exists in the timeline but has no persisted ordinal.
- Long stakeholder role or Unicode content threatens graph geometry or privacy scanning.

### Completion and exports

- Final transcript binding missing or mismatched.
- JSON/CSV route requested while the run is still active.
- CSV cell begins with formula or control characters.
- User starts a new coordination after downloading one or both exports.

## What We Are Building

- One complete launch-decision workflow with conflict-enabled and conflict-disabled paths.
- Composer, live workspace, synchronized graph/conversation/data/lifecycle, replay, final package, and exports.
- Genuine Gemini and ADK agent participation bounded by HumanWire authority rules.
- Durable asynchronous execution and refresh-safe public progress.
- Clear failure, retry, and privacy behavior.
- Desktop and mobile accessibility.
- Judge-visible Google Cloud proof and reproducible documentation.

## What We Would Add With More Time

- Additional decision templates after the launch-decision path proves reliable.
- Real email, Telegram, Slack, or Teams delivery through explicitly authorized providers.
- Calendar writes following a separate confirmation and authorization boundary.
- Multi-tenant organizations, user accounts, retention policies, and administrative audit controls.
- Gemini Enterprise Agent Platform governance, agent registry, memory bank, and model-armor integrations.
- Additional Google models such as Veo, Lyria, or Gemma when they improve the product rather than decorate the submission.
- Analytics comparing time saved, stakeholder interruption count, recovery rates, and decision throughput across real deployments.

## Submission Proof Points

1. A deployed Cloud Run URL opens directly to the polished HumanWire composer.
2. One user action starts an autonomous, asynchronous workflow.
3. Gemini and ADK specialist work is visible and connected to saved product events.
4. The default chronology proves conflict → evidence → revision → approval → availability → meeting package.
5. Typed authority prevents premature or fabricated transitions.
6. Refresh and replay preserve the same durable history.
7. A selected event synchronizes graph, conversation, data, lifecycle, and explanation.
8. Meeting-ready JSON and CSV match the visible result and pass privacy checks.
9. The architecture diagram, Cloud Run revision, durable state, Pub/Sub flow, and logs agree with the demo.
10. The repository can be reproduced from a clean environment and clearly discloses reused HumanWire components.
