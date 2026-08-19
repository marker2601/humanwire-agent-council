# HumanWire Gemini Company Council Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a visible, evidence-bound virtual company council on Vertex AI Gemini 3.7 Flash while HumanWire retains persistence and a real human retains final approval.

**Architecture:** A pure planner selects a bounded subset of functional specialists from the organization and authority graph. A private Pub/Sub-triggered Cloud Run worker executes schema-constrained Google ADK specialists, reserves cost before each provider call, records safe usage receipts, and submits advisory candidates to a HumanWire council gateway. A tenant-bound projection streams meaningful work, challenges, revisions, and human approval state to DecisionOS without exposing prompts, private reasoning, or provider traces.

**Tech Stack:** Python 3.12, Pydantic 2, Google ADK 2.x, Google Gen AI SDK, Vertex AI ADC, Cloud Run, Pub/Sub OIDC, Firestore transactions, FastAPI, Jinja, vanilla JavaScript/SVG, pytest, Node harness.

**Spec:** `docs/superpowers/specs/2026-08-18-humanwire-ai-company-onboarding-design.md`

**Prerequisite plan:** `docs/superpowers/plans/2026-08-18-humanwire-organization-foundation.md`

## Global Constraints

- Primary provider model is the GA Vertex model ID `gemini-3.7-flash`.
- Browser code never receives provider credentials, prompts, raw reasoning, source directory rows, or unredacted model output.
- AI roles are functional specialists and are never represented as real employees.
- Chief of Staff and Decision Recorder are always selected; normal councils activate at most eight AI specialists.
- Every specialist has an exact schema, evidence grant, tool allowlist, deadline, thinking level, token ceiling, retry limit, and cost reservation.
- Market/Customer/Evidence/Operations default to medium thinking; Risk/Legal/consequential Finance/final synthesis default to high thinking.
- Exactly one retry is allowed for transport failure or invalid structured output.
- Normal decision target is $0.07-$0.15; default hard ceiling is $0.25.
- No provider request begins unless its maximum estimated cost is already reserved.
- Model output is advisory until accepted by the HumanWire council gateway.
- An AI specialist cannot invite, activate, assign authority, approve, send externally, or mutate Firestore.
- Human approval binds authenticated UID, organization, workspace, decision, recommendation digest, authority assignment, nonce, expiry, and workflow state.
- Existing persona, cloud demo, Caspian gateway, deterministic scenario, and frozen replay behavior remain unchanged when the council feature flag is off.

## File structure

- `src/humanwire/council_models.py`: specialist, plan, assignment, candidate, usage, projection, and approval contracts.
- `src/humanwire/council_catalog.py`: immutable specialist mandates and tool/schema policies.
- `src/humanwire/council_planner.py`: pure dynamic specialist selection.
- `src/humanwire/council_budget.py`: pricing versions, reservations, receipts, and ceilings.
- `src/humanwire/google_council.py`: ADK/Gemini 3.7 execution adapter.
- `src/humanwire/google_organization_mapper.py`: schema-bound Gemini mapper implementing the foundation plan protocol.
- `src/humanwire/council_gateway.py`: candidate validation and human approval boundary.
- `src/humanwire/council_orchestrator.py`: parallel analysis, cross-examination, risk challenge, synthesis, and terminality.
- `src/humanwire/decisionos_council_store.py`: tenant-bound run/projection/approval persistence.
- `src/humanwire/decisionos_council_worker.py`: private Pub/Sub worker.
- `src/humanwire/decisionos_council_routes.py`: authenticated run, projection, and approval API.
- `src/humanwire/council_projection.py`: safe product projection.
- `src/humanwire/decisionos_static/company-council.js` and `.css`: live company workflow.

---

### Task 1: Specialist and council contracts

**Files:**
- Create: `src/humanwire/council_models.py`
- Create: `tests/humanwire/test_council_models.py`

**Interfaces:**
- Consumes: organization/authority IDs, workspace IDs, evidence identifiers, strict Pydantic conventions, and aware UTC clocks.
- Produces: `SpecialistKey`, `ThinkingLevel`, `DecisionRisk`, `CandidateStatus`, `CouncilRunState`, `CouncilSpecialist`, `CouncilPlanRequest`, `CouncilPlan`, `SpecialistAssignment`, `ProposedAction`, `SpecialistCandidate`, `CouncilRecommendation`, `CouncilRunRequest`, `CouncilRunResult`, `CouncilRun`, `CouncilUsageReceipt`, `CouncilProjection`, `ApprovalChallenge`, `ApprovalRequest`, `ApprovalDecision`, and exact evidence/ID contracts.

- [ ] **Step 1: Write contract RED tests**

```python
def test_specialist_candidate_cannot_claim_approval() -> None:
    with pytest.raises(ValidationError):
        SpecialistCandidate(
            assignment_id=ASSIGNMENT,
            specialist_key=SpecialistKey.FINANCE_CAPITAL,
            status=CandidateStatus.COMPLETE,
            recommendation="Approve the round",
            requested_authority=AuthorityFunction.APPROVER,
            evidence_refs=("evd_01K00000000000000000000000",),
            assumptions=(),
            confidence=0.72,
        )


def test_projection_cannot_retain_prompt_or_reasoning() -> None:
    with pytest.raises(ValidationError):
        CouncilProjection.model_validate(
            {**valid_projection_payload(), "reasoning": "private chain"}
        )
```

Cover duplicate specialists, missing Chief of Staff/Recorder, more than eight normal
specialists, invalid thinking levels, arbitrary tool names, unsupported evidence
references, non-finite confidence/cost, output above token ceiling, naive timestamps,
private prompt keys, provider IDs in public projection, and AI approval claims.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_council_models.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement strict models**

```python
class SpecialistKey(StrEnum):
    CHIEF_OF_STAFF = "chief_of_staff"
    PRODUCT_STRATEGY = "product_strategy"
    TECHNICAL_ARCHITECTURE = "technical_architecture"
    MARKET_INTELLIGENCE = "market_intelligence"
    CUSTOMER_RESEARCH = "customer_research"
    GROWTH_SALES = "growth_sales"
    FINANCE_CAPITAL = "finance_capital"
    OPERATIONS = "operations"
    DATA_EVIDENCE = "data_evidence"
    LEGAL_COMPLIANCE = "legal_compliance"
    RISK_CHALLENGER = "risk_challenger"
    INVESTOR_RELATIONS = "investor_relations"
    PEOPLE_ORGANIZATION = "people_organization"
    DECISION_RECORDER = "decision_recorder"


class ThinkingLevel(StrEnum):
    MEDIUM = "medium"
    HIGH = "high"


class SpecialistCandidate(_CouncilModel):
    assignment_id: str
    specialist_key: SpecialistKey = Field(strict=False)
    status: CandidateStatus = Field(strict=False)
    recommendation: str = Field(min_length=1, max_length=4_000)
    evidence_refs: tuple[str, ...] = Field(max_length=32)
    assumptions: tuple[str, ...] = Field(max_length=16)
    challenges: tuple[str, ...] = Field(default=(), max_length=16)
    proposed_actions: tuple[ProposedAction, ...] = Field(default=(), max_length=16)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
```

Define exact ID patterns and validators. Public models contain safe summaries only;
private provider response/usage structures remain separate.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/humanwire/test_council_models.py -v`

Expected: PASS with stable JSON and semantic digest fixtures.

- [ ] **Step 5: Commit**

```powershell
git add src/humanwire/council_models.py tests/humanwire/test_council_models.py
git commit -m "feat: define DecisionOS council contracts"
```

### Task 2: Functional specialist catalog

**Files:**
- Create: `src/humanwire/council_catalog.py`
- Create: `tests/humanwire/test_council_catalog.py`

**Interfaces:**
- Consumes: `CouncilSpecialist`, specialist enums, schema identifiers, and tool names.
- Produces: `SPECIALIST_CATALOG`, `specialist_for(key)`, and `catalog_projection()`.

- [ ] **Step 1: Write catalog RED tests**

```python
def test_catalog_has_exact_approved_functional_roles() -> None:
    assert tuple(SPECIALIST_CATALOG) == tuple(SpecialistKey)


def test_no_specialist_has_mutation_or_approval_tool() -> None:
    forbidden = {"firestore_write", "invite", "activate", "approve", "send_message"}
    for specialist in SPECIALIST_CATALOG.values():
        assert forbidden.isdisjoint(specialist.tool_allowlist)
```

Assert the exact public display names and mandates from the spec, safe prompt
instructions, medium/high thinking defaults, maximum output tokens, deadlines,
input/output schemas, and absence of names that imply a real employee.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_council_catalog.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement immutable catalog entries**

```python
_CATALOG_ROWS = (
    (SpecialistKey.CHIEF_OF_STAFF, "Chief of Staff", "Select the relevant council, coordinate dependencies, and expose blockers.", ThinkingLevel.MEDIUM, ("read_decision", "read_authority_map"), 800, 30),
    (SpecialistKey.PRODUCT_STRATEGY, "Product Strategy", "Evaluate product direction, priorities, roadmap, and trade-offs.", ThinkingLevel.MEDIUM, ("read_decision", "read_evidence"), 1_200, 45),
    (SpecialistKey.TECHNICAL_ARCHITECTURE, "Technical Architecture", "Evaluate feasibility, architecture, security, reliability, and engineering effort.", ThinkingLevel.MEDIUM, ("read_decision", "read_evidence"), 1_200, 45),
    (SpecialistKey.MARKET_INTELLIGENCE, "Market Intelligence", "Evaluate market structure, competition, positioning, and opportunity.", ThinkingLevel.MEDIUM, ("read_decision", "read_evidence"), 1_200, 45),
    (SpecialistKey.CUSTOMER_RESEARCH, "Customer Research", "Evaluate customer problems, personas, evidence, and unmet needs.", ThinkingLevel.MEDIUM, ("read_decision", "read_evidence"), 1_200, 45),
    (SpecialistKey.GROWTH_SALES, "Growth and Sales", "Evaluate acquisition, pricing, partnerships, pipeline, and revenue mechanics.", ThinkingLevel.MEDIUM, ("read_decision", "read_evidence"), 1_200, 45),
    (SpecialistKey.FINANCE_CAPITAL, "Finance and Capital", "Evaluate budget, runway, unit economics, financing, and capital allocation.", ThinkingLevel.HIGH, ("read_decision", "read_evidence", "calculate_finance"), 1_400, 60),
    (SpecialistKey.OPERATIONS, "Operations", "Evaluate execution, dependencies, timing, capacity, and ownership.", ThinkingLevel.MEDIUM, ("read_decision", "read_evidence"), 1_200, 45),
    (SpecialistKey.DATA_EVIDENCE, "Data and Evidence", "Verify claims, source coverage, contradictions, and missing proof.", ThinkingLevel.MEDIUM, ("read_decision", "read_evidence", "verify_claims"), 1_400, 60),
    (SpecialistKey.LEGAL_COMPLIANCE, "Legal and Compliance", "Identify regulatory, contractual, privacy, and policy constraints.", ThinkingLevel.HIGH, ("read_decision", "read_evidence", "read_policy"), 1_400, 60),
    (SpecialistKey.RISK_CHALLENGER, "Risk Challenger", "Challenge assumptions, identify failure scenarios, and preserve unresolved objections.", ThinkingLevel.HIGH, ("read_decision", "read_evidence", "read_candidates"), 1_400, 60),
    (SpecialistKey.INVESTOR_RELATIONS, "Investor Relations", "Evaluate funding narrative, diligence readiness, and investment memo structure.", ThinkingLevel.MEDIUM, ("read_decision", "read_evidence"), 1_200, 45),
    (SpecialistKey.PEOPLE_ORGANIZATION, "People and Organization", "Evaluate hiring, capacity, incentives, and organizational impact.", ThinkingLevel.MEDIUM, ("read_decision", "read_organization"), 1_200, 45),
    (SpecialistKey.DECISION_RECORDER, "Decision Recorder", "Produce the safe audit timeline, actions, owners, and final package.", ThinkingLevel.MEDIUM, ("read_decision", "read_candidates", "read_authority_map"), 1_000, 30),
)
SPECIALIST_CATALOG = MappingProxyType(
    {
        key: CouncilSpecialist(
            key=key,
            display_name=display_name,
            mandate=mandate,
            thinking_level=thinking_level,
            tool_allowlist=tools,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        for key, display_name, mandate, thinking_level, tools, max_output_tokens, timeout_seconds in _CATALOG_ROWS
    }
)
```

Do not use generated role text. Keep the explicit rows in approved enum order and
bind each row to its input/output schema identifiers in `CouncilSpecialist`.

- [ ] **Step 4: Run GREEN and copy scan**

Run:

```powershell
python -m pytest tests/humanwire/test_council_catalog.py -v
rg -n -i "fake employee|live employee|autonomous approver" src/humanwire/council_catalog.py
```

Expected: tests PASS and claim scan returns no hits.

- [ ] **Step 5: Commit**

```powershell
git add src/humanwire/council_catalog.py tests/humanwire/test_council_catalog.py
git commit -m "feat: catalog HumanWire company specialists"
```

### Task 3: Explainable dynamic council planner

**Files:**
- Create: `src/humanwire/council_planner.py`
- Create: `tests/humanwire/test_council_planner.py`

**Interfaces:**
- Consumes: `CouncilPlanRequest`, specialist catalog, organization graph, authority map, evidence classes, decision type, and risk level.
- Produces: `plan_council(request: CouncilPlanRequest) -> CouncilPlan`.

- [ ] **Step 1: Write decision-fixture RED tests**

```python
def test_fundraising_selects_exact_bounded_council() -> None:
    plan = plan_council(fundraising_request())
    assert plan.specialist_keys == (
        SpecialistKey.CHIEF_OF_STAFF,
        SpecialistKey.MARKET_INTELLIGENCE,
        SpecialistKey.GROWTH_SALES,
        SpecialistKey.FINANCE_CAPITAL,
        SpecialistKey.DATA_EVIDENCE,
        SpecialistKey.LEGAL_COMPLIANCE,
        SpecialistKey.RISK_CHALLENGER,
        SpecialistKey.DECISION_RECORDER,
    )
    assert set(plan.selection_reasons) == set(plan.specialist_keys)


def test_normal_plan_never_exceeds_eight_specialists() -> None:
    plan = plan_council(request_matching_every_domain())
    assert len(plan.specialist_keys) <= 8
    assert plan.requires_full_company_confirmation is True
```

Cover product, hiring, partnership, general strategy, low-risk operations, missing
authority, required Legal policy, capability gaps, disabled specialist, deterministic
order, and a malicious decision objective attempting to select tools or roles.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_council_planner.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement pure rule-driven selection**

```python
def plan_council(request: CouncilPlanRequest) -> CouncilPlan:
    selected = {
        SpecialistKey.CHIEF_OF_STAFF,
        SpecialistKey.DECISION_RECORDER,
    }
    selected.update(_DECISION_SPECIALISTS[request.decision_type])
    selected.update(_required_by_authority(request.authority_assignments))
    selected.update(_required_by_evidence(request.evidence_classes))
    ordered = tuple(key for key in SpecialistKey if key in selected)
    return CouncilPlan.from_selection(request=request, ordered=ordered)
```

Selection is deterministic configuration, not model output. When more than eight
roles are warranted, emit the best bounded normal plan plus excluded roles/reasons
and require explicit full-company confirmation.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/humanwire/test_council_planner.py -v`

Expected: PASS and identical plan digest under shuffled graph/evidence input.

- [ ] **Step 5: Commit**

```powershell
git add src/humanwire/council_planner.py tests/humanwire/test_council_planner.py
git commit -m "feat: plan bounded company councils"
```

### Task 4: Atomic model budget ledger

**Files:**
- Create: `src/humanwire/council_budget.py`
- Create: `tests/humanwire/test_council_budget.py`

**Interfaces:**
- Consumes: model ID, pricing version, maximum input/output/reasoning tokens, organization/run IDs, and provider usage.
- Produces: `PriceVersion`, `CouncilBudgetPolicy`, `CostEstimate`, `BudgetReservationRequest`, `BudgetReservation`, `CouncilBudgetLedger`, `CouncilUsageReceipt`, `InMemoryCouncilBudgetLedger`, `FirestoreCouncilBudgetLedger`, `estimate_cost`, `reserve`, `settle`, and `release`.

- [ ] **Step 1: Write arithmetic and concurrency RED tests**

```python
def test_gemini_37_introductory_estimate_is_exact() -> None:
    estimate = estimate_cost(
        price=PriceVersion(
            model_id="gemini-3.7-flash",
            input_usd_per_million=Decimal("0.75"),
            output_usd_per_million=Decimal("3.75"),
            effective_from=INTRO_START,
            effective_until=datetime(2027, 1, 1, tzinfo=UTC),
        ),
        maximum_input_tokens=25_000,
        maximum_output_tokens=8_000,
    )
    assert estimate.maximum_usd == Decimal("0.048750")


def test_concurrent_reservations_cannot_exceed_run_ceiling(ledger) -> None:
    results = run_concurrently(
        lambda: ledger.reserve(reservation_request(maximum_usd=Decimal("0.15"))),
        workers=2,
    )
    assert sum(item.status == "reserved" for item in results) == 1
```

Cover decimal-only arithmetic, missing/stale price version, output reasoning counted,
retry reservation, duplicate idempotency key, settlement below/at/above reservation,
provider 4xx/5xx, interrupted worker, abandoned reservation expiry, tenant isolation,
and private exception graphs.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_council_budget.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement price versions and semantic in-memory ledger**

```python
def estimate_cost(
    *,
    price: PriceVersion,
    maximum_input_tokens: int,
    maximum_output_tokens: int,
) -> CostEstimate:
    million = Decimal(1_000_000)
    value = (
        Decimal(maximum_input_tokens) * price.input_usd_per_million
        + Decimal(maximum_output_tokens) * price.output_usd_per_million
    ) / million
    return CostEstimate(maximum_usd=value.quantize(Decimal("0.000001")))
```

Store introductory and 2027 announced prices as dated configuration fixtures, not
authority constants. The runtime accepts an operator price configuration and refuses
an unknown model/effective date.

- [ ] **Step 4: Implement Firestore transactional reservation**

One transaction checks run ceiling, organization ceiling, existing idempotency key,
and active reservations before incrementing reserved microdollars. Settlement records
only token counts, price version, safe status, and totals; never prompt content.

- [ ] **Step 5: Run GREEN**

Run: `python -m pytest tests/humanwire/test_council_budget.py -v`

Expected: PASS under repeated 20-worker reservation race.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/council_budget.py tests/humanwire/test_council_budget.py
git commit -m "feat: bound DecisionOS model spending"
```

### Task 5: Gemini 3.7 ADK specialist and organization mapper

**Files:**
- Create: `src/humanwire/google_council.py`
- Create: `src/humanwire/google_organization_mapper.py`
- Modify: `src/humanwire/google_config.py`
- Create: `tests/humanwire/test_google_council.py`
- Create: `tests/humanwire/test_google_organization_mapper.py`
- Modify: `tests/humanwire/test_google_decision_engine.py`

**Interfaces:**
- Consumes: specialist assignments, exact evidence tool callbacks, `OrganizationMapper` from the foundation plan, `CouncilBudgetLedger`, Vertex ADC runtime, and monotonic deadlines.
- Produces: `GoogleAdkSpecialistRunner.evaluate(assignment) -> SpecialistCandidate`, `GeminiOrganizationMapper.map(snapshot, current_graph) -> OrganizationGraphCandidate`, and safe usage settlement.

- [ ] **Step 1: Write SDK configuration RED tests**

```python
def test_gemini_37_uses_thinking_level_without_deprecated_sampling() -> None:
    request = capture_generate_request(runner_for(SpecialistKey.RISK_CHALLENGER))
    assert request.model == "gemini-3.7-flash"
    assert request.config.thinking_config.thinking_level == "high"
    assert request.config.temperature is None
    assert request.config.top_p is None
    assert request.config.top_k is None
    assert request.config.candidate_count is None


def test_provider_call_does_not_start_when_budget_reservation_fails() -> None:
    provider = RecordingProvider()
    runner = google_runner(provider=provider, ledger=rejecting_ledger())
    with pytest.raises(CouncilUnavailable, match="budget_exceeded"):
        runner.evaluate(valid_assignment())
    assert provider.requests == []
```

Cover medium/high mapping, exact output schema, invalid JSON, extra keys, wrong
evidence refs, prompt injection in source content, timeout, cancellation, provider
error, malformed usage, one retry, second retry denial, cleanup, no raw exception,
and no ambient AI Studio key in Vertex mode.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_google_council.py tests/humanwire/test_google_organization_mapper.py -v`

Expected: missing modules.

- [ ] **Step 3: Implement Gemini 3.7 specialist runner**

```python
agent = Agent(
    name=specialist.key.value,
    description=specialist.mandate,
    model=runtime.model_id,
    instruction=build_system_instruction(specialist),
    output_schema=SpecialistCandidate,
    include_contents="none",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    timeout=assignment.timeout_seconds,
    generate_content_config=types.GenerateContentConfig(
        max_output_tokens=assignment.max_output_tokens,
        thinking_config=types.ThinkingConfig(
            thinking_level=assignment.thinking_level.value,
        ),
    ),
)
```

Build user payload as canonical JSON inside explicit untrusted-content delimiters.
Evidence tools return typed, bounded excerpts by allowed ID. Reserve before runner
construction, validate candidate after receipt, settle exact usage metadata, and
release on a proved no-charge failure.

- [ ] **Step 4: Implement typed Gemini organization mapper**

Send only normalized source fields needed for mapping, never connector credentials or
member UIDs. The output is `OrganizationGraphCandidate`; run all Task 2 graph checks.
Any ambiguous identity, manager, authority, or protected-attribute inference becomes
`NEEDS_REVIEW`, not a guessed committed value.

- [ ] **Step 5: Run GREEN and direct provider-boundary tests**

Run:

```powershell
python -m pytest tests/humanwire/test_google_council.py tests/humanwire/test_google_organization_mapper.py tests/humanwire/test_google_decision_engine.py -q
python -m ruff check src/humanwire/google_council.py src/humanwire/google_organization_mapper.py tests/humanwire/test_google_council.py tests/humanwire/test_google_organization_mapper.py
```

Expected: PASS using only local fake ADK/provider boundaries; no paid live call.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/google_council.py src/humanwire/google_organization_mapper.py src/humanwire/google_config.py tests/humanwire/test_google_council.py tests/humanwire/test_google_organization_mapper.py tests/humanwire/test_google_decision_engine.py
git commit -m "feat: run Gemini 3.7 company specialists"
```

### Task 6: Council orchestration, gateway, and human approval

**Files:**
- Create: `src/humanwire/council_gateway.py`
- Create: `src/humanwire/council_orchestrator.py`
- Create: `tests/humanwire/test_council_gateway.py`
- Create: `tests/humanwire/test_council_orchestrator.py`

**Interfaces:**
- Consumes: `CouncilPlan`, specialist runner, evidence registry, authority evaluator, budget ledger, authenticated approver context, and append-only run repository.
- Produces: `CouncilOrchestrator.run`, `CouncilAuthorityGateway.accept_candidate`, `create_approval_challenge`, and `decide_approval`.

- [ ] **Step 1: Write orchestration RED tests**

```python
def test_conflict_forces_risk_challenge_before_synthesis(orchestrator) -> None:
    result = orchestrator.run(plan_with_conflicting_market_and_finance())
    assert result.milestone_names.index("conflict_identified") < result.milestone_names.index("risk_challenge_completed")
    assert result.milestone_names.index("risk_challenge_completed") < result.milestone_names.index("recommendation_ready")


def test_ai_candidate_cannot_approve(gateway) -> None:
    with pytest.raises(CouncilRejected, match="human_approval_required"):
        gateway.decide_approval(ai_principal_context(), valid_approval_request())
```

Cover partial specialist failure, all-specialist failure, evidence request, invalid
candidate inertness, challenge/revision, deterministic commit order after parallel
work, deadline cancellation, late result, duplicate candidate, stale recommendation
digest, expired nonce, wrong authority, wrong tenant, replayed approval, reject, and
request-changes.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_council_gateway.py tests/humanwire/test_council_orchestrator.py -v`

Expected: missing modules.

- [ ] **Step 3: Implement staged orchestrator**

```python
class CouncilOrchestrator:
    def run(self, request: CouncilRunRequest) -> CouncilRunResult:
        plan = self._planner(request.plan_request)
        analyses = self._run_parallel(plan.domain_assignments)
        cross_exam = self._cross_examine(analyses)
        challenged = self._run_risk_if_required(plan, cross_exam)
        recommendation = self._synthesize(plan, analyses, challenged)
        return self._gateway.accept_recommendation(request, recommendation)
```

Parallel completion order is not authoritative. Commit domain candidates in plan
order after validation. Cross-examination uses only safe summaries and evidence refs.
Final recommendation separates confirmed facts, source assertions, model inference,
human assumptions, and unresolved conflicts.

- [ ] **Step 4: Implement exact human approval**

Approval requires an active membership and matching Authority Map assignment. Bind
challenge nonce, recommendation semantic digest, organization, workspace, run,
decision type, UID, workflow state, and expiry in one transaction. Model text and
reporting hierarchy never satisfy approval.

- [ ] **Step 5: Run GREEN and gateway regressions**

Run:

```powershell
python -m pytest tests/humanwire/test_council_gateway.py tests/humanwire/test_council_orchestrator.py -q
python -m pytest tests/humanwire/test_workflow.py tests/humanwire/test_caspian_gateway.py -q
```

Expected: council tests PASS; existing gateway/workflow tests unchanged.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/council_gateway.py src/humanwire/council_orchestrator.py tests/humanwire/test_council_gateway.py tests/humanwire/test_council_orchestrator.py
git commit -m "feat: orchestrate HumanWire company decisions"
```

### Task 7: Tenant-bound council store and private worker

**Files:**
- Create: `src/humanwire/decisionos_council_store.py`
- Create: `src/humanwire/decisionos_council_worker.py`
- Modify: `src/humanwire/cloud_worker_app.py`
- Create: `tests/humanwire/test_decisionos_council_store.py`
- Create: `tests/humanwire/test_decisionos_council_worker.py`
- Modify: `tests/humanwire/test_cloud_worker.py`

**Interfaces:**
- Consumes: council plan/orchestrator, Firestore, Pub/Sub OIDC identity, pricing settings, and organization/workspace bindings.
- Produces: `CouncilRunRepository`, `FirestoreCouncilRunRepository`, `DecisionOSCouncilWorker.handle`, and exact opaque dispatch messages.

- [ ] **Step 1: Write repository/worker RED tests**

```python
def test_worker_claims_one_tenant_bound_run_once(worker, repository) -> None:
    first = worker.handle(dispatch_for(ORG, WORKSPACE, RUN))
    second = worker.handle(dispatch_for(ORG, WORKSPACE, RUN))
    assert first is WorkerDisposition.COMPLETED
    assert second is WorkerDisposition.ALREADY_TERMINAL
    assert repository.load_run(ORG, WORKSPACE, RUN).state == "approval_required"


def test_dispatch_with_mismatched_org_is_inert(worker, repository) -> None:
    assert worker.handle(dispatch_for(ORG_B, WORKSPACE_A, RUN_A)) is WorkerDisposition.REJECTED
    assert repository.timeline(ORG_A, WORKSPACE_A, RUN_A) == ()
```

Cover exact Pub/Sub envelope, duplicate keys, lease renewal, crash recovery, late
worker, partial results, budget exhaustion, model timeout, worker shutdown, cross-
tenant path, private exceptions, terminal idempotency, and projection atomicity.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_decisionos_council_store.py tests/humanwire/test_decisionos_council_worker.py -v`

Expected: missing modules.

- [ ] **Step 3: Implement store and worker lifecycle**

Use collections `organizations/{org}/council_runs/{run}` for safe metadata/projection
and private run/timeline/usage collections for authoritative state. Claims bind org,
workspace, run, owner, expiry, attempt, plan digest, and price version. Publish terminal
projection only after final repository state is committed.

- [ ] **Step 4: Wire a separate private endpoint**

Add exact `POST /internal/pubsub/decisionos-councils` to the private worker app with
the existing OIDC/host/security envelope. Do not alter `/internal/pubsub/runs`
semantics. Startup refuses missing model/pricing/budget settings.

- [ ] **Step 5: Run GREEN and hard-timeout proof**

Run:

```powershell
python -m pytest tests/humanwire/test_decisionos_council_store.py tests/humanwire/test_decisionos_council_worker.py tests/humanwire/test_cloud_worker.py -q
```

Expected: PASS, with no worker/thread/process surviving the adversarial deadline test.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/decisionos_council_store.py src/humanwire/decisionos_council_worker.py src/humanwire/cloud_worker_app.py tests/humanwire/test_decisionos_council_store.py tests/humanwire/test_decisionos_council_worker.py tests/humanwire/test_cloud_worker.py
git commit -m "feat: execute DecisionOS councils privately"
```

### Task 8: Council API, live projection, and company workflow UI

**Files:**
- Create: `src/humanwire/council_projection.py`
- Create: `src/humanwire/decisionos_council_routes.py`
- Modify: `src/humanwire/decisionos_app.py`
- Modify: `src/humanwire/decisionos_web.py`
- Modify: `src/humanwire/templates/decisionos_shell.html`
- Create: `src/humanwire/decisionos_static/company-council.js`
- Create: `src/humanwire/decisionos_static/company-council.css`
- Create: `tests/humanwire/test_decisionos_council_app.py`
- Create: `tests/humanwire/company_council_frontend_harness.js`
- Create: `tests/humanwire/test_company_council_frontend.py`

**Interfaces:**
- Consumes: planner, run repository, Pub/Sub publisher, approval gateway, and safe projection.
- Produces: create/status/approval endpoints and live role/activity/conflict/revision/cost visualization.

- [ ] **Step 1: Write API and projection RED tests**

```python
def test_create_returns_selected_specialists_and_budget(client, owner_headers) -> None:
    response = client.post(
        f"/api/organizations/{ORG}/workspaces/{WORKSPACE}/councils",
        headers=owner_headers,
        json=valid_decision_request(),
    )
    assert response.status_code == 202
    assert response.json()["model"] == "Gemini 3.7 Flash"
    assert Decimal(response.json()["maximum_cost_usd"]) <= Decimal("0.25")


def test_approval_requires_exact_authority(client, contributor_headers) -> None:
    response = client.post(
        f"/api/organizations/{ORG}/workspaces/{WORKSPACE}/councils/{RUN}/approval",
        headers=contributor_headers,
        json=valid_approval_payload(),
    )
    assert response.status_code == 403
    assert response.json() == {"error": "approval_authority_required"}
```

Cover raw paths, methods, query strings, CSRF/App Check, body limits, duplicate keys,
wrong tenant/workspace, active-run conflict, budget refusal before dispatch, polling
ETag/ordinal, stale approval, fixed errors, no prompt/reasoning, and terminal hydration.

- [ ] **Step 2: Write hostile frontend RED tests**

The controllable Node harness must exercise create, 202 polling, unchanged 200/304,
parallel role updates, conflict, risk challenge, revision, approval required, reject,
request changes, approve, terminal hydration, pause/follow/replay, cost display,
visibility change, network failure, reduced motion, and mobile views.

- [ ] **Step 3: Run RED**

Run:

```powershell
python -m pytest tests/humanwire/test_decisionos_council_app.py tests/humanwire/test_company_council_frontend.py -v
```

Expected: routes/controller are missing.

- [ ] **Step 4: Implement exact APIs and safe projection**

Expose:

```text
POST /api/organizations/{org}/workspaces/{workspace}/councils
GET  /api/organizations/{org}/workspaces/{workspace}/councils/{run}
POST /api/organizations/{org}/workspaces/{workspace}/councils/{run}/approval-challenge
POST /api/organizations/{org}/workspaces/{workspace}/councils/{run}/approval
```

Projection includes specialist functional name, purpose, safe milestone, evidence
coverage counts, challenge/revision status, selected event, human action required,
estimated/reserved/final cost, and terminal artifacts. It excludes private source
text, prompts, hidden reasoning, provider response, connector values, emails, and UIDs.

- [ ] **Step 5: Implement the live company experience**

New Decision assembles the chosen departments, explains why each is active, and
animates real saved milestones through the company graph. Every specialist card is
clickable and exposes mandate, safe contribution, evidence refs, objections, status,
and cost. Human approval is a visually distinct terminal gate. Historical selection,
final outcome, and live state remain separate.

- [ ] **Step 6: Run GREEN and browser acceptance**

```powershell
python -m pytest tests/humanwire/test_decisionos_council_app.py tests/humanwire/test_company_council_frontend.py tests/humanwire/test_decisionos_frontend.py -q
node --check src/humanwire/decisionos_static/company-council.js
node tests/humanwire/company_council_frontend_harness.js
```

Browser-check a real completed council at 1680x950, 600x900, and 390x844. Require
readable department graph, no overlap/clipping, synchronized selected event, visible
human approval, visible model/cost, all effective controls at least 44px, and no
console error.

- [ ] **Step 7: Commit**

```powershell
git add src/humanwire/council_projection.py src/humanwire/decisionos_council_routes.py src/humanwire/decisionos_app.py src/humanwire/decisionos_web.py src/humanwire/templates/decisionos_shell.html src/humanwire/decisionos_static/company-council.js src/humanwire/decisionos_static/company-council.css tests/humanwire/test_decisionos_council_app.py tests/humanwire/company_council_frontend_harness.js tests/humanwire/test_company_council_frontend.py
git commit -m "feat: visualize Gemini company decisions"
```

### Task 9: Evaluation, deployment, live budget gate, and release

**Files:**
- Create: `src/humanwire/council_evals.py`
- Create: `tests/fixtures/humanwire/council/company_decisions_v1.json`
- Create: `tests/humanwire/test_council_evals.py`
- Modify: `infra/google/deploy-decisionos.ps1`
- Modify: `infra/google/deploy-decisionos.sh`
- Modify: `infra/google/README.md`
- Modify: `tests/humanwire/test_decisionos_deployment_contract.py`
- Create: `tests/humanwire/test_company_council_e2e.py`

**Interfaces:**
- Consumes: all council components and an explicit optional live-evaluation configuration.
- Produces: `CouncilEvaluation`, deterministic release gate, aggregate usage/quality report, and deployable feature flag.

- [ ] **Step 1: Write deterministic evaluation RED tests**

```python
def test_release_gate_rejects_unsupported_claim() -> None:
    evaluation = evaluate_case(case_with_unbacked_market_claim())
    assert evaluation.release_ready is False
    assert evaluation.unsupported_claim_rate > 0


def test_release_gate_rejects_budget_overrun() -> None:
    evaluation = evaluate_case(case_costing(Decimal("0.250001")))
    assert evaluation.release_ready is False
    assert "budget" in evaluation.failure_codes
```

Measure schema validity, evidence precision/coverage, unsupported claims, challenge
recall, authority correctness, deterministic commit order, timeout/partial rate,
latency, input/output tokens, and settled cost.

- [ ] **Step 2: Implement deterministic evaluators and E2E fixture**

The E2E creates an organization and authority map, starts a fundraising council,
selects exactly eight specialists, processes evidence-bound parallel responses,
records a conflict and risk challenge, revises the recommendation, requires the exact
human approver, records one approval, produces artifacts, and settles below $0.25.

- [ ] **Step 3: Add explicit live-evaluation command**

Live evaluation is disabled by default and requires project, model, case set, maximum
USD, and `--confirm-provider-cost`. It reserves the full maximum first and writes only
aggregate metrics. No deployment test or unit test performs a paid call.

- [ ] **Step 4: Update deployment configuration**

Add exact model ID, Google location, price version/effective date, input/output rates,
decision ceiling, specialist concurrency, timeout, and feature flag. Require Vertex
ADC; reject AI Studio keys. Validate `gemini-3.7-flash` availability during an
explicit operator preflight, not every app startup.

- [ ] **Step 5: Run final gates**

```powershell
python -m pytest tests/humanwire/test_council_models.py tests/humanwire/test_council_catalog.py tests/humanwire/test_council_planner.py tests/humanwire/test_council_budget.py tests/humanwire/test_google_council.py tests/humanwire/test_google_organization_mapper.py tests/humanwire/test_council_gateway.py tests/humanwire/test_council_orchestrator.py tests/humanwire/test_decisionos_council_store.py tests/humanwire/test_decisionos_council_worker.py tests/humanwire/test_decisionos_council_app.py tests/humanwire/test_company_council_frontend.py tests/humanwire/test_council_evals.py tests/humanwire/test_company_council_e2e.py -q
python -m pytest tests/humanwire/test_google_decision_engine.py tests/humanwire/test_google_e2e.py tests/humanwire/test_synthetic.py tests/humanwire/test_workflow.py tests/humanwire/test_caspian_gateway.py -q
python -m ruff check src tests
node --check src/humanwire/decisionos_static/company-council.js
node tests/humanwire/company_council_frontend_harness.js
git diff --check
```

- [ ] **Step 6: Independent authority, model, cost, privacy, product, and browser review**

Require no Critical or Important findings. Confirm the UI says Gemini 3.7 Flash,
functional AI specialist, and human approval required; confirm it never claims real
employees, autonomous approval, hidden provider verification, or unbounded cost.

- [ ] **Step 7: Commit**

```powershell
git add src/humanwire/council_evals.py tests/fixtures/humanwire/council tests/humanwire/test_council_evals.py tests/humanwire/test_company_council_e2e.py infra/google/deploy-decisionos.ps1 infra/google/deploy-decisionos.sh infra/google/README.md tests/humanwire/test_decisionos_deployment_contract.py
git commit -m "test: qualify Gemini company councils"
```
