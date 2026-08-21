# HumanWire Unified Mission Modes Design

**Date:** 2026-08-20

**Status:** Approved for implementation planning

**Product:** HumanWire

**Visual map:** [HumanWire Demo and Connected Organization Architecture](https://www.figma.com/board/xYhDlC8q89w2hTGzkgovZT)

## 1. Decision

HumanWire will expose one mission workflow with two explicit operating modes:

1. **Demo run** uses Gemini agents and fictional AI stakeholders to demonstrate the full coordination workflow without contacting external people.
2. **Connected organization** uses the same Gemini agents as the preparation and synthesis team, then communicates with consented real participants through configured organization routes.

The product must not call the Demo run “fabricated” in ordinary interface copy. “Demo run” and visible AI identity labels are the concise truth boundary. The product must also never present a demo stakeholder as a real person or a simulated delivery as an external message.

Connected organization mode is not a passive directory view. When a consented route and provider are configured, HumanWire must send the outreach, receive and authenticate the reply, update the mission, and continue the workflow. When those requirements are absent, it must stop with a specific readiness reason and must not invent a delivery or response.

## 2. Product Promise

One authorized requester submits a decision, agenda, or mandate. HumanWire:

1. interprets the request and identifies the minimum required roles;
2. lets its Gemini specialist agents research organization evidence and prepare focused questions;
3. finds the people who hold the facts, constraints, commitments, or approval authority;
4. contacts them through registered routes according to urgency and policy;
5. follows up or escalates to another configured route when there is no response;
6. records authenticated replies as evidence without treating silence as agreement;
7. resolves safe disagreements or prepares a focused interview when needed;
8. gathers availability only after the substantive decision gate;
9. returns a decision brief, outstanding risks, exact approvals, agenda, meeting package, and audit trail.

The purpose is not to replace meaningful human judgment. It is to remove repeated status meetings and manual chasing so the final human discussion can focus on unresolved decisions.

## 3. Chosen Architecture

### Recommended: one mission orchestrator over existing services

A new mission layer will connect the existing DecisionOS Agent Council, organization graph and activation records, coordination workflow, Caspian gateway, meeting package, and projections. It will own mode selection and mission status, but it will not duplicate the state machines already responsible for authority, outreach, delivery, responses, negotiation, or scheduling.

The same `MissionRequest` contract will drive both modes. A mode-specific participant resolver is the only branch:

- `demo_run` resolves fictional AI stakeholders and injected demo routes;
- `connected_organization` resolves committed organization subjects, authority, consent, and configured external routes.

After resolution, both modes use the same engagement planner, response/evidence contracts, conflict logic, synthesis, and projection vocabulary. This keeps the demonstration representative of product behavior while preserving the external-delivery boundary.

### Alternatives not selected

- **Put communication tools directly inside every Gemini specialist.** This would make delivery authority difficult to audit, duplicate retry logic, and let model output choose destinations. Rejected.
- **Keep Agent Council and coordination as separate products with a manual handoff.** This preserves existing code but fails the intended one-request experience and continues the current product gap. Rejected.
- **Replace the existing workflow with a new agent framework.** This would discard mature authority, persistence, replay, and failure handling for little product value. Rejected.

## 4. Identity and Truth Boundaries

Every participant shown in a mission has an explicit machine-readable actor type:

- `ai_specialist`: a Gemini preparation, analysis, synthesis, or challenge agent;
- `demo_stakeholder`: a fictional AI stakeholder used only in Demo run;
- `organization_subject`: a real directory subject who has not yet activated access;
- `human_member`: an activated, authenticated organization participant.

The user interface uses natural labels:

- mode badge: **Demo run** or **Connected organization**;
- AI profiles: **AI specialist** or **AI stakeholder**;
- activated people: their organization display name and role;
- directory-only subjects: **Not connected**;
- invited subjects: **Invitation pending**.

The interface does not repeat legalistic disclosure text on every card. It gives one persistent mode badge and one short mode explanation near the mission controls. Exports retain the exact mode and actor types for auditability.

Demo run must never enqueue a provider call. Connected organization must never fall back to demo participants or simulated responses after it starts.

## 5. Mission Lifecycle

### 5.1 Compose

The requester enters the objective, desired timing, urgency, and any known constraints. The workspace shows a two-option mode selector before start:

- **Demo run — Explore with AI stakeholders. No external messages.**
- **Connected organization — Work with AI agents and consented people.**

Connected mode also shows readiness counts for eligible people, consented routes, and configured providers. The start action is disabled only when the mission cannot truthfully leave the workspace; the exact missing requirement is shown.

### 5.2 Plan

The mission orchestrator creates a role-and-evidence plan. Gemini specialists may recommend roles and questions, but deterministic policy validates:

- required decision authority;
- minimum necessary participants;
- permitted engagement type;
- organization and workspace binding;
- whether each route is consented and active;
- whether an external provider is configured for that route;
- whether an escalation route is allowed for the mission urgency.

No model may invent an address, user identity, organization role, approval, consent, availability window, or delivery receipt.

### 5.3 Engage

Demo run uses deterministic or model-assisted AI stakeholder policies behind the existing injected simulation gateway. The event stream must label the mission **Demo run** and the actors **AI stakeholders**.

Connected organization uses the existing durable engagement and Caspian gateway path. Initial scope supports the routes that already have real transport code:

- email;
- Telegram.

Additional adapters such as Google Chat, Slack, SMS, or telephony are separate provider integrations behind the same route interface. They are displayed as unavailable until configured and verified. A UI toggle alone never claims an adapter is working.

The escalation policy follows urgency and consent. It can send a reminder on the active route, then advance to the next registered route. It cannot call an unregistered number, message an unconsented account, or broaden the audience because a response is late.

### 5.4 Collect and resolve

Authenticated responses update the exact assignment that generated the outreach. HumanWire records safe evidence, source role, time, channel class, and authority effect. It distinguishes acknowledgement, factual response, interview answer, approval, rejection, change request, and availability.

When evidence conflicts, Gemini agents can summarize the disagreement and prepare targeted questions. The workflow may open a focused asynchronous interview. Voice is an optional future route: Gemini Live may conduct the conversation only after a telephony provider, consent, recording policy, and verified inbound identity are configured.

### 5.5 Decide and schedule

The Agent Council synthesizes collected evidence, shows uncertainty and dissent, and produces a draft recommendation. Required approval remains an explicit authenticated human action. Only after approval may HumanWire request availability and prepare the smallest useful meeting.

The first implementation keeps the existing ICS package. Direct Google Calendar or Meet creation is a later write integration and must remain visibly unavailable until OAuth scopes and a verified calendar writer are present.

## 6. Core Components

### Mission contracts

Create focused immutable models for:

- `MissionMode` (`demo_run`, `connected_organization`);
- `MissionActorType`;
- `MissionRequest`;
- `MissionReadiness`;
- `MissionParticipant`;
- `MissionSnapshot`;
- `MissionOutcome`.

The mission ID, organization ID, workspace ID, requester, mode, and created time are bound in every persisted snapshot. Mode cannot change after any outreach is prepared.

### Mission participant resolver

The resolver accepts an authorized context and plan. Demo resolution reads only the committed demo catalog. Connected resolution reads the committed organization graph and activation state. It returns only participants who satisfy the requested role and authority constraints. Contact destinations remain private and are passed directly to the delivery layer, never to Gemini prompts or public projections.

### Mission coordinator

The coordinator invokes the existing council runtime and workflow in a defined order, persists resumable state, and exposes one status stream. It translates existing events into stable mission stages rather than creating a competing event store.

### Provider adapters

The existing Caspian gateway remains the initial external transport adapter. The mission coordinator depends on a narrow transport capability interface and a readiness registry. Provider calls occur only after deterministic validation and durable delivery preparation. Provider exceptions become fixed safe status codes while private diagnostics stay server-side.

### Workspace projection

DecisionOS gains a mission composer and live mission workspace. It shows:

- the mode badge;
- AI specialists and participants with distinct actor labels;
- current stage and next automatic action;
- per-person engagement, delivery, response, and authority status;
- a synchronized conversation and evidence timeline;
- conflicts, unresolved questions, and approval gates;
- the decision brief and meeting package.

The existing organization onboarding and Agent Council surfaces remain available; the mission workspace becomes the primary product flow connecting them.

## 7. API Boundary

The first vertical slice adds tenant-bound routes following the existing authentication, App Check, CSRF, body-limit, canonical-model, and fixed-error rules:

- `POST /api/organizations/{organization_id}/workspaces/{workspace_id}/missions`
- `GET /api/organizations/{organization_id}/workspaces/{workspace_id}/missions/{mission_id}`
- `GET /api/organizations/{organization_id}/workspaces/{workspace_id}/missions/{mission_id}/events`

The create request includes `mode`, objective, timing, urgency, and conflict policy. It does not accept contact addresses, provider IDs, arbitrary actor IDs, or claimed authority from the browser. Connected participants are resolved server-side from the organization graph and consent state.

External provider webhooks remain on provider-specific authenticated endpoints and are normalized through the existing gateway before they reach workflow logic.

## 8. Failure and Recovery

The mission must fail closed with stable public reasons:

- `organization_not_ready`;
- `no_eligible_participant`;
- `participant_not_connected`;
- `no_consented_route`;
- `provider_not_configured`;
- `delivery_failed`;
- `delivery_state_unknown`;
- `response_expired`;
- `approval_required`;
- `mission_unavailable`.

Unknown delivery state is not retried blindly. A restart resumes from durable mission/workflow state and cannot create a second active outreach for the same assignment. Demo failures do not switch to Connected mode, and Connected failures do not switch to Demo run.

## 9. Security and Privacy

- Firebase authentication, organization membership, workspace authorization, App Check, and CSRF remain mandatory for mission mutations.
- Connected outreach requires subject activation, consented route metadata, and organization policy.
- Gemini receives only the minimum safe evidence and public role context required for its task.
- Raw addresses, tokens, provider payloads, private responses, and secrets never enter public JSON, model prompts outside their explicit policy, browser logs, exception messages, or exported projections.
- AI output cannot authorize a send, create a destination, approve a decision, or mark a delivery successful.
- Every external send and authenticated response has a durable correlation ID and audit event.

## 10. Delivery Scope

### Vertical slice required now

1. Add the persistent mode selector and truthful Demo run copy.
2. Introduce mission contracts and a tenant-bound mission service.
3. Run Gemini Agent Council in both modes.
4. Resolve Demo run AI stakeholders through the existing synthetic scenario.
5. Resolve Connected organization participants from committed, activated organization subjects.
6. Use configured Caspian email or Telegram routes for real outreach and reply ingestion.
7. Persist and render the shared mission timeline through decision brief or a specific blocked state.
8. Prove with tests that Demo run performs zero provider sends and Connected mode cannot fake success.

### Deferred integrations

- Google Chat and Gmail adapters independent of Caspian;
- Slack;
- telephony plus Gemini Live voice;
- direct Google Calendar and Meet writes;
- Microsoft 365 organization sync.

Deferred integrations may appear only as disabled roadmap items with exact configuration requirements. They are not submission claims until verified end to end.

## 11. Acceptance Criteria

1. A signed-in user can create a Demo run, see the Gemini agents and AI stakeholders work, and reach a saved outcome without any external provider call.
2. The UI and export identify the mode as `Demo run`/`demo_run` and AI actors as AI; ordinary copy does not use “fabricated.”
3. A signed-in organization owner can create a Connected organization mission only when an eligible activated participant, consented route, and configured provider exist.
4. A Connected organization mission performs a real adapter send in an injected integration test and records a durable delivery result.
5. An authenticated provider reply updates the correct participant assignment and becomes evidence for the mission.
6. AI specialists remain visible and active in Connected organization mode without gaining transport or approval authority.
7. No response triggers only the registered reminder/escalation policy; unregistered channels are never attempted.
8. Conflict, change request, approval, availability, and meeting-package ordering remain authoritative.
9. Browser refresh resumes the same selected mission and event state.
10. Desktop and 390 px mobile views expose all primary controls without clipping or horizontal page overflow.
11. Existing organization, Agent Council, studio, synthetic, gateway, and workflow compatibility suites remain green.
12. No live-provider claim is made until a consented, operator-controlled end-to-end verification succeeds.

## 12. Implementation Order

1. Mission models, repository, and mode invariants.
2. Demo resolver and zero-provider proof.
3. Connected resolver, readiness, and fail-closed provider boundary.
4. Council/workflow mission coordinator and durable event projection.
5. Mission composer and live workspace UI.
6. Browser, privacy, restart, hostile input, and real configured-route acceptance.
7. Deployment and submission evidence only after the product gate is green.
