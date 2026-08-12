# HumanWire Adaptive Engagement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make HumanWire choose and execute the minimum necessary engagement for each stakeholder while preserving real cross-channel interviews, explicit human authority, the one-message mandate experience, and competition-ready public proof.

**Architecture:** Add an engagement contract to planned stakeholders and durable assignments, validate model suggestions with deterministic policy, and route each assignment through a type-aware coordinator that reuses the proven interview machinery only for quick responses and structured interviews. Keep `MandateState.INTERVIEWING` as the stored compatibility state while public views call the phase `Coordinating`; append-only events remain the source of truth. Close the existing Task 11 calendar-UID blocker first, then update projections, deterministic demo, Decision Room, Propagation Lanes, analytics, and integration proof around the same persisted engagement data.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2, SQLite, FastAPI, Jinja2, vanilla JavaScript/CSS, Caspian SDK, Featherless JSON model adapter, pytest, Ruff.

## Global Constraints

- HumanWire assigns the minimum necessary engagement per stakeholder; it does not interview everybody.
- The supported engagement types are `INFORM`, `ACKNOWLEDGE`, `QUICK_RESPONSE`, `STRUCTURED_INTERVIEW`, `REVIEW_APPROVAL`, and `AVAILABILITY`.
- The initiating mandate authorizes policy-compliant auto-release after a short preview; strict deployments may require explicit `GO <token>`.
- Only the original authorized initiator may release early, override an engagement, or cancel the mandate.
- A model may recommend an engagement but cannot create destinations, invent authority, downgrade a deterministic required contribution, approve a decision, or mutate state directly.
- Silence, delivery success, acknowledgement, delivery failure, ambiguity, and optional responses never become agreement or approval.
- `INFORM` produces no reminder after successful delivery and creates no decision evidence.
- `REVIEW_APPROVAL` always requires an explicit authenticated approve, reject, or change response from the independently registered stakeholder.
- Only unresolved response-required assignments enter the reminder and alternate-channel ladder.
- Negotiation stops after two rounds.
- Private evidence content is excluded from previews, public views, logs, exports, shared model prompts, and generated calendar artifacts.
- The Decision Room and APIs visualize persisted state; they do not become an alternate source of truth.
- Public lifecycle copy uses `Coordinating` and `Engagement progress`; it must not call every stakeholder contact an interview.
- Propagation Lanes remain the default Reach visualization; no traditional org chart is required.
- External calendar mutation remains disabled unless a real connector is configured.
- Build and deploy a fresh HumanWire database; no task silently reinterprets or destructively migrates an existing local database.
- Preserve `secondsignal` and `src/index.py` until the integration gate passes.
- Never commit `.env`, `.env.local`, `.vercel`, `data/organization.json`, databases, keys, tokens, direct contact destinations, or private interview content.
- Run Python commands with `.\.venv\Scripts\python.exe` from repository root.
- Every task captures a genuine RED before production edits, runs focused and relevant regression tests, appends its SDD report, receives independent review, and ends in one focused commit.
- Before deployment or completion, run the full suite, Ruff, `git diff --check`, offline smoke, and browser verification.

## File and Responsibility Map

```text
src/humanwire/domain.py                 Engagement enums and durable domain contracts
src/humanwire/database.py               SQLAlchemy engagement/decision columns and records
src/humanwire/repository.py             Domain-record mapping and engagement queries
src/humanwire/engagement_policy.py      Deterministic type selection and override validation
src/humanwire/planning.py               Rule/model engagement suggestions and safe resolution
src/humanwire/engagements.py             Type-aware start, response, retry, and completion coordinator
src/humanwire/interviews.py              Authenticated multi-turn engine for quick/interview types only
src/humanwire/messages.py                Engagement-specific channel copy
src/humanwire/commands.py                GO, ENGAGE, DECIDE, ACK, answer, and availability parsing
src/humanwire/services.py                Preview, release, assignment creation, and contribution readiness
src/humanwire/workflow.py                One-handler routing, due work, callbacks, and synthesis orchestration
src/humanwire/web.py                     Redacted engagement projections and RFC-safe calendar export
src/humanwire/demo.py                    Exact deterministic adaptive public story
src/humanwire/templates/*.html           Decision Room, Reach, and analytics views
src/humanwire/static/*                    Responsive presentation, polling, replay, and filters
scripts/smoke_humanwire.py                Offline full-product proof
tests/humanwire/*                         Focused and integration contracts
```

---

### Task 1: Close the RFC-Safe Calendar UID Amendment

**Files:**

- Modify: `src/humanwire/web.py`
- Modify: `tests/humanwire/test_web.py`
- Append report: `.superpowers/sdd/2026-08-11-humanwire/task-11-report.md`

**Interfaces:**

- Consumes: a raw meeting UUID after package, authority, availability, evidence, and creation-event proof have all succeeded.
- Produces: `_public_calendar_uid(meeting_id: str, denied_values: frozenset[str]) -> str` whose private-collision form is deterministic, non-reversible, unique, ASCII-safe, and short enough that the complete `UID:` content line is at most 75 UTF-8 octets.

- [ ] **Step 1: Write the failing RFC-length regression**

Add focused tests that calculate the expected unpadded Base64URL digest and check two denied IDs:

```python
def expected_private_uid(meeting_id: str) -> str:
    digest = hashlib.sha256(b"humanwire:calendar:uid:v1:" + meeting_id.encode("ascii")).digest()
    token = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"{token}@humanwire.local"


def test_private_calendar_uid_is_unique_stable_and_below_rfc_fold_limit() -> None:
    first = _public_calendar_uid(MEETING_A, frozenset({MEETING_A}))
    second = _public_calendar_uid(MEETING_B, frozenset({MEETING_B}))
    assert first == expected_private_uid(MEETING_A)
    assert first == _public_calendar_uid(MEETING_A, frozenset({MEETING_A}))
    assert first != second
    assert len(f"UID:{first}".encode("utf-8")) <= 75
    assert MEETING_A not in first
```

Retain the route-level test proving raw meeting proof fails before UID derivation and the resulting ICS has intact `BEGIN:VCALENDAR`, `BEGIN:VEVENT`, `UID`, and `END:VCALENDAR` lines.

- [ ] **Step 2: Run the focused test and capture RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "calendar_uid or denied_meeting_ids" -v
```

Expected: FAIL because the hexadecimal digest makes the `UID:` line 84 octets.

- [ ] **Step 3: Implement unpadded Base64URL encoding**

Change only the private-collision branch:

```python
digest = hashlib.sha256(_CALENDAR_UID_NAMESPACE + meeting_id.encode("ascii")).digest()
token = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
return f"{token}@humanwire.local"
```

Do not shorten the digest, introduce a secret, use randomness, or derive the UID before raw proof succeeds.

- [ ] **Step 4: Verify Task 11 and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py tests\humanwire\test_demo.py -v
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py tests\humanwire\test_web.py
git diff --check
```

Append the approved amendment evidence to the existing Task 11 report, commit, generate a bounded review package, and obtain a clean independent re-review.

```powershell
git add src/humanwire/web.py tests/humanwire/test_web.py
git commit -m "fix: keep HumanWire calendar UIDs RFC-safe"
```

---

### Task 2: Persist the Adaptive Engagement Contract

**Files:**

- Modify: `src/humanwire/domain.py`
- Modify: `src/humanwire/database.py`
- Modify: `src/humanwire/repository.py`
- Modify: `src/humanwire/state_machine.py`
- Modify: `tests/humanwire/test_repository.py`
- Modify: `tests/humanwire/test_state_machine.py`
- Modify: constructors in `tests/humanwire/` only where required by the explicit new assertions

**Interfaces:**

- Produces: `EngagementType`, `EngagementDecisionKind`, `EngagementDecision`, engagement fields on `PlannedStakeholder` and `StakeholderAssignment`, and repository methods `add_engagement_decision`, `get_engagement_decision`, and `list_engagement_decisions`.
- Preserves: existing `InterviewSession` for `QUICK_RESPONSE` and `STRUCTURED_INTERVIEW`; existing rows/default constructors deserialize as structured interviews during the transition.

- [ ] **Step 1: Write failing domain and repository tests**

Add exact round-trip coverage:

```python
def test_assignment_round_trip_preserves_engagement_contract(repository, assignment) -> None:
    value = assignment.model_copy(update={
        "engagement_type": EngagementType.ACKNOWLEDGE,
        "response_required": True,
    })
    repository.add_assignment(value)
    assert repository.get_assignment(value.assignment_id) == value


def test_engagement_decision_is_idempotent_and_queryable(repository, decision) -> None:
    repository.add_engagement_decision(decision)
    repository.add_engagement_decision(decision)
    assert repository.get_engagement_decision(decision.assignment_id) == decision
    assert repository.list_engagement_decisions(decision.mandate_id) == [decision]
```

Add validation cases proving `INFORM` cannot have `response_required=True`, quick responses have one or two questions, structured interviews have three to five, and approval/availability assignments have no interview questions.

- [ ] **Step 2: Run the focused tests and capture RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_repository.py tests\humanwire\test_state_machine.py -v
```

Expected: FAIL because engagement types, columns, records, and repository methods do not exist.

- [ ] **Step 3: Add exact domain types**

Implement:

```python
class EngagementType(StrEnum):
    INFORM = "inform"
    ACKNOWLEDGE = "acknowledge"
    QUICK_RESPONSE = "quick_response"
    STRUCTURED_INTERVIEW = "structured_interview"
    REVIEW_APPROVAL = "review_approval"
    AVAILABILITY = "availability"


class EngagementDecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CHANGE = "change"
```

`PlannedStakeholder` gains `engagement_type`, `response_required`, and zero-to-five questions with a model validator enforcing the table in the approved design. `StakeholderAssignment` gains the same first two fields. Use compatibility defaults of `STRUCTURED_INTERVIEW` and `True` only at the domain/record boundary; every newly planned assignment must set both explicitly.

Add:

```python
class EngagementDecision(BaseModel):
    decision_id: UUID
    mandate_id: UUID
    assignment_id: UUID
    stakeholder_id: str
    response: EngagementDecisionKind
    change_text: str | None = Field(default=None, max_length=400)
    source_message_id: str
    created_at: datetime
    idempotency_key: str
```

- [ ] **Step 4: Add database mapping and safe transitions**

Add non-null assignment columns with explicit SQL defaults for fresh schema construction, a unique decision record keyed by `idempotency_key`, and repository mappings. Do not mutate any existing on-disk database in this task.

Allow these additional assignment transitions:

```text
DELIVERED -> COMPLETE                  inform delivery confirmed
AWAITING_ACKNOWLEDGEMENT -> COMPLETE  explicit acknowledgement/decision
ACKNOWLEDGED -> COMPLETE              acknowledgement-only completion
```

The transition method still sets `completed_at` only for terminal states and never completes from delivery failure or silence.

- [ ] **Step 5: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_repository.py tests\humanwire\test_state_machine.py -v
.\.venv\Scripts\python.exe -m pytest tests\humanwire -q
.\.venv\Scripts\python.exe -m ruff check src\humanwire\domain.py src\humanwire\database.py src\humanwire\repository.py src\humanwire\state_machine.py tests\humanwire
git diff --check
git add src/humanwire/domain.py src/humanwire/database.py src/humanwire/repository.py src/humanwire/state_machine.py tests/humanwire
git commit -m "feat: persist adaptive stakeholder engagements"
```

---

### Task 3: Select Engagements with Deterministic Policy

**Files:**

- Create: `src/humanwire/engagement_policy.py`
- Modify: `src/humanwire/planning.py`
- Create: `tests/humanwire/test_engagement_policy.py`
- Modify: `tests/humanwire/test_planning.py`

**Interfaces:**

- Produces: `EngagementPolicy.select(stakeholder, *, objective, required_decisions) -> PlannedStakeholder` and `EngagementPolicy.validate_override(current, requested) -> EngagementType`.
- Consumes: resolved people/directions only; it never reads or creates contact destinations.

- [ ] **Step 1: Write the failing policy matrix**

Cover the complete deterministic matrix:

```python
@pytest.mark.parametrize(("reason", "required", "questions", "expected"), [
    ("Keep Finance informed.", False, [], EngagementType.INFORM),
    ("Acknowledge sponsorship of the rollout.", True, [], EngagementType.ACKNOWLEDGE),
    ("Confirm the deployment date.", True, ["Which date is committed?"], EngagementType.QUICK_RESPONSE),
    ("Gather facts and constraints.", True, ["Fact?", "Constraint?", "Commitment?"], EngagementType.STRUCTURED_INTERVIEW),
    ("Approve the launch decision.", True, [], EngagementType.REVIEW_APPROVAL),
    ("Provide meeting availability.", True, [], EngagementType.AVAILABILITY),
])
def test_policy_selects_minimum_engagement(
    policy, reason, required, questions, expected
) -> None:
    candidate = PlannedStakeholder(
        person_ref="stakeholder",
        reason=reason,
        direction=Direction.LATERAL,
        required=required,
        engagement_type=expected,
        response_required=expected is not EngagementType.INFORM,
        questions=questions,
    )
    selected = policy.select(
        candidate,
        objective=reason,
        required_decisions=["Complete the stated mandate"],
    )
    assert selected.engagement_type is expected
    assert selected.response_required is (expected is not EngagementType.INFORM)
```

Add tests that a model-suggested `INFORM` cannot downgrade explicit approval or required questions, an optional zero-question assignment is not upgraded to an interview, and overrides cannot change required approval/evidence/availability into `INFORM` or `ACKNOWLEDGE`.

- [ ] **Step 2: Run and capture RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_engagement_policy.py tests\humanwire\test_planning.py -v
```

Expected: FAIL because the policy and schema do not exist.

- [ ] **Step 3: Implement the policy as a pure function**

Use case-insensitive whole-word patterns over objective, required decisions, stakeholder reason, and questions:

```text
approve|approval|authorize|sign off|decision owner -> REVIEW_APPROVAL
availability|schedule|time window                     -> AVAILABILITY
3-5 required substantive questions                    -> STRUCTURED_INTERVIEW
1-2 required substantive questions                    -> QUICK_RESPONSE
required zero-question receipt/sponsor language       -> ACKNOWLEDGE
optional zero-question awareness language             -> INFORM
```

Reject contradictory combinations instead of guessing. `validate_override` may increase depth, reduce a structured interview to a quick response only when no required question is lost, or switch optional zero-question work between inform/acknowledge. It may not weaken approval, availability, or required evidence.

- [ ] **Step 4: Update both planners**

The rule planner recognizes explicit verbs such as `inform`, `notify`, `acknowledge`, `ask`, `interview`, `approve`, and `schedule`, creates the smallest question set, then passes every stakeholder through `EngagementPolicy.select`.

The Featherless exact JSON schema becomes:

```json
{
  "person_ref": "string",
  "reason": "string",
  "direction": "downward|lateral|upward|external",
  "required": true,
  "engagement_type": "inform|acknowledge|quick_response|structured_interview|review_approval|availability",
  "response_required": true,
  "questions": []
}
```

Continue rejecting extra fields, channel/destination claims, approvals, and state mutations. Resolve the person and actual organizational direction locally, then run deterministic policy; model output never bypasses it.

- [ ] **Step 5: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_engagement_policy.py tests\humanwire\test_planning.py -v
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_model_client.py tests\humanwire\test_directory.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\engagement_policy.py src\humanwire\planning.py tests\humanwire
git diff --check
git add src/humanwire/engagement_policy.py src/humanwire/planning.py tests/humanwire
git commit -m "feat: choose minimum stakeholder engagements"
```

---

### Task 4: Orchestrate Inform, Acknowledge, Quick Response, and Interview

**Files:**

- Create: `src/humanwire/engagements.py`
- Modify: `src/humanwire/interviews.py`
- Modify: `src/humanwire/messages.py`
- Modify: `src/humanwire/state_machine.py`
- Create: `tests/humanwire/test_engagements.py`
- Modify: `tests/humanwire/test_interviews.py`

**Interfaces:**

- Produces: `PreparedEngagement`, `EngagementCoordinator.prepare_start`, `process_due_assignment`, `acknowledge`, `record_answer`, `mark_delivery_success`, and `mark_delivery_failure`.
- Delegates: quick and structured question correlation/evidence extraction to `InterviewCoordinator`; no interview session is created for inform or acknowledgement-only work.

- [ ] **Step 1: Write failing type-specific start tests**

Assert:

```python
def test_inform_has_one_delivery_no_interview_and_completes_only_after_callback(
    coordinator, repository, inform_assignment, inform_plan, now
) -> None:
    prepared = coordinator.prepare_start(
        inform_assignment, inform_plan, "HW-2411", "Coordinate launch", now
    )
    assert prepared.interview is None
    assert prepared.assignment.response_required is False
    assert len([prepared.delivery]) == 1

    coordinator.persist_prepared(prepared)
    assert repository.get_assignment(inform_assignment.assignment_id).completed_at is None

    coordinator.mark_delivery_success(
        inform_assignment.assignment_id, "provider-delivery-1", now
    )
    saved = repository.get_assignment(inform_assignment.assignment_id)
    assert saved.state is StakeholderState.COMPLETE
    assert saved.next_action_at is None
    assert repository.list_interviews(inform_assignment.mandate_id) == []
```

Add separate named tests for acknowledgement-only completion without a question, a one-question quick-response session, a three-question structured interview continuing across channels, a successful inform assignment never entering due work, duplicate provider callbacks, and duplicate inbound messages.

- [ ] **Step 2: Run and capture RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_engagements.py tests\humanwire\test_interviews.py -v
```

Expected: FAIL because the engagement coordinator and engagement-specific renderers do not exist.

- [ ] **Step 3: Add engagement-specific channel copy**

Create renderers with exact headings and safe instructions:

```text
HUMANWIRE UPDATE                 inform; no response requested
HUMANWIRE ACKNOWLEDGEMENT        reply ACK <token>
HUMANWIRE QUICK RESPONSE         one or two questions after ACK
HUMANWIRE INTERVIEW              three to five questions after ACK
```

Alternate-channel copy names the engagement correctly and says only that the prior registered route did not receive the required response. No renderer exposes a destination, another person's evidence, or model prose.

- [ ] **Step 4: Implement type-aware coordination**

Use a caller-owned prepared object:

```python
@dataclass(frozen=True)
class PreparedEngagement:
    assignment: StakeholderAssignment
    interview: InterviewSession | None
    events: tuple[DomainEvent, ...]
    delivery: DeliveryInstruction
```

Rules:

- `INFORM`: prepare one delivery; provider success transitions `DELIVERED -> COMPLETE`; no acknowledgement deadline is scheduled. Provider failure may use the next registered route but successful delivery never triggers a reminder.
- `ACKNOWLEDGE`: schedule the existing reminder/alternate ladder; authenticated ACK transitions to `COMPLETE` without a question or evidence.
- `QUICK_RESPONSE`: delegate one or two questions to the interview engine.
- `STRUCTURED_INTERVIEW`: delegate three to five questions and preserve the existing exact route/conversation/token correlation amendment.
- Provider success records delivery only; it never satisfies response-required types.
- Provider failure never becomes silence or completion and remains replay-safe.

- [ ] **Step 5: Verify regressions and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_engagements.py tests\humanwire\test_interviews.py tests\humanwire\test_repository.py -v
.\.venv\Scripts\python.exe -m pytest tests\humanwire -q
.\.venv\Scripts\python.exe -m ruff check src\humanwire\engagements.py src\humanwire\interviews.py src\humanwire\messages.py tests\humanwire
git diff --check
git add src/humanwire/engagements.py src/humanwire/interviews.py src/humanwire/messages.py src/humanwire/state_machine.py tests/humanwire
git commit -m "feat: orchestrate adaptive stakeholder responses"
```

---

### Task 5: Record Explicit Approval and Availability Responses

**Files:**

- Modify: `src/humanwire/commands.py`
- Modify: `src/humanwire/engagements.py`
- Modify: `src/humanwire/messages.py`
- Modify: `src/humanwire/workflow.py`
- Modify: `tests/humanwire/test_commands.py`
- Modify: `tests/humanwire/test_engagements.py`
- Modify: `tests/humanwire/test_workflow.py`

**Interfaces:**

- Produces: `EngagementDecisionCommand`, `EngagementCoordinator.record_decision`, and `EngagementCoordinator.record_availability`.
- Preserves: proposal responses (`ACCEPT|REJECT|CHANGE`) and meeting scheduling availability behavior; engagement approval uses a distinct `DECIDE` command.

- [ ] **Step 1: Write failing command/authentication tests**

Parse only:

```text
DECIDE HW-2411 APPROVE
DECIDE HW-2411 REJECT <optional safe reason>
DECIDE HW-2411 CHANGE <required requested change>
AVAILABLE HW-2411 <start>/<end> [<start>/<end> ...]
```

Assert wrong token, wrong person, wrong registered route/conversation, terminal assignment, duplicate message ID, and free-text lookalikes produce no decision, evidence, event, or assignment mutation.

- [ ] **Step 2: Run and capture RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_commands.py tests\humanwire\test_engagements.py -k "decide or approval or availability" -v
```

Expected: FAIL because the decision command and coordinator methods do not exist.

- [ ] **Step 3: Implement approval messages and records**

`REVIEW_APPROVAL` outreach states what decision is requested and renders only:

```text
DECIDE <token> APPROVE
DECIDE <token> REJECT <reason>
DECIDE <token> CHANGE <requested change>
```

On an authenticated response, atomically add `EngagementDecision`, add an append-only `engagement.decision_recorded` event, and complete the assignment. Add a deterministic `EvidenceType.DECISION` item whose statement is only `Approval response: approved`, `Approval response: rejected`, or `Approval response: change requested`; raw change text is stored only in the decision record and follows existing privacy rules.

- [ ] **Step 4: Bind availability to requested assignments**

Accept availability only from a registered person with an active `AVAILABILITY` assignment or from the independently computed meeting attendee set during `SCHEDULING`. Store timezone-aware windows durably using the existing runtime-status/event proof, complete the assignment when applicable, and never treat windows as substantive agreement.

- [ ] **Step 5: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_commands.py tests\humanwire\test_engagements.py tests\humanwire\test_workflow.py -k "decide or approval or availability" -v
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_alignment.py tests\humanwire\test_meetings.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\commands.py src\humanwire\engagements.py src\humanwire\messages.py src\humanwire\workflow.py tests\humanwire
git diff --check
git add src/humanwire/commands.py src/humanwire/engagements.py src/humanwire/messages.py src/humanwire/workflow.py tests/humanwire
git commit -m "feat: record explicit engagement decisions"
```

---

### Task 6: Add Plan Preview, Override, and Policy-Controlled Release

**Files:**

- Modify: `src/humanwire/config.py`
- Modify: `src/humanwire/commands.py`
- Modify: `src/humanwire/services.py`
- Modify: `src/humanwire/workflow.py`
- Modify: `src/humanwire/container.py`
- Modify: `tests/humanwire/test_config.py`
- Modify: `tests/humanwire/test_commands.py`
- Modify: `tests/humanwire/test_workflow.py`
- Modify: `tests/humanwire/test_container.py`

**Interfaces:**

- Produces: `GO <token>`, `ENGAGE <token> <person_id> <type>`, `MandateService.release`, and settings `engagement_preview_seconds: int = 15` plus `engagement_require_go: bool = False`.
- Consumes: `EngagementPolicy`, `EngagementCoordinator`, planned assignments, and the existing due-action worker.

- [ ] **Step 1: Write failing creation/preview tests**

Assert one mandate initially persists as `PLANNED`, assignments are queued without interview sessions or stakeholder deliveries, and the initiator receives a preview containing safe names, departments, directions, reasons, engagement labels, response-required flags, question counts, and safe primary/alternate channel labels.

```python
def test_mandate_previews_mixed_engagements_before_outreach(workflow, mandate_message) -> None:
    result = workflow.handle(mandate_message)
    assert len(result.deliveries) == 1
    assert "Quick response" in result.deliveries[0].text
    assert "Approval review" in result.deliveries[0].text
    assert repository.list_interviews(mandate_id) == []
```

Assert no route destination appears in preview text.

- [ ] **Step 2: Write failing authorization and release tests**

Cover:

- automatic release exactly once at `created_at + preview_seconds`;
- strict `engagement_require_go=True` never auto-releases;
- authorized `GO` releases early exactly once;
- authorized `ENGAGE` changes an allowed type and appends provenance;
- unsafe downgrade, wrong initiator, wrong route/thread, late override, duplicate command, and cancelled/expired mandate make no change;
- process restart before due time still releases from persisted state;
- release transaction rolls back mandate/assignments/sessions/events together on failure.

- [ ] **Step 3: Run and capture RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_config.py tests\humanwire\test_commands.py tests\humanwire\test_workflow.py -k "preview or engage or release or require_go" -v
```

Expected: FAIL because creation currently sends interviews immediately and the new settings/commands do not exist.

- [ ] **Step 4: Implement preview-first creation**

`MandateService.create` now:

1. validates initiator, plan, policy, people, and routes;
2. persists the mandate in `PLANNED` and assignments in `CONTACT_QUEUED`;
3. sets `mandate.next_action_at` to the preview deadline only when auto-release is enabled;
4. appends `mandate.received`, `mandate.planned`, and `engagement.plan_previewed` atomically;
5. returns only the initiator preview.

No interview or stakeholder delivery exists before release. Missing required routes continue to create a truthful partial result without claiming outreach.

- [ ] **Step 5: Implement authorized override and release**

`ENGAGE` resolves the saved assignment by exact person ID, calls `EngagementPolicy.validate_override`, updates both the assignment and matching planned stakeholder, and appends `engagement.override_recorded` with safe old/new type metadata. It cannot change routes, people, authority, required status, or required question content.

`release` prepares every engagement with the type-aware coordinator, transitions the mandate to stored `INTERVIEWING`, clears the preview deadline, appends `engagement.plan_released` and `mandate.interviewing`/coordination metadata, and returns deliveries only after the transaction commits.

`process_due` releases due planned mandates before processing due response ladders. `mark_delivery_result` delegates to `EngagementCoordinator`.

- [ ] **Step 6: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_workflow.py tests\humanwire\test_engagements.py tests\humanwire\test_container.py -v
.\.venv\Scripts\python.exe -m pytest tests\humanwire -q
.\.venv\Scripts\python.exe -m ruff check src\humanwire tests\humanwire
git diff --check
git add src/humanwire/config.py src/humanwire/commands.py src/humanwire/services.py src/humanwire/workflow.py src/humanwire/container.py tests/humanwire
git commit -m "feat: preview and release engagement plans"
```

---

### Task 7: Evaluate Contribution Readiness and Seed the Adaptive Demo

**Files:**

- Modify: `src/humanwire/alignment.py`
- Modify: `src/humanwire/services.py`
- Modify: `src/humanwire/web.py`
- Modify: `src/humanwire/demo.py`
- Modify: `tests/humanwire/test_alignment.py`
- Modify: `tests/humanwire/test_workflow.py`
- Modify: `tests/humanwire/test_web.py`
- Modify: `tests/humanwire/test_demo.py`

**Interfaces:**

- Produces: engagement-aware synthesis readiness and public fields `phase_label`, `engagement_type`, `response_required`, `engagement_status`, `progress_current`, and `progress_total`.
- Preserves: stored state value `interviewing`, Task 11 authentication/privacy boundary, meeting proof, and bodyless 404 behavior.

- [ ] **Step 1: Write failing contribution-readiness tests**

Assert:

- delivered `INFORM` may complete but cannot satisfy a required decision or create agreement evidence;
- `ACKNOWLEDGE` proves receipt only;
- required quick/interview assignments need completion plus confirmed evidence;
- required approval needs a persisted authenticated decision;
- reject/change decisions create a deterministic blocking issue;
- availability supplies scheduling data only;
- unresolved required contributions block alignment or produce truthful partial output;
- optional unresolved engagements do not block synthesis.

- [ ] **Step 2: Run and capture RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_alignment.py tests\humanwire\test_workflow.py -k "engagement or contribution or approval" -v
```

Expected: FAIL because synthesis currently treats all required assignments as completed interviews.

- [ ] **Step 3: Implement deterministic contribution checks**

Add a pure helper used by synthesis and alignment:

```python
class ContributionState(StrEnum):
    COMPLETE = "complete"
    MISSING_RESPONSE = "missing_response"
    MISSING_EVIDENCE = "missing_evidence"
    REJECTED = "rejected"
    CHANGE_REQUESTED = "change_requested"
    DELIVERY_FAILED = "delivery_failed"


class ContributionStatus(BaseModel):
    state: ContributionState
    blocking: bool


def contribution_status(
    assignment: StakeholderAssignment,
    *,
    evidence: Sequence[EvidenceItem],
    decisions: Sequence[EngagementDecision],
    has_availability: bool,
) -> ContributionStatus:
    if assignment.state in {StakeholderState.DELIVERY_FAILED, StakeholderState.UNREACHABLE}:
        return ContributionStatus(
            state=ContributionState.DELIVERY_FAILED,
            blocking=assignment.required,
        )
    if assignment.state is not StakeholderState.COMPLETE:
        return ContributionStatus(
            state=ContributionState.MISSING_RESPONSE,
            blocking=assignment.required,
        )
    if assignment.engagement_type is EngagementType.REVIEW_APPROVAL:
        decision = next(
            (item for item in decisions if item.assignment_id == assignment.assignment_id),
            None,
        )
        if decision is None:
            return ContributionStatus(
                state=ContributionState.MISSING_RESPONSE,
                blocking=assignment.required,
            )
        if decision.response is EngagementDecisionKind.REJECT:
            return ContributionStatus(state=ContributionState.REJECTED, blocking=True)
        if decision.response is EngagementDecisionKind.CHANGE:
            return ContributionStatus(
                state=ContributionState.CHANGE_REQUESTED,
                blocking=True,
            )
    if assignment.engagement_type is EngagementType.AVAILABILITY and not has_availability:
        return ContributionStatus(
            state=ContributionState.MISSING_RESPONSE,
            blocking=assignment.required,
        )
    if assignment.engagement_type in {
        EngagementType.QUICK_RESPONSE,
        EngagementType.STRUCTURED_INTERVIEW,
    } and not any(item.assignment_id == assignment.assignment_id for item in evidence):
        return ContributionStatus(
            state=ContributionState.MISSING_EVIDENCE,
            blocking=assignment.required,
        )
    return ContributionStatus(state=ContributionState.COMPLETE, blocking=False)
```

The result distinguishes `complete`, `missing_response`, `missing_evidence`, `rejected`, `change_requested`, and `delivery_failed`. Alignment uses it to add required `MISSING_EVIDENCE`, `AUTHORITY_GAP`, or `HARD_CONSTRAINT` issues; model output cannot remove them.

- [ ] **Step 4: Add redacted public projections**

For each assignment, derive engagement progress without inventing state:

```text
INFORM               delivered 0/1 or 1/1
ACKNOWLEDGE          acknowledgement 0/1 or 1/1
QUICK_RESPONSE       answered questions / total questions
STRUCTURED_INTERVIEW answered questions / total questions
REVIEW_APPROVAL      pending/approved/rejected/change requested
AVAILABILITY         missing/recorded
```

Add `phase_label="coordinating"` when stored state is `interviewing`. Keep direct destinations, route IDs, raw decision change text, private evidence, and provider bodies outside all public projections.

- [ ] **Step 5: Seed the exact public story**

Update `HW-2411` so:

- two downward team leads have completed `QUICK_RESPONSE` engagements;
- Priya Shah is the only active `STRUCTURED_INTERVIEW` and is on registered alternate Telegram;
- Nora Chen has completed upward `ACKNOWLEDGE`;
- Maya Brooks has pending `REVIEW_APPROVAL`;
- one optional stakeholder has completed `INFORM` with no reminder event;
- the event log contains the preview, release, type-specific outreach, acknowledgement, answers, and approval-pending truth;
- aligned and meeting-ready secondary cases remain useful and deterministic.

- [ ] **Step 6: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_alignment.py tests\humanwire\test_workflow.py tests\humanwire\test_web.py tests\humanwire\test_demo.py -v
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src\humanwire tests\humanwire
git diff --check
git add src/humanwire/alignment.py src/humanwire/services.py src/humanwire/web.py src/humanwire/demo.py tests/humanwire
git commit -m "feat: project adaptive engagement progress"
```

---

### Task 8: Build the Adaptive Decision Room

**Files:**

- Create or replace: `src/humanwire/templates/base.html`
- Create or replace: `src/humanwire/templates/dashboard.html`
- Create or replace: `src/humanwire/templates/mandate.html`
- Create or replace: `src/humanwire/static/styles.css`
- Create or replace: `src/humanwire/static/app.js`
- Modify: `src/humanwire/web.py`
- Modify: `tests/humanwire/test_web.py`
- Update ignored references: `.superpowers/sdd/2026-08-11-humanwire/design/task-12-decision-room-*.png`

**Interfaces:**

- Consumes: redacted engagement projections from Task 7.
- Produces: accessible dashboard/Decision Room, adaptive selected-engagement ladder, `window.HumanWire.refreshMandate(token)`, countdown updates, and state filters.

- [ ] **Step 1: Update the locked visual references before coding**

Use the already approved navy/ice layout as the edit target and change only the approved semantics:

```text
Interviewing -> Coordinating
Interview progress -> Engagement progress
Priya -> Structured interview
team leads -> Quick response
Nora -> Acknowledgement
Maya -> Approval review
optional stakeholder -> Inform only
```

Keep the same desktop composition, mobile reflow, palette, typography, next-action rail, event timeline, and Reach preview. Save desktop and 600px references to the existing ignored paths and inspect both images before implementation.

- [ ] **Step 2: Write failing semantic and adaptive-ladder tests**

Assert:

```python
def test_decision_room_uses_coordination_and_adaptive_engagements(client) -> None:
    html = client.get("/mandates/HW-2411").text
    assert 'data-testid="workflow-step-coordinating"' in html
    assert 'aria-current="step"' in html
    assert "Engagement progress" in html
    assert "Structured interview" in html
    assert "Quick response" in html
    assert "Approval review" in html
    assert "Interview progress" not in html
```

Add tests that an inform row has no interview ladder, acknowledgement ends at ACK, approval shows decision steps, Priya alone shows the interview ladder, no mutating form exists, and all Reach/Data links are correct.

- [ ] **Step 3: Run and capture RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "decision_room or dashboard or engagement_ladder" -v
```

Expected: FAIL because templates/static adaptive behavior do not exist.

- [ ] **Step 4: Implement the server-rendered UI**

Use semantic landmarks, skip link, visible focus, minimum 14px body/control text, true deep navy, ice text, calm green completion, restrained cyan single active glow, amber only for real pending attention, thin borders, and modest radii.

Desktop uses the approved larger left workflow area and narrower next-action/evidence rail. Below 760px, lifecycle and stakeholder rows recompose into a single column; no desktop table is squeezed into horizontal page overflow.

The selected ladder is generated from `engagement_type`, not hard-coded to interview steps. Evidence and AI draft sections remain visibly separate and assumptions remain zero unless persisted proof says otherwise.

- [ ] **Step 5: Implement persisted-state polling only**

`app.js` exposes `window.HumanWire.refreshMandate(token)`, polls the authenticated/read-only detail endpoint every five seconds only while visible, updates countdown/timestamps, and reloads only when returned `updated_at` changes. It never advances state, sends messages, fabricates events, or calls a mutating route. Respect `prefers-reduced-motion`.

- [ ] **Step 6: Verify browser fidelity and commit**

Run the deterministic demo and capture 1536×1024 plus 600px screenshots. Inspect each reference and implementation screenshot with image viewing in the same QA pass. Record at least five comparisons covering copy, layout, typography, palette, engagement ladder, timeline, responsive reflow, and overflow; fix all material drift.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py tests\humanwire\test_web.py
git diff --check
git add src/humanwire/templates src/humanwire/static src/humanwire/web.py tests/humanwire/test_web.py
git commit -m "feat: build the adaptive HumanWire Decision Room"
```

---

### Task 9: Show Engagement Types in Propagation Lanes

**Files:**

- Create: `src/humanwire/templates/reach.html`
- Modify: `src/humanwire/static/styles.css`
- Modify: `src/humanwire/static/app.js`
- Modify: `src/humanwire/web.py`
- Modify: `tests/humanwire/test_web.py`

**Interfaces:**

- Consumes: direction-grouped assignments plus append-only engagement events.
- Produces: responsive Gather input, Coordinate policy, and Get approval lanes with replay, filters, safe person details, and engagement labels.

- [ ] **Step 1: Write failing lane tests**

Assert three lanes, no org chart, event ordering by first contact, one glowing current step, adaptive engagement label/progress for every person, and no interview language on inform/ack/approval steps.

- [ ] **Step 2: Run and capture RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "reach or lane or replay" -v
```

Expected: FAIL because the full Reach template and adaptive lane behavior do not exist.

- [ ] **Step 3: Implement responsive Propagation Lanes**

Render one spacious origin card followed by:

```text
Gather input        downward
Coordinate policy  lateral
Get approval        upward
```

Each step shows safe person/department, persisted engagement type, engagement-specific progress, safe channel label, timestamp, and result. Use three columns above 850px and stacked lanes below it. Replay highlights only persisted events and never changes workflow state.

- [ ] **Step 4: Verify browser behavior and commit**

Inspect at 1280×720 and 600×900; confirm no full-page horizontal scroll, clipped labels, or cramped hierarchy.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py tests\humanwire\test_web.py
git diff --check
git add src/humanwire/templates/reach.html src/humanwire/static src/humanwire/web.py tests/humanwire/test_web.py
git commit -m "feat: visualize adaptive propagation lanes"
```

---

### Task 10: Extend the Analytics and Power BI Contract

**Files:**

- Create: `src/humanwire/templates/data.html`
- Modify: `src/humanwire/web.py`
- Modify: `src/humanwire/static/styles.css`
- Modify: `tests/humanwire/test_web.py`
- Create or modify: `docs/analytics.md`

**Interfaces:**

- Produces: one canonical redacted HTML/JSON/CSV outreach projection with engagement fields and read-only-token protection outside demo mode.

- [ ] **Step 1: Write failing export tests**

Use stable headers:

```python
EXPECTED_HEADERS = [
    "mandate_token", "timestamp", "initiator_id", "source_department",
    "target_person_id", "target_department", "direction", "channel",
    "engagement_type", "response_required", "engagement_status",
    "event_type", "previous_state", "new_state", "outcome",
    "response_latency_seconds",
]
```

Cover filtering by engagement type/status as well as department, safe person ID, channel, direction, event type, and timestamp. Assert HTML, JSON, and CSV use the same rows and exclude destinations, routes, raw change requests, private evidence, secrets, and arbitrary event metadata.

- [ ] **Step 2: Run and capture RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "csv or data_table or engagement_filter" -v
```

Expected: FAIL because the adaptive data contract does not exist.

- [ ] **Step 3: Implement one canonical projection**

`_outreach_rows(repository, mandate, filters)` maps allowlisted saved assignment/event values, computes response latency from the first delivery to the first required human response, uses empty strings for unavailable values, orders deterministically, and feeds HTML/JSON/CSV. Inform delivery latency must not be mislabeled as human response latency.

- [ ] **Step 4: Document Power BI use and commit**

Document authenticated JSON and downloaded CSV, stable fields, refresh, engagement filters, and the rule never to connect Power BI directly to `humanwire.db`.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py tests\humanwire\test_web.py
git diff --check
git add src/humanwire/templates/data.html src/humanwire/static/styles.css src/humanwire/web.py tests/humanwire/test_web.py docs/analytics.md
git commit -m "feat: expose adaptive HumanWire analytics"
```

---

### Task 11: Prove the Full Adaptive Product Flow

**Files:**

- Create or modify: `scripts/smoke_humanwire.py`
- Modify: `src/humanwire/__main__.py`
- Modify: `tests/humanwire/test_workflow.py`
- Modify: `tests/humanwire/test_caspian_gateway.py`
- Modify: `tests/humanwire/test_demo.py`
- Modify: `docs/demo-script.md`
- Modify: `docs/architecture.md`
- Modify: `docs/threat-model.md`

**Interfaces:**

- Consumes: complete adaptive HumanWire package.
- Produces: deterministic offline smoke proof and an opt-in live operator checklist; it never sends live messages without the existing explicit listener/live procedure.

- [ ] **Step 1: Write one complete workflow integration test**

Drive through the real workflow and fake adapters:

```text
manager sends /mandate
preview contains inform + acknowledge + quick + interview + approval
authorized override changes one optional engagement
preview deadline releases exactly once
inform delivery succeeds and receives no reminder
two quick responses complete over email
Priya primary route times out, ACKs on Telegram, and completes the same interview
Nora ACKs upward without receiving a question
Maya explicitly requests a change; no approval is inferred
two-round proposal cap is reached
required attendees submit availability
verified meeting package reaches MEETING_READY
```

Assert restart safety, append-only event ordering, distinct engagement completion rules, one Caspian handler, both channels, private evidence exclusion, preview/override provenance, and no duplicate delivery on callbacks/replays.

- [ ] **Step 2: Run and capture RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_workflow.py tests\humanwire\test_caspian_gateway.py -k "adaptive_product_flow" -v
```

Expected: FAIL until the complete integration story is connected.

- [ ] **Step 3: Implement the offline smoke harness**

Build the deterministic demo, call health/list/detail/Decision Room/Reach/data/CSV/ICS, and print exactly:

```text
PASS domain
PASS adaptive-engagement
PASS preview-override
PASS cross-channel-interview
PASS explicit-approval
PASS negotiation-limit
PASS meeting-package
PASS decision-room
PASS propagation-lanes
PASS analytics-export
PASS privacy-scan
```

Exit nonzero on any failure. `--live` prints the operator checklist and requires `--confirm-live`; it does not synthesize or transmit test messages automatically.

- [ ] **Step 4: Update truthful product documentation**

Architecture, threat model, and demo script must say HumanWire interviews only where needed, explain the preview/override window, distinguish acknowledgement from approval, show Priya as the only active structured interview in `HW-2411`, and retain limitations around external calendar mutation and administrator access to stored data.

- [ ] **Step 5: Run the integration gate and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire -v
.\.venv\Scripts\python.exe scripts\smoke_humanwire.py
.\.venv\Scripts\python.exe -m ruff check src\humanwire tests\humanwire scripts\smoke_humanwire.py
git diff --check
git add scripts/smoke_humanwire.py src/humanwire/__main__.py tests/humanwire docs/architecture.md docs/threat-model.md docs/demo-script.md
git commit -m "test: prove adaptive HumanWire coordination"
```

After this task passes independent review, resume the original Task 16 cutover/deployment procedure with these copy changes:

- the 20-second pitch says HumanWire selects the minimum necessary engagement;
- live proof includes one inform, acknowledgement, quick response, email interview, Telegram continuation, and explicit approval response;
- all three submissions distinguish engagement types and never claim everybody is interviewed;
- deployment still updates `https://secondsignal.vercel.app/` only after local/full/browser/live gates pass.

## Completion Checklist

- [ ] Task 11 calendar UID is private, distinct, deterministic, and below the RFC 5545 fold limit.
- [ ] One mandate safely persists several engagement types.
- [ ] Model suggestions cannot weaken deterministic contribution or authority requirements.
- [ ] Initiator preview contains no destinations and releases according to configured policy.
- [ ] Authorized overrides are constrained, durable, replay-safe, and pre-outreach only.
- [ ] Inform delivery creates no reminder, evidence, agreement, or approval.
- [ ] Acknowledgement completes without an interview question and proves receipt only.
- [ ] Quick responses use one or two questions; structured interviews use three to five.
- [ ] Explicit approval/availability responses are authenticated and independently persisted.
- [ ] Only unresolved response-required work enters reminders/alternate routes.
- [ ] Synthesis evaluates required contributions rather than universal interview completion.
- [ ] Public API/demo uses `Coordinating` and engagement progress without privacy regressions.
- [ ] Decision Room selected ladders adapt to engagement type at desktop and 600px width.
- [ ] Propagation Lanes show engagement type without becoming an org chart.
- [ ] HTML/JSON/CSV share one redacted analytics contract with engagement fields.
- [ ] Full fake-Caspian workflow, offline smoke, full pytest, Ruff, diff checks, and browser QA pass.
- [ ] Live proof and deployment occur only after the integration gate and preserve all secrets/private data.
