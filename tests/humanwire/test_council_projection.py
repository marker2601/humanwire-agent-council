from __future__ import annotations

from humanwire.council_models import (
    ChallengeSeverity,
    ClaimClassification,
    CouncilCandidate,
    CouncilChallenge,
    CouncilRecommendation,
    EvidenceClaim,
)
from humanwire.council_projection import build_council_projection
from humanwire.google_council import (
    CouncilExecutionEvent,
    CouncilExecutionResult,
    CouncilExecutionStatus,
)


def _result() -> CouncilExecutionResult:
    fact = EvidenceClaim(
        claim_id="claim_market_01",
        statement="Three pilots completed the evaluation period.",
        classification=ClaimClassification.CONFIRMED_FACT,
        evidence_ids=("evidence_market_01",),
        confidence=0.9,
    )
    inference = EvidenceClaim(
        claim_id="claim_inference_01",
        statement="The limited launch may improve conversion learning.",
        classification=ClaimClassification.MODEL_INFERENCE,
        evidence_ids=(),
        confidence=0.65,
    )
    candidate_ids = tuple(
        f"candidate_{name}_01"
        for name in (
            "market_intelligence",
            "financial_analysis",
            "product_technical",
            "risk_compliance",
        )
    )
    candidates = tuple(
        CouncilCandidate(
            candidate_id=candidate_id,
            specialist_id=candidate_id.removeprefix("candidate_").removesuffix("_01"),
            summary="The specialist completed an evidence-bound review.",
            claims=(fact,),
            questions=("Which pilots converted to paid contracts?",),
            recommended_action="Confirm conversion evidence.",
            policy_version="council-v1",
        )
        for candidate_id in candidate_ids
    )
    challenge = CouncilChallenge(
        challenge_id="challenge_red_01",
        challenger_id="red_team",
        target_candidate_id=candidate_ids[0],
        challenged_claim_ids=(fact.claim_id,),
        severity=ChallengeSeverity.MATERIAL,
        issue="Pilot completion does not establish paid conversion.",
        required_action="Confirm conversion before broad launch.",
        policy_version="council-v1",
    )
    recommendation = CouncilRecommendation(
        recommendation_id="recommendation_final_01",
        summary="Proceed with a limited launch after conversion evidence is confirmed.",
        claims=(fact, inference),
        challenges=(challenge,),
        recommended_action="Collect conversion evidence and run a limited launch.",
        required_human_action="An authorized approver must decide on this exact digest.",
        source_candidate_ids=candidate_ids,
        policy_version="council-v1",
    )
    names = (
        "market_intelligence",
        "financial_analysis",
        "product_technical",
        "risk_compliance",
        "decision_synthesis",
        "red_team",
        "final_synthesis",
    )
    events = tuple(
        CouncilExecutionEvent(
            ordinal=index,
            specialist_id=name,
            display_name=name.replace("_", " ").title(),
            status=CouncilExecutionStatus.COMPLETED,
        )
        for index, name in enumerate(names, start=1)
    )
    return CouncilExecutionResult(
        candidates=candidates,
        challenges=(challenge,),
        recommendation=recommendation,
        events=events,
    )


def test_projection_exposes_real_graph_claim_types_and_human_boundary() -> None:
    projection = build_council_projection(
        run_id="council_run_01",
        objective="Decide whether the product is ready for a limited launch.",
        result=_result(),
    )

    assert projection.state == "human_approval_required"
    assert [node.specialist_id for node in projection.nodes] == [
        "market_intelligence",
        "financial_analysis",
        "product_technical",
        "risk_compliance",
        "decision_synthesis",
        "red_team",
        "final_synthesis",
    ]
    assert all(node.status == "complete" for node in projection.nodes)
    assert len(projection.handoffs) == 6
    assert projection.evidence_claims[0].classification == "confirmed_fact"
    assert projection.inference_claims[0].classification == "model_inference"
    assert projection.required_human_action.startswith("An authorized approver")
    assert projection.recommendation_digest == _result().recommendation.semantic_digest


def test_projection_contains_no_prompt_tool_payload_or_private_context() -> None:
    projection = build_council_projection(
        run_id="council_run_01",
        objective="Decide whether the product is ready for a limited launch.",
        result=_result(),
    )
    payload = projection.model_dump_json()

    assert "founder-01" not in payload
    assert "google.com" not in payload
    assert "tool_payload" not in payload
    assert "prompt" not in payload
    assert "UNTRUSTED" not in payload


def test_running_projection_keeps_waiting_running_and_complete_distinct() -> None:
    events = (
        CouncilExecutionEvent(
            ordinal=1,
            specialist_id="market_intelligence",
            display_name="Market Intelligence",
            status=CouncilExecutionStatus.COMPLETED,
        ),
        CouncilExecutionEvent(
            ordinal=2,
            specialist_id="financial_analysis",
            display_name="Financial Analysis",
            status=CouncilExecutionStatus.STARTED,
        ),
    )

    projection = build_council_projection(
        run_id="council_run_01",
        objective="Assess launch readiness.",
        events=events,
    )
    statuses = {node.specialist_id: node.status for node in projection.nodes}

    assert projection.state == "running"
    assert statuses["market_intelligence"] == "complete"
    assert statuses["financial_analysis"] == "running"
    assert statuses["red_team"] == "waiting"
