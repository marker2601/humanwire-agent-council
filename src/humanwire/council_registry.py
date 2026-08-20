"""Versioned functional specialist registries for DecisionOS playbooks."""

from __future__ import annotations

from humanwire.council_models import CouncilSpecialist
from humanwire.decisionos_models import WorkspacePlaybook

_READ_ONLY_TOOLS = frozenset(
    {"list_evidence", "read_evidence_excerpt", "read_prior_decision"}
)
_POLICY = "council-v1"


def _specialist(
    specialist_id: str,
    display_name: str,
    purpose: str,
    *,
    inputs: tuple[str, ...],
    output_schema: str = "CouncilCandidate",
    timeout_seconds: int = 75,
    token_budget: int = 2_048,
) -> CouncilSpecialist:
    return CouncilSpecialist(
        specialist_id=specialist_id,
        display_name=display_name,
        purpose=purpose,
        tool_allowlist=_READ_ONLY_TOOLS,
        required_inputs=inputs,
        output_schema=output_schema,
        timeout_seconds=timeout_seconds,
        token_budget=token_budget,
        maximum_attempts=2,
        policy_version=_POLICY,
    )


_COMMON_RESEARCH = (
    _specialist(
        "objective_framing",
        "Objective Framing",
        "Defines the exact decision, constraints, and unresolved questions.",
        inputs=("objective", "decision_context"),
        timeout_seconds=45,
        token_budget=1_024,
    ),
    _specialist(
        "market_intelligence",
        "Market Intelligence",
        "Tests market claims against cited customer and market evidence.",
        inputs=("objective", "evidence_catalog"),
    ),
    _specialist(
        "financial_analysis",
        "Financial Analysis",
        "Checks financial coherence, runway, and bounded scenario assumptions.",
        inputs=("objective", "evidence_catalog"),
    ),
    _specialist(
        "product_technical",
        "Product and Technical Diligence",
        "Evaluates product readiness, differentiation, and technical execution risk.",
        inputs=("objective", "evidence_catalog"),
    ),
    _specialist(
        "risk_compliance",
        "Risk and Compliance",
        "Surfaces material operational, legal, security, and policy gaps.",
        inputs=("objective", "evidence_catalog"),
    ),
    _specialist(
        "stakeholder_authority",
        "Stakeholder and Authority",
        "Identifies missing perspectives and the exact human authority still required.",
        inputs=("objective", "decision_context"),
        timeout_seconds=45,
        token_budget=1_024,
    ),
)

_FUNDRAISING_ONLY = (
    _specialist(
        "investor_fit",
        "Investor Fit",
        "Assesses narrative and investor-fit assumptions without predicting funding.",
        inputs=("objective", "evidence_catalog", "prior_decisions"),
    ),
    _specialist(
        "diligence_readiness",
        "Diligence Readiness",
        "Finds missing diligence evidence and classifies unresolved readiness gaps.",
        inputs=("objective", "evidence_catalog"),
    ),
)

_SYNTHESIS = (
    _specialist(
        "decision_synthesis",
        "Decision Synthesis",
        "Combines specialist candidates into one evidence-bound draft recommendation.",
        inputs=("specialist_candidates", "evidence_catalog"),
        output_schema="CouncilRecommendation",
        timeout_seconds=90,
        token_budget=3_072,
    ),
    _specialist(
        "red_team",
        "Red Team",
        "Challenges unsupported claims, contradictions, and unsafe conclusions.",
        inputs=("draft_recommendation", "evidence_catalog"),
        output_schema="CouncilChallenge",
        timeout_seconds=75,
        token_budget=2_048,
    ),
    _specialist(
        "final_synthesis",
        "Final Synthesis",
        "Resolves valid challenges and prepares the exact digest for human review.",
        inputs=("draft_recommendation", "red_team_challenges"),
        output_schema="CouncilRecommendation",
        timeout_seconds=90,
        token_budget=3_072,
    ),
)

_LAUNCH = _COMMON_RESEARCH + _SYNTHESIS
_FUNDRAISING = _COMMON_RESEARCH + _FUNDRAISING_ONLY + _SYNTHESIS


def specialist_registry(
    playbook_id: WorkspacePlaybook | str,
) -> tuple[CouncilSpecialist, ...]:
    try:
        playbook = WorkspacePlaybook(playbook_id)
    except (TypeError, ValueError):
        raise ValueError("unsupported_playbook") from None
    if playbook is WorkspacePlaybook.LAUNCH_DECISION:
        return _LAUNCH
    if playbook is WorkspacePlaybook.FUNDRAISING_READINESS:
        return _FUNDRAISING
    raise ValueError("unsupported_playbook")
