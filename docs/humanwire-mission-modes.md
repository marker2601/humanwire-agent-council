# HumanWire mission modes

HumanWire turns one decision or agenda into a durable mission. The same Gemini Agent Council, evidence vocabulary, timeline, and decision-brief projection are used in both modes; only participant identity and delivery authority change.

## Demo run

`demo_run` is the submission-ready product walkthrough. Gemini specialists and clearly labeled AI stakeholders work through the mission, save ordered activity, and produce a decision brief. Demo run performs zero provider sends and never presents an AI stakeholder as a real person.

1. Sign in and open a decision workspace.
2. Select **Demo run**.
3. Enter the decision or agenda, choose urgency, and decide whether disagreement must be resolved.
4. Select **Start mission**.
5. Watch the AI specialists, AI stakeholders, saved timeline, and decision brief.

The interface uses the concise label **Demo run**. It does not claim real delivery or a real-person response.

## Connected organization

`connected_organization` keeps Gemini specialists active but replaces AI stakeholders with activated organization members from the committed organization graph. HumanWire checks participant eligibility, route consent, and provider readiness before Gemini begins. If any requirement is missing, the mission stops with an exact reason and does not silently switch to Demo run.

The first supported delivery adapter is Caspian email or Telegram. A connected deployment must provide all of the following outside browser configuration:

- an activated human subject bound to the current organization;
- an active, consented route bound to that subject;
- an explicitly configured server transport matching the route class;
- one authenticated inbound handler that normalizes the reply and calls `MissionService.record_response` for the exact outstanding participant assignment.

The browser never supplies an address, conversation identifier, token, or provider payload. Gemini never chooses a destination, authorizes a send, confirms delivery, or approves the final decision.

## Readiness states

| Code | Meaning | Safe operator action |
| --- | --- | --- |
| `no_eligible_participant` | No activated organization member can satisfy the mission | Complete organization onboarding or activation |
| `no_consented_route` | The member has no active consented route | Register consent through an approved private route workflow |
| `provider_not_configured` | A route exists but its server transport is absent | Configure the approved provider outside public JSON |
| `delivery_failed` | The provider reported a failed delivery | Review the provider-side failure before retrying |
| `delivery_state_unknown` | Delivery could not be proven | Reconcile state; never retry blindly |

The current Google Cloud deployment enables Demo run. Connected delivery remains fail-closed until a private route registry and real provider listener are configured and verified end to end.

## Local verification

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\humanwire\test_mission_models.py `
  tests\humanwire\test_mission_store.py `
  tests\humanwire\test_mission_participants.py `
  tests\humanwire\test_mission_transport.py `
  tests\humanwire\test_mission_service.py `
  tests\humanwire\test_mission_projection.py `
  tests\humanwire\test_decisionos_mission_app.py `
  tests\humanwire\test_decisionos_frontend.py -q

node --check src\humanwire\decisionos_static\decisionos-app.js
node tests\humanwire\decisionos_mission_harness.js
```

## Deferred channels

Google Chat, direct Gmail, Slack, telephony with Gemini Live, Google Calendar/Meet writes, and Microsoft 365 directory sync are roadmap integrations. They remain disabled and are not submission claims until each has its own consent, idempotency, privacy, and end-to-end delivery proof.
