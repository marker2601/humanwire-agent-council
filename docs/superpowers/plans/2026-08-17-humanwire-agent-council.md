# HumanWire Transparent Agent Council Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Replace the shallow one-agent experience with a visible, evidence-bound Google ADK specialist graph whose outputs remain advisory to the HumanWire authority gateway.

**Architecture:** Parallel functional specialists read tenant-scoped sanitized evidence through server tools, followed by synthesis, red-team, and final-synthesis nodes. The HumanWire gateway validates identity, evidence, state, and authority before anything persists; no ADK tool writes authoritative state.

**Tech Stack:** Google ADK 2.x graph workflows, Gemini on Vertex AI, Pydantic v2, existing HumanWire gateway/workflow/repository, Firestore, pytest.

**Spec:** docs/superpowers/specs/2026-08-17-humanwire-decisionos-design.md

## Global Constraints

- Firebase identity and active membership are prerequisites for a private council.
- Agents use functional names and never impersonate people.
- Model output cannot authenticate, authorize, approve, send, schedule, or persist directly.
- Every factual claim is classified and bound to evidence or rendered as an inference or assumption.
- Existing deterministic and Google submission flows remain byte-compatible unless an explicit DecisionOS council flag is enabled.
- Every model/tool call has a timeout, token budget, retry cap, and safe failure result.
- No tool, trace, log, or projection may expose credentials, routes, email addresses, private identifiers, or unrelated source content.

---

### Task 1: Council contracts and specialist registry

**Files:**
- Create: src/humanwire/council_models.py
- Create: src/humanwire/council_registry.py
- Test: tests/humanwire/test_council_models.py

**Interfaces:**
- Consumes: DecisionOSContext and Pydantic strict models.
- Produces: CouncilSpecialist, EvidenceClaim, CouncilCandidate, CouncilChallenge, CouncilRecommendation, CouncilRunRequest, and specialist_registry(playbook_id).

- [ ] **Step 1: Write schema and truth-boundary RED tests**

~~~python
def test_confirmed_fact_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        EvidenceClaim(
            statement="Revenue grew.",
            classification="confirmed_fact",
            evidence_ids=(),
            confidence=0.9,
        )


def test_registry_uses_functional_specialists() -> None:
    ids = {item.specialist_id for item in specialist_registry("launch_decision")}
    assert {"market_intelligence", "risk_compliance", "red_team", "decision_synthesis"} <= ids
~~~

Add cases for unknown claim classes, duplicate evidence IDs, unsafe content, unbounded confidence, absent policy version, noncanonical specialist order, and private path/token/email strings.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_council_models.py -v
Expected: collection fails because the modules do not exist.

- [ ] **Step 3: Implement strict contracts and versioned registries**

~~~python
class ClaimClassification(StrEnum):
    CONFIRMED_FACT = "confirmed_fact"
    SOURCE_ASSERTION = "source_assertion"
    MODEL_INFERENCE = "model_inference"
    HUMAN_ASSUMPTION = "human_assumption"
    UNRESOLVED_CONFLICT = "unresolved_conflict"


class CouncilCandidate(_CouncilModel):
    specialist_id: str
    summary: str = Field(min_length=1, max_length=600)
    claims: tuple[EvidenceClaim, ...] = Field(max_length=20)
    questions: tuple[str, ...] = Field(default=(), max_length=10)
    recommended_action: str = Field(min_length=1, max_length=400)
~~~

Registry entries define display name, purpose, tool allowlist, required inputs, output schema, timeout, token budget, and maximum attempts.

- [ ] **Step 4: Run GREEN, lint, and commit**

~~~bash
python -m pytest tests/humanwire/test_council_models.py -v
python -m ruff check src/humanwire/council_models.py src/humanwire/council_registry.py tests/humanwire/test_council_models.py
git add src/humanwire/council_models.py src/humanwire/council_registry.py tests/humanwire/test_council_models.py
git commit -m "feat: define transparent council contracts"
~~~

### Task 2: Tenant-scoped evidence tools

**Files:**
- Create: src/humanwire/council_tools.py
- Test: tests/humanwire/test_council_tools.py

**Interfaces:**
- Consumes: DecisionOSContext, opaque evidence registry, and ADK FunctionTool.
- Produces: CouncilToolContext, list_evidence, read_evidence_excerpt, read_prior_decision, and build_council_tools.

- [ ] **Step 1: Write authorization and minimization RED tests**

~~~python
def test_tool_cannot_cross_organization(tool_context) -> None:
    with pytest.raises(CouncilToolDenied, match="evidence_unavailable"):
        read_evidence_excerpt(tool_context, "other_org_evidence", 0, 500)


def test_output_is_bounded_and_cited(tool_context) -> None:
    result = read_evidence_excerpt(tool_context, "evidence_01", 0, 500)
    assert len(result.text) <= 500
    assert result.evidence_id == "evidence_01"
    assert result.source_digest
~~~

Cover traversal, oversized spans, deleted/quarantined sources, stale extraction, private exception graphs, and attempts to pass credentials/contact fields.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_council_tools.py -v
Expected: missing-module collection failure.

- [ ] **Step 3: Implement read-only FunctionTools**

~~~python
def build_council_tools(context: CouncilToolContext) -> tuple[FunctionTool, ...]:
    return (
        FunctionTool(partial(list_evidence, context)),
        FunctionTool(partial(read_evidence_excerpt, context)),
        FunctionTool(partial(read_prior_decision, context)),
    )
~~~

Do not expose repository/client objects to ADK. Tool functions return strict safe models and have no mutation methods.

- [ ] **Step 4: Run GREEN, lint, and commit**

~~~bash
python -m pytest tests/humanwire/test_council_tools.py -v
python -m ruff check src/humanwire/council_tools.py tests/humanwire/test_council_tools.py
git add src/humanwire/council_tools.py tests/humanwire/test_council_tools.py
git commit -m "feat: add evidence-bound council tools"
~~~

### Task 3: Real ADK graph execution

**Files:**
- Create: src/humanwire/google_council.py
- Test: tests/humanwire/test_google_council.py

**Interfaces:**
- Consumes: Tasks 1–2, google.adk.workflow.Workflow, ADK Agent, and injected runner/model factory.
- Produces: build_council_workflow, GoogleCouncilRunner.run, and ordered CouncilExecutionEvent callbacks.

- [ ] **Step 1: Write graph-shape and execution RED tests**

~~~python
def test_graph_runs_parallel_research_then_challenge() -> None:
    workflow = build_council_workflow(fake_agent_factory)
    assert workflow_shape(workflow) == {
        "parallel": ["market_intelligence", "financial_analysis", "product_technical", "risk_compliance"],
        "then": ["decision_synthesis", "red_team", "final_synthesis"],
    }


def test_agent_output_cannot_mutate_repository(fake_repository, council_runner) -> None:
    council_runner.run(council_request())
    assert fake_repository.authoritative_mutation_count == 0
~~~

Add partial failure, timeout, malformed output, invalid citation, duplicate callback, runner close, bounded retry, deterministic ordering, and model exception privacy cases.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_google_council.py -v
Expected: missing-module collection failure.

- [ ] **Step 3: Build an explicit graph**

~~~python
workflow = Workflow(
    name="humanwire_decision_council",
    input_schema=CouncilRunRequest,
    output_schema=CouncilRecommendation,
    state_schema=CouncilState,
    max_concurrency=4,
    edges=[
        (START, (market_agent, financial_agent, product_agent, risk_agent)),
        ((market_agent, financial_agent, product_agent, risk_agent), synthesis_agent),
        (synthesis_agent, red_team_agent),
        (red_team_agent, final_synthesis_agent),
    ],
)
~~~

Verify the installed ADK API at implementation time. Each Agent uses a strict output schema, explicit tool list, bounded generation configuration, and callbacks that publish only safe stage metadata.

- [ ] **Step 4: Run GREEN, lint, and commit**

~~~bash
python -m pytest tests/humanwire/test_google_council.py -v
python -m ruff check src/humanwire/google_council.py tests/humanwire/test_google_council.py
git add src/humanwire/google_council.py tests/humanwire/test_google_council.py
git commit -m "feat: execute HumanWire ADK council graph"
~~~

### Task 4: Gateway integration and human approval digest

**Files:**
- Create: src/humanwire/council_gateway.py
- Modify: src/humanwire/cloud_worker.py
- Modify: src/humanwire/cloud_store.py
- Test: tests/humanwire/test_council_gateway.py
- Test: tests/humanwire/test_cloud_worker.py

**Interfaces:**
- Consumes: CouncilRecommendation and current repository/gateway transactions.
- Produces: CouncilGatewayResult, inert/accepted council events, and ApprovalChallenge bound to a semantic digest.

- [ ] **Step 1: Write authority RED tests**

~~~python
def test_unconfirmed_recommendation_is_inert(gateway, repository) -> None:
    result = gateway.evaluate(recommendation_with_unconfirmed_fact())
    assert result.accepted is False
    assert result.reason == "evidence_unconfirmed"
    assert repository.decision_mutation_count == 0


def test_approval_binds_identity_role_workspace_and_digest(gateway, context) -> None:
    challenge = gateway.prepare_approval(valid_recommendation(), context)
    assert challenge.organization_id == context.organization_id
    assert challenge.approver_role is DecisionOSRole.APPROVER
    assert challenge.recommendation_digest == valid_recommendation().semantic_digest
~~~

Add stale digest, expired nonce, wrong tenant, revoked membership, duplicate approval, model-supplied approver, and cross-run replay cases.

- [ ] **Step 2: Run RED and implement the minimal gateway**

Run: python -m pytest tests/humanwire/test_council_gateway.py -v.

The gateway validates evidence bindings and converts only accepted candidates into existing HumanWire workflow commands. Rejected candidates append an inert audit event.

- [ ] **Step 3: Preserve legacy compatibility**

The worker selects the council only for DecisionOS runs with an explicit council_policy_version. Run:

~~~bash
python -m pytest tests/humanwire/test_cloud_worker.py tests/humanwire/test_google_e2e.py tests/humanwire/test_synthetic.py -q
~~~

Expected: PASS without fixture changes.

- [ ] **Step 4: Commit**

~~~bash
git add src/humanwire/council_gateway.py src/humanwire/cloud_worker.py src/humanwire/cloud_store.py tests/humanwire/test_council_gateway.py tests/humanwire/test_cloud_worker.py
git commit -m "feat: bind council output to HumanWire authority"
~~~

### Task 5: Meaningful realtime council projection

**Files:**
- Create: src/humanwire/council_projection.py
- Modify: src/humanwire/templates/decisionos_shell.html
- Modify: src/humanwire/decisionos_static/decisionos-app.js
- Modify: src/humanwire/decisionos_static/decisionos.css
- Test: tests/humanwire/test_council_projection.py
- Test: tests/humanwire/decisionos_frontend_harness.js

**Interfaces:**
- Consumes: safe execution events and accepted gateway results.
- Produces: specialist nodes, evidence edges, challenges, assumptions, required human actions, and artifact summary.

- [ ] **Step 1: Write projection RED tests**

Assert repetitive persistence rows collapse into milestones; running, waiting, blocked, failed, and complete remain distinct; inference is visibly separate from evidence; prompts and tool payloads are absent; graph, evidence, activity, and selected event stay synchronized.

- [ ] **Step 2: Write controller RED tests**

The harness must prove realtime updates, reconnect/poll fallback, historical selection, terminal hydration, no stale queue, keyboard navigation, accessible tooltips, and a clear Human approval required state.

- [ ] **Step 3: Implement projection and UI**

Render functional specialist names and their purposes. Each edge represents a real dependency or handoff. Default activity shows milestones; an audit toggle shows exact safe events.

- [ ] **Step 4: Run GREEN, browser QA, and commit**

~~~bash
python -m pytest tests/humanwire/test_council_projection.py tests/humanwire/test_decisionos_frontend.py -q
node tests/humanwire/decisionos_frontend_harness.js
git add src/humanwire/council_projection.py src/humanwire/templates/decisionos_shell.html src/humanwire/decisionos_static tests/humanwire/test_council_projection.py tests/humanwire/decisionos_frontend_harness.js
git commit -m "feat: visualize evidence-bound agent council"
~~~

Browser-check 1680×950, 600×900, and 390×844 using a real completed council. Require no overlap, clipping, sub-44px controls, or console errors.

### Task 6: Evaluation, budget, and release gate

**Files:**
- Create: src/humanwire/council_evals.py
- Create: tests/fixtures/humanwire/council/launch_decision_v1.json
- Create: tests/humanwire/test_council_evals.py
- Modify: infra/google/README.md

**Interfaces:**
- Consumes: completed council results and frozen cases.
- Produces: CouncilEvaluation, aggregate cost/latency counters, and release threshold command.

- [ ] **Step 1: Write evaluation RED tests**

~~~python
def test_release_gate_rejects_unsupported_claims() -> None:
    evaluation = evaluate_case(case_with_hallucinated_citation())
    assert evaluation.release_ready is False
    assert evaluation.unsupported_claim_rate > 0
~~~

Evaluate schema validity, evidence precision/coverage, unsupported claims, red-team issue recall, gateway acceptance, latency, timeout rate, and estimated cost.

- [ ] **Step 2: Implement deterministic evaluators**

Default evaluation makes no provider call. Live evaluation requires an explicit project, budget, case set, and confirmation flag, and writes only aggregate metrics.

- [ ] **Step 3: Run release gates and independent review**

~~~powershell
python -m pytest tests/humanwire/test_council_models.py tests/humanwire/test_council_tools.py tests/humanwire/test_google_council.py tests/humanwire/test_council_gateway.py tests/humanwire/test_council_projection.py tests/humanwire/test_council_evals.py -q
python -m pytest tests/humanwire/test_google_e2e.py tests/humanwire/test_synthetic.py -q
python -m ruff check src tests
git diff --check
~~~

Resolve all Critical and Important findings before enabling the council flag.

- [ ] **Step 4: Commit**

~~~bash
git add src/humanwire/council_evals.py tests/fixtures/humanwire/council tests/humanwire/test_council_evals.py infra/google/README.md
git commit -m "test: gate HumanWire council quality"
~~~
