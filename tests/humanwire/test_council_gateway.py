from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from humanwire.council_gateway import CouncilGateway, CouncilGatewayDenied
from humanwire.council_models import (
    ChallengeSeverity,
    ClaimClassification,
    CouncilChallenge,
    CouncilRecommendation,
    EvidenceClaim,
)
from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
    OrganizationMembership,
)

ORG = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
OTHER_ORG = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AB"
WORKSPACE = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _context(
    *, role: DecisionOSRole = DecisionOSRole.APPROVER, organization_id: str = ORG
) -> DecisionOSContext:
    return DecisionOSContext(
        principal=DecisionOSPrincipal(
            uid="approver-01", email_verified=True, provider_ids=("google.com",)
        ),
        membership=OrganizationMembership(
            organization_id=organization_id,
            uid="approver-01",
            role=role,
            status=MembershipStatus.ACTIVE,
        ),
    )


def _recommendation(
    *, classification: ClaimClassification = ClaimClassification.CONFIRMED_FACT
) -> CouncilRecommendation:
    claim = EvidenceClaim(
        claim_id="claim_market_01",
        statement="Three pilots completed the evaluation period.",
        classification=classification,
        evidence_ids=("evidence_market_01",)
        if classification
        in {ClaimClassification.CONFIRMED_FACT, ClaimClassification.SOURCE_ASSERTION}
        else (),
        confidence=0.9,
    )
    challenge = CouncilChallenge(
        challenge_id="challenge_red_01",
        challenger_id="red_team",
        target_candidate_id="candidate_market_intelligence_01",
        challenged_claim_ids=(claim.claim_id,),
        severity=ChallengeSeverity.MATERIAL,
        issue="Pilot completion does not establish paid conversion.",
        required_action="Confirm conversion before broad launch.",
        policy_version="council-v1",
    )
    return CouncilRecommendation(
        recommendation_id="recommendation_final_01",
        summary="Proceed only after conversion evidence is confirmed.",
        claims=(claim,),
        challenges=(challenge,),
        recommended_action="Collect conversion evidence.",
        required_human_action="An authorized approver must decide.",
        source_candidate_ids=("candidate_market_intelligence_01",),
        policy_version="council-v1",
    )


def test_unconfirmed_recommendation_is_inert() -> None:
    gateway = CouncilGateway(nonce_factory=lambda: "nonce-01")

    result = gateway.evaluate(_recommendation(), confirmed_evidence_ids=())

    assert result.accepted is False
    assert result.reason == "evidence_unconfirmed"
    assert result.authoritative_mutation_count == 0


def test_approval_binds_identity_role_workspace_run_and_digest() -> None:
    gateway = CouncilGateway(nonce_factory=lambda: "nonce-01")
    recommendation = _recommendation()

    challenge = gateway.prepare_approval(
        recommendation,
        _context(),
        workspace_id=WORKSPACE,
        run_id="council_run_01",
        confirmed_evidence_ids=("evidence_market_01",),
        now=NOW,
    )

    assert challenge.organization_id == ORG
    assert challenge.workspace_id == WORKSPACE
    assert challenge.run_id == "council_run_01"
    assert challenge.approver_role is DecisionOSRole.APPROVER
    assert challenge.recommendation_digest == recommendation.semantic_digest
    assert challenge.nonce.get_secret_value() == "nonce-01"


def test_stale_digest_wrong_tenant_and_cross_run_replay_are_rejected() -> None:
    gateway = CouncilGateway(nonce_factory=lambda: "nonce-01")
    recommendation = _recommendation()
    challenge = gateway.prepare_approval(
        recommendation,
        _context(),
        workspace_id=WORKSPACE,
        run_id="council_run_01",
        confirmed_evidence_ids=("evidence_market_01",),
        now=NOW,
    )
    changed = recommendation.model_copy(
        update={"recommended_action": "A changed recommendation."}
    )

    hostile = (
        (changed, _context(), WORKSPACE, "council_run_01"),
        (recommendation, _context(organization_id=OTHER_ORG), WORKSPACE, "council_run_01"),
        (recommendation, _context(), WORKSPACE, "council_run_02"),
    )
    for candidate, context, workspace, run_id in hostile:
        with pytest.raises(CouncilGatewayDenied, match="^approval_unavailable$"):
            gateway.approve(
                challenge.challenge_id,
                "nonce-01",
                candidate,
                context,
                workspace_id=workspace,
                run_id=run_id,
                now=NOW + timedelta(minutes=1),
            )


def test_expired_nonce_revoked_role_and_duplicate_approval_are_rejected() -> None:
    gateway = CouncilGateway(nonce_factory=lambda: "nonce-01")
    recommendation = _recommendation()
    challenge = gateway.prepare_approval(
        recommendation,
        _context(),
        workspace_id=WORKSPACE,
        run_id="council_run_01",
        confirmed_evidence_ids=("evidence_market_01",),
        now=NOW,
        expires_in=timedelta(minutes=5),
    )

    with pytest.raises(CouncilGatewayDenied, match="^approval_unavailable$"):
        gateway.approve(
            challenge.challenge_id,
            "nonce-01",
            recommendation,
            _context(role=DecisionOSRole.VIEWER),
            workspace_id=WORKSPACE,
            run_id="council_run_01",
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(CouncilGatewayDenied, match="^approval_unavailable$"):
        gateway.approve(
            challenge.challenge_id,
            "nonce-01",
            recommendation,
            _context(),
            workspace_id=WORKSPACE,
            run_id="council_run_01",
            now=NOW + timedelta(minutes=6),
        )

    fresh = CouncilGateway(nonce_factory=lambda: "nonce-02")
    challenge = fresh.prepare_approval(
        recommendation,
        _context(),
        workspace_id=WORKSPACE,
        run_id="council_run_02",
        confirmed_evidence_ids=("evidence_market_01",),
        now=NOW,
    )
    receipt = fresh.approve(
        challenge.challenge_id,
        "nonce-02",
        recommendation,
        _context(),
        workspace_id=WORKSPACE,
        run_id="council_run_02",
        now=NOW + timedelta(minutes=1),
    )
    assert receipt.approver_uid == "approver-01"
    assert receipt.approver_role is DecisionOSRole.APPROVER
    with pytest.raises(CouncilGatewayDenied, match="^approval_unavailable$"):
        fresh.approve(
            challenge.challenge_id,
            "nonce-02",
            recommendation,
            _context(),
            workspace_id=WORKSPACE,
            run_id="council_run_02",
            now=NOW + timedelta(minutes=2),
        )
