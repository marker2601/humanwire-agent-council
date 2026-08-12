from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

import humanwire.repository as repository_module
from humanwire import domain
from humanwire.database import (
    EngagementDecisionRecord,
    InterviewSessionRecord,
    create_session_factory,
)
from humanwire.domain import (
    AlignmentIssue,
    AlignmentIssueType,
    Channel,
    Direction,
    DomainEvent,
    EvidenceItem,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    InterviewSession,
    MandateState,
    MeetingPackage,
    Proposal,
    ProposalResponse,
    ProposalResponseKind,
    StakeholderAssignment,
    StakeholderState,
)
from humanwire.repository import (
    DuplicateMandateError,
    RepositoryUnitOfWork,
    SqlAlchemyHumanWireRepository,
)


@pytest.fixture
def repository() -> SqlAlchemyHumanWireRepository:
    return SqlAlchemyHumanWireRepository(create_session_factory("sqlite://"))


@pytest.fixture
def make_mandate(now):
    def factory(**updates):
        values = {
            "mandate_id": uuid4(),
            "token": "HW-7K4P2M",
            "initiator_id": "manager",
            "origin_channel": Channel.TELEGRAM,
            "origin_conversation_id": "manager-conversation",
            "origin_message_id": "message-1",
            "redacted_request": "Prepare the staffing proposal",
            "objective": "Align staffing plan",
            "plan": {
                "objective": "Align staffing plan",
                "required_decisions": ["Approve the plan"],
                "stakeholders": [
                    {
                        "person_ref": "team-lead",
                        "reason": "Owns delivery",
                        "direction": Direction.DOWNWARD,
                        "questions": ["What capacity is available?"],
                    }
                ],
                "completion_conditions": ["All required people respond"],
            },
            "state": MandateState.INTERVIEWING,
            "created_at": now,
            "updated_at": now,
            "expires_at": now + timedelta(days=1),
            "idempotency_key": "mandate:sample",
        }
        values.update(updates)
        from humanwire.domain import Mandate

        return Mandate(**values)

    return factory


@pytest.fixture
def sample_mandate(make_mandate):
    return make_mandate()


@pytest.fixture
def make_assignment(sample_mandate, now):
    def factory(**updates):
        values = {
            "assignment_id": uuid4(),
            "mandate_id": sample_mandate.mandate_id,
            "person_id": "team-lead",
            "department": "Operations",
            "direction": Direction.DOWNWARD,
            "reason": "Owns delivery",
            "required": True,
            "state": StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            "route_ids": ["team-lead-email", "team-lead-telegram"],
            "next_action_at": now - timedelta(seconds=1),
        }
        values.update(updates)
        return StakeholderAssignment(**values)

    return factory


@pytest.fixture
def due_assignments(repository, sample_mandate, make_assignment, now):
    repository.add_mandate(sample_mandate)
    follow_up = make_assignment()
    complete = make_assignment(
        assignment_id=uuid4(), state=StakeholderState.COMPLETE, completed_at=now
    )
    repository.add_assignment(follow_up)
    repository.add_assignment(complete)
    return SimpleNamespace(follow_up=follow_up, complete=complete)


def test_round_trips_mandate_and_idempotency_lookup(repository, sample_mandate) -> None:
    repository.add_mandate(sample_mandate)

    assert repository.get_mandate_by_token(sample_mandate.token) == sample_mandate
    assert (
        repository.get_mandate_by_idempotency_key(sample_mandate.idempotency_key) == sample_mandate
    )
    assert repository.list_recent_mandates() == [sample_mandate]


def test_mandate_idempotency_key_is_unique(repository, sample_mandate, make_mandate) -> None:
    repository.add_mandate(sample_mandate)

    with pytest.raises(DuplicateMandateError):
        repository.add_mandate(make_mandate(token="HW-OTHER", idempotency_key="mandate:sample"))


def test_round_trips_assignment_and_interview(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    assignment = make_assignment(next_action_at=None)
    interview = InterviewSession(
        session_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        questions=["What capacity is available?"],
        current_channel=Channel.EMAIL,
        channel_history=[Channel.EMAIL],
        started_at=now,
        updated_at=now,
    )
    repository.add_assignment(assignment)
    repository.add_interview(interview)

    assert repository.get_assignment(assignment.assignment_id) == assignment
    assert repository.list_assignments(sample_mandate.mandate_id) == [assignment]
    assert repository.get_interview(interview.session_id) == interview
    assert (
        repository.find_active_interview(assignment.mandate_id, assignment.person_id) == interview
    )
    assert repository.list_interviews(sample_mandate.mandate_id) == [interview]


def test_assignment_round_trip_preserves_engagement_contract(
    repository, sample_mandate, make_assignment
) -> None:
    repository.add_mandate(sample_mandate)
    assignment = make_assignment(
        engagement_type=domain.EngagementType.ACKNOWLEDGE,
        response_required=True,
    )

    repository.add_assignment(assignment)

    assert repository.get_assignment(assignment.assignment_id) == assignment


@pytest.mark.parametrize(
    ("engagement_type", "response_required", "questions"),
    [
        ("inform", False, []),
        ("acknowledge", True, []),
        ("quick_response", True, ["One?"]),
        ("quick_response", True, ["One?", "Two?"]),
        ("structured_interview", True, ["One?", "Two?", "Three?"]),
        (
            "structured_interview",
            True,
            ["One?", "Two?", "Three?", "Four?", "Five?"],
        ),
        ("review_approval", True, []),
        ("availability", True, []),
    ],
)
def test_planned_stakeholder_accepts_valid_engagement_contract(
    engagement_type, response_required, questions
) -> None:
    stakeholder = domain.PlannedStakeholder(
        person_ref="team-lead",
        reason="Needed for the mandate",
        direction=Direction.DOWNWARD,
        engagement_type=engagement_type,
        response_required=response_required,
        questions=questions,
    )

    assert stakeholder.engagement_type.value == engagement_type


@pytest.mark.parametrize(
    ("engagement_type", "response_required", "questions"),
    [
        ("inform", True, []),
        ("inform", False, ["Unexpected question?"]),
        ("acknowledge", False, []),
        ("acknowledge", True, ["Unexpected question?"]),
        ("quick_response", True, []),
        ("quick_response", True, ["One?", "Two?", "Three?"]),
        ("structured_interview", True, ["One?", "Two?"]),
        ("review_approval", True, ["Unexpected question?"]),
        ("availability", True, ["Unexpected question?"]),
    ],
)
def test_planned_stakeholder_rejects_invalid_engagement_contract(
    engagement_type, response_required, questions
) -> None:
    with pytest.raises(ValidationError):
        domain.PlannedStakeholder(
            person_ref="team-lead",
            reason="Needed for the mandate",
            direction=Direction.DOWNWARD,
            engagement_type=engagement_type,
            response_required=response_required,
            questions=questions,
        )


def test_legacy_planned_stakeholder_rejects_zero_questions() -> None:
    with pytest.raises(ValidationError):
        domain.PlannedStakeholder(
            person_ref="team-lead",
            reason="Needed for the mandate",
            direction=Direction.DOWNWARD,
            questions=[],
        )


@pytest.mark.parametrize(
    ("questions", "expected_type"),
    [
        (["Legacy quick question?"], "quick_response"),
        (["One?", "Two?"], "quick_response"),
        (["One?", "Two?", "Three?"], "structured_interview"),
    ],
)
def test_legacy_planned_stakeholder_infers_valid_engagement_contract(
    questions, expected_type
) -> None:
    stakeholder = domain.PlannedStakeholder(
        person_ref="team-lead",
        reason="Needed for the mandate",
        direction=Direction.DOWNWARD,
        questions=questions,
    )

    assert stakeholder.engagement_type.value == expected_type
    assert stakeholder.response_required is True
    assert domain.PlannedStakeholder.model_validate(stakeholder.model_dump()) == stakeholder


def test_engagement_decision_is_idempotent_and_queryable(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    assignment = make_assignment()
    repository.add_assignment(assignment)
    decision = domain.EngagementDecision(
        decision_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        response=domain.EngagementDecisionKind.APPROVE,
        source_message_id="message-approval",
        created_at=now,
        idempotency_key="engagement-decision:approval",
    )

    repository.add_engagement_decision(decision)
    repository.add_engagement_decision(decision)

    assert repository.get_engagement_decision(decision.assignment_id) == decision
    assert repository.list_engagement_decisions(decision.mandate_id) == [decision]


def test_exact_duplicate_engagement_decision_in_same_unit_of_work_is_inert(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    assignment = make_assignment()
    repository.add_assignment(assignment)
    decision = domain.EngagementDecision(
        decision_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        response=domain.EngagementDecisionKind.CHANGE,
        change_text="Move the deadline.",
        source_message_id="message-change",
        created_at=now,
        idempotency_key="engagement-decision:uow",
    )

    with repository.transaction() as unit:
        unit.add_engagement_decision(decision)
        unit.add_engagement_decision(decision)
        assert unit.get_engagement_decision(assignment.assignment_id) == decision
        assert unit.list_engagement_decisions(sample_mandate.mandate_id) == [decision]


def test_reused_engagement_decision_idempotency_key_with_changed_payload_rejects(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    first_assignment = make_assignment()
    second_assignment = make_assignment(assignment_id=uuid4(), person_id="second")
    repository.add_assignment(first_assignment)
    repository.add_assignment(second_assignment)
    first = domain.EngagementDecision(
        decision_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=first_assignment.assignment_id,
        stakeholder_id=first_assignment.person_id,
        response=domain.EngagementDecisionKind.APPROVE,
        source_message_id="message-first",
        created_at=now,
        idempotency_key="engagement-decision:reused",
    )
    changed = first.model_copy(
        update={
            "decision_id": uuid4(),
            "assignment_id": second_assignment.assignment_id,
            "stakeholder_id": second_assignment.person_id,
        }
    )
    repository.add_engagement_decision(first)

    with pytest.raises(ValueError, match="idempotency key conflicts"):
        repository.add_engagement_decision(changed)

    assert repository.get_engagement_decision(first_assignment.assignment_id) == first
    assert repository.get_engagement_decision(second_assignment.assignment_id) is None


def test_engagement_decision_unit_of_work_rolls_back_sibling_writes_on_conflict(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    assignment = make_assignment()
    repository.add_assignment(assignment)
    first = domain.EngagementDecision(
        decision_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        response=domain.EngagementDecisionKind.APPROVE,
        source_message_id="message-first",
        created_at=now,
        idempotency_key="engagement-decision:durable",
    )
    repository.add_engagement_decision(first)
    conflicting = first.model_copy(
        update={
            "decision_id": uuid4(),
            "response": domain.EngagementDecisionKind.REJECT,
            "source_message_id": "message-conflict",
            "idempotency_key": "engagement-decision:conflict",
        }
    )

    with (
        pytest.raises(ValueError, match="already has a decision"),
        repository.transaction() as unit,
    ):
        unit.set_runtime_status("review.sibling", "must-roll-back", now)
        unit.add_engagement_decision(conflicting)

    assert repository.get_runtime_status("review.sibling") is None
    assert repository.get_engagement_decision(assignment.assignment_id) == first


def test_engagement_decision_unit_of_work_is_atomic_on_later_failure(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    assignment = make_assignment()
    repository.add_assignment(assignment)
    decision = domain.EngagementDecision(
        decision_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        response=domain.EngagementDecisionKind.APPROVE,
        source_message_id="message-atomic",
        created_at=now,
        idempotency_key="engagement-decision:atomic",
    )

    with (
        pytest.raises(RuntimeError, match="abort sibling write"),
        repository.transaction() as unit,
    ):
        unit.add_engagement_decision(decision)
        unit.set_runtime_status("atomic.sibling", "must-roll-back", now)
        raise RuntimeError("abort sibling write")

    assert repository.get_engagement_decision(assignment.assignment_id) is None
    assert repository.get_runtime_status("atomic.sibling") is None


@pytest.mark.parametrize("entrypoint", ["repository", "unit_of_work"])
def test_exact_duplicate_decision_race_is_inert_and_session_remains_usable(
    tmp_path, sample_mandate, make_assignment, now, entrypoint
) -> None:
    database_path = tmp_path / f"engagement-race-{entrypoint}.db"
    factory = create_session_factory(f"sqlite:///{database_path.as_posix()}")
    with factory.kw["bind"].begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    repository = SqlAlchemyHumanWireRepository(factory)
    repository.add_mandate(sample_mandate)
    assignment = make_assignment()
    repository.add_assignment(assignment)
    decision = domain.EngagementDecision(
        decision_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        response=domain.EngagementDecisionKind.APPROVE,
        source_message_id="message-race",
        created_at=now,
        idempotency_key="engagement-decision:race",
    )

    raced = False

    def interleave_exact_winner(session, flush_context, instances) -> None:
        del flush_context, instances
        nonlocal raced
        has_pending_decision = any(
            isinstance(item, EngagementDecisionRecord) for item in session.new
        )
        if has_pending_decision and not raced:
            raced = True
            with factory() as winning_session:
                winning_session.add(
                    EngagementDecisionRecord(
                        decision_id=str(decision.decision_id),
                        mandate_id=str(decision.mandate_id),
                        assignment_id=str(decision.assignment_id),
                        stakeholder_id=decision.stakeholder_id,
                        response=decision.response.value,
                        change_text=decision.change_text,
                        source_message_id=decision.source_message_id,
                        created_at=decision.created_at,
                        idempotency_key=decision.idempotency_key,
                    )
                )
                winning_session.commit()

    event.listen(factory.class_, "before_flush", interleave_exact_winner)
    try:
        if entrypoint == "repository":
            repository.add_engagement_decision(decision)
            repository.set_runtime_status("race.session", "usable", now)
        else:
            with factory() as losing_session:
                unit = RepositoryUnitOfWork(losing_session)
                unit.add_engagement_decision(decision)
                unit.set_runtime_status("race.session", "usable", now)
                losing_session.commit()
    finally:
        event.remove(factory.class_, "before_flush", interleave_exact_winner)

    assert raced
    assert repository.get_engagement_decision(assignment.assignment_id) == decision
    assert repository.get_runtime_status("race.session") == ("usable", now)


def test_exact_duplicate_race_preserves_earlier_unit_of_work_sibling_write(
    tmp_path, sample_mandate, make_assignment, now, monkeypatch
) -> None:
    database_path = tmp_path / "engagement-race-sibling.db"
    factory = create_session_factory(f"sqlite:///{database_path.as_posix()}")
    repository = SqlAlchemyHumanWireRepository(factory)
    repository.add_mandate(sample_mandate)
    assignment = make_assignment()
    repository.add_assignment(assignment)
    decision = domain.EngagementDecision(
        decision_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        response=domain.EngagementDecisionKind.APPROVE,
        source_message_id="message-race-sibling",
        created_at=now,
        idempotency_key="engagement-decision:race-sibling",
    )
    original_check = repository_module._engagement_decision_exists_or_conflicts
    losing_session = None
    raced = False

    def interleave_winner(session, value):
        nonlocal raced
        result = original_check(session, value)
        if session is losing_session and not result and not raced:
            raced = True
            with factory() as winning_session:
                winning_session.add(repository_module._engagement_decision_record(value))
                winning_session.commit()
        return result

    monkeypatch.setattr(
        repository_module,
        "_engagement_decision_exists_or_conflicts",
        interleave_winner,
    )
    with factory() as session:
        losing_session = session
        unit = RepositoryUnitOfWork(session)
        unit.set_runtime_status("race.earlier-sibling", "preserved", now)
        with session.no_autoflush:
            unit.add_engagement_decision(decision)
        session.commit()

    assert raced
    assert repository.get_engagement_decision(assignment.assignment_id) == decision
    assert repository.get_runtime_status("race.earlier-sibling") == ("preserved", now)


def test_engagement_decisions_are_ordered_by_created_at_then_decision_id(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    later_assignment = make_assignment(assignment_id=uuid4(), person_id="later")
    first_assignment = make_assignment(assignment_id=uuid4(), person_id="first")
    second_assignment = make_assignment(assignment_id=uuid4(), person_id="second")
    for assignment in (later_assignment, first_assignment, second_assignment):
        repository.add_assignment(assignment)
    first_id = uuid4()
    second_id = uuid4()
    if str(first_id) > str(second_id):
        first_id, second_id = second_id, first_id
    decisions = [
        domain.EngagementDecision(
            decision_id=second_id,
            mandate_id=sample_mandate.mandate_id,
            assignment_id=second_assignment.assignment_id,
            stakeholder_id=second_assignment.person_id,
            response=domain.EngagementDecisionKind.REJECT,
            source_message_id="message-second",
            created_at=now,
            idempotency_key="engagement-decision:second",
        ),
        domain.EngagementDecision(
            decision_id=uuid4(),
            mandate_id=sample_mandate.mandate_id,
            assignment_id=later_assignment.assignment_id,
            stakeholder_id=later_assignment.person_id,
            response=domain.EngagementDecisionKind.APPROVE,
            source_message_id="message-later",
            created_at=now + timedelta(seconds=1),
            idempotency_key="engagement-decision:later",
        ),
        domain.EngagementDecision(
            decision_id=first_id,
            mandate_id=sample_mandate.mandate_id,
            assignment_id=first_assignment.assignment_id,
            stakeholder_id=first_assignment.person_id,
            response=domain.EngagementDecisionKind.CHANGE,
            change_text="Revise the timing.",
            source_message_id="message-first",
            created_at=now,
            idempotency_key="engagement-decision:first",
        ),
    ]
    for decision in decisions:
        repository.add_engagement_decision(decision)

    assert repository.list_engagement_decisions(sample_mandate.mandate_id) == [
        decisions[2],
        decisions[0],
        decisions[1],
    ]


def test_conflicting_engagement_decision_cannot_replace_first_durable_decision(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    assignment = make_assignment()
    repository.add_assignment(assignment)
    first = domain.EngagementDecision(
        decision_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        response=domain.EngagementDecisionKind.APPROVE,
        source_message_id="message-first",
        created_at=now,
        idempotency_key="engagement-decision:first-durable",
    )
    conflicting = first.model_copy(
        update={
            "decision_id": uuid4(),
            "response": domain.EngagementDecisionKind.REJECT,
            "source_message_id": "message-conflicting",
            "idempotency_key": "engagement-decision:conflicting",
        }
    )
    repository.add_engagement_decision(first)

    with pytest.raises(ValueError, match="already has a decision"):
        repository.add_engagement_decision(conflicting)

    assert repository.get_engagement_decision(assignment.assignment_id) == first


def test_engagement_decision_is_immutable(sample_mandate, make_assignment, now) -> None:
    decision = domain.EngagementDecision(
        decision_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=make_assignment().assignment_id,
        stakeholder_id="team-lead",
        response=domain.EngagementDecisionKind.APPROVE,
        source_message_id="message-approval",
        created_at=now,
        idempotency_key="engagement-decision:immutable",
    )

    with pytest.raises(ValidationError):
        decision.response = domain.EngagementDecisionKind.REJECT


def test_rejects_duplicate_active_interview_for_the_same_stakeholder(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    first_assignment = make_assignment(next_action_at=None)
    second_assignment = make_assignment(assignment_id=uuid4(), next_action_at=None)
    repository.add_assignment(first_assignment)
    repository.add_assignment(second_assignment)
    repository.add_interview(
        InterviewSession(
            session_id=uuid4(),
            mandate_id=sample_mandate.mandate_id,
            assignment_id=first_assignment.assignment_id,
            questions=["Capacity?"],
            started_at=now,
            updated_at=now,
        )
    )

    with pytest.raises(ValueError, match="active interview"):
        repository.add_interview(
            InterviewSession(
                session_id=uuid4(),
                mandate_id=sample_mandate.mandate_id,
                assignment_id=second_assignment.assignment_id,
                questions=["Capacity?"],
                started_at=now,
                updated_at=now,
            )
        )


def test_completed_interview_allows_later_active_interview_for_the_same_stakeholder(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    completed_assignment = make_assignment(
        state=StakeholderState.COMPLETE, completed_at=now, next_action_at=None
    )
    next_assignment = make_assignment(assignment_id=uuid4(), next_action_at=None)
    repository.add_assignment(completed_assignment)
    repository.add_assignment(next_assignment)
    repository.add_interview(
        InterviewSession(
            session_id=uuid4(),
            mandate_id=sample_mandate.mandate_id,
            assignment_id=completed_assignment.assignment_id,
            questions=["Capacity?"],
            started_at=now,
            updated_at=now,
            completed_at=now,
        )
    )

    repository.add_interview(
        InterviewSession(
            session_id=uuid4(),
            mandate_id=sample_mandate.mandate_id,
            assignment_id=next_assignment.assignment_id,
            questions=["Capacity?"],
            started_at=now,
            updated_at=now,
        )
    )

    assert repository.find_active_interview(sample_mandate.mandate_id, "team-lead") is not None


def test_active_interview_unique_index_rejects_direct_duplicate_insert(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    first_assignment = make_assignment(next_action_at=None)
    second_assignment = make_assignment(assignment_id=uuid4(), next_action_at=None)
    repository.add_assignment(first_assignment)
    repository.add_assignment(second_assignment)
    first = InterviewSessionRecord(
        session_id=str(uuid4()),
        mandate_id=str(sample_mandate.mandate_id),
        assignment_id=str(first_assignment.assignment_id),
        stakeholder_person_id="team-lead",
        questions=["Capacity?"],
        current_question_index=0,
        current_channel=None,
        channel_history=[],
        default_visibility=EvidenceVisibility.SHAREABLE.value,
        started_at=now,
        updated_at=now,
        completed_at=None,
    )
    duplicate = InterviewSessionRecord(
        session_id=str(uuid4()),
        mandate_id=str(sample_mandate.mandate_id),
        assignment_id=str(second_assignment.assignment_id),
        stakeholder_person_id="team-lead",
        questions=["Capacity?"],
        current_question_index=0,
        current_channel=None,
        channel_history=[],
        default_visibility=EvidenceVisibility.SHAREABLE.value,
        started_at=now,
        updated_at=now,
        completed_at=None,
    )
    with repository._session_factory() as session:
        session.add(first)
        session.commit()
    with repository._session_factory() as session:
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()


def test_round_trips_evidence_issue_proposal_response_and_meeting_package(
    repository, sample_mandate, make_assignment, now
) -> None:
    repository.add_mandate(sample_mandate)
    assignment = make_assignment(next_action_at=None)
    repository.add_assignment(assignment)
    evidence = EvidenceItem(
        evidence_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        evidence_type=EvidenceType.FACT,
        statement="Two engineers are available.",
        visibility=EvidenceVisibility.SHAREABLE,
        status=EvidenceStatus.CONFIRMED,
        source_message_id="message-2",
        channel=Channel.EMAIL,
        created_at=now,
    )
    issue = AlignmentIssue(
        issue_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        issue_type=AlignmentIssueType.RESOURCE_CONFLICT,
        evidence_ids=[evidence.evidence_id],
        stakeholder_ids=[assignment.person_id],
        summary="Capacity is limited.",
        blocking=True,
    )
    proposal = Proposal(
        proposal_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        round_number=1,
        text="Move the deadline by one week.",
        issue_ids=[issue.issue_id],
        required_respondent_ids=[assignment.person_id],
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    response = ProposalResponse(
        response_id=uuid4(),
        proposal_id=proposal.proposal_id,
        stakeholder_id=assignment.person_id,
        response=ProposalResponseKind.ACCEPT,
        source_message_id="message-3",
        created_at=now,
        idempotency_key="response:sample",
    )
    package = MeetingPackage(
        meeting_id=uuid4(),
        mandate_id=sample_mandate.mandate_id,
        purpose="Resolve staffing",
        decision_owner_id="manager",
        required_attendee_ids=[assignment.person_id],
        agreed_facts=["Capacity is limited."],
        open_decisions=["Choose a date."],
        agenda=["Review options"],
        pre_read_evidence_ids=[evidence.evidence_id],
        created_at=now,
    )
    repository.add_evidence(evidence)
    repository.add_issue(issue)
    repository.add_proposal(proposal)
    repository.add_proposal_response(response)
    repository.save_meeting_package(package)

    assert repository.list_evidence(sample_mandate.mandate_id) == [evidence]
    assert repository.list_issues(sample_mandate.mandate_id) == [issue]
    assert repository.get_active_proposal(sample_mandate.mandate_id) == proposal
    assert repository.list_proposal_responses(proposal.proposal_id) == [response]
    assert repository.get_meeting_package(sample_mandate.mandate_id) == package


def test_saves_mutable_aggregates(repository, sample_mandate, make_assignment, now) -> None:
    repository.add_mandate(sample_mandate)
    planned = sample_mandate.model_copy(update={"state": MandateState.PLANNED, "reason": "planned"})
    repository.save_mandate(planned)
    assignment = make_assignment(next_action_at=None)
    repository.add_assignment(assignment)
    completed = assignment.model_copy(
        update={"state": StakeholderState.COMPLETE, "completed_at": now}
    )
    repository.save_assignment(completed)

    assert repository.get_mandate_by_token(planned.token) == planned
    assert repository.get_assignment(completed.assignment_id) == completed


def test_event_order_is_stable(repository, sample_mandate, now) -> None:
    repository.add_mandate(sample_mandate)
    repository.append_event(
        sample_mandate.mandate_id,
        DomainEvent(
            event_type="mandate.created",
            created_at=now,
            idempotency_key="mandate:sample:created",
            metadata={"safe": True},
        ),
    )
    repository.append_event(
        sample_mandate.mandate_id,
        DomainEvent(
            event_type="mandate.planned",
            created_at=now,
            idempotency_key="mandate:sample:planned",
            metadata={},
        ),
    )

    assert [event.event_type for event in repository.list_events(sample_mandate.mandate_id)] == [
        "mandate.created",
        "mandate.planned",
    ]


def test_event_idempotency_is_unique_and_metadata_rejects_destinations(
    repository, sample_mandate, now
) -> None:
    repository.add_mandate(sample_mandate)
    event = DomainEvent(
        event_type="mandate.created", created_at=now, idempotency_key="event:sample", metadata={}
    )
    repository.append_event(sample_mandate.mandate_id, event)

    with pytest.raises(ValueError):
        repository.append_event(sample_mandate.mandate_id, event)
    with pytest.raises(ValueError, match="destination"):
        repository.append_event(
            sample_mandate.mandate_id,
            event.model_copy(
                update={
                    "idempotency_key": "event:unsafe",
                    "metadata": {"recipient": "private@example.test"},
                }
            ),
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"route_id": "private.person@example.test"},
        {"person_id": "+1 (555) 010-9999"},
        {"references": [{"route_id": "tg://resolve?domain=private-chat"}]},
    ],
)
def test_event_metadata_rejects_destination_values_under_safe_keys(
    repository, sample_mandate, now, metadata
) -> None:
    repository.add_mandate(sample_mandate)

    with pytest.raises(ValueError, match="destination"):
        repository.append_event(
            sample_mandate.mandate_id,
            DomainEvent(
                event_type="outreach.attempted",
                created_at=now,
                idempotency_key="event:nested-destination",
                metadata=metadata,
            ),
        )


def test_due_assignments_excludes_terminal_states(repository, due_assignments, now) -> None:
    tokens = {item.assignment_id for item in repository.list_due_assignments(now)}
    assert due_assignments.follow_up.assignment_id in tokens
    assert due_assignments.complete.assignment_id not in tokens


def test_round_trips_runtime_status(repository, now) -> None:
    repository.set_runtime_status("channel.email", "ready", now)

    assert repository.get_runtime_status("channel.email") == ("ready", now)


def test_transaction_commits_mandate_update_and_event_together(
    repository, sample_mandate, now
) -> None:
    repository.add_mandate(sample_mandate)
    updated = sample_mandate.model_copy(update={"state": MandateState.PLANNED, "reason": "planned"})
    event = DomainEvent(
        event_type="mandate.planned",
        created_at=now,
        idempotency_key="event:transaction",
        metadata={},
    )

    with repository.transaction() as unit:
        unit.save_mandate(updated)
        unit.append_event(updated.mandate_id, event)

    assert repository.get_mandate_by_token(updated.token) == updated
    assert repository.list_events(updated.mandate_id) == [event]
