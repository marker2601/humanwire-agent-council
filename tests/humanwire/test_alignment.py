from datetime import timedelta
from uuid import uuid4

import pytest

from humanwire.alignment import (
    AlignmentEngine,
    AlignmentReport,
    ContributionState,
    HybridAlignmentEngine,
    NegotiationCoordinator,
    NegotiationLimitReached,
    NegotiationOutcome,
    contribution_status,
)
from humanwire.database import create_session_factory
from humanwire.domain import (
    AlignmentIssueType,
    Channel,
    Direction,
    EngagementDecision,
    EngagementDecisionKind,
    EngagementType,
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


def _contribution_assignment(
    mandate: Mandate,
    engagement_type: EngagementType,
    *,
    state: StakeholderState = StakeholderState.COMPLETE,
    required: bool = True,
) -> StakeholderAssignment:
    return StakeholderAssignment(
        assignment_id=uuid4(),
        mandate_id=mandate.mandate_id,
        person_id="contributor",
        department="Operations",
        direction=Direction.LATERAL,
        reason="Supply the trusted contribution",
        required=required,
        engagement_type=engagement_type,
        response_required=engagement_type is not EngagementType.INFORM,
        state=state,
        route_ids=["contributor-route"],
    )


def _contribution_evidence(
    assignment: StakeholderAssignment,
    now,
    *,
    visibility: EvidenceVisibility = EvidenceVisibility.SHAREABLE,
    status: EvidenceStatus = EvidenceStatus.CONFIRMED,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=uuid4(),
        mandate_id=assignment.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        evidence_type=EvidenceType.FACT,
        statement="A persisted fact was supplied.",
        visibility=visibility,
        status=status,
        source_message_id="contribution-message",
        channel=Channel.EMAIL,
        created_at=now,
    )


def _approval_decision(
    assignment: StakeholderAssignment,
    now,
    response: EngagementDecisionKind,
) -> EngagementDecision:
    return EngagementDecision(
        decision_id=uuid4(),
        mandate_id=assignment.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        response=response,
        change_text=("PRIVATE-CHANGE-TEXT" if response is EngagementDecisionKind.CHANGE else None),
        source_message_id="approval-message",
        created_at=now,
        idempotency_key=f"approval:{assignment.assignment_id}",
    )


@pytest.mark.parametrize(
    ("engagement_type", "uses_evidence", "uses_decision", "has_availability"),
    [
        (EngagementType.INFORM, False, False, False),
        (EngagementType.ACKNOWLEDGE, False, False, False),
        (EngagementType.QUICK_RESPONSE, True, False, False),
        (EngagementType.STRUCTURED_INTERVIEW, True, False, False),
        (EngagementType.REVIEW_APPROVAL, False, True, False),
        (EngagementType.AVAILABILITY, False, False, True),
    ],
)
def test_contribution_complete_matrix_uses_each_engagement_contract(
    mandate,
    now,
    engagement_type,
    uses_evidence,
    uses_decision,
    has_availability,
) -> None:
    assignment = _contribution_assignment(mandate, engagement_type)
    evidence = [_contribution_evidence(assignment, now)] if uses_evidence else []
    decisions = (
        [_approval_decision(assignment, now, EngagementDecisionKind.APPROVE)]
        if uses_decision
        else []
    )

    result = contribution_status(
        assignment,
        evidence=evidence,
        decisions=decisions,
        has_availability=has_availability,
    )

    assert result.state is ContributionState.COMPLETE
    assert result.blocking is False


@pytest.mark.parametrize("required", [True, False], ids=["required", "optional"])
@pytest.mark.parametrize("engagement_type", list(EngagementType))
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (StakeholderState.AWAITING_ACKNOWLEDGEMENT, ContributionState.MISSING_RESPONSE),
        (StakeholderState.DECLINED, ContributionState.MISSING_RESPONSE),
        (StakeholderState.UNREACHABLE, ContributionState.DELIVERY_FAILED),
        (StakeholderState.DELIVERY_FAILED, ContributionState.DELIVERY_FAILED),
    ],
)
def test_contribution_lifecycle_matrix_preserves_required_and_optional_blocking(
    mandate, state, expected, engagement_type, required
) -> None:
    assignment = _contribution_assignment(
        mandate,
        engagement_type,
        state=state,
        required=required,
    )

    result = contribution_status(
        assignment,
        evidence=[],
        decisions=[],
        has_availability=False,
    )

    assert result.state is expected
    assert result.blocking is required


@pytest.mark.parametrize(
    ("mutation", "visibility", "status"),
    [
        ({"assignment_id": uuid4()}, EvidenceVisibility.SHAREABLE, EvidenceStatus.CONFIRMED),
        ({"mandate_id": uuid4()}, EvidenceVisibility.SHAREABLE, EvidenceStatus.CONFIRMED),
        ({"stakeholder_id": "another-person"}, EvidenceVisibility.SHAREABLE, EvidenceStatus.CONFIRMED),
        ({}, EvidenceVisibility.ANONYMOUS, EvidenceStatus.CONFIRMED),
        ({}, EvidenceVisibility.SHAREABLE, EvidenceStatus.DISPUTED),
        ({}, EvidenceVisibility.SHAREABLE, EvidenceStatus.ASSERTED),
    ],
    ids=["wrong-assignment", "wrong-mandate", "wrong-person", "anonymous", "disputed", "unconfirmed"],
)
def test_contribution_evidence_requires_exact_confirmed_assignment_proof(
    mandate, now, mutation, visibility, status
) -> None:
    assignment = _contribution_assignment(mandate, EngagementType.QUICK_RESPONSE)
    evidence = _contribution_evidence(
        assignment,
        now,
        visibility=visibility,
        status=status,
    ).model_copy(update=mutation)

    result = contribution_status(
        assignment,
        evidence=[evidence],
        decisions=[],
        has_availability=False,
    )

    assert result.state is ContributionState.MISSING_EVIDENCE
    assert result.blocking is True


def test_private_contribution_evidence_establishes_internal_readiness(mandate, now) -> None:
    assignment = _contribution_assignment(
        mandate, EngagementType.STRUCTURED_INTERVIEW
    )
    private = _contribution_evidence(
        assignment,
        now,
        visibility=EvidenceVisibility.PRIVATE,
    )

    result = contribution_status(
        assignment,
        evidence=[private],
        decisions=[],
        has_availability=False,
    )

    assert result.state is ContributionState.COMPLETE
    assert shareable_evidence([private]) == []


@pytest.mark.parametrize(
    ("response", "expected", "blocking"),
    [
        (EngagementDecisionKind.APPROVE, ContributionState.COMPLETE, False),
        (EngagementDecisionKind.REJECT, ContributionState.REJECTED, True),
        (EngagementDecisionKind.CHANGE, ContributionState.CHANGE_REQUESTED, True),
    ],
)
def test_contribution_approval_uses_only_exact_persisted_decision(
    mandate, now, response, expected, blocking
) -> None:
    assignment = _contribution_assignment(mandate, EngagementType.REVIEW_APPROVAL)

    result = contribution_status(
        assignment,
        evidence=[],
        decisions=[_approval_decision(assignment, now, response)],
        has_availability=False,
    )

    assert result.state is expected
    assert result.blocking is blocking


def test_contribution_approval_and_availability_fail_closed_without_exact_records(
    mandate, now
) -> None:
    approval = _contribution_assignment(mandate, EngagementType.REVIEW_APPROVAL)
    unrelated = _contribution_assignment(mandate, EngagementType.REVIEW_APPROVAL)
    availability = _contribution_assignment(mandate, EngagementType.AVAILABILITY)

    approval_result = contribution_status(
        approval,
        evidence=[],
        decisions=[_approval_decision(unrelated, now, EngagementDecisionKind.APPROVE)],
        has_availability=False,
    )
    availability_result = contribution_status(
        availability,
        evidence=[],
        decisions=[],
        has_availability=False,
    )

    assert approval_result.state is ContributionState.MISSING_RESPONSE
    assert approval_result.blocking is True
    assert availability_result.state is ContributionState.MISSING_RESPONSE
    assert availability_result.blocking is True


@pytest.mark.parametrize(
    "engagement_type",
    [
        EngagementType.INFORM,
        EngagementType.ACKNOWLEDGE,
        EngagementType.AVAILABILITY,
    ],
)
def test_engagement_receipt_delivery_and_availability_cannot_cover_required_decision(
    mandate, now, engagement_type
) -> None:
    assignment = _contribution_assignment(mandate, engagement_type)
    evidence = _contribution_evidence(assignment, now).model_copy(
        update={
            "evidence_type": EvidenceType.COMMITMENT,
            "statement": "Everyone agreed and approved this decision.",
            "related_decision": "Launch date",
        }
    )
    plan = MandatePlan(
        objective="Choose a launch plan",
        required_decisions=["Launch date"],
        stakeholders=[
            PlannedStakeholder(
                person_ref=assignment.person_id,
                reason=assignment.reason,
                direction=assignment.direction,
                engagement_type=engagement_type,
                response_required=engagement_type is not EngagementType.INFORM,
                questions=[],
            )
        ],
        completion_conditions=["Record the required decision"],
    )

    report = AlignmentEngine(mandate.mandate_id).analyze(
        plan,
        shareable_evidence([evidence]),
        [assignment],
        contribution_evidence=[evidence],
        decisions=[],
        availability_assignment_ids=(
            {assignment.assignment_id}
            if engagement_type is EngagementType.AVAILABILITY
            else set()
        ),
    )

    assert report.is_aligned is False
    assert report.agreements == []
    assert report.covered_decisions == []
    assert any(
        issue.issue_type is AlignmentIssueType.AUTHORITY_GAP and issue.blocking
        for issue in report.issues
    )


def test_model_cannot_remove_deterministic_contribution_issue_or_invent_approval(
    mandate, now
) -> None:
    assignment = _contribution_assignment(
        mandate,
        EngagementType.REVIEW_APPROVAL,
        state=StakeholderState.COMPLETE,
    )
    plan = MandatePlan(
        objective="Choose a launch plan",
        required_decisions=["Launch date"],
        stakeholders=[
            PlannedStakeholder(
                person_ref=assignment.person_id,
                reason=assignment.reason,
                direction=assignment.direction,
                engagement_type=EngagementType.REVIEW_APPROVAL,
                response_required=True,
                questions=[],
            )
        ],
        completion_conditions=["Persist authenticated approval"],
    )

    class ApprovalInventingClient:
        def complete_json(self, system: str, user: str) -> dict:
            return {"issues": []}

    report = HybridAlignmentEngine(
        mandate.mandate_id, ApprovalInventingClient()
    ).analyze(
        plan,
        [],
        [assignment],
        contribution_evidence=[],
        decisions=[],
        availability_assignment_ids=set(),
    )

    assert report.is_aligned is False
    assert report.covered_decisions == []
    assert any(
        issue.issue_type is AlignmentIssueType.AUTHORITY_GAP and issue.blocking
        for issue in report.issues
    )


def test_assignment_from_another_mandate_cannot_supply_local_authority(
    mandate, now
) -> None:
    assignment = _contribution_assignment(
        mandate, EngagementType.QUICK_RESPONSE
    ).model_copy(update={"mandate_id": uuid4()})
    evidence = _contribution_evidence(assignment, now).model_copy(
        update={"related_decision": "Launch date"}
    )
    plan = MandatePlan(
        objective="Choose a launch plan",
        required_decisions=["Launch date"],
        stakeholders=[
            PlannedStakeholder(
                person_ref=assignment.person_id,
                reason=assignment.reason,
                direction=assignment.direction,
                engagement_type=EngagementType.QUICK_RESPONSE,
                questions=["Which date is viable?"],
            )
        ],
        completion_conditions=["Record the decision"],
    )

    report = AlignmentEngine(mandate.mandate_id).analyze(
        plan,
        shareable_evidence([evidence]),
        [assignment],
        contribution_evidence=[evidence],
    )

    assert report.is_aligned is False
    assert report.covered_decisions == []


def test_optional_anonymous_response_cannot_be_projected_as_agreement(
    mandate, now
) -> None:
    assignment = _contribution_assignment(
        mandate,
        EngagementType.QUICK_RESPONSE,
        required=False,
    )
    evidence = _contribution_evidence(
        assignment,
        now,
        visibility=EvidenceVisibility.ANONYMOUS,
    ).model_copy(update={"evidence_type": EvidenceType.COMMITMENT})
    plan = MandatePlan(
        objective="Share an optional launch observation",
        required_decisions=["Launch date"],
        stakeholders=[
            PlannedStakeholder(
                person_ref=assignment.person_id,
                reason=assignment.reason,
                direction=assignment.direction,
                required=False,
                engagement_type=EngagementType.QUICK_RESPONSE,
                questions=["Any optional observation?"],
            )
        ],
        completion_conditions=["Record any optional response"],
    )

    report = AlignmentEngine(mandate.mandate_id).analyze(
        plan,
        shareable_evidence([evidence]),
        [assignment],
        contribution_evidence=[evidence],
    )

    assert report.agreements == []


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
    assert report.blocking_issue_count == 2
    assert {issue.stakeholder_ids[0] for issue in report.issues} == {"ops", "people"}


def test_missing_planned_assignments_are_blocking_issues(engine) -> None:
    report = engine.analyze(sample_plan(), [], [])

    assert report.is_aligned is False
    assert {issue.stakeholder_ids[0] for issue in report.issues} == {"ops", "people"}


def test_partial_assignment_collection_identifies_the_unassigned_stakeholder(
    engine, complete_assignments
) -> None:
    report = engine.analyze(sample_plan(), [], complete_assignments[:1])

    assert [issue.stakeholder_ids for issue in report.issues] == [["people"]]


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


def test_different_but_compatible_facts_are_not_labeled_contradictory(
    engine, evidence_factory, complete_assignments
) -> None:
    evidence = [
        evidence_factory(EvidenceType.FACT, "The launch team has a checklist"),
        evidence_factory(
            EvidenceType.FACT, "The launch team has completed training", stakeholder_id="people"
        ),
    ]

    report = engine.analyze(sample_plan(), shareable_evidence(evidence), complete_assignments)

    assert not any(issue.issue_type == "contradiction" for issue in report.issues)


def test_explicit_incompatible_fact_dates_are_contradictory(
    engine, evidence_factory, complete_assignments
) -> None:
    evidence = [
        evidence_factory(EvidenceType.FACT, "Launch starts Friday"),
        evidence_factory(EvidenceType.FACT, "Launch starts Monday", stakeholder_id="people"),
    ]

    report = engine.analyze(sample_plan(), shareable_evidence(evidence), complete_assignments)

    assert any(issue.issue_type == "contradiction" and issue.blocking for issue in report.issues)


def test_all_required_stakeholders_must_explicitly_accept(
    coordinator, mandate, report, now
) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-accept-1", now
    )

    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.NEXT_ROUND


def test_reject_prevents_alignment(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    for stakeholder_id, kind in (("ops", ProposalResponseKind.ACCEPT), ("people", ProposalResponseKind.REJECT)):
        coordinator.record_response(
            proposal, stakeholder_id, kind, None, f"{stakeholder_id}-{kind.value}-1", now
        )

    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.NEXT_ROUND


def test_change_creates_open_change_request(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.CHANGE, "Start Monday", "ops-change-1", now
    )

    assert coordinator.open_change_requests(proposal) == ["Start Monday"]
    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.NEXT_ROUND


def test_duplicate_response_is_idempotent(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    first = coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-accept-1", now
    )
    second = coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-accept-1", now
    )

    assert second == first
    assert len(coordinator.repository.list_proposal_responses(proposal.proposal_id)) == 1


def test_final_acceptance_closes_the_persisted_proposal(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-accept-1", now
    )
    coordinator.record_response(
        proposal, "people", ProposalResponseKind.ACCEPT, None, "people-accept-1", now
    )

    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.ALIGNED
    assert coordinator.repository.get_active_proposal(mandate.mandate_id) is None


def test_same_content_from_a_later_message_is_a_new_response(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-accept-1", now
    )
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-accept-2", now
    )

    assert len(coordinator.repository.list_proposal_responses(proposal.proposal_id)) == 2


def test_later_accept_closes_an_earlier_change_request(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.CHANGE, "Start Monday", "ops-01-change", now
    )
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-02-accept", now
    )

    assert coordinator.open_change_requests(proposal) == []


def test_latest_response_wins_after_accept_reject_accept(
    coordinator, mandate, report, now
) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-01-accept", now
    )
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.REJECT, None, "ops-02-reject", now
    )
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-03-accept", now
    )
    coordinator.record_response(
        proposal, "people", ProposalResponseKind.ACCEPT, None, "people-01-accept", now
    )

    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.ALIGNED


def test_equal_timestamps_use_source_identity_for_stable_latest_response(
    coordinator, mandate, report, now
) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "z-first-accept", now
    )
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.REJECT, None, "a-second-reject", now
    )
    coordinator.record_response(
        proposal, "people", ProposalResponseKind.ACCEPT, None, "people-01-accept", now
    )

    assert [
        response.source_message_id
        for response in coordinator.repository.list_proposal_responses(proposal.proposal_id)
    ] == ["z-first-accept", "a-second-reject", "people-01-accept"]
    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.NEXT_ROUND


def test_same_source_replay_after_alignment_returns_original_response(
    coordinator, mandate, report, now
) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    original = coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-accept-1", now
    )
    coordinator.record_response(
        proposal, "people", ProposalResponseKind.ACCEPT, None, "people-accept-1", now
    )

    replay = coordinator.record_response(
        proposal, "ops", ProposalResponseKind.REJECT, None, "ops-accept-1", now + timedelta(hours=1)
    )

    assert replay == original
    assert coordinator.evaluate_round(proposal) is NegotiationOutcome.ALIGNED
    assert len(coordinator.repository.list_proposal_responses(proposal.proposal_id)) == 2


def test_same_source_replay_after_expiry_returns_original_response(
    coordinator, mandate, report, now
) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    original = coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-accept-1", now
    )

    replay = coordinator.record_response(
        proposal,
        "ops",
        ProposalResponseKind.REJECT,
        None,
        "ops-accept-1",
        now + timedelta(days=2),
    )

    assert replay == original
    assert len(coordinator.repository.list_proposal_responses(proposal.proposal_id)) == 1


def test_expired_proposal_rejects_late_response(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)

    with pytest.raises(ValueError, match="expired"):
        coordinator.record_response(
            proposal,
            "ops",
            ProposalResponseKind.ACCEPT,
            None,
            "ops-late-accept",
            now + timedelta(days=2),
        )

    assert coordinator.repository.list_proposal_responses(proposal.proposal_id) == []


def test_terminal_proposal_rejects_response(coordinator, mandate, report, now) -> None:
    proposal = coordinator.create_proposal(mandate, report, round_number=1, now=now)
    coordinator.record_response(
        proposal, "ops", ProposalResponseKind.ACCEPT, None, "ops-accept-1", now
    )
    coordinator.record_response(
        proposal, "people", ProposalResponseKind.ACCEPT, None, "people-accept-1", now
    )

    with pytest.raises(ValueError, match="not awaiting"):
        coordinator.record_response(
            proposal, "ops", ProposalResponseKind.REJECT, None, "ops-late-reject", now
        )


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


def test_model_issue_cannot_claim_authority(mandate, complete_assignments) -> None:
    class UntrustedClient:
        def complete_json(self, system: str, user: str) -> dict:
            return {
                "issues": [
                    {
                        "issue_type": "agreement",
                        "summary": "Everyone signed off and approved the plan.",
                        "related_decision": "Launch date",
                    }
                ]
            }

    report = HybridAlignmentEngine(mandate.mandate_id, UntrustedClient()).analyze(
        sample_plan(), [], complete_assignments
    )

    assert not any("signed off" in issue.summary for issue in report.issues)


def test_model_can_add_nonblocking_advisory_issue(mandate, complete_assignments) -> None:
    class AdvisoryClient:
        def complete_json(self, system: str, user: str) -> dict:
            return {
                "issues": [
                    {
                        "issue_type": "agreement",
                        "related_decision": "Launch date",
                    }
                ]
            }

    report = HybridAlignmentEngine(mandate.mandate_id, AdvisoryClient()).analyze(
        sample_plan(), [], complete_assignments
    )

    assert all("Clarify the deployment handoff." not in issue.summary for issue in report.issues)
    assert any(issue.summary == "An advisory human review item was identified." for issue in report.issues)
    assert report.blocking_issue_count == 0


@pytest.mark.parametrize(
    "claim",
    [
        "Everyone has accepted this proposal.",
        "The leaders signed off on this plan.",
        "This is the authorized consensus decision.",
        "The plan is agreed, endorsed, committed, and confirmed.",
        "This received a unanimous green-light and was ratified.",
        "The unlisted phrase of authority is florp-certified.",
    ],
)
def test_model_proposal_cannot_claim_human_authority(
    repository, mandate, report, now, claim
) -> None:
    class UntrustedClient:
        def complete_json(self, system: str, user: str) -> dict:
            return {"proposal": claim}

    proposal = NegotiationCoordinator(repository, UntrustedClient()).create_proposal(
        mandate, report, round_number=1, now=now
    )

    assert claim not in proposal.text
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
