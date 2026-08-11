# HumanWire Product Design

**Date:** 2026-08-11  
**Status:** Approved for implementation
**Product:** AI chief of staff that interviews the organization  
**Target submissions:** Caspian Buildathon, ML Empowerment Build Challenge 2.0, and Build Beyond

## 1. Objective

HumanWire turns one leadership or management mandate into a traceable human-coordination workflow. It identifies the decisions that must be made, reaches the relevant people through registered email or Telegram routes, conducts short private interviews, records evidence with provenance, detects agreement and disagreement, proposes bounded compromises, and either produces an alignment brief or prepares the smallest useful meeting.

The product is not a chatbot that answers from generic knowledge. Its core behavior is to ask the real people who hold the facts, constraints, authority, or commitments required for a decision.

The competition demonstration must show a genuine multi-person, cross-channel workflow through one Caspian message handler. The live web experience must make the agent's autonomy understandable: viewers can see what finished, what is active, who has not responded, which follow-up route will be used, and what the agent will do next.

## 2. Product Positioning

### Name

**HumanWire**

### Tagline

**One mandate in. The organization interviewed. The decision made ready.**

### One-line pitch

Send HumanWire a mandate, and it interviews the right people across channels, resolves what it safely can, and prepares the exact decision or meeting your organization needs.

### Demo closing line

**Executives do not need another chatbot. They need an agent that can talk to the organization.**

### Differentiator

Most assistants summarize documents, draft messages, or schedule a meeting after the user has already gathered the necessary information. HumanWire performs the missing coordination layer: it finds the stakeholders, asks them targeted questions, follows up across channels, preserves each response as evidence, and moves the mandate toward alignment before a meeting is created.

Multi-channel communication is not a notification feature. It is the mechanism that lets the agent continue a human workflow when one channel goes unanswered.

### Primary users

- Executives issuing cross-functional mandates
- Managers coordinating upward, laterally, and downward
- Program and operations leaders gathering decisions from distributed teams
- Small organizations without a dedicated chief of staff or program office
- Coordinators who repeatedly chase responses and prepare alignment meetings

## 3. Product Principles

1. **Interview before answering.** HumanWire does not invent organizational facts that a real stakeholder can provide.
2. **Evidence before synthesis.** Every fact, constraint, commitment, disagreement, and availability claim retains its source.
3. **Silence is never agreement.** A missing response is shown as pending or unreachable.
4. **Autonomy must be visible.** The interface always shows the current stage, blocked participants, and next automatic action.
5. **Use the fewest necessary people.** The agent avoids organization-wide message blasts and meeting invitations.
6. **Respect organizational authority.** Downward outreach gathers input or commitments, lateral outreach coordinates, and upward outreach requests sponsorship or approval.
7. **Negotiate within limits.** AI proposals are labeled as drafts, require explicit human responses, and stop after two rounds.
8. **Preserve a technical record.** The visual workflow, event table, exports, and analytics API all read from the same saved source of truth.

## 4. Competition Fit

The same product is used for all three submissions, with a different emphasis in each narrative.

### Caspian Buildathon

- One Caspian handler receives and correlates both email and Telegram messages.
- The product performs real outbound interviews, follow-ups, alternate-channel escalation, acknowledgements, and final brief delivery.
- Its central value depends on communicating across multiple live channels.

### ML Empowerment Build Challenge 2.0

- A Featherless-hosted model performs structured mandate planning, interview-question generation, evidence extraction, semantic conflict detection, and bounded proposal drafting.
- The model empowers people by bringing overlooked constraints and frontline knowledge into decisions.
- HumanWire exposes model uncertainty and keeps human assertions distinct from generated suggestions.

### Build Beyond

- The project demonstrates a complete agentic workflow rather than a single prompt-response interaction.
- It combines communication, persistence, scheduling logic, visualization, exports, and external analytics readiness.
- The same core can support leadership mandates, interview coordination, negotiation, and meeting preparation.

The entrant has stated that organizers confirmed eligibility for the relevant events. Submission-specific wording and evidence will be finalized from the organizer-confirmed requirements before submission.

## 5. Success Criteria

HumanWire is competition-ready when all of the following are true:

1. An authorized person can create a mandate from Telegram or email.
2. The single Caspian handler receives the mandate, plans it, and sends real interview prompts through at least two live channels.
3. The planner identifies the objective, decisions, stakeholders, interview questions, deadline, and completion conditions in a validated schema.
4. A stakeholder can acknowledge and complete an interview through either email or Telegram.
5. A non-responder progresses through the configured response ladder: primary channel, reminder, alternate registered channel, and final unreachable/escalation result.
6. A person who changes channels continues the same interview context instead of restarting it.
7. Every extracted evidence item retains the stakeholder, source message, channel, timestamp, visibility, and confirmation status.
8. The system distinguishes agreement, disagreement, missing evidence, conflicting facts, hard constraints, and preferences.
9. The agent can run no more than two explicit negotiation rounds and records `ACCEPT`, `REJECT`, or `CHANGE` responses.
10. An aligned case produces an alignment brief; an unresolved case produces a meeting-ready package with attendees, availability, agenda, agreed facts, open decisions, and pre-read.
11. The Decision Room visibly advances through the workflow and shows the next automatic action.
12. The Reach page shows a clean Propagation Lanes visualization that works for any authorized initiating role.
13. The event table, CSV export, JSON API, and visualization are derived from the same persisted records.
14. Missing responses, delivery failures, and model failures never appear as agreement or approval.
15. Three consecutive live demonstration runs complete without database edits, staged-event manipulation, or code changes.

## 6. Scope

### Included

- One Caspian `on_message` handler for Telegram and email
- Authorized mandate initiation from either channel
- Seeded organization and stakeholder directory
- Registered primary and alternate contact routes
- Structured mandate planning through Featherless
- Role- and authority-aware stakeholder routing
- Private multi-turn stakeholder interviews
- Evidence extraction with provenance and visibility controls
- Response acknowledgement and cross-channel continuation
- Configurable non-response reminders and alternate-channel escalation
- Agreement, contradiction, missing-evidence, and constraint detection
- Maximum two-round negotiation loop
- Explicit proposal responses: accept, reject, or request changes
- Availability collection and meeting-ready package generation
- Meeting confirmation and reminder messages through Caspian
- Downloadable meeting brief and calendar-ready event details
- SQLite persistence with append-only domain events
- Decision Room with a glowing lifecycle step and next-action countdown
- Compact reach summary linking to a dedicated Reach page
- Propagation Lanes visualization for downward, lateral, and upward routes
- Separate technical data table, CSV export, and read-only JSON API
- Power BI-compatible Web/JSON or CSV access pattern
- Seeded competition demonstration scenario
- Health endpoints, structured logs, safe diagnostics, and automated tests

### Excluded from the competition build

- Automatic employee discovery from private corporate systems
- A full HRIS or organization-chart administration product
- Unrestricted cold outreach to unregistered contacts
- Autonomous hiring, firing, spending, or policy approval
- Unlimited negotiation loops
- General-purpose employee surveillance or sentiment scoring
- Recording private evidence in shared briefs without permission
- Automatic inference that silence means consent
- Native Google Calendar or Outlook mutation without an explicitly configured connector
- Video meetings, voice calls, and call transcription
- Billing, subscriptions, and multi-tenant enterprise administration
- A mobile application or browser extension

These exclusions keep the product demonstrable, safe, and clearly centered on real cross-channel coordination.

## 7. Primary User Experience

### Manager-originated demonstration

An authorized support manager sends this Telegram message:

```text
/mandate

Coordinate weekend launch coverage. Interview the US and APAC team leads,
confirm staffing-policy constraints with People, obtain the required sponsor
approval, and prepare the smallest useful meeting before Friday.
```

HumanWire immediately responds:

```text
Mandate HW-2411 created.

Objective: Prepare approved weekend launch coverage.
Required routes:
• Downward — team-lead availability and operating constraints
• Lateral — People policy and staffing constraints
• Upward — VP Support sponsorship and COO approval

I am starting 4 stakeholder interviews across registered email and Telegram routes.
Track progress: <Decision Room link>
```

HumanWire then:

1. Contacts the US team lead by email.
2. Contacts the APAC team lead by Telegram.
3. Contacts the People stakeholder by email.
4. Sends the evidence-backed approval request upward after sufficient input exists.
5. Records each delivery, acknowledgement, interview answer, and transition.

If the People stakeholder does not acknowledge the email, HumanWire sends one reminder and then contacts the person's registered Telegram route. An acknowledgement on Telegram resumes the same interview with the existing case and question state.

### Stakeholder interview

Each message explains the mandate, why the person was selected, the current question, and how the answer may be shared:

```text
HUMANWIRE INTERVIEW · HW-2411

Arun Patel requested a weekend launch-coverage plan.
You were contacted to confirm staffing-policy constraints.

Question 1 of 3:
What policy or notice-period constraint could prevent this plan?

Reply normally. Prefix your answer with:
SHAREABLE — may be attributed in the brief
ANONYMOUS — may be summarized without your name
PRIVATE — excluded from shared outputs
```

HumanWire may ask a targeted follow-up, but it does not exceed the planned question budget unless the stakeholder explicitly agrees.

### Negotiation

When responses conflict, HumanWire produces a labeled proposal:

```text
HUMANWIRE DRAFT PROPOSAL · HW-2411

Operations needs two-person weekend coverage.
People requires voluntary shifts and 72 hours' notice.

Proposal: staff one voluntary on-call pair this weekend and begin the
full rotating schedule next week.

Reply ACCEPT HW-2411, REJECT HW-2411, or CHANGE HW-2411 <request>.
```

Only explicit compatible responses produce alignment. After two unsuccessful rounds, the case moves to meeting preparation.

### Meeting-ready outcome

For unresolved disagreement, HumanWire gathers availability and produces:

- Proposed time and timezone
- Smallest attendee set needed to make the open decisions
- Purpose and decision owner
- Agreed facts
- Remaining disagreements
- Required decisions
- Evidence-linked pre-read
- Confirmation/reminder messages for each attendee
- Calendar-ready event details or downloadable `.ics` when supported

The competition build does not claim a meeting has been written into an external calendar unless a calendar connector is actually configured.

## 8. Workflow Lifecycle

### Mandate states

```text
RECEIVED
PLANNED
INTERVIEWING
SYNTHESIZING
NEGOTIATING
ALIGNED
MEETING_REQUIRED
SCHEDULING
MEETING_READY
PARTIAL
EXPIRED
CANCELLED
DELIVERY_FAILED
```

Terminal states are `ALIGNED`, `MEETING_READY`, `PARTIAL`, `EXPIRED`, `CANCELLED`, and `DELIVERY_FAILED`.

### Normal transitions

```text
RECEIVED -> PLANNED
PLANNED -> INTERVIEWING
INTERVIEWING -> SYNTHESIZING
INTERVIEWING -> PARTIAL
SYNTHESIZING -> ALIGNED
SYNTHESIZING -> NEGOTIATING
NEGOTIATING -> ALIGNED
NEGOTIATING -> MEETING_REQUIRED
MEETING_REQUIRED -> SCHEDULING
SCHEDULING -> MEETING_READY
SCHEDULING -> PARTIAL
```

Any nonterminal state may move to `CANCELLED` when the authorized mandate owner cancels. Defined timeout and delivery-failure transitions are handled explicitly. Terminal states cannot be reopened; a materially changed mandate creates a new case linked to the previous one.

### Stakeholder states

```text
NOT_CONTACTED
CONTACT_QUEUED
DELIVERED
AWAITING_ACKNOWLEDGEMENT
ACKNOWLEDGED
INTERVIEWING
COMPLETE
FOLLOW_UP_DUE
ALTERNATE_CHANNEL
DECLINED
UNREACHABLE
DELIVERY_FAILED
```

Stakeholder state is independent from mandate state. A mandate may continue with an explicitly recorded partial result when policy allows, but an unreachable required approver prevents an `ALIGNED` result.

## 9. Response Ladder

Each stakeholder has ordered, policy-controlled contact routes. The default competition ladder is:

1. Send the first interview prompt through the preferred registered channel.
2. Wait for an acknowledgement window.
3. Send one reminder on the same channel.
4. Wait for the follow-up window.
5. Send the same case context through the alternate registered channel.
6. Continue the interview on whichever registered route receives a valid acknowledgement.
7. If all permitted routes fail, mark the stakeholder `UNREACHABLE` and notify the mandate owner.

Production windows are configurable. Demonstration windows may be shortened so the behavior is visible during recording.

The response ladder guarantees:

- No duplicate active interviews for the same stakeholder and case
- No restart when the person switches channels
- No contact through an address or chat absent from the directory
- No escalation beyond the case's authority policy
- No interpretation of non-response as agreement
- A persisted event for every attempt and delivery result

## 10. Architecture

```text
Telegram or Email
       |
       v
Caspian CommClient
       |
       v
Single on_message handler
       |
       v
Authenticated message router
       |
       +------------------------ stakeholder reply / acknowledgement
       |
       +------------------------ status / cancel / proposal response
       |
       +------------------------ new mandate
                                        |
                                        v
                                Mandate planner
                                        |
                        +---------------+---------------+
                        |                               |
                        v                               v
              Organization directory          Authority/contact policy
                        |                               |
                        +---------------+---------------+
                                        |
                                        v
                              Interview orchestrator
                                        |
                      Caspian email / Telegram outreach
                                        |
                                        v
                                  Evidence ledger
                                        |
                                        v
                                  Alignment engine
                               +--------+---------+
                               |                  |
                               v                  v
                       Negotiation loop       Alignment brief
                               |
                               v
                         Meeting coordinator
                               |
                               v
                       Meeting-ready package
                               |
                  +------------+-------------+
                  |                          |
                  v                          v
          Decision Room + Reach       JSON/CSV analytics API
```

### Runtime shape

The Python application retains two runtime surfaces that share the same application services and database:

1. A long-running Caspian listener receives and sends Telegram and email messages.
2. A FastAPI application serves the Decision Room, Reach visualization, redacted data views, health routes, downloads, and read-only APIs.

The existing deployment can continue serving the public web experience, while the competition demonstration runs the real channel listener in an environment suitable for long-lived connections.

### Technology stack

- Python 3.12
- `caspian-sdk`
- FastAPI and Uvicorn
- Pydantic v2
- SQLAlchemy 2 and SQLite for the competition build
- Jinja2 with progressive vanilla JavaScript for the web experience
- Featherless through an OpenAI-compatible API
- `httpx` for model calls
- `pytest`, `pytest-asyncio`, and Ruff
- A production graph/layout component only where needed; Propagation Lanes remain standard responsive UI rather than a free-form org graph

## 11. Component Boundaries

### Caspian gateway

Connects email and Telegram once, normalizes messages, invokes the single application handler, and sends returned channel instructions. It contains no planning, evidence, or alignment policy.

### Authenticated message router

Deterministically handles `/mandate`, `/status`, `/cancel`, acknowledgement, interview replies, availability replies, and proposal responses before model invocation. It correlates replies using sender metadata, conversation context, and a case token.

### Organization directory

Stores people, roles, departments, reporting relationships, aliases, timezones, and registered contact routes. The competition build uses seeded configuration plus persisted route state. The model cannot create destinations.

### Authority and contact policy

Determines whether the initiator may create the mandate, which stakeholders may be contacted, which direction each route represents, and whether the case can continue without a particular response. Upward contacts receive an approval request rather than language implying the initiator has executive authority.

### Mandate planner

Produces a validated `MandatePlan` containing objective, required decisions, stakeholders or required roles, questions, deadline, completion conditions, and proposed outreach direction. Directory resolution and policy validation occur after model planning.

### Interview orchestrator

Creates interview sessions, sends questions, tracks acknowledgement and question position, processes channel switching, limits follow-ups, and invokes the response ladder. It is the only component allowed to advance stakeholder interview state.

### Evidence extractor and ledger

Transforms a reply into structured candidate evidence, asks for clarification when required, and stores only schema-valid items. The ledger preserves the raw-message reference, but shared views expose redacted or permitted content only.

### Alignment engine

Combines deterministic checks with model-assisted semantic comparison. It identifies compatible commitments, contradictory facts, resource/deadline conflicts, missing evidence, hard constraints, and preferences. It cannot mark agreement without explicit supporting evidence.

### Negotiation coordinator

Creates clearly labeled draft compromises, gathers explicit responses, records requested changes, and stops after two rounds. It cannot overwrite a hard constraint or private evidence.

### Meeting coordinator

Identifies the smallest decision-capable attendee set, gathers availability, calculates overlap, builds the agenda and pre-read, and sends confirmation/reminder messages. External calendar mutation is adapter-controlled and disabled unless configured.

### Decision Room and Reach views

Render saved state only. They do not independently alter mandate or stakeholder state. User actions such as cancel or retry call application services with authorization checks.

### Analytics API

Provides redacted, read-only mandate, outreach, event, and evidence-summary datasets as JSON or CSV. It never exposes contact credentials, raw private interview content, tokens, or secrets.

## 12. Domain Model

### Mandate

- Internal UUID and public `HW-` token
- Initiator identity, role, and origin conversation
- Objective and original redacted request
- Plan, deadline, completion conditions, and current state
- Current workflow step and next scheduled action
- Creation, update, completion, and expiration timestamps
- Parent mandate when the work is restarted or materially revised

### Stakeholder assignment

- Mandate, person, department, and required role
- Organizational direction: downward, lateral, upward, or external
- Reason for contact and decision authority
- Required versus optional status
- Primary and alternate opaque route references
- Current stakeholder state and active interview session
- Attempt, acknowledgement, response, and completion timestamps

### Interview session

- Question plan and current question index
- Channel history
- Acknowledgement and reply correlation data
- Follow-up count and next-response deadline
- Completion, decline, unreachable, or failure reason

### Evidence item

- Type: fact, constraint, concern, preference, commitment, availability, or decision
- Normalized statement and safe summary
- Source stakeholder, message reference, channel, and timestamp
- Visibility: shareable, anonymous, or private
- Confirmation: asserted, clarified, confirmed, disputed, or withdrawn
- Optional related decision, deadline, resource, or proposal

### Alignment issue

- Type: agreement, contradiction, resource conflict, deadline conflict, missing evidence, or authority gap
- Related evidence IDs
- Affected stakeholders and decision
- Severity and blocking status
- Resolution or open-state explanation

### Proposal

- HumanWire-generated text labeled as a draft
- Evidence and issues it attempts to reconcile
- Round number, maximum two
- Stakeholder responses and requested changes
- Final compatible, rejected, or unresolved status

### Meeting package

- Purpose, decision owner, and required decisions
- Required and optional attendees
- Availability responses and proposed timezone-aware slot
- Agreed facts, open issues, agenda, and pre-read
- Confirmation and reminder delivery states
- Calendar-ready metadata and optional generated artifact link

### Append-only event

- Event UUID, mandate token, event type, and timestamp
- Initiator, actor, stakeholder, department, and safe route metadata
- Channel, organizational direction, previous state, and new state
- Safe outcome metadata and source correlation ID
- Idempotency key

## 13. Evidence and Privacy Rules

### Visibility modes

| Mode | Shared brief | Negotiation | Technical event log |
| --- | --- | --- | --- |
| `SHAREABLE` | May be attributed | May be quoted or summarized safely | Safe metadata plus permitted summary |
| `ANONYMOUS` | May be summarized without identity | May inform a proposal without attribution | Identity restricted in shared views |
| `PRIVATE` | Excluded | Content cannot be revealed; may only flag that a private blocker exists | Event type only; content excluded |

The competition build clearly explains that database administrators can access stored application data; it does not make unsupported end-to-end-encryption claims.

### Model boundaries

- Model output is schema-validated and treated as untrusted.
- The model may suggest roles but cannot create contact destinations.
- The model may draft a proposal but cannot record acceptance.
- The model may detect a possible conflict but cannot silently change evidence.
- Private content is excluded from shared synthesis prompts.
- Suspicious user content is delimited as data and cannot invoke tools or change policy.
- Deterministic fallback keeps the workflow safe when the model is unavailable.

## 14. Decision Room

The primary dashboard answers four questions immediately:

1. What mandate is being handled?
2. Which lifecycle stage is active?
3. Who is blocking or awaiting action?
4. What will HumanWire do next?

### Main components

- Mandate title, initiator, token, deadline, and live status
- Compact lifecycle stepper:
  `Received -> Planned -> Interviewing -> Synthesizing -> Negotiating -> Meeting Ready`
- Glowing active step; completed steps are calm green; future steps are visibly pending
- Stakeholder list with department, channel, acknowledgement, interview progress, and last contact
- Selected stakeholder response ladder
- Next-automatic-action countdown
- Evidence counts, unresolved issues, and missing required responses
- Append-only activity timeline
- Compact Reach summary linking to the dedicated visualization
- Clear labels separating human evidence from AI-generated proposals

The dashboard must remain legible during a recorded demonstration and on a narrow in-app browser surface.

## 15. Reach Visualization

### Approved primary design: Propagation Lanes

The main visualization is not a traditional organization chart. It begins with the initiating person's mandate and separates actual outreach into three large, readable routes:

1. **Gather input** — downward outreach to people closest to the work
2. **Coordinate policy** — lateral or cross-department outreach
3. **Get approval** — upward outreach to sponsors and decision owners

Each lane shows only relevant people, ordered contact steps, channel used, current state, response time, and lane-level completion. The current contact glows; finished contacts display complete; future contacts remain pending. The layout is three columns on wide screens and three stacked cards on narrow screens.

### Interaction

- Replay the outreach journey from the initiating message
- Filter by completed, active, pending, or unreachable
- Select a person to inspect the saved contact history
- Open the matching technical rows in the Data Table view
- Switch to an optional full-organization context view only when required

The traditional hierarchy is not part of the primary competition story. It may be a later contextual view, but it cannot make the default Reach page cramped.

## 16. Technical Data and Analytics

The Data Table is a separate tab so technical detail does not crowd the visual narrative.

### Outreach-event fields

- Mandate ID and public token
- Initiator ID and role
- Source and target department/person IDs
- Organizational direction
- Channel and opaque route type
- Event type and timestamp
- Delivery, acknowledgement, interview, proposal, and completion status
- Response latency
- Safe evidence references
- Previous and new state
- Persisted/idempotency status

### Read-only endpoints

```text
GET /api/v1/mandates
GET /api/v1/mandates/{token}
GET /api/v1/mandates/{token}/stakeholders
GET /api/v1/mandates/{token}/outreach-events
GET /api/v1/mandates/{token}/evidence-summary
GET /api/v1/mandates/{token}/outreach-events.csv
```

The public demonstration exposes a redacted fixture or allowed case view. Full analytics endpoints require a read-only token in non-demo environments.

Power BI and similar tools can consume the authenticated JSON endpoint through a Web connector or import the scheduled/on-demand CSV export. HumanWire does not expose the operational SQLite file directly.

## 17. Security and Trust Model

### Trust boundaries

- Incoming mandate and interview text is untrusted.
- Model output is untrusted until schema and policy validation succeed.
- Contact routes come only from the registered directory.
- A channel reply proves control of the registered account route, not absolute legal identity.
- The initiator's role does not automatically grant authority over every recipient.
- Dashboard and API consumers see only data permitted by their view policy.

### Safety invariants

- No unknown or model-generated contact destination is used.
- No missing response becomes agreement, acceptance, or approval.
- No proposal becomes a decision without explicit compatible responses.
- No private evidence appears in shared output.
- No third negotiation round is started automatically.
- No required approver may be bypassed to report `ALIGNED`.
- No duplicate incoming message creates duplicate mandates or outreach.
- No terminal mandate state silently reopens.
- No dashboard visualization changes source-of-truth state.
- No secret, raw token, complete contact address, or private content appears in logs or analytics exports.

### Authorization

The competition build uses an allowlist of initiators and stakeholders. Each mandate policy specifies permitted departments, upward approval boundaries, and whether optional stakeholders may be added. Unauthorized senders receive a safe rejection without directory disclosure.

## 18. Failure Handling

- **Model timeout or malformed output:** use deterministic fallback, record `MODEL_FALLBACK_USED`, and require clarification when a safe plan cannot be built.
- **Unknown stakeholder or ambiguous role:** pause the affected assignment and ask the authorized initiator to choose; do not guess a destination.
- **Primary-channel delivery failure:** advance only to a registered alternate route allowed by policy.
- **No acknowledgement:** follow the response ladder, then mark `UNREACHABLE`.
- **Required stakeholder unreachable:** prevent `ALIGNED`; produce a partial result or meeting/owner escalation.
- **Duplicate delivery or message:** return existing state using idempotency keys.
- **Late response:** explain that the interview or proposal round is closed and show the current case state.
- **Contradictory facts:** preserve both as disputed; do not collapse them into a fabricated consensus.
- **No common availability:** request additional windows once, then return a meeting-ready package with the scheduling conflict visible.
- **Database error:** emit safe structured diagnostics and stop state advancement.
- **Dashboard failure:** channel orchestration continues because the web UI is not the authority path.

## 19. Demonstration Story

### Core 75–90 second sequence

1. A support manager sends one real Telegram mandate.
2. HumanWire creates the plan and the Decision Room advances to `INTERVIEWING`.
3. Real interview prompts reach stakeholders through email and Telegram from the same Caspian handler.
4. Two stakeholders respond; their lane steps turn complete.
5. One email receives no acknowledgement; the response ladder visibly moves to registered Telegram.
6. The person acknowledges on Telegram and continues the same interview.
7. HumanWire shows one policy conflict and sends a labeled compromise proposal.
8. If the proposal is accepted, show the alignment brief. For the primary dramatic path, keep one disagreement unresolved and advance to meeting preparation.
9. HumanWire shows the proposed time, smallest attendee set, agenda, agreed facts, and open decision.
10. Show the saved activity table and the JSON/CSV/Power BI access point.

### Judge takeaway

One message caused real interviews, a channel failover, evidence-backed synthesis, bounded negotiation, and a meeting-ready decision package. The web interface did not simulate the workflow; it visualized events produced by the live channels.

## 20. Testing Strategy

### Unit tests

- Command parsing and authorization
- Mandate-plan schema validation and deterministic fallback
- Directory resolution and authority policy
- Downward, lateral, and upward route classification
- Mandate and stakeholder state transitions
- Response-ladder timing and alternate-channel selection
- Cross-channel acknowledgement and interview continuation
- Evidence visibility, confirmation, redaction, and provenance
- Conflict, missing-evidence, and hard-constraint classification
- Proposal round limit and explicit response parsing
- Availability overlap and smallest-attendee selection
- Event idempotency and terminal-state immutability
- Analytics redaction and CSV serialization

### Integration tests

- Telegram mandate produces both Telegram and email interview instructions
- Email mandate can produce Telegram and email assignments
- Stakeholder replies correlate to the correct case and question
- Alternate-channel acknowledgement resumes the existing interview
- Missing required response prevents false alignment
- Compatible proposal responses produce `ALIGNED`
- Unresolved proposal responses produce `MEETING_REQUIRED`
- Availability replies produce a deterministic meeting-ready package
- Decision Room and Reach APIs reflect persisted events without mutating them
- Model failure still produces safe, explainable behavior

### Live verification

Before recording:

1. Run the manager-originated scenario three consecutive times.
2. Exercise both email and Telegram as origin channels.
3. Demonstrate one same-channel reminder and one alternate-channel escalation.
4. Confirm all visible dashboard events correspond to real channel events.
5. Export the event table as CSV and load the JSON endpoint.
6. Verify no secret or private answer appears in logs, exports, or public pages.

## 21. Observability

Structured logs include mandate token, event type, state transition, safe person/department reference, organizational direction, channel, attempt number, duration, and failure reason. They exclude raw private content, full email addresses, Telegram tokens, API keys, and recovery credentials.

The FastAPI service exposes:

- `/health/live` for process health
- `/health/ready` for database, required configuration, and channel readiness
- Redacted mandate and outreach APIs
- Structured error responses with correlation IDs

The Decision Room event stream is built from the append-only event store rather than transient UI state.

## 22. Reuse and Replacement Strategy

The existing SecondSignal implementation supplies proven infrastructure but not the new product domain.

### Reuse or adapt

- Caspian email and Telegram connection bootstrap
- Single-handler gateway pattern
- Incoming-message normalization
- Configuration and secret-loading pattern
- SQLite engine and repository transaction pattern
- Append-only event-store pattern
- State-machine discipline and idempotency approach
- Featherless OpenAI-compatible client and schema-validation approach
- FastAPI deployment surface
- Structured logging, health checks, and test fixtures

### Replace

- SecondSignal branding and verification-case terminology
- `/verify` command and YES/NO verification workflow
- Risk-analysis schema and scam-specific rules
- Identity-verification state machine
- Verification receipts and security dashboard
- Existing demo fixtures, submission copy, and threat model

The final repository presents HumanWire as one coherent product. SecondSignal-specific behavior does not remain as a confusing alternate mode unless deliberately preserved in a clearly separated archive.

## 23. Delivery Priorities

Implementation follows this order:

1. Define the new mandate, stakeholder, interview, evidence, proposal, meeting, and event models with tests.
2. Prove real mandate-to-interview routing through one Caspian handler and both channels.
3. Complete acknowledgement, multi-turn interview, and alternate-channel continuation.
4. Add structured Featherless planning and evidence extraction with safe fallback.
5. Add deterministic synthesis, two-round negotiation, and meeting-ready preparation.
6. Build the Decision Room and live lifecycle/event views.
7. Build the responsive Propagation Lanes Reach page.
8. Add the separate data table, CSV, and JSON analytics API.
9. Add full safety, failure, integration, and live-smoke verification.
10. Update public documentation, demo script, submission copy, and deployed experience.

No optional organization chart, calendar connector, additional channel, or visual flourish may delay the reliable live interview-and-coordination loop.

## 24. Final Product Boundary

HumanWire is complete for the competition when an authorized person can issue one real mandate and watch it progress—through real people and real communication channels—to either explicit alignment or a meeting-ready decision package, with every action visible and auditable.

The product does not claim to replace management judgment. It removes the repetitive coordination work required to bring trustworthy human evidence to that judgment.
