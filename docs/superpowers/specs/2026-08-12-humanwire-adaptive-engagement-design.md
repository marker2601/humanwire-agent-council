# HumanWire Adaptive Stakeholder Engagement Design

**Date:** 2026-08-12
**Status:** Approved for implementation
**Product:** HumanWire

## 1. Decision

HumanWire does not interview every stakeholder for every mandate. It assigns the minimum engagement needed to obtain that person's required contribution.

A structured interview remains a core HumanWire capability, but it is one engagement type rather than the universal workflow. Notifications, acknowledgements, focused questions, approvals, and availability requests must not be presented as interviews.

This amendment supersedes any earlier wording that implies every stakeholder assignment creates a multi-question interview.

## 2. Chosen Approach

HumanWire automatically proposes an engagement type for each stakeholder, validates that proposal with deterministic policy, shows the initiator a compact preview, and permits an override before outreach leaves the queue.

The initiating mandate is the authorization to proceed within configured organizational policy. HumanWire therefore preserves the one-message experience:

1. The authorized initiator sends one mandate.
2. HumanWire returns the proposed people, reasons, directions, and engagement types immediately.
3. Initial outreach is queued for a short configurable preview window.
4. The initiator may change or cancel the plan during that window.
5. If no override arrives, the already-authorized mandate proceeds. This is not interpreted as stakeholder agreement or approval.

An organization may configure a stricter policy that requires an explicit `GO <token>` before outreach. Upward sponsorship, approval, proposal acceptance, and decision authority always require an explicit human response regardless of the preview policy.

### Alternatives not selected

- **Interview everyone:** maximizes evidence but creates fatigue, slows simple mandates, and makes the product feel mechanical.
- **Make the initiator choose every engagement manually:** offers control but defeats the chief-of-staff automation and increases setup friction.
- **Selected — automatic plan with preview and override:** keeps the one-message workflow while making the engagement proportionate and transparent.

## 3. Engagement Contract

`EngagementType` is independent of whether an assignment is required or optional.

| Engagement type | Intended use | Completion condition | Evidence/authority effect |
| --- | --- | --- | --- |
| `INFORM` | A person only needs visibility | Confirmed delivery | Produces no agreement, approval, or decision evidence |
| `ACKNOWLEDGE` | Receipt or sponsorship acknowledgement is required | Explicit authenticated acknowledgement | Proves receipt only; does not imply agreement |
| `QUICK_RESPONSE` | One or two focused facts are needed | Required focused answers recorded | May produce evidence with provenance |
| `STRUCTURED_INTERVIEW` | Several related facts, constraints, or concerns are needed | Required question plan completed or explicitly declined | May produce multiple evidence items with provenance |
| `REVIEW_APPROVAL` | A decision owner must accept, reject, or request changes | Explicit authenticated decision response | Silence, delivery, or acknowledgement never becomes approval |
| `AVAILABILITY` | Scheduling windows are needed | Explicit valid availability response | Produces availability only, not substantive agreement |

The planner may recommend an engagement type but cannot create destinations, invent authority, downgrade a required approver to `INFORM`, or turn silence into completion for a response-required assignment.

## 4. Selection Rules

Deterministic policy validates every proposed engagement:

- A decision owner or required approver uses `REVIEW_APPROVAL`.
- A stakeholder whose facts or constraints are needed uses `QUICK_RESPONSE` or `STRUCTURED_INTERVIEW` according to the number and dependency of the questions.
- A meeting attendee whose substantive input is already complete may use `AVAILABILITY`.
- A sponsor who only needs to confirm receipt uses `ACKNOWLEDGE`.
- A person who only needs awareness uses `INFORM`.
- An ambiguous role, missing authority record, or unclear required contribution pauses that assignment for initiator clarification; HumanWire does not guess.
- The model may propose fewer questions, but it cannot remove a deterministic required decision, authority check, or evidence obligation.

The preview shows, for each person: safe name, department, organizational direction, reason for contact, engagement type, whether a response is required, question count when applicable, and planned primary/alternate channel labels without exposing destinations.

## 5. Response and Follow-Up Policy

HumanWire follows up only when the engagement requires a response and remains unresolved.

- `INFORM`: send once; retry only for delivery failure according to transport policy. Do not chase acknowledgement.
- `ACKNOWLEDGE`: primary delivery, one reminder, then one registered alternate route when required.
- `QUICK_RESPONSE`: primary prompt, reminder, alternate route, then unresolved/unreachable according to assignment policy.
- `STRUCTURED_INTERVIEW`: use the existing authenticated multi-turn and cross-channel continuation behavior.
- `REVIEW_APPROVAL`: request an explicit approve/reject/change response; reminders and alternate routing never imply approval.
- `AVAILABILITY`: request structured windows; reminders and alternate routing never invent availability.

Optional assignments may stop after the configured primary/reminder policy without blocking synthesis. Required unresolved assignments block alignment or produce an explicitly partial outcome according to their contribution type.

## 6. State and Persistence

Each stakeholder assignment persists at least:

- `engagement_type`
- `response_required`
- `question_count` or engagement-specific progress
- engagement-specific completion reason
- preview/override provenance
- the existing required/optional, direction, routing, acknowledgement, attempt, and timing fields

Interview sessions exist only for `STRUCTURED_INTERVIEW` and, where the implementation reuses the question engine, `QUICK_RESPONSE`. Approval and availability responses remain their own authenticated domain records.

For compatibility with the already-built workflow, the internal mandate state `INTERVIEWING` may remain the coordination-phase storage value. Public views label that phase **Coordinating**. Existing append-only interview events remain historical truth; new generic engagement events must not rewrite or reinterpret them.

## 7. Completion and Synthesis

Mandate readiness is evaluated against required contribution contracts rather than a universal completed-interview count:

- Delivered `INFORM` assignments may be complete but provide no decision evidence.
- `ACKNOWLEDGE` satisfies receipt only.
- Required factual decisions need confirmed evidence from `QUICK_RESPONSE` or `STRUCTURED_INTERVIEW` assignments.
- Required approval needs an explicit `REVIEW_APPROVAL` response from the independently registered authority holder.
- Required scheduling needs valid `AVAILABILITY` data.
- Silence, delivery failure, acknowledgement alone, or an optional response never becomes agreement.

The synthesis engine receives only the evidence types relevant to the mandate and preserves the existing privacy and provenance rules.

## 8. Decision Room and Reach Changes

The user-facing lifecycle is:

`Received -> Planned -> Coordinating -> Synthesizing -> Aligning -> Meeting Ready`

The stored state may still be `INTERVIEWING`, while the public label and stable test hook use the coordination concept.

The Decision Room replaces universal **Interview progress** with **Engagement progress**. Every stakeholder row shows the persisted engagement type and its appropriate progress:

- delivery for `INFORM`
- acknowledgement for `ACKNOWLEDGE`
- answers for `QUICK_RESPONSE`
- question position for `STRUCTURED_INTERVIEW`
- decision state for `REVIEW_APPROVAL`
- availability state for `AVAILABILITY`

The selected response ladder adapts to the engagement. It must not display interview-only steps for notification, approval, or availability assignments. The current action still glows; completed actions remain calm green; future actions remain pending.

Propagation Lanes continue to represent organizational direction—Gather input, Coordinate policy, Get approval—while each person displays the correct engagement type.

## 9. Competition Demo Story

The approved `HW-2411` public story demonstrates that HumanWire is selective:

- Arun Patel sends the manager-originated mandate.
- Two team leads complete focused `QUICK_RESPONSE` engagements.
- Priya Shah is the only active `STRUCTURED_INTERVIEW`, currently using the registered alternate Telegram route.
- Nora Chen completes an upward `ACKNOWLEDGE` engagement.
- Maya Brooks has a pending `REVIEW_APPROVAL` engagement.
- A nonblocking stakeholder receives an `INFORM` engagement without unnecessary reminders.

The demo therefore proves real interviewing without suggesting that every organizational contact must complete an interview.

## 10. Safety, Errors, and Privacy

- Unknown or ambiguous engagement selection pauses before outreach.
- An initiator override is authorized and recorded as an append-only event.
- An override cannot assign approval authority to an unregistered person or add a destination.
- Private evidence remains excluded from previews, public views, logs, exports, and shared model prompts.
- Direct contact destinations remain opaque in the UI and APIs.
- Delivery failure remains delivery failure; it never satisfies a response-required engagement.
- Late responses receive a safe terminal-state explanation and do not silently reopen a closed engagement.

## 11. Acceptance Tests

Implementation must prove:

- one mandate can create multiple engagement types;
- the deterministic validator prevents unsafe downgrades;
- preview and authorized override occur before queued outreach;
- one-message auto-proceed follows configured policy without treating stakeholder silence as approval;
- `INFORM` produces no reminder or decision evidence after successful delivery;
- required acknowledgement, quick response, interview, approval, and availability each use their own completion rule;
- only unresolved response-required assignments enter the follow-up ladder;
- public APIs and the Decision Room show engagement progress without leaking destinations or private content;
- the demo contains only one active structured interview and still reaches a correct alignment or meeting-ready outcome;
- existing cross-channel interview correlation, two-round negotiation, and meeting-proof protections remain intact.

## 12. Scope Boundary

This amendment changes planning, assignment contracts, engagement orchestration, public projections, the deterministic demo, Decision Room labels, Reach details, analytics fields, and end-to-end tests.

It does not add a new communication channel, organization editor, calendar connector, or mutating competition web API.
