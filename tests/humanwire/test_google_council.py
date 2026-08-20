from __future__ import annotations

import json
import time
from threading import Event

import pytest
from google.adk.agents import Agent, ParallelAgent, SequentialAgent
from google.adk.models import LlmResponse
from google.genai import types

import humanwire.google_council as google_council_module
from humanwire.council_models import (
    ChallengeSeverity,
    ClaimClassification,
    CouncilRecommendation,
    CouncilRunRequest,
)
from humanwire.council_registry import specialist_registry
from humanwire.council_tools import (
    CouncilEvidenceRecord,
    CouncilEvidenceStatus,
    CouncilToolContext,
)
from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
    OrganizationMembership,
    WorkspacePlaybook,
)
from humanwire.google_council import (
    CouncilExecutionFailure,
    CouncilExecutionStatus,
    GoogleCouncilRunner,
    build_council_workflow,
    workflow_shape,
)

ORG_ID = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
WORKSPACE_ID = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
DIGEST = "a" * 64
RESEARCH_IDS = (
    "market_intelligence",
    "financial_analysis",
    "product_technical",
    "risk_compliance",
)


class FakeRegistry:
    def __init__(self) -> None:
        self.record = CouncilEvidenceRecord(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            evidence_id="evidence_market_01",
            title="Pilot evaluation",
            sanitized_text="Three pilots completed the bounded evaluation period.",
            source_digest=DIGEST,
            extraction_version="extract-v1",
            status=CouncilEvidenceStatus.READY,
        )

    def list_evidence(self, organization_id: str, workspace_id: str):
        return (self.record,)

    def load_evidence(self, organization_id: str, workspace_id: str, evidence_id: str):
        return self.record if evidence_id == self.record.evidence_id else None

    def load_prior_decision(
        self, organization_id: str, workspace_id: str, decision_id: str
    ):
        return None


def _context() -> DecisionOSContext:
    return DecisionOSContext(
        principal=DecisionOSPrincipal(
            uid="founder-01",
            email_verified=True,
            provider_ids=("google.com",),
        ),
        membership=OrganizationMembership(
            organization_id=ORG_ID,
            uid="founder-01",
            role=DecisionOSRole.DECISION_OWNER,
            status=MembershipStatus.ACTIVE,
        ),
    )


def _request() -> CouncilRunRequest:
    return CouncilRunRequest(
        context=_context(),
        workspace_id=WORKSPACE_ID,
        decision_id="decision_launch_01",
        playbook_id=WorkspacePlaybook.LAUNCH_DECISION,
        objective="Decide whether the product is ready for a limited launch.",
        evidence_ids=("evidence_market_01",),
        policy_version="council-v1",
    )


def _tool_context() -> CouncilToolContext:
    return CouncilToolContext(
        context=_context(),
        workspace_id=WORKSPACE_ID,
        registry=FakeRegistry(),
    )


def _claim() -> dict[str, object]:
    return {
        "claim_id": "claim_market_01",
        "statement": "Three pilots completed the bounded evaluation period.",
        "classification": ClaimClassification.CONFIRMED_FACT.value,
        "evidence_ids": ["evidence_market_01"],
        "confidence": 0.9,
    }


def _challenge() -> dict[str, object]:
    return {
        "challenge_id": "challenge_red_01",
        "challenger_id": "red_team",
        "target_candidate_id": "candidate_market_intelligence_01",
        "challenged_claim_ids": ["claim_market_01"],
        "severity": ChallengeSeverity.MATERIAL.value,
        "issue": "Pilot completion does not establish paid conversion.",
        "required_action": "Confirm conversion evidence before approval.",
        "policy_version": "council-v1",
    }


def _recommendation(*, final: bool) -> dict[str, object]:
    return {
        "recommendation_id": (
            "recommendation_final_01" if final else "recommendation_draft_01"
        ),
        "summary": "Proceed only after conversion evidence is confirmed.",
        "claims": [_claim()],
        "challenges": [_challenge()] if final else [],
        "recommended_action": "Collect conversion evidence before approval.",
        "required_human_action": "An authorized approver must decide on the digest.",
        "source_candidate_ids": [
            f"candidate_{specialist_id}_01" for specialist_id in RESEARCH_IDS
        ],
        "policy_version": "council-v1",
    }


def _scripted_callback(calls: list[str], *, corrupt_final: bool = False):
    def callback(*, callback_context, llm_request):
        del llm_request
        name = callback_context.agent_name
        calls.append(name)
        if name in RESEARCH_IDS:
            payload = {
                "candidate_id": f"candidate_{name}_01",
                "specialist_id": name,
                "summary": "The bounded specialist reviewed the available evidence.",
                "claims": [_claim()],
                "questions": ["Which pilots converted to paid contracts?"],
                "recommended_action": "Confirm conversion evidence.",
                "policy_version": "council-v1",
            }
        elif name == "decision_synthesis":
            payload = _recommendation(final=False)
        elif name == "red_team":
            payload = _challenge()
        else:
            payload = _recommendation(final=True)
            if corrupt_final:
                payload["claims"][0]["evidence_ids"] = ["evidence_invented_01"]
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=json.dumps(payload))],
            ),
            turn_complete=True,
        )

    return callback


def test_graph_runs_parallel_research_then_challenge() -> None:
    created: list[str] = []

    def fake_agent_factory(specialist):
        created.append(specialist.specialist_id)
        return Agent(
            name=specialist.specialist_id,
            model="gemini-3.6-flash",
            instruction="Return one typed bounded result.",
        )

    workflow = build_council_workflow(fake_agent_factory)

    assert isinstance(workflow, SequentialAgent)
    assert isinstance(workflow.sub_agents[0], ParallelAgent)
    assert workflow_shape(workflow) == {
        "parallel": list(RESEARCH_IDS),
        "then": ["decision_synthesis", "red_team", "final_synthesis"],
    }
    assert tuple(created) == RESEARCH_IDS + (
        "decision_synthesis",
        "red_team",
        "final_synthesis",
    )


def test_real_adk_council_returns_typed_deterministic_result() -> None:
    calls: list[str] = []
    published = []
    runner = GoogleCouncilRunner(
        model_identifier="gemini-3.6-flash",
        tool_context=_tool_context(),
        before_model_callback=_scripted_callback(calls),
    )

    result = runner.run(
        _request(),
        deadline=time.monotonic() + 15,
        cancellation=Event(),
        on_event=published.append,
    )

    assert isinstance(result.recommendation, CouncilRecommendation)
    assert tuple(item.specialist_id for item in result.candidates) == RESEARCH_IDS
    assert len(result.challenges) == 1
    assert result.partial is False
    assert set(calls) == set(RESEARCH_IDS) | {
        "decision_synthesis",
        "red_team",
        "final_synthesis",
    }
    assert result.events == tuple(published)
    assert result.events == tuple(sorted(result.events, key=lambda item: item.ordinal))
    assert all("PRIVATE" not in repr(item) for item in result.events)
    assert {
        item.specialist_id
        for item in result.events
        if item.status is CouncilExecutionStatus.COMPLETED
    } >= set(RESEARCH_IDS)


def test_model_output_cannot_invent_a_citation() -> None:
    runner = GoogleCouncilRunner(
        model_identifier="gemini-3.6-flash",
        tool_context=_tool_context(),
        before_model_callback=_scripted_callback([], corrupt_final=True),
    )

    with pytest.raises(CouncilExecutionFailure, match="^invalid_output$") as captured:
        runner.run(
            _request(),
            deadline=time.monotonic() + 15,
            cancellation=Event(),
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_provider_exception_has_fixed_private_boundary(monkeypatch) -> None:
    warnings: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        google_council_module._LOGGER,
        "warning",
        lambda *args, **kwargs: warnings.append((*args, kwargs)),
    )

    def failing_callback(*, callback_context, llm_request):
        del callback_context, llm_request
        raise RuntimeError("PRIVATE-GEMINI-PAYLOAD C:\\private\\credentials.json")

    runner = GoogleCouncilRunner(
        model_identifier="gemini-3.6-flash",
        tool_context=_tool_context(),
        before_model_callback=failing_callback,
    )

    with pytest.raises(
        CouncilExecutionFailure, match="^provider_unavailable$"
    ) as captured:
        runner.run(
            _request(),
            deadline=time.monotonic() + 15,
            cancellation=Event(),
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "PRIVATE" not in repr(captured.value)
    assert "credentials" not in repr(captured.value)
    assert warnings == [
        (
            "council_provider_failed category=%s code=%s",
            "runtime",
            "none",
            {},
        )
    ]


@pytest.mark.parametrize("cancelled", [False, True])
def test_deadline_or_cancellation_stops_before_model(
    cancelled: bool,
) -> None:
    calls: list[str] = []
    cancellation = Event()
    if cancelled:
        cancellation.set()
    runner = GoogleCouncilRunner(
        model_identifier="gemini-3.6-flash",
        tool_context=_tool_context(),
        before_model_callback=_scripted_callback(calls),
    )

    with pytest.raises(CouncilExecutionFailure, match="^timeout$"):
        runner.run(
            _request(),
            deadline=time.monotonic() - 1,
            cancellation=cancellation,
        )
    assert calls == []


def test_council_has_no_authoritative_repository_capability() -> None:
    class FakeRepository:
        authoritative_mutation_count = 0

    repository = FakeRepository()
    runner = GoogleCouncilRunner(
        model_identifier="gemini-3.6-flash",
        tool_context=_tool_context(),
        before_model_callback=_scripted_callback([]),
    )

    runner.run(
        _request(),
        deadline=time.monotonic() + 15,
        cancellation=Event(),
    )

    assert repository.authoritative_mutation_count == 0


def test_leaf_agents_use_vertex_compatible_json_contracts_budgets_and_tools() -> None:
    runner = GoogleCouncilRunner(
        model_identifier="gemini-3.6-flash",
        tool_context=_tool_context(),
        before_model_callback=_scripted_callback([]),
    )
    workflow = runner.build_workflow(_request())
    leaves = tuple(workflow.sub_agents[0].sub_agents) + tuple(workflow.sub_agents[1:])
    definitions = {
        item.specialist_id: item
        for item in specialist_registry(WorkspacePlaybook.LAUNCH_DECISION)
    }

    for agent in leaves:
        definition = definitions[agent.name]
        assert agent.timeout <= definition.timeout_seconds
        assert agent.retry_config.max_attempts == definition.maximum_attempts
        assert agent.generate_content_config.max_output_tokens == definition.token_budget
        assert {tool.name for tool in agent.tools} <= definition.tool_allowlist
        # ADK's output_schema adapter becomes a set_model_response function
        # declaration that Gemini 3.5 Flash rejects on Vertex.  Keep the model
        # call provider-compatible and validate the returned JSON ourselves.
        assert agent.output_schema is None
        assert "Return only one compact JSON object" in agent.instruction
        assert '"policy_version"' in agent.instruction
        if agent.name in RESEARCH_IDS:
            assert '"candidate_id"' in agent.instruction
            assert '"claims"' in agent.instruction
            assert f"claim_{agent.name}_01" in agent.instruction
        elif agent.name == "red_team":
            assert '"challenge_id"' in agent.instruction
            assert '"challenged_claim_ids"' in agent.instruction
        else:
            assert '"recommendation_id"' in agent.instruction
            assert '"source_candidate_ids"' in agent.instruction
