from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from humanwire.council_models import (
    ChallengeSeverity,
    ClaimClassification,
    CouncilCandidate,
    CouncilChallenge,
    CouncilRecommendation,
    CouncilRunRequest,
    EvidenceClaim,
)
from humanwire.council_registry import specialist_registry
from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
    OrganizationMembership,
    WorkspacePlaybook,
)

ORG_ID = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
WORKSPACE_ID = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"


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


def _claim(**overrides: object) -> EvidenceClaim:
    values: dict[str, object] = {
        "claim_id": "claim_market_01",
        "statement": "Three signed pilots are present in the uploaded evidence.",
        "classification": ClaimClassification.CONFIRMED_FACT,
        "evidence_ids": ("evidence_pilots_01",),
        "confidence": 0.95,
    }
    values.update(overrides)
    return EvidenceClaim.model_validate(values)


def _candidate(**overrides: object) -> CouncilCandidate:
    values: dict[str, object] = {
        "candidate_id": "candidate_market_01",
        "specialist_id": "market_intelligence",
        "summary": "Pilot evidence supports a bounded market-validation claim.",
        "claims": (_claim(),),
        "questions": ("Which pilots have converted to paid contracts?",),
        "recommended_action": "Validate conversion evidence before approval.",
        "policy_version": "council-v1",
    }
    values.update(overrides)
    return CouncilCandidate.model_validate(values)


def test_confirmed_fact_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        _claim(evidence_ids=())


def test_source_assertion_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        _claim(
            classification=ClaimClassification.SOURCE_ASSERTION,
            evidence_ids=(),
        )


def test_human_assumption_cannot_masquerade_as_sourced() -> None:
    with pytest.raises(ValidationError):
        _claim(
            classification=ClaimClassification.HUMAN_ASSUMPTION,
            evidence_ids=("evidence_pilots_01",),
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01, "0.8", True])
def test_claim_confidence_is_strict_and_bounded(confidence: object) -> None:
    with pytest.raises(ValidationError):
        _claim(confidence=confidence)


def test_evidence_ids_are_canonical_and_unique() -> None:
    with pytest.raises(ValidationError):
        _claim(evidence_ids=("evidence_pilots_01", "evidence_pilots_01"))
    with pytest.raises(ValidationError):
        _claim(evidence_ids=("Evidence-Pilots",))


@pytest.mark.parametrize(
    "unsafe",
    [
        "Contact founder@example.invalid for details.",
        "Read C:\\private\\account.json before deciding.",
        "Use /home/private/account.json before deciding.",
        "Authorization: Bearer private-provider-token",
        "api_key=private-provider-token",
        "Line one\nLine two",
    ],
)
def test_public_council_text_rejects_private_or_control_content(unsafe: str) -> None:
    with pytest.raises(ValidationError):
        _claim(statement=unsafe)


def test_candidate_requires_policy_version() -> None:
    with pytest.raises(ValidationError):
        _candidate(policy_version="")


def test_candidate_rejects_unknown_specialist_and_duplicate_claim_ids() -> None:
    with pytest.raises(ValidationError):
        _candidate(specialist_id="unknown_specialist")
    with pytest.raises(ValidationError):
        _candidate(claims=(_claim(), _claim()))


def test_registry_uses_functional_specialists_in_canonical_order() -> None:
    registry = specialist_registry(WorkspacePlaybook.LAUNCH_DECISION)
    ids = tuple(item.specialist_id for item in registry)

    assert {
        "market_intelligence",
        "risk_compliance",
        "red_team",
        "decision_synthesis",
    } <= set(ids)
    assert ids == tuple(dict.fromkeys(ids))
    assert ids.index("decision_synthesis") < ids.index("red_team")
    assert ids[-1] == "final_synthesis"
    assert all(item.policy_version == "council-v1" for item in registry)
    assert all(item.timeout_seconds <= 120 for item in registry)
    assert all(item.token_budget <= 4096 for item in registry)
    assert all(item.maximum_attempts <= 2 for item in registry)


def test_registry_is_playbook_specific_and_immutable() -> None:
    launch = specialist_registry("launch_decision")
    fundraising = specialist_registry("fundraising_readiness")

    assert launch is specialist_registry("launch_decision")
    assert fundraising is specialist_registry("fundraising_readiness")
    assert {item.specialist_id for item in fundraising} >= {
        "market_intelligence",
        "financial_analysis",
        "risk_compliance",
    }
    with pytest.raises(ValueError, match="unsupported_playbook"):
        specialist_registry("unknown")


def test_specialists_have_read_only_tools_and_no_person_impersonation() -> None:
    for specialist in specialist_registry("fundraising_readiness"):
        assert specialist.tool_allowlist <= {
            "list_evidence",
            "read_evidence_excerpt",
            "read_prior_decision",
        }
        assert specialist.display_name.casefold() not in {
            "employee",
            "investor",
            "lawyer",
            "accountant",
            "founder",
        }


def test_challenge_binds_exact_claims_and_requires_action() -> None:
    challenge = CouncilChallenge(
        challenge_id="challenge_red_01",
        challenger_id="red_team",
        target_candidate_id="candidate_market_01",
        challenged_claim_ids=("claim_market_01",),
        severity=ChallengeSeverity.BLOCKING,
        issue="The pilot evidence does not establish paid conversion.",
        required_action="Provide conversion evidence or classify the claim as an assumption.",
        policy_version="council-v1",
    )

    assert challenge.challenged_claim_ids == ("claim_market_01",)
    with pytest.raises(ValidationError):
        CouncilChallenge.model_validate(
            {**challenge.model_dump(), "challenged_claim_ids": ()}
        )


def test_recommendation_digest_is_deterministic_and_content_bound() -> None:
    recommendation = CouncilRecommendation(
        recommendation_id="recommendation_01",
        summary="Proceed only after conversion evidence is confirmed.",
        claims=(_claim(),),
        challenges=(
            CouncilChallenge(
                challenge_id="challenge_red_01",
                challenger_id="red_team",
                target_candidate_id="candidate_market_01",
                challenged_claim_ids=("claim_market_01",),
                severity=ChallengeSeverity.BLOCKING,
                issue="Conversion is not yet evidenced.",
                required_action="Confirm conversion evidence.",
                policy_version="council-v1",
            ),
        ),
        recommended_action="Collect conversion evidence before approval.",
        required_human_action="An authorized approver must decide on the final digest.",
        source_candidate_ids=("candidate_market_01",),
        policy_version="council-v1",
    )

    assert re.fullmatch(r"[0-9a-f]{64}", recommendation.semantic_digest)
    assert recommendation.semantic_digest == recommendation.model_copy().semantic_digest
    assert recommendation.semantic_digest != recommendation.model_copy(
        update={"summary": "Stop until conversion evidence is confirmed."}
    ).semantic_digest


def test_run_request_binds_active_context_workspace_and_policy() -> None:
    request = CouncilRunRequest(
        context=_context(),
        workspace_id=WORKSPACE_ID,
        decision_id="decision_launch_01",
        playbook_id=WorkspacePlaybook.LAUNCH_DECISION,
        objective="Decide whether the product is ready for a limited launch.",
        evidence_ids=("evidence_pilots_01",),
        policy_version="council-v1",
    )

    assert request.organization_id == ORG_ID
    assert request.context.principal.uid == "founder-01"
    with pytest.raises(ValidationError):
        CouncilRunRequest.model_validate({**request.model_dump(), "policy_version": ""})


def test_all_council_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EvidenceClaim.model_validate(
            {
                **_claim().model_dump(),
                "provider_trace": "PRIVATE-PROVIDER-TRACE",
            }
        )
