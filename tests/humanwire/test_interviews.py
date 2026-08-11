from datetime import timedelta
from uuid import uuid4

import pytest

from humanwire.config import Settings
from humanwire.database import create_session_factory
from humanwire.directory import InitiatorPolicy, OrganizationDirectory, OrganizationDocument
from humanwire.domain import (
    Channel,
    ContactRoute,
    Direction,
    IncomingMessage,
    InterviewSession,
    Mandate,
    MandateState,
    Person,
    StakeholderAssignment,
    StakeholderState,
)
from humanwire.evidence import RuleBasedEvidenceExtractor
from humanwire.interviews import InterviewCoordinator
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.state_machine import StakeholderStateMachine


@pytest.fixture
def directory() -> OrganizationDirectory:
    manager = Person(
        person_id="manager",
        display_name="Morgan Lee",
        role="Manager",
        department="Operations",
        timezone="America/Chicago",
        routes=[
            ContactRoute(
                route_id="manager-telegram",
                channel=Channel.TELEGRAM,
                sender_address="manager-chat",
                conversation_id="manager-conversation",
                preferred=True,
            )
        ],
    )
    priya = Person(
        person_id="priya",
        display_name="Priya Raman",
        role="People Partner",
        department="People",
        timezone="America/Chicago",
        routes=[
            ContactRoute(
                route_id="email-priya",
                channel=Channel.EMAIL,
                sender_address="priya@example.test",
                recipient="priya@example.test",
                preferred=True,
            ),
            ContactRoute(
                route_id="telegram-priya",
                channel=Channel.TELEGRAM,
                sender_address="priya-telegram",
                conversation_id="tg-priya",
            ),
        ],
    )
    return OrganizationDirectory(
        OrganizationDocument(
            people=[manager, priya],
            initiator_policies=[
                InitiatorPolicy(
                    person_id="manager",
                    allowed_directions={Direction.LATERAL},
                    allowed_departments={"People"},
                )
            ],
        )
    )


@pytest.fixture
def repository() -> SqlAlchemyHumanWireRepository:
    return SqlAlchemyHumanWireRepository(create_session_factory("sqlite://"))


@pytest.fixture
def mandate(now) -> Mandate:
    return Mandate(
        mandate_id=uuid4(),
        token="HW-2411",
        initiator_id="manager",
        origin_channel=Channel.TELEGRAM,
        origin_conversation_id="manager-conversation",
        origin_message_id="origin-1",
        redacted_request="Coordinate launch coverage",
        objective="Coordinate launch coverage",
        plan={
            "objective": "Coordinate launch coverage",
            "required_decisions": ["Approve coverage"],
            "stakeholders": [
                {
                    "person_ref": "priya",
                    "reason": "Need policy constraints",
                    "direction": "lateral",
                    "questions": ["What policy applies?"],
                }
            ],
            "completion_conditions": ["Interview complete"],
        },
        state=MandateState.PLANNED,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
        idempotency_key="mandate:interview",
    )


@pytest.fixture
def coordinator(directory, repository) -> InterviewCoordinator:
    return InterviewCoordinator(
        directory,
        repository,
        StakeholderStateMachine(),
        RuleBasedEvidenceExtractor(),
        Settings(acknowledgement_seconds=60, reminder_seconds=30),
    )


def _assignment(mandate: Mandate, *, attempt_count: int = 0, **updates) -> StakeholderAssignment:
    values = {
        "assignment_id": uuid4(),
        "mandate_id": mandate.mandate_id,
        "person_id": "priya",
        "department": "People",
        "direction": Direction.LATERAL,
        "reason": "Need policy constraints",
        "required": True,
        "state": StakeholderState.NOT_CONTACTED,
        "route_ids": ["email-priya", "telegram-priya"],
        "attempt_count": attempt_count,
    }
    values.update(updates)
    return StakeholderAssignment(**values)


def _message(now, *, text: str, channel: Channel, sender: str, conversation: str) -> IncomingMessage:
    return IncomingMessage(
        message_id=f"message-{channel.value}-{sender}",
        conversation_id=conversation,
        connection_id="connection-1",
        channel=channel,
        sender_address=sender,
        text=text,
        received_at=now,
    )


def _add_session(repository, assignment: StakeholderAssignment, now, questions=None) -> StakeholderAssignment:
    session_id = uuid4()
    prepared = assignment.model_copy(update={"interview_id": session_id})
    repository.add_assignment(prepared)
    repository.add_interview(
        InterviewSession(
            session_id=session_id,
            mandate_id=prepared.mandate_id,
            assignment_id=prepared.assignment_id,
            questions=questions or ["One?", "Two?", "Three?"],
            started_at=now,
            updated_at=now,
        )
    )
    return prepared


def test_start_assignment_constructs_email_intro_and_limits_questions(
    coordinator, repository, mandate, now
) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    repository.add_assignment(assignment)

    result = coordinator.start_assignment(
        assignment,
        ["One?", "Two?", "Three?", "Four?", "Five?", "Six?"],
        now,
    )

    session = repository.get_interview(repository.get_assignment(assignment.assignment_id).interview_id)
    assert result.deliveries[0].recipient == "priya@example.test"
    assert "HUMANWIRE INTERVIEW" in result.deliveries[0].text
    assert "ACK HW-2411" in result.deliveries[0].text
    assert session is not None
    assert len(session.questions) == 5


def test_acknowledgement_on_alternate_channel_resumes_same_session(
    coordinator, repository, mandate, now
) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(
        mandate,
        state=StakeholderState.ALTERNATE_CHANNEL,
        active_route_index=1,
        attempt_count=2,
    )
    assignment_after_email_timeout = _add_session(repository, assignment, now)
    telegram_ack = _message(
        now, text="ACK HW-2411", channel=Channel.TELEGRAM, sender="priya-telegram", conversation="tg-priya"
    )

    result = coordinator.acknowledge(telegram_ack, assignment_after_email_timeout, now)
    session = repository.get_interview(assignment_after_email_timeout.interview_id)
    assert session is not None
    assert session.current_channel is Channel.TELEGRAM
    assert session.current_question_index == 0
    assert result.deliveries[0].conversation_id == "tg-priya"
    assert "Question 1 of 3" in result.deliveries[0].text


def test_answer_advances_question_and_persists_evidence(coordinator, repository, mandate, now) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    assignment = _add_session(repository, assignment, now)
    coordinator.start_assignment(assignment, ["One?", "Two?", "Three?"], now)
    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None
    coordinator.acknowledge(
        _message(now, text="ACK HW-2411", channel=Channel.EMAIL, sender="priya@example.test", conversation="mail-priya"),
        saved,
        now,
    )
    interviewing_assignment = repository.get_assignment(assignment.assignment_id)
    assert interviewing_assignment is not None
    email_answer = _message(
        now,
        text="PRIVATE We must give 72 hours notice.",
        channel=Channel.EMAIL,
        sender="priya@example.test",
        conversation="mail-priya",
    )

    result = coordinator.record_answer(email_answer, interviewing_assignment, now)

    assert "Question 2 of 3" in result.deliveries[0].text
    assert repository.list_evidence(interviewing_assignment.mandate_id)


@pytest.mark.parametrize(
    ("attempt", "expected_state", "event_type"),
    [
        (0, StakeholderState.AWAITING_ACKNOWLEDGEMENT, "outreach.primary_sent"),
        (1, StakeholderState.FOLLOW_UP_DUE, "outreach.reminder_sent"),
        (2, StakeholderState.ALTERNATE_CHANNEL, "outreach.alternate_sent"),
        (3, StakeholderState.UNREACHABLE, "stakeholder.unreachable"),
    ],
)
def test_response_ladder_never_assumes_agreement(
    coordinator, repository, mandate, attempt, expected_state, event_type, now
) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate, attempt_count=attempt)
    if attempt == 1:
        assignment = assignment.model_copy(update={"state": StakeholderState.AWAITING_ACKNOWLEDGEMENT})
    elif attempt == 2:
        assignment = assignment.model_copy(update={"state": StakeholderState.FOLLOW_UP_DUE})
    elif attempt == 3:
        assignment = assignment.model_copy(update={"state": StakeholderState.ALTERNATE_CHANNEL})
    assignment = _add_session(repository, assignment, now)

    coordinator.process_due_assignment(assignment, now)

    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None
    assert saved.state is expected_state
    assert event_type in [event.event_type for event in repository.list_events(assignment.mandate_id)]
    assert saved.state is not StakeholderState.COMPLETE


def test_two_undeliverable_routes_mark_delivery_failed(coordinator, repository, mandate, now) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate, route_ids=[])
    assignment = _add_session(repository, assignment, now)

    coordinator.process_due_assignment(assignment, now)

    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None
    assert saved.state is StakeholderState.DELIVERY_FAILED


def test_one_route_has_no_duplicate_alternate_send(coordinator, repository, mandate, now) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(
        mandate,
        route_ids=["email-priya"],
        state=StakeholderState.FOLLOW_UP_DUE,
        attempt_count=2,
    )
    assignment = _add_session(repository, assignment, now)

    result = coordinator.process_due_assignment(assignment, now)

    assert all("same interview will continue" not in delivery.text for delivery in result.deliveries)
    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None
    assert saved.state is StakeholderState.UNREACHABLE


def test_explicit_decline_records_declined_without_treating_it_as_an_answer(
    coordinator, repository, mandate, now
) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    repository.add_assignment(assignment)
    coordinator.start_assignment(assignment, ["One?"], now)
    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None

    result = coordinator.record_answer(
        _message(
            now,
            text="DECLINE HW-2411",
            channel=Channel.EMAIL,
            sender="priya@example.test",
            conversation="mail-priya",
        ),
        saved,
        now,
    )

    declined = repository.get_assignment(assignment.assignment_id)
    assert result.deliveries == []
    assert declined is not None
    assert declined.state is StakeholderState.DECLINED
    assert repository.list_evidence(mandate.mandate_id) == []


def test_availability_command_requires_the_assignment_token(coordinator, repository, mandate, now) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    repository.add_assignment(assignment)
    coordinator.start_assignment(assignment, ["When are you available?"], now)
    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None

    result = coordinator.record_answer(
        _message(
            now,
            text="AVAILABLE HW-9999 2026-08-14T15:00:00-05:00/2026-08-14T16:00:00-05:00",
            channel=Channel.EMAIL,
            sender="priya@example.test",
            conversation="mail-priya",
        ),
        saved,
        now,
    )

    assert result.deliveries == []
    assert repository.list_evidence(mandate.mandate_id) == []


def test_wrong_token_acknowledgement_cannot_be_persisted_as_an_answer(
    coordinator, repository, mandate, now
) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    repository.add_assignment(assignment)
    coordinator.start_assignment(assignment, ["One?"], now)
    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None

    result = coordinator.record_answer(
        _message(
            now,
            text="ACK HW-9999",
            channel=Channel.EMAIL,
            sender="priya@example.test",
            conversation="mail-priya",
        ),
        saved,
        now,
    )

    assert result.deliveries == []
    assert repository.list_evidence(mandate.mandate_id) == []


def test_delivery_success_is_idempotent(coordinator, repository, mandate, now) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    repository.add_assignment(assignment)
    coordinator.start_assignment(assignment, ["One?"], now)

    coordinator.mark_delivery_success(assignment.assignment_id, "delivery-1", now)
    coordinator.mark_delivery_success(assignment.assignment_id, "delivery-1", now)

    events = repository.list_events(mandate.mandate_id)
    assert [event.event_type for event in events].count("outreach.delivery_confirmed") == 1


def test_tokenless_answer_on_active_route_and_conversation_is_accepted(
    coordinator, repository, mandate, now
) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    repository.add_assignment(assignment)
    coordinator.start_assignment(assignment, ["One?", "Two?"], now)
    pending = repository.get_assignment(assignment.assignment_id)
    assert pending is not None
    coordinator.acknowledge(
        _message(
            now,
            text="ACK HW-2411",
            channel=Channel.EMAIL,
            sender="priya@example.test",
            conversation="mail-priya",
        ),
        pending,
        now,
    )
    interviewing = repository.get_assignment(assignment.assignment_id)
    assert interviewing is not None

    result = coordinator.record_answer(
        _message(
            now,
            text="We need 72 hours notice.",
            channel=Channel.EMAIL,
            sender="priya@example.test",
            conversation="mail-priya",
        ),
        interviewing,
        now,
    )

    assert "Question 2 of 2" in result.deliveries[0].text
    assert len(repository.list_evidence(mandate.mandate_id)) == 1


def test_tokenless_answer_from_active_route_with_wrong_conversation_is_rejected(
    coordinator, repository, mandate, now
) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    repository.add_assignment(assignment)
    coordinator.start_assignment(assignment, ["One?", "Two?"], now)
    pending = repository.get_assignment(assignment.assignment_id)
    assert pending is not None
    coordinator.acknowledge(
        _message(
            now,
            text="ACK HW-2411",
            channel=Channel.EMAIL,
            sender="priya@example.test",
            conversation="mail-priya",
        ),
        pending,
        now,
    )
    interviewing = repository.get_assignment(assignment.assignment_id)
    assert interviewing is not None

    result = coordinator.record_answer(
        _message(
            now,
            text="We need 72 hours notice.",
            channel=Channel.EMAIL,
            sender="priya@example.test",
            conversation="old-mail-thread",
        ),
        interviewing,
        now,
    )

    session = repository.get_interview(interviewing.interview_id)
    assert result.deliveries == []
    assert repository.list_evidence(mandate.mandate_id) == []
    assert session is not None
    assert session.current_question_index == 0


def test_token_correlated_cross_channel_answer_switches_the_active_session(
    coordinator, repository, mandate, now
) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    repository.add_assignment(assignment)
    coordinator.start_assignment(assignment, ["One?", "Two?"], now)
    pending = repository.get_assignment(assignment.assignment_id)
    assert pending is not None
    coordinator.acknowledge(
        _message(
            now,
            text="ACK HW-2411",
            channel=Channel.EMAIL,
            sender="priya@example.test",
            conversation="mail-priya",
        ),
        pending,
        now,
    )
    interviewing = repository.get_assignment(assignment.assignment_id)
    assert interviewing is not None

    result = coordinator.record_answer(
        _message(
            now,
            text="HW-2411 We need 72 hours notice.",
            channel=Channel.TELEGRAM,
            sender="priya-telegram",
            conversation="tg-priya",
        ),
        interviewing,
        now,
    )

    session = repository.get_interview(interviewing.interview_id)
    assert "Question 2 of 2" in result.deliveries[0].text
    assert session is not None
    assert session.current_route_id == "telegram-priya"
    assert session.current_conversation_id == "tg-priya"


def test_tokenless_stale_previous_channel_reply_is_rejected(
    coordinator, repository, mandate, now
) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    repository.add_assignment(assignment)
    coordinator.start_assignment(assignment, ["One?", "Two?", "Three?"], now)
    pending = repository.get_assignment(assignment.assignment_id)
    assert pending is not None
    coordinator.acknowledge(
        _message(
            now,
            text="ACK HW-2411",
            channel=Channel.EMAIL,
            sender="priya@example.test",
            conversation="mail-priya",
        ),
        pending,
        now,
    )
    interviewing = repository.get_assignment(assignment.assignment_id)
    assert interviewing is not None
    coordinator.record_answer(
        _message(
            now,
            text="HW-2411 Current answer.",
            channel=Channel.TELEGRAM,
            sender="priya-telegram",
            conversation="tg-priya",
        ),
        interviewing,
        now,
    )
    switched = repository.get_assignment(assignment.assignment_id)
    assert switched is not None

    result = coordinator.record_answer(
        _message(
            now,
            text="Stale email answer.",
            channel=Channel.EMAIL,
            sender="priya@example.test",
            conversation="mail-priya",
        ),
        switched,
        now,
    )

    session = repository.get_interview(switched.interview_id)
    assert result.deliveries == []
    assert len(repository.list_evidence(mandate.mandate_id)) == 1
    assert session is not None
    assert session.current_question_index == 1


def test_alternate_escalation_persists_route_correlation_for_tokenless_continuity(
    coordinator, repository, mandate, now
) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(mandate)
    repository.add_assignment(assignment)
    coordinator.start_assignment(assignment, ["One?", "Two?"], now)
    primary = repository.get_assignment(assignment.assignment_id)
    assert primary is not None
    coordinator.process_due_assignment(primary, now + timedelta(seconds=60))
    reminder = repository.get_assignment(assignment.assignment_id)
    assert reminder is not None
    coordinator.process_due_assignment(reminder, now + timedelta(seconds=90))
    alternate = repository.get_assignment(assignment.assignment_id)
    assert alternate is not None

    session = repository.get_interview(alternate.interview_id)
    assert session is not None
    assert session.current_route_id == "telegram-priya"
    assert session.current_conversation_id == "tg-priya"
    result = coordinator.record_answer(
        _message(
            now,
            text="We need 72 hours notice.",
            channel=Channel.TELEGRAM,
            sender="priya-telegram",
            conversation="tg-priya",
        ),
        alternate,
        now,
    )

    assert "Question 2 of 2" in result.deliveries[0].text
    assert len(repository.list_evidence(mandate.mandate_id)) == 1


def test_completed_assignment_never_appears_in_due_work(repository, mandate, now) -> None:
    repository.add_mandate(mandate)
    assignment = _assignment(
        mandate,
        state=StakeholderState.COMPLETE,
        completed_at=now,
        next_action_at=now,
    )
    repository.add_assignment(assignment)

    assert assignment.assignment_id not in {item.assignment_id for item in repository.list_due_assignments(now)}
