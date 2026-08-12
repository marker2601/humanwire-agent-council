from datetime import timedelta
from uuid import uuid4

import pytest

from humanwire.alignment import (
    AlignmentEngine,
    AlignmentReport,
    HybridAlignmentEngine,
    NegotiationCoordinator,
    NegotiationLimitReached,
    NegotiationOutcome,
)
from humanwire.database import create_session_factory
from humanwire.domain import (
    Channel,
    Direction,
    EvidenceItem,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    Mandate,
    MandatePlan,
    MandateState,
    PlannedStakeholder,
    ProposalResponseKind,
    StakeholderAssignment,
    StakeholderState,
)
from humanwire.evidence import shareable_evidence
from humanwire.messages import render_proposal
from humanwire.repository import SqlAlchemyHumanWireRepository


def sample_plan() -> MandatePlan:
    return MandatePlan(
        objective="Choose a launch plan",
        required_decisions=["Launch date"],
        stakeholders=[
            PlannedStakeholder(
                person_ref="ops",
                reason="Owns operations",
                direction=Direction.LATERAL,
                questions=["What must be true?"],
            ),
            PlannedStakeholder(
                person_ref="people",
                reason="Owns staffing",
                direction=Direction.LATERAL,
                questions=["What must be true?"],
            ),
        ],
        completion_conditions=["All required stakeholders explicitly respond"],
    )


@pytest.fixture
def mandate(now, repository) -> Mandate:
    value = Mandate(
        mandate_id=uuid4(),
        token="HW-ALIGN1",
        initiator_id="manager",
        origin_channel=Channel.TELEGRAM,
        origin_conversation_id="manager-conversation",
        origin_message_id="origin-message",
        redacted_request="Choose a safe launch date",
        objective="Choose a launch plan",
        plan=sample_plan(),
        state=MandateState.SYNTHESIZING,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
        idempotency_key="mandate:alignment",
    )
    repository.add_mandate(value)
    return value


@pytest.fixture
def complete_assignments(mandate, now) -> list[StakeholderAssignment]:
    return [
        StakeholderAssignment(
            assignment_id=uuid4(),
            mandate_id=mandate.mandate_id,
            person_id=person_id,
            department="Operations",
            direction=Direction.LATERAL,
            reason="Required input",
            required=True,
            state=StakeholderState.COMPLETE,
            route_ids=[f"{person_id}-route"],
            completed_at=now,
        )
        for person_id in ("ops", "people")
    ]


@pytest.fixture
def unreachable_required_assignment(mandate) -> StakeholderAssignment:
    return StakeholderAssignment(
        assignment_id=uuid4(),
        mandate_id=mandate.mandate_id,
        person_id="ops",
        department="Operations",
        direction=Direction.LATERAL,
        reason="Required input",
        required=True,
        state=StakeholderState.UNREACHABLE,
        route_ids=["ops-route"],
    )


@pytest.fixture
def evidence_factory(mandate, complete_assignments, now):
    assignments = {assignment.person_id: assignment for assignment in complete_assignments}

    def factory(
        evidence_type: EvidenceType,
        statement: str,
        *,
        stakeholder_id: str = "ops",
        status: EvidenceStatus = EvidenceStatus.CONFIRMED,
        visibility: EvidenceVisibility = EvidenceVisibility.SHAREABLE,
        related_decision: str | None = "Launch date",
        deadline=None,
        resource: str | None = None,
    ) -> EvidenceItem:
        return EvidenceItem(
            evidence_id=uuid4(),
            mandate_id=mandate.mandate_id,
            assignment_id=assignments[stakeholder_id].assignment_id,
            stakeholder_id=stakeholder_id,
            evidence_type=evidence_type,
            statement=statement,
            visibility=visibility,
            status=status,
            source_message_id=f"{stakeholder_id}-message",
            channel=Channel.EMAIL,
            created_at=now,
            related_decision=related_decision,
            deadline=deadline,
            resource=resource,
        )

    return factory


@pytest.fixture
def engine(mandate) -> AlignmentEngine:
    return AlignmentEngine(mandate.mandate_id)


@pytest.fixture
def repository() -> SqlAlchemyHumanWireRepository:
    return SqlAlchemyHumanWireRepository(create_session_factory("sqlite://"))


@pytest.fixture
def coordinator(repository) -> NegotiationCoordinator:
    return NegotiationCoordinator(repository)


@pytest.fixture
def report(mandate) -> AlignmentReport:
    return AlignmentReport(mandate_id=mandate.mandate_id, is_aligned=False)


def test_conflicting_facts_remain_disputed(engine, evidence_factory, complete_assignments) -> None:
    evidence = [
        evidence_factory(EvidenceType.FACT, "Launch starts Friday", stakeholder_id="ops"),
        evidence_factory(EvidenceType.FACT, "Launch starts Monday", stakeholder_id="people"),
    ]

    report = engine.analyze(sample_plan(), shareable_evidence(evidence), complete_assignments)

    assert report.is_aligned is False
    assert any(issue.issue_type == "contradiction" for issue in report.issues)


def test_missing_required_response_blocks_alignment(
    engine, unreachable_required_assignment
) -> None:
    report = engine.analyze(sample_plan(), [], [unreachable_required_assignment])

    assert report.is_aligned is False
    assert report.blocking_issue_count == 1
    assert report.issues[0].issue_type == "missing_evidence"


def test_conflicting_resource_quantities_exceed_asserted_limit(
    engine, evidence_factory, complete_assignments
) -> None:
    evidence = [
        evidence_factory(EvidenceType.CONSTRAINT, "Only 3 engineers are available", resource="3"),
        evidence_factory(EvidenceType.COMMITMENT, "We need 4 engineers", stakeholder_id="people", resource="4"),
    ]

    report = engine.analyze(sample_plan(), shareable_evidence(evidence), complete_assignments)

    assert any(issue.issue_type == "resource_conflict" and issue.blocking for issue in report.issues)


def test_incompatible_explicit_deadlines_block_alignment(
    engine, evidence_factory, complete_assignments, now
) -> None:
    evidence = [
        evidence_factory(EvidenceType.COMMITMENT, "Operations delivers Friday", deadline=now),
        evidence_factory(
            EvidenceType.COMMITMENT,
            "People delivers Monday",
            stakeholder_id="people",
            deadline=now + timedelta(days=3),
        ),
    ]

    report = engine.analyze(sample_plan(), shareable_evidence(evidence), complete_assignments)

    assert any(issue.issue_type == "deadline_conflict" and issue.blocking for issue in report.issues)


def test_hard_constraint_is_a_blocker(engine, evidence_factory, complete_assignments) -> None:
    evidence = [evidence_factory(EvidenceType.CONSTRAINT, "Launch cannot start before approval")]

    report = engine.analyze(sample_plan(), shareable_evidence(evidence), complete_assignments)

    assert any(issue.issue_type == "hard_constraint" and issue.blocking for issue in report.issues)


def test_private_constraints_only_contribute_a_count(engine, evidence_factory, complete_assignments) -> None:
    private = evidence_factory(
        EvidenceType.CONSTRAINT,
        "Private medical leave blocks Friday",
        visibility=EvidenceVisibility.PRIVATE,
    )

    report = engine.analyze(
        sample_plan(), shareable_evidence([private]), complete_assignments, private_blocker_count=1
    )

    assert report.private_blocker_count == 1
    assert all("medical leave" not in issue.summary for issue in report.issues)
    assert report.is_aligned is False


def test_compatible_confirmed_commitments_cover_required_decision(
    engine, evidence_factory, complete_assignments
) -> None:
    evidence = [
        evidence_factory(EvidenceType.COMMITMENT, "Operations supports Friday launch"),
        evidence_factory(
            EvidenceType.COMMITMENT, "People supports Friday launch", stakeholder_id="people"
        ),
    ]

    report = engine.analyze(sample_plan(), shareable_evidence(evidence), complete_assignments)

    assert report.is_aligned is True
    assert report.covered_decisions == ["Launch date"]


def test_all_required_stakeholders_must_explicitly_accept(
    coordinator, mandate, report, now
) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(proposal, "ops", ProposalResponseKind.ACCEPT, None, now)

    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.NEXT_ROUND


def test_reject_prevents_alignment(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    for stakeholder_id, kind in (("ops", ProposalResponseKind.ACCEPT), ("people", ProposalResponseKind.REJECT)):
        coordinator.record_response(proposal, stakeholder_id, kind, None, now)

    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.NEXT_ROUND


def test_change_creates_open_change_request(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.CHANGE, "Start Monday", now
    )

    assert coordinator.open_change_requests(proposal) == ["Start Monday"]
    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.NEXT_ROUND


def test_duplicate_response_is_idempotent(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    first = coordinator.record_response(proposal, "ops", ProposalResponseKind.ACCEPT, None, now)
    second = coordinator.record_response(proposal, "ops", ProposalResponseKind.ACCEPT, None, now)

    assert second == first
    assert len(coordinator.repository.list_proposal_responses(proposal.proposal_id)) == 1


def test_final_acceptance_closes_the_persisted_proposal(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(proposal, "ops", ProposalResponseKind.ACCEPT, None, now)
    coordinator.record_response(proposal, "people", ProposalResponseKind.ACCEPT, None, now)

    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.ALIGNED
    assert coordinator.repository.get_active_proposal(mandate.mandate_id) is None


def test_third_round_is_forbidden(coordinator, mandate, report, now) -> None:
    with pytest.raises(NegotiationLimitReached):
        coordinator.create_proposal(mandate, report, round_number=3, now=now)


def test_model_cannot_turn_unfinished_work_into_agreement(mandate, complete_assignments) -> None:
    class UntrustedClient:
        def complete_json(self, system: str, user: str) -> dict:
            return {"agreements": ["Everyone accepted and approved the plan"], "issues": []}

    report = HybridAlignmentEngine(mandate.mandate_id, UntrustedClient()).analyze(
        sample_plan(), [], complete_assignments
    )

    assert report.is_aligned is False
    assert report.agreements == []


def test_model_proposal_cannot_claim_human_acceptance(repository, mandate, report, now) -> None:
    class UntrustedClient:
        def complete_json(self, system: str, user: str) -> dict:
            return {"proposal": "Everyone has accepted this proposal."}

    proposal = NegotiationCoordinator(repository, UntrustedClient()).create_proposal(
        mandate, report, round_number=1, now=now
    )

    assert "Everyone has accepted" not in proposal.text
    assert "Reply ACCEPT" in proposal.text


def test_proposal_renderer_omits_private_content_and_anonymous_identity(
    coordinator, mandate, report, evidence_factory, now
) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    anonymous = shareable_evidence(
        [
            evidence_factory(
                EvidenceType.FACT,
                "Anonymous coverage confirms Friday",
                stakeholder_id="ops",
                visibility=EvidenceVisibility.ANONYMOUS,
            ),
            evidence_factory(
                EvidenceType.CONSTRAINT,
                "Private surgery prevents attendance",
                stakeholder_id="people",
                visibility=EvidenceVisibility.PRIVATE,
            ),
        ]
    )

    message = render_proposal(mandate.token, proposal, anonymous)

    assert "Anonymous coverage confirms Friday" in message
    assert "Private surgery" not in message
    assert "ops" not in message
    assert "Round 1 of 2" in message
    assert f"ACCEPT {mandate.token}" in message


def test_private_evidence_cannot_reach_hybrid_model(
    mandate, complete_assignments, evidence_factory
) -> None:
    captured: list[str] = []

    class CapturingClient:
        def complete_json(self, system: str, user: str) -> dict:
            captured.append(user)
            return {"agreements": [], "issues": []}

    private = evidence_factory(
        EvidenceType.CONSTRAINT,
        "Private compensation condition",
        visibility=EvidenceVisibility.PRIVATE,
    )

    HybridAlignmentEngine(mandate.mandate_id, CapturingClient()).analyze(
        sample_plan(), [private], complete_assignments  # type: ignore[list-item]
    )

    assert "Private compensation condition" not in captured[0]
