"""Safe, meaningful public projection of a DecisionOS council run."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from humanwire.council_models import CouncilChallenge, EvidenceClaim
from humanwire.council_registry import specialist_registry
from humanwire.decisionos_models import WorkspacePlaybook
from humanwire.google_council import (
    CouncilExecutionEvent,
    CouncilExecutionResult,
    CouncilExecutionStatus,
)

_ORDER = (
    "market_intelligence",
    "financial_analysis",
    "product_technical",
    "risk_compliance",
    "decision_synthesis",
    "red_team",
    "final_synthesis",
)


class _ProjectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CouncilNodeProjection(_ProjectionModel):
    specialist_id: str
    display_name: str
    purpose: str
    status: Literal["waiting", "running", "complete", "failed", "blocked"]
    summary: str | None = None


class CouncilHandoffProjection(_ProjectionModel):
    source_specialist_id: str
    target_specialist_id: str
    relationship: Literal["research_input", "draft_review", "challenge_resolution"]


class CouncilClaimProjection(_ProjectionModel):
    claim_id: str
    statement: str
    classification: Literal[
        "confirmed_fact",
        "source_assertion",
        "model_inference",
        "human_assumption",
        "unresolved_conflict",
    ]
    evidence_ids: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)


class CouncilProjection(_ProjectionModel):
    run_id: str
    objective: str
    state: Literal[
        "running",
        "failed",
        "blocked",
        "human_approval_required",
        "complete",
    ]
    nodes: tuple[CouncilNodeProjection, ...]
    handoffs: tuple[CouncilHandoffProjection, ...]
    evidence_claims: tuple[CouncilClaimProjection, ...] = ()
    inference_claims: tuple[CouncilClaimProjection, ...] = ()
    challenges: tuple[CouncilChallenge, ...] = ()
    recommendation_summary: str | None = None
    recommended_action: str | None = None
    required_human_action: str | None = None
    recommendation_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    events: tuple[CouncilExecutionEvent, ...] = ()


def _claim_projection(claim: EvidenceClaim) -> CouncilClaimProjection:
    return CouncilClaimProjection(
        claim_id=claim.claim_id,
        statement=claim.statement,
        classification=claim.classification.value,
        evidence_ids=claim.evidence_ids,
        confidence=claim.confidence,
    )


def _node_statuses(
    events: tuple[CouncilExecutionEvent, ...],
) -> dict[str, Literal["waiting", "running", "complete", "failed", "blocked"]]:
    statuses: dict[
        str, Literal["waiting", "running", "complete", "failed", "blocked"]
    ] = {name: "waiting" for name in _ORDER}
    for event in sorted(events, key=lambda item: item.ordinal):
        if event.specialist_id not in statuses:
            continue
        if event.status is CouncilExecutionStatus.STARTED:
            statuses[event.specialist_id] = "running"
        elif event.status is CouncilExecutionStatus.COMPLETED:
            statuses[event.specialist_id] = "complete"
        elif event.status is CouncilExecutionStatus.FAILED:
            statuses[event.specialist_id] = "failed"
    return statuses


def build_council_projection(
    *,
    run_id: str,
    objective: str,
    events: tuple[CouncilExecutionEvent, ...] = (),
    result: CouncilExecutionResult | None = None,
    failed: bool = False,
) -> CouncilProjection:
    """Collapse safe execution metadata and typed output into a synchronized view."""

    if result is not None:
        events = result.events
    definitions = {
        item.specialist_id: item
        for item in specialist_registry(WorkspacePlaybook.LAUNCH_DECISION)
    }
    statuses = _node_statuses(events)
    summaries: dict[str, str] = {}
    if result is not None:
        summaries.update(
            {item.specialist_id: item.summary for item in result.candidates}
        )
        summaries["red_team"] = result.challenges[0].issue
        summaries["final_synthesis"] = result.recommendation.summary
    nodes = tuple(
        CouncilNodeProjection(
            specialist_id=name,
            display_name=definitions[name].display_name,
            purpose=definitions[name].purpose,
            status=statuses[name],
            summary=summaries.get(name),
        )
        for name in _ORDER
    )
    handoffs = tuple(
        CouncilHandoffProjection(
            source_specialist_id=name,
            target_specialist_id="decision_synthesis",
            relationship="research_input",
        )
        for name in _ORDER[:4]
    ) + (
        CouncilHandoffProjection(
            source_specialist_id="decision_synthesis",
            target_specialist_id="red_team",
            relationship="draft_review",
        ),
        CouncilHandoffProjection(
            source_specialist_id="red_team",
            target_specialist_id="final_synthesis",
            relationship="challenge_resolution",
        ),
    )
    if result is None:
        return CouncilProjection(
            run_id=run_id,
            objective=objective,
            state="failed" if failed else "running",
            nodes=nodes,
            handoffs=handoffs,
            events=events,
        )
    sourced: list[CouncilClaimProjection] = []
    inferred: list[CouncilClaimProjection] = []
    for claim in result.recommendation.claims:
        projected = _claim_projection(claim)
        if claim.evidence_ids:
            sourced.append(projected)
        else:
            inferred.append(projected)
    return CouncilProjection(
        run_id=run_id,
        objective=objective,
        state="human_approval_required",
        nodes=nodes,
        handoffs=handoffs,
        evidence_claims=tuple(sourced),
        inference_claims=tuple(inferred),
        challenges=result.challenges,
        recommendation_summary=result.recommendation.summary,
        recommended_action=result.recommendation.recommended_action,
        required_human_action=result.recommendation.required_human_action,
        recommendation_digest=result.recommendation.semantic_digest,
        events=events,
    )
