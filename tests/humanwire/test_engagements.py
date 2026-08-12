import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from humanwire.commands import EngagementDecisionCommand, parse_command
from humanwire.config import Settings
from humanwire.database import create_session_factory
from humanwire.directory import InitiatorPolicy, OrganizationDirectory, OrganizationDocument
from humanwire.domain import (
    AvailabilityWindow,
    Channel,
    ContactRoute,
    Direction,
    DomainEvent,
    EngagementDecisionKind,
    EngagementType,
    EvidenceStatus,
    EvidenceType,
    IncomingMessage,
    Mandate,
    MandateState,
    Person,
    StakeholderAssignment,
    StakeholderState,
    WorkflowResult,
)
from humanwire.engagements import EngagementCoordinator
from humanwire.evidence import RuleBasedEvidenceExtractor
from humanwire.repository import RepositoryUnitOfWork, SqlAlchemyHumanWireRepository
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
                conversation_id="mail-priya",
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
    stranger = Person(
        person_id="stranger",
        display_name="Sam Stranger",
        role="Analyst",
        department="Finance",
        timezone="America/Chicago",
        routes=[
            ContactRoute(
                route_id="stranger-email",
                channel=Channel.EMAIL,
                sender_address="stranger@example.test",
                recipient="stranger@example.test",
                conversation_id="mail-stranger",
            )
        ],
    )
    return OrganizationDirectory(
        OrganizationDocument(
            people=[manager, priya, stranger],
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
            "required_decisions": ["Confirm launch coverage"],
            "stakeholders": [
                {
                    "person_ref": "priya",
                    "reason": "Need policy constraints",
                    "direction": "lateral",
                    "engagement_type": "structured_interview",
                    "response_required": True,
                    "questions": ["Fact?", "Constraint?", "Commitment?"],
                }
            ],
            "completion_conditions": ["Required contribution recorded"],
        },
        state=MandateState.INTERVIEWING,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
        idempotency_key="mandate:engagement",
    )


@pytest.fixture
def coordinator(directory, repository) -> EngagementCoordinator:
    return EngagementCoordinator(
        directory,
        repository,
        StakeholderStateMachine(),
        RuleBasedEvidenceExtractor(),
        Settings(acknowledgement_seconds=60, reminder_seconds=30),
    )


def _assignment(
    mandate: Mandate,
    engagement_type: EngagementType,
    *,
    route_ids: list[str] | None = None,
    **updates,
) -> StakeholderAssignment:
    values = {
        "assignment_id": uuid4(),
        "mandate_id": mandate.mandate_id,
        "person_id": "priya",
        "department": "People",
        "direction": Direction.LATERAL,
        "reason": "Need a proportionate contribution",
        "required": engagement_type is not EngagementType.INFORM,
        "engagement_type": engagement_type,
        "response_required": engagement_type is not EngagementType.INFORM,
        "state": StakeholderState.NOT_CONTACTED,
        "route_ids": route_ids or ["email-priya", "telegram-priya"],
    }
    values.update(updates)
    return StakeholderAssignment(**values)


def _message(
    now,
    *,
    message_id: str,
    text: str,
    channel: Channel = Channel.EMAIL,
    sender: str = "priya@example.test",
    conversation: str = "mail-priya",
) -> IncomingMessage:
    return IncomingMessage(
        message_id=message_id,
        conversation_id=conversation,
        connection_id="connection-1",
        channel=channel,
        sender_address=sender,
        text=text,
        received_at=now,
    )


def _decision(text: str) -> EngagementDecisionCommand:
    command = parse_command(text)
    assert isinstance(command, EngagementDecisionCommand)
    return command


def _availability_windows() -> tuple[AvailabilityWindow, ...]:
    return (
        AvailabilityWindow(
            start="2026-08-14T15:00:00-05:00",
            end="2026-08-14T16:00:00-05:00",
        ),
        AvailabilityWindow(
            start="2026-08-15T10:00:00-05:00",
            end="2026-08-15T11:30:00-05:00",
        ),
    )


def _prepare(
    coordinator: EngagementCoordinator,
    repository: SqlAlchemyHumanWireRepository,
    mandate: Mandate,
    assignment: StakeholderAssignment,
    questions: list[str],
    now,
):
    repository.add_mandate(mandate)
    repository.add_assignment(assignment)
    prepared = coordinator.prepare_start(
        assignment,
        questions,
        mandate.token,
        mandate.objective,
        now,
    )
    coordinator.persist_prepared(prepared)
    return prepared


def _provider_delivery_id(delivery) -> str:
    source = delivery.message_id or "|".join(
        [
            delivery.kind.value,
            delivery.recipient or "",
            delivery.conversation_id or "",
            str(delivery.assignment_id),
        ]
    )
    return hashlib.sha256(source.encode()).hexdigest()[:48]


def _file_repository(tmp_path) -> SqlAlchemyHumanWireRepository:
    database_path = tmp_path / f"engagement-review-{uuid4().hex}.db"
    factory = create_session_factory(f"sqlite:///{database_path.as_posix()}")
    with factory.kw["bind"].begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA busy_timeout=5000")
    return SqlAlchemyHumanWireRepository(factory)


def _file_coordinator(directory, repository) -> EngagementCoordinator:
    return EngagementCoordinator(
        directory,
        repository,
        StakeholderStateMachine(),
        RuleBasedEvidenceExtractor(),
        Settings(acknowledgement_seconds=60, reminder_seconds=30),
    )


def test_inform_prepares_one_delivery_and_completes_only_after_provider_success(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.INFORM)
    prepared = _prepare(coordinator, repository, mandate, assignment, [], now)

    before_callback = repository.get_assignment(assignment.assignment_id)
    assert prepared.interview is None
    assert prepared.delivery.assignment_id == assignment.assignment_id
    assert prepared.delivery.text.startswith("HUMANWIRE UPDATE")
    assert "no response requested" in prepared.delivery.text.lower()
    assert before_callback is not None
    assert before_callback.state is StakeholderState.DELIVERED
    assert before_callback.completed_at is None
    assert before_callback.next_action_at is None
    assert repository.list_interviews(mandate.mandate_id) == []

    coordinator.mark_delivery_success(
        assignment.assignment_id,
        _provider_delivery_id(prepared.delivery),
        now,
    )

    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    assert saved.next_action_at is None
    assert repository.list_evidence(mandate.mandate_id) == []
    assert assignment.assignment_id not in {
        item.assignment_id for item in repository.list_due_assignments(now + timedelta(days=1))
    }


def test_inform_provider_failure_advances_once_then_success_is_replay_safe(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.INFORM)
    prepared = _prepare(coordinator, repository, mandate, assignment, [], now)
    primary_delivery_id = _provider_delivery_id(prepared.delivery)

    first = coordinator.mark_delivery_failure(
        assignment.assignment_id, primary_delivery_id, now
    )
    duplicate_failure = coordinator.mark_delivery_failure(
        assignment.assignment_id, primary_delivery_id, now
    )

    alternate = repository.get_assignment(assignment.assignment_id)
    assert alternate is not None
    assert alternate.state is StakeholderState.ALTERNATE_CHANNEL
    assert alternate.active_route_index == 1
    assert alternate.next_action_at is None
    assert len(first.deliveries) == 1
    assert first.deliveries[0].conversation_id == "tg-priya"
    assert "HUMANWIRE UPDATE" in first.deliveries[0].text
    assert "confirmed delivery" in first.deliveries[0].text.lower()
    assert "priya@example.test" not in first.deliveries[0].text
    assert "tg-priya" not in first.deliveries[0].text
    assert duplicate_failure.deliveries == []

    alternate_delivery_id = _provider_delivery_id(first.deliveries[0])
    coordinator.mark_delivery_success(
        assignment.assignment_id, alternate_delivery_id, now
    )
    coordinator.mark_delivery_success(
        assignment.assignment_id, alternate_delivery_id, now
    )

    saved = repository.get_assignment(assignment.assignment_id)
    events = repository.list_events(mandate.mandate_id)
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    assert [event.event_type for event in events].count("outreach.alternate_sent") == 1
    assert [event.event_type for event in events].count("outreach.delivery_confirmed") == 1


def test_inform_exhausted_routes_truthfully_marks_delivery_failed_and_notifies_owner(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(
        mandate, EngagementType.INFORM, route_ids=["email-priya"]
    )
    prepared = _prepare(coordinator, repository, mandate, assignment, [], now)

    result = coordinator.mark_delivery_failure(
        assignment.assignment_id,
        _provider_delivery_id(prepared.delivery),
        now,
    )

    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None
    assert saved.state is StakeholderState.DELIVERY_FAILED
    assert len(result.deliveries) == 1
    notice = result.deliveries[0].text.lower()
    assert "delivery could not be confirmed" in notice
    assert "no required response was recorded" in notice
    assert "did not respond" not in notice
    assert "disagreed" not in notice


def test_acknowledgement_completes_on_exact_registered_ack_without_question_or_evidence(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.ACKNOWLEDGE)
    prepared = _prepare(coordinator, repository, mandate, assignment, [], now)

    coordinator.mark_delivery_success(
        assignment.assignment_id,
        _provider_delivery_id(prepared.delivery),
        now,
    )
    after_delivery = repository.get_assignment(assignment.assignment_id)
    assert prepared.interview is None
    assert prepared.delivery.text.startswith("HUMANWIRE ACKNOWLEDGEMENT")
    assert "Reply ACK HW-2411" in prepared.delivery.text
    assert after_delivery is not None
    assert after_delivery.state is StakeholderState.AWAITING_ACKNOWLEDGEMENT
    assert after_delivery.completed_at is None

    result = coordinator.acknowledge(
        _message(now, message_id="ack-1", text="ACK HW-2411"),
        after_delivery,
        now,
    )

    saved = repository.get_assignment(assignment.assignment_id)
    assert result.deliveries == []
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    assert saved.acknowledged_at == now
    assert repository.list_interviews(mandate.mandate_id) == []
    assert repository.list_evidence(mandate.mandate_id) == []


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(
            lambda now: _message(now, message_id="bad-token", text="ACK HW-9999"),
            id="wrong-token",
        ),
        pytest.param(
            lambda now: _message(
                now,
                message_id="bad-person",
                text="ACK HW-2411",
                sender="stranger@example.test",
                conversation="mail-stranger",
            ),
            id="wrong-person",
        ),
        pytest.param(
            lambda now: _message(
                now,
                message_id="bad-channel",
                text="ACK HW-2411",
                channel=Channel.TELEGRAM,
                sender="priya-telegram",
                conversation="tg-priya",
            ),
            id="inactive-channel",
        ),
        pytest.param(
            lambda now: _message(
                now,
                message_id="bad-thread",
                text="ACK HW-2411",
                conversation="other-mail-thread",
            ),
            id="wrong-thread",
        ),
        pytest.param(
            lambda now: _message(now, message_id="lookalike", text="yes, acknowledged"),
            id="free-text-lookalike",
        ),
    ],
)
def test_acknowledgement_rejects_wrong_token_person_channel_thread_and_lookalikes(
    coordinator, repository, mandate, now, message
) -> None:
    assignment = _assignment(mandate, EngagementType.ACKNOWLEDGE)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    before = repository.get_assignment(assignment.assignment_id)
    event_count = len(repository.list_events(mandate.mandate_id))

    result = coordinator.acknowledge(message(now), assignment, now)

    assert result.deliveries == []
    assert repository.get_assignment(assignment.assignment_id) == before
    assert len(repository.list_events(mandate.mandate_id)) == event_count


def test_acknowledgement_duplicate_message_and_terminal_replies_are_inert(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.ACKNOWLEDGE)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    acknowledgement = _message(now, message_id="same-ack", text="ACK HW-2411")

    coordinator.acknowledge(acknowledgement, assignment, now)
    after_first = repository.get_assignment(assignment.assignment_id)
    event_count = len(repository.list_events(mandate.mandate_id))
    duplicate = coordinator.acknowledge(acknowledgement, assignment, now)
    late = coordinator.acknowledge(
        _message(now, message_id="late-ack", text="ACK HW-2411"), assignment, now
    )

    assert duplicate.deliveries == []
    assert late.deliveries == []
    assert repository.get_assignment(assignment.assignment_id) == after_first
    assert len(repository.list_events(mandate.mandate_id)) == event_count


def test_initiated_email_acknowledgement_requires_exact_token_but_can_bind_new_thread(
    directory, repository, mandate, now
) -> None:
    document = directory.document.model_copy(deep=True)
    priya = next(person for person in document.people if person.person_id == "priya")
    primary = next(route for route in priya.routes if route.route_id == "email-priya")
    primary.conversation_id = None
    coordinator = EngagementCoordinator(
        OrganizationDirectory(document),
        repository,
        StakeholderStateMachine(),
        RuleBasedEvidenceExtractor(),
        Settings(acknowledgement_seconds=60, reminder_seconds=30),
    )
    assignment = _assignment(mandate, EngagementType.ACKNOWLEDGE)
    _prepare(coordinator, repository, mandate, assignment, [], now)

    lookalike = coordinator.acknowledge(
        _message(
            now,
            message_id="tokenless-new-thread",
            text="I acknowledge this",
            conversation="new-mail-thread",
        ),
        assignment,
        now,
    )
    exact = coordinator.acknowledge(
        _message(
            now,
            message_id="exact-new-thread",
            text="ACK HW-2411",
            conversation="new-mail-thread",
        ),
        assignment,
        now,
    )

    saved = repository.get_assignment(assignment.assignment_id)
    assert lookalike.deliveries == []
    assert exact.deliveries == []
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE


def test_acknowledgement_due_ladder_is_durable_type_labelled_and_replay_safe(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.ACKNOWLEDGE)
    _prepare(coordinator, repository, mandate, assignment, [], now)

    reminder = coordinator.process_due_assignment(assignment, now + timedelta(seconds=60))
    duplicate_reminder = coordinator.process_due_assignment(
        assignment, now + timedelta(seconds=60)
    )
    alternate = coordinator.process_due_assignment(
        assignment, now + timedelta(seconds=90)
    )
    duplicate_alternate = coordinator.process_due_assignment(
        assignment, now + timedelta(seconds=90)
    )
    unreachable = coordinator.process_due_assignment(
        assignment, now + timedelta(seconds=150)
    )

    saved = repository.get_assignment(assignment.assignment_id)
    events = [event.event_type for event in repository.list_events(mandate.mandate_id)]
    assert "HUMANWIRE ACKNOWLEDGEMENT" in reminder.deliveries[0].text
    assert "HUMANWIRE ACKNOWLEDGEMENT" in alternate.deliveries[0].text
    assert "required acknowledgement" in alternate.deliveries[0].text.lower()
    assert "priya@example.test" not in alternate.deliveries[0].text
    assert "tg-priya" not in alternate.deliveries[0].text
    assert duplicate_reminder.deliveries == []
    assert duplicate_alternate.deliveries == []
    assert saved is not None
    assert saved.state is StakeholderState.UNREACHABLE
    assert "no required response was recorded" in unreachable.deliveries[0].text.lower()
    assert events.count("outreach.reminder_sent") == 1
    assert events.count("outreach.alternate_sent") == 1
    assert events.count("stakeholder.unreachable") == 1


@pytest.mark.parametrize(
    ("engagement_type", "questions", "heading"),
    [
        (EngagementType.QUICK_RESPONSE, ["Committed date?"], "HUMANWIRE QUICK RESPONSE"),
        (
            EngagementType.QUICK_RESPONSE,
            ["Committed date?", "Owner?"],
            "HUMANWIRE QUICK RESPONSE",
        ),
        (
            EngagementType.STRUCTURED_INTERVIEW,
            ["Fact?", "Constraint?", "Commitment?"],
            "HUMANWIRE INTERVIEW",
        ),
        (
            EngagementType.STRUCTURED_INTERVIEW,
            ["One?", "Two?", "Three?", "Four?", "Five?"],
            "HUMANWIRE INTERVIEW",
        ),
    ],
)
def test_question_engagements_create_bounded_sessions_and_type_aware_intro(
    coordinator, repository, mandate, now, engagement_type, questions, heading
) -> None:
    assignment = _assignment(mandate, engagement_type)
    prepared = _prepare(coordinator, repository, mandate, assignment, questions, now)

    assert prepared.interview is not None
    assert prepared.interview.questions == questions
    assert prepared.delivery.text.startswith(heading)
    assert "ACK HW-2411" in prepared.delivery.text


def test_quick_response_completes_only_after_every_required_answer(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.QUICK_RESPONSE)
    _prepare(coordinator, repository, mandate, assignment, ["Date?", "Owner?"], now)
    coordinator.acknowledge(
        _message(now, message_id="quick-ack", text="ACK HW-2411"), assignment, now
    )

    first = coordinator.record_answer(
        _message(now, message_id="quick-answer-1", text="Friday"), assignment, now
    )
    midway = repository.get_assignment(assignment.assignment_id)
    second = coordinator.record_answer(
        _message(now, message_id="quick-answer-2", text="Priya"), assignment, now
    )

    saved = repository.get_assignment(assignment.assignment_id)
    assert "Question 2 of 2" in first.deliveries[0].text
    assert midway is not None
    assert midway.state is StakeholderState.INTERVIEWING
    assert second.deliveries == []
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    assert len(repository.list_evidence(mandate.mandate_id)) == 2


def test_structured_interview_preserves_exact_cross_channel_token_correlation(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.STRUCTURED_INTERVIEW)
    _prepare(
        coordinator,
        repository,
        mandate,
        assignment,
        ["Fact?", "Constraint?", "Commitment?"],
        now,
    )
    coordinator.acknowledge(
        _message(now, message_id="interview-ack", text="ACK HW-2411"),
        assignment,
        now,
    )

    wrong = coordinator.record_answer(
        _message(
            now,
            message_id="cross-channel-tokenless",
            text="A tokenless stale answer",
            channel=Channel.TELEGRAM,
            sender="priya-telegram",
            conversation="tg-priya",
        ),
        assignment,
        now,
    )
    accepted = coordinator.record_answer(
        _message(
            now,
            message_id="cross-channel-token",
            text="HW-2411 A correlated answer",
            channel=Channel.TELEGRAM,
            sender="priya-telegram",
            conversation="tg-priya",
        ),
        assignment,
        now,
    )

    saved = repository.get_assignment(assignment.assignment_id)
    session = repository.get_interview(saved.interview_id) if saved else None
    assert wrong.deliveries == []
    assert "Question 2 of 3" in accepted.deliveries[0].text
    assert session is not None
    assert session.current_route_id == "telegram-priya"
    assert session.current_conversation_id == "tg-priya"
    assert len(repository.list_evidence(mandate.mandate_id)) == 1


@pytest.mark.parametrize(
    ("engagement_type", "questions", "heading", "response_label"),
    [
        (
            EngagementType.QUICK_RESPONSE,
            ["Date?"],
            "HUMANWIRE QUICK RESPONSE",
            "required quick response",
        ),
        (
            EngagementType.STRUCTURED_INTERVIEW,
            ["Fact?", "Constraint?", "Commitment?"],
            "HUMANWIRE INTERVIEW",
            "required interview response",
        ),
    ],
)
def test_question_reminder_and_alternate_copy_uses_type_without_destinations(
    coordinator,
    repository,
    mandate,
    now,
    engagement_type,
    questions,
    heading,
    response_label,
) -> None:
    assignment = _assignment(mandate, engagement_type)
    _prepare(coordinator, repository, mandate, assignment, questions, now)

    reminder = coordinator.process_due_assignment(assignment, now + timedelta(seconds=60))
    alternate = coordinator.process_due_assignment(assignment, now + timedelta(seconds=90))

    assert heading in reminder.deliveries[0].text
    assert heading in alternate.deliveries[0].text
    assert response_label in alternate.deliveries[0].text.lower()
    for delivery in [*reminder.deliveries, *alternate.deliveries]:
        assert "priya@example.test" not in delivery.text
        assert "mail-priya" not in delivery.text
        assert "tg-priya" not in delivery.text


@pytest.mark.parametrize(
    ("engagement_type", "heading", "required_copy"),
    [
        (
            EngagementType.REVIEW_APPROVAL,
            "HUMANWIRE APPROVAL REVIEW",
            "DECIDE HW-2411 APPROVE",
        ),
        (
            EngagementType.AVAILABILITY,
            "HUMANWIRE AVAILABILITY REQUEST",
            "AVAILABLE HW-2411 <start>/<end>",
        ),
    ],
)
def test_approval_and_availability_prepare_without_interview_and_use_typed_ladder(
    coordinator,
    repository,
    mandate,
    now,
    engagement_type,
    heading,
    required_copy,
) -> None:
    assignment = _assignment(mandate, engagement_type)
    prepared = _prepare(coordinator, repository, mandate, assignment, [], now)

    assert prepared.interview is None
    assert prepared.delivery.text.startswith(heading)
    assert required_copy in prepared.delivery.text
    assert "interview" not in prepared.delivery.text.casefold()
    assert repository.list_interviews(mandate.mandate_id) == []

    coordinator.mark_delivery_success(
        assignment.assignment_id,
        _provider_delivery_id(prepared.delivery),
        now,
    )
    after_success = repository.get_assignment(assignment.assignment_id)
    assert after_success is not None
    assert after_success.state is StakeholderState.AWAITING_ACKNOWLEDGEMENT
    assert after_success.completed_at is None

    reminder = coordinator.process_due_assignment(
        assignment, now + timedelta(seconds=60)
    )
    alternate = coordinator.process_due_assignment(
        assignment, now + timedelta(seconds=90)
    )
    unreachable = coordinator.process_due_assignment(
        assignment, now + timedelta(seconds=150)
    )

    assert reminder.deliveries[0].text.startswith(heading)
    assert alternate.deliveries[0].text.startswith(heading)
    assert required_copy.split(" <", 1)[0] in alternate.deliveries[0].text
    assert "interview" not in reminder.deliveries[0].text.casefold()
    assert "interview" not in alternate.deliveries[0].text.casefold()
    assert "required" in unreachable.deliveries[0].text.casefold()
    assert "disagreed" not in unreachable.deliveries[0].text.casefold()
    assert repository.get_assignment(assignment.assignment_id).state is (
        StakeholderState.UNREACHABLE
    )


@pytest.mark.parametrize(
    "engagement_type",
    [EngagementType.REVIEW_APPROVAL, EngagementType.AVAILABILITY],
)
def test_approval_and_availability_provider_failure_uses_registered_routes_only(
    coordinator, repository, mandate, now, engagement_type
) -> None:
    assignment = _assignment(mandate, engagement_type)
    prepared = _prepare(coordinator, repository, mandate, assignment, [], now)

    alternate = coordinator.mark_delivery_failure(
        assignment.assignment_id,
        _provider_delivery_id(prepared.delivery),
        now,
    )
    duplicate = coordinator.mark_delivery_failure(
        assignment.assignment_id,
        _provider_delivery_id(prepared.delivery),
        now,
    )

    saved = repository.get_assignment(assignment.assignment_id)
    assert len(alternate.deliveries) == 1
    assert alternate.deliveries[0].conversation_id == "tg-priya"
    assert "priya@example.test" not in alternate.deliveries[0].text
    assert "tg-priya" not in alternate.deliveries[0].text
    assert duplicate.deliveries == []
    assert saved is not None
    assert saved.state is StakeholderState.ALTERNATE_CHANNEL
    assert saved.completed_at is None


@pytest.mark.parametrize(
    ("text", "expected_response", "expected_change", "expected_statement"),
    [
        (
            "DECIDE HW-2411 APPROVE",
            EngagementDecisionKind.APPROVE,
            None,
            "Approval response: approved",
        ),
        (
            "DECIDE HW-2411 REJECT PRIVATE-REJECT-SENTINEL",
            EngagementDecisionKind.REJECT,
            "PRIVATE-REJECT-SENTINEL",
            "Approval response: rejected",
        ),
        (
            "DECIDE HW-2411 CHANGE PRIVATE-CHANGE-SENTINEL",
            EngagementDecisionKind.CHANGE,
            "PRIVATE-CHANGE-SENTINEL",
            "Approval response: change requested",
        ),
    ],
)
def test_authenticated_approval_persists_exact_decision_safe_evidence_and_typed_completion(
    coordinator,
    repository,
    mandate,
    now,
    text,
    expected_response,
    expected_change,
    expected_statement,
) -> None:
    assignment = _assignment(mandate, EngagementType.REVIEW_APPROVAL)
    prepared = _prepare(coordinator, repository, mandate, assignment, [], now)
    message = _message(now, message_id=f"decision-{expected_response.value}", text=text)

    result = coordinator.record_decision(
        message,
        assignment,
        _decision(text),
        now,
    )

    saved = repository.get_assignment(assignment.assignment_id)
    decisions = repository.list_engagement_decisions(mandate.mandate_id)
    evidence = repository.list_evidence(mandate.mandate_id)
    decision_events = [
        event
        for event in repository.list_events(mandate.mandate_id)
        if event.event_type == "engagement.decision_recorded"
    ]
    assert result.deliveries == []
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    assert saved.completed_at == now
    assert len(decisions) == 1
    assert decisions[0].assignment_id == assignment.assignment_id
    assert decisions[0].stakeholder_id == "priya"
    assert decisions[0].response is expected_response
    assert decisions[0].change_text == expected_change
    assert decisions[0].source_message_id == message.message_id
    assert len(evidence) == 1
    assert evidence[0].evidence_type is EvidenceType.DECISION
    assert evidence[0].status is EvidenceStatus.CONFIRMED
    assert evidence[0].statement == expected_statement
    assert len(decision_events) == 1
    assert decision_events[0].metadata == {"outcome": expected_response.value}

    public_text = "\n".join(
        [
            prepared.delivery.text,
            evidence[0].model_dump_json(),
            decision_events[0].model_dump_json(),
            *[delivery.text for delivery in result.deliveries],
        ]
    )
    if expected_change is not None:
        assert expected_change not in public_text


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(
            lambda now: _message(
                now,
                message_id="decision-wrong-token",
                text="DECIDE HW-9999 APPROVE",
            ),
            id="wrong-token",
        ),
        pytest.param(
            lambda now: _message(
                now,
                message_id="decision-wrong-person",
                text="DECIDE HW-2411 APPROVE",
                sender="stranger@example.test",
                conversation="mail-stranger",
            ),
            id="wrong-person",
        ),
        pytest.param(
            lambda now: _message(
                now,
                message_id="decision-wrong-route",
                text="DECIDE HW-2411 APPROVE",
                channel=Channel.TELEGRAM,
                sender="priya-telegram",
                conversation="tg-priya",
            ),
            id="wrong-route",
        ),
        pytest.param(
            lambda now: _message(
                now,
                message_id="decision-wrong-thread",
                text="DECIDE HW-2411 APPROVE",
                conversation="wrong-thread",
            ),
            id="wrong-thread",
        ),
    ],
)
def test_approval_rejects_wrong_token_person_route_and_thread_without_mutation(
    coordinator, repository, mandate, now, message
) -> None:
    assignment = _assignment(mandate, EngagementType.REVIEW_APPROVAL)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    incoming = message(now)
    before_assignment = repository.get_assignment(assignment.assignment_id)
    before_events = repository.list_events(mandate.mandate_id)

    result = coordinator.record_decision(
        incoming,
        assignment,
        _decision(incoming.text),
        now,
    )

    assert result.deliveries == []
    assert repository.get_assignment(assignment.assignment_id) == before_assignment
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert repository.list_evidence(mandate.mandate_id) == []
    assert repository.list_events(mandate.mandate_id) == before_events


def test_approval_ack_free_text_terminal_duplicate_and_conflicting_replay_are_inert(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.REVIEW_APPROVAL)
    _prepare(coordinator, repository, mandate, assignment, [], now)

    coordinator.acknowledge(
        _message(now, message_id="approval-ack-only", text="ACK HW-2411"),
        assignment,
        now,
    )
    coordinator.record_answer(
        _message(now, message_id="approval-free-text", text="I approve"),
        assignment,
        now,
    )
    before_decision = repository.get_assignment(assignment.assignment_id)
    assert before_decision is not None
    assert before_decision.state is StakeholderState.AWAITING_ACKNOWLEDGEMENT
    assert repository.list_engagement_decisions(mandate.mandate_id) == []

    first_message = _message(
        now,
        message_id="approval-replay",
        text="DECIDE HW-2411 REJECT First durable reason",
    )
    coordinator.record_decision(
        first_message,
        assignment,
        _decision(first_message.text),
        now,
    )
    durable = repository.list_engagement_decisions(mandate.mandate_id)
    events = repository.list_events(mandate.mandate_id)
    evidence = repository.list_evidence(mandate.mandate_id)

    coordinator.record_decision(
        first_message,
        assignment,
        _decision(first_message.text),
        now,
    )
    conflicting = first_message.model_copy(
        update={"text": "DECIDE HW-2411 CHANGE Conflicting replacement"}
    )
    coordinator.record_decision(
        conflicting,
        assignment,
        _decision(conflicting.text),
        now,
    )
    coordinator.record_decision(
        _message(
            now,
            message_id="approval-late-terminal",
            text="DECIDE HW-2411 APPROVE",
        ),
        assignment,
        _decision("DECIDE HW-2411 APPROVE"),
        now,
    )

    assert repository.list_engagement_decisions(mandate.mandate_id) == durable
    assert durable[0].response is EngagementDecisionKind.REJECT
    assert durable[0].change_text == "First durable reason"
    assert repository.list_events(mandate.mandate_id) == events
    assert repository.list_evidence(mandate.mandate_id) == evidence


def test_decision_transaction_failure_rolls_back_assignment_decision_event_and_evidence(
    coordinator, repository, mandate, now, monkeypatch
) -> None:
    assignment = _assignment(mandate, EngagementType.REVIEW_APPROVAL)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    before_assignment = repository.get_assignment(assignment.assignment_id)
    before_events = repository.list_events(mandate.mandate_id)
    original = RepositoryUnitOfWork.add_evidence

    def fail_after_evidence_is_staged(self, evidence):
        original(self, evidence)
        raise RuntimeError("injected decision evidence failure")

    monkeypatch.setattr(RepositoryUnitOfWork, "add_evidence", fail_after_evidence_is_staged)
    message = _message(
        now,
        message_id="approval-rollback",
        text="DECIDE HW-2411 APPROVE",
    )

    with pytest.raises(RuntimeError, match="injected decision evidence failure"):
        coordinator.record_decision(
            message,
            assignment,
            _decision(message.text),
            now,
        )

    assert repository.get_assignment(assignment.assignment_id) == before_assignment
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert repository.list_evidence(mandate.mandate_id) == []
    assert repository.list_events(mandate.mandate_id) == before_events


def test_threaded_exact_decision_duplicate_records_one_atomic_result(
    directory, mandate, now, tmp_path, monkeypatch
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, EngagementType.REVIEW_APPROVAL)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    message = _message(
        now,
        message_id="approval-threaded-duplicate",
        text="DECIDE HW-2411 APPROVE",
    )
    command = _decision(message.text)
    original_transaction = repository.transaction
    writers_ready = threading.Barrier(2)

    @contextmanager
    def synchronized_transaction():
        writers_ready.wait(timeout=5)
        with original_transaction() as unit:
            yield unit

    monkeypatch.setattr(repository, "transaction", synchronized_transaction)

    def decide():
        return coordinator.record_decision(message, assignment, command, now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: decide(), range(2)))

    saved = repository.get_assignment(assignment.assignment_id)
    assert all(result.deliveries == [] for result in results)
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    assert len(repository.list_engagement_decisions(mandate.mandate_id)) == 1
    assert len(repository.list_evidence(mandate.mandate_id)) == 1
    assert sum(
        event.event_type == "engagement.decision_recorded"
        for event in repository.list_events(mandate.mandate_id)
    ) == 1


@pytest.mark.parametrize(
    "terminal_state",
    [MandateState.CANCELLED, MandateState.EXPIRED],
)
@pytest.mark.parametrize(
    "response_kind",
    [EngagementType.REVIEW_APPROVAL, EngagementType.AVAILABILITY],
)
def test_terminal_mandate_commit_wins_before_explicit_response_uow(
    directory,
    mandate,
    now,
    tmp_path,
    monkeypatch,
    terminal_state,
    response_kind,
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, response_kind)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    before_assignment = repository.get_assignment(assignment.assignment_id)
    before_events = repository.list_events(mandate.mandate_id)
    before_evidence = repository.list_evidence(mandate.mandate_id)
    original_transaction = repository.transaction
    response_validated = threading.Event()
    terminal_committed = threading.Event()
    role = threading.local()

    @contextmanager
    def terminal_first_transaction():
        if getattr(role, "name", None) == "response":
            response_validated.set()
            assert terminal_committed.wait(timeout=5)
        with original_transaction() as unit:
            yield unit

    monkeypatch.setattr(repository, "transaction", terminal_first_transaction)

    def respond():
        role.name = "response"
        if response_kind is EngagementType.REVIEW_APPROVAL:
            message = _message(
                now,
                message_id=f"terminal-race-{terminal_state.value}-decision",
                text="DECIDE HW-2411 APPROVE",
            )
            return coordinator.record_decision(
                message,
                assignment,
                _decision(message.text),
                now,
            )
        windows = _availability_windows()
        text = "AVAILABLE HW-2411 " + " ".join(
            f"{window.start.isoformat()}/{window.end.isoformat()}"
            for window in windows
        )
        return coordinator.record_availability(
            _message(
                now,
                message_id=f"terminal-race-{terminal_state.value}-availability",
                text=text,
            ),
            assignment,
            windows,
            now,
        )

    def terminate() -> None:
        assert response_validated.wait(timeout=5)
        current = repository.get_mandate_by_token(mandate.token)
        assert current is not None
        updates = {
            "state": terminal_state,
            "updated_at": now,
            "completed_at": now,
        }
        if terminal_state is MandateState.EXPIRED:
            updates["expires_at"] = now
        with original_transaction() as unit:
            unit.save_mandate(current.model_copy(update=updates))
        terminal_committed.set()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="terminal-response") as executor:
        response_future = executor.submit(respond)
        terminal_future = executor.submit(terminate)
        result = response_future.result(timeout=10)
        terminal_future.result(timeout=10)

    saved_mandate = repository.get_mandate_by_token(mandate.token)
    assert result.deliveries == []
    assert saved_mandate is not None and saved_mandate.state is terminal_state
    assert repository.get_assignment(assignment.assignment_id) == before_assignment
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert repository.list_evidence(mandate.mandate_id) == before_evidence
    assert repository.list_events(mandate.mandate_id) == before_events
    assert repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:priya"
    ) is None


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("engagement_type", EngagementType.ACKNOWLEDGE),
        ("response_required", False),
        ("person_id", "stranger"),
        ("route_ids", ["telegram-priya"]),
    ],
)
@pytest.mark.parametrize(
    "response_kind",
    [EngagementType.REVIEW_APPROVAL, EngagementType.AVAILABILITY],
)
def test_explicit_response_loses_authorization_contract_change_after_validation(
    directory,
    mandate,
    now,
    tmp_path,
    monkeypatch,
    field,
    changed_value,
    response_kind,
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, response_kind)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    expected = repository.get_assignment(assignment.assignment_id)
    assert expected is not None
    changed = expected.model_copy(update={field: changed_value})
    before_events = repository.list_events(mandate.mandate_id)
    before_evidence = repository.list_evidence(mandate.mandate_id)
    original_transaction = repository.transaction
    response_validated = threading.Event()
    contract_committed = threading.Event()
    role = threading.local()

    @contextmanager
    def contract_first_transaction():
        if getattr(role, "name", None) == "response":
            response_validated.set()
            assert contract_committed.wait(timeout=5)
        with original_transaction() as unit:
            yield unit

    monkeypatch.setattr(repository, "transaction", contract_first_transaction)

    def respond():
        role.name = "response"
        if response_kind is EngagementType.REVIEW_APPROVAL:
            message = _message(
                now,
                message_id=f"contract-race-{field}-decision",
                text="DECIDE HW-2411 APPROVE",
            )
            return coordinator.record_decision(
                message,
                assignment,
                _decision(message.text),
                now,
            )
        windows = _availability_windows()
        text = "AVAILABLE HW-2411 " + " ".join(
            f"{window.start.isoformat()}/{window.end.isoformat()}"
            for window in windows
        )
        return coordinator.record_availability(
            _message(
                now,
                message_id=f"contract-race-{field}-availability",
                text=text,
            ),
            assignment,
            windows,
            now,
        )

    def change_contract() -> None:
        assert response_validated.wait(timeout=5)
        with original_transaction() as unit:
            assert unit.compare_and_save_assignment(expected, changed) is True
        contract_committed.set()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="contract-response") as executor:
        response_future = executor.submit(respond)
        contract_future = executor.submit(change_contract)
        result = response_future.result(timeout=10)
        contract_future.result(timeout=10)

    assert result == WorkflowResult()
    assert repository.get_assignment(assignment.assignment_id) == changed
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert repository.list_evidence(mandate.mandate_id) == before_evidence
    assert repository.list_events(mandate.mandate_id) == before_events
    assert repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:priya"
    ) is None


def test_authenticated_availability_persists_compatible_windows_and_typed_completion(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.AVAILABILITY)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    windows = _availability_windows()
    text = "AVAILABLE HW-2411 " + " ".join(
        f"{window.start.isoformat()}/{window.end.isoformat()}" for window in windows
    )
    message = _message(now, message_id="engagement-availability", text=text)

    result = coordinator.record_availability(message, assignment, windows, now)

    saved = repository.get_assignment(assignment.assignment_id)
    stored = repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:priya"
    )
    recorded = [
        event
        for event in repository.list_events(mandate.mandate_id)
        if event.event_type == "availability.recorded"
    ]
    assert result.deliveries == []
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    assert stored is not None
    assert stored[0] == "|".join(
        f"{window.start.isoformat()}/{window.end.isoformat()}" for window in windows
    )
    assert len(recorded) == 1
    assert recorded[0].metadata == {"attempt_count": 2}
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert repository.list_evidence(mandate.mandate_id) == []


def test_availability_wrong_route_ack_duplicate_and_conflicting_replay_cannot_mutate_windows(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.AVAILABILITY)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    windows = _availability_windows()
    text = (
        "AVAILABLE HW-2411 "
        f"{windows[0].start.isoformat()}/{windows[0].end.isoformat()}"
    )
    wrong_route = _message(
        now,
        message_id="availability-wrong-route",
        text=text,
        channel=Channel.TELEGRAM,
        sender="priya-telegram",
        conversation="tg-priya",
    )
    before = repository.get_assignment(assignment.assignment_id)

    coordinator.record_availability(wrong_route, assignment, windows[:1], now)
    coordinator.acknowledge(
        _message(now, message_id="availability-ack", text="ACK HW-2411"),
        assignment,
        now,
    )

    assert repository.get_assignment(assignment.assignment_id) == before
    assert repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:priya"
    ) is None

    accepted = _message(
        now,
        message_id="availability-replay",
        text=text,
    )
    coordinator.record_availability(accepted, assignment, windows[:1], now)
    durable = repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:priya"
    )
    events = repository.list_events(mandate.mandate_id)
    conflicting_window = AvailabilityWindow(
        start="2026-08-16T09:00:00-05:00",
        end="2026-08-16T10:00:00-05:00",
    )
    conflicting_text = (
        "AVAILABLE HW-2411 "
        f"{conflicting_window.start.isoformat()}/{conflicting_window.end.isoformat()}"
    )
    conflicting = accepted.model_copy(update={"text": conflicting_text})

    coordinator.record_availability(accepted, assignment, windows[:1], now)
    coordinator.record_availability(
        conflicting,
        assignment,
        (conflicting_window,),
        now,
    )

    assert repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:priya"
    ) == durable
    assert repository.list_events(mandate.mandate_id) == events
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert repository.list_evidence(mandate.mandate_id) == []


@pytest.mark.parametrize(
    ("engagement_type", "response_required"),
    [
        (EngagementType.INFORM, True),
        (EngagementType.ACKNOWLEDGE, False),
    ],
)
def test_prepare_rejects_an_inconsistent_persisted_response_contract(
    coordinator, repository, mandate, now, engagement_type, response_required
) -> None:
    assignment = _assignment(
        mandate,
        engagement_type,
        response_required=response_required,
    )
    repository.add_mandate(mandate)
    repository.add_assignment(assignment)

    with pytest.raises(ValueError, match="response_required"):
        coordinator.prepare_start(
            assignment,
            [],
            mandate.token,
            mandate.objective,
            now,
        )

    assert repository.get_assignment(assignment.assignment_id) == assignment
    assert repository.list_events(mandate.mandate_id) == []


def test_persist_prepared_rolls_back_assignment_interview_and_events_together(
    coordinator, repository, mandate, now
) -> None:
    assignment = _assignment(mandate, EngagementType.QUICK_RESPONSE)
    repository.add_mandate(mandate)
    repository.add_assignment(assignment)
    prepared = coordinator.prepare_start(
        assignment,
        ["Committed date?"],
        mandate.token,
        mandate.objective,
        now,
    )
    unsafe = DomainEvent(
        event_type="outreach.unsafe",
        created_at=now,
        idempotency_key="outreach:unsafe",
        assignment_id=assignment.assignment_id,
        metadata={"recipient": "private@example.test"},
    )
    invalid = replace(prepared, events=(*prepared.events, unsafe))

    with pytest.raises(ValueError, match="unapproved field"):
        coordinator.persist_prepared(invalid)

    assert repository.get_assignment(assignment.assignment_id) == assignment
    assert repository.list_interviews(mandate.mandate_id) == []
    assert repository.list_events(mandate.mandate_id) == []


def test_review_route_delivery_persists_stable_current_attempt_correlation(
    directory, mandate, now, tmp_path
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, EngagementType.QUICK_RESPONSE)
    prepared = _prepare(
        coordinator,
        repository,
        mandate,
        assignment,
        ["Committed date?"],
        now,
    )

    sent = repository.list_events(mandate.mandate_id)[0]
    assert prepared.delivery.message_id is not None
    assert sent.metadata == {
        "attempt": 1,
        "delivery_id": _provider_delivery_id(prepared.delivery),
        "route_fingerprint": sent.metadata["route_fingerprint"],
        "route_index": 0,
    }
    assert len(sent.metadata["route_fingerprint"]) == 32
    assert "email-priya" not in sent.metadata["route_fingerprint"]
    assert "priya@example.test" not in str(sent.metadata)


def test_review_late_question_delivery_failure_after_interviewing_is_inert(
    directory, mandate, now, tmp_path
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, EngagementType.QUICK_RESPONSE)
    prepared = _prepare(
        coordinator,
        repository,
        mandate,
        assignment,
        ["Committed date?"],
        now,
    )
    coordinator.acknowledge(
        _message(now, message_id="review-late-ack", text="ACK HW-2411"),
        assignment,
        now,
    )
    before = repository.get_assignment(assignment.assignment_id)
    event_count = len(repository.list_events(mandate.mandate_id))

    result = coordinator.mark_delivery_failure(
        assignment.assignment_id,
        _provider_delivery_id(prepared.delivery),
        now + timedelta(seconds=1),
    )

    assert result.deliveries == []
    assert repository.get_assignment(assignment.assignment_id) == before
    assert len(repository.list_events(mandate.mandate_id)) == event_count


def test_review_stale_primary_failure_cannot_exhaust_active_alternate(
    directory, mandate, now, tmp_path
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, EngagementType.STRUCTURED_INTERVIEW)
    prepared = _prepare(
        coordinator,
        repository,
        mandate,
        assignment,
        ["Fact?", "Constraint?", "Commitment?"],
        now,
    )
    coordinator.process_due_assignment(assignment, now + timedelta(seconds=60))
    coordinator.process_due_assignment(assignment, now + timedelta(seconds=90))
    before = repository.get_assignment(assignment.assignment_id)
    event_count = len(repository.list_events(mandate.mandate_id))

    result = coordinator.mark_delivery_failure(
        assignment.assignment_id,
        _provider_delivery_id(prepared.delivery),
        now + timedelta(seconds=91),
    )

    assert result.deliveries == []
    assert repository.get_assignment(assignment.assignment_id) == before
    assert len(repository.list_events(mandate.mandate_id)) == event_count


def test_review_unmatched_question_callback_is_inert(
    directory, mandate, now, tmp_path
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, EngagementType.QUICK_RESPONSE)
    _prepare(
        coordinator,
        repository,
        mandate,
        assignment,
        ["Committed date?"],
        now,
    )
    before = repository.get_assignment(assignment.assignment_id)
    event_count = len(repository.list_events(mandate.mandate_id))

    result = coordinator.mark_delivery_failure(
        assignment.assignment_id,
        "0" * 48,
        now,
    )

    assert result.deliveries == []
    assert repository.get_assignment(assignment.assignment_id) == before
    assert len(repository.list_events(mandate.mandate_id)) == event_count


def test_review_threaded_duplicate_same_callback_is_inert(
    directory, mandate, now, tmp_path, monkeypatch
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, EngagementType.ACKNOWLEDGE)
    prepared = _prepare(coordinator, repository, mandate, assignment, [], now)
    delivery_id = _provider_delivery_id(prepared.delivery)
    original_transaction = repository.transaction
    writers_ready = threading.Barrier(2)

    @contextmanager
    def synchronized_transaction():
        writers_ready.wait(timeout=5)
        with original_transaction() as unit:
            yield unit

    monkeypatch.setattr(repository, "transaction", synchronized_transaction)

    def callback() -> None:
        coordinator.mark_delivery_success(assignment.assignment_id, delivery_id, now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: callback(), range(2)))

    events = repository.list_events(mandate.mandate_id)
    assert [event.event_type for event in events].count("outreach.delivery_confirmed") == 1


def test_review_threaded_duplicate_exact_ack_is_inert(
    directory, mandate, now, tmp_path, monkeypatch
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, EngagementType.ACKNOWLEDGE)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    acknowledgement = _message(
        now,
        message_id="review-threaded-same-ack",
        text="ACK HW-2411",
    )
    original_transaction = repository.transaction
    writers_ready = threading.Barrier(2)

    @contextmanager
    def synchronized_transaction():
        writers_ready.wait(timeout=5)
        with original_transaction() as unit:
            yield unit

    monkeypatch.setattr(repository, "transaction", synchronized_transaction)

    def acknowledge():
        return coordinator.acknowledge(acknowledgement, assignment, now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: acknowledge(), range(2)))

    assert sum(len(result.deliveries) for result in outcomes) == 0
    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    events = repository.list_events(mandate.mandate_id)
    assert [event.event_type for event in events].count("stakeholder.acknowledged") == 1


def test_review_due_worker_losing_to_ack_cannot_resurrect_complete_or_send_reminder(
    directory, mandate, now, tmp_path, monkeypatch
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, EngagementType.ACKNOWLEDGE)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    acknowledgement = _message(
        now,
        message_id="review-due-race-ack",
        text="ACK HW-2411",
    )
    original_transaction = repository.transaction
    due_waiting = threading.Event()
    acknowledgement_committed = threading.Event()
    role = threading.local()

    @contextmanager
    def ordered_transaction():
        if getattr(role, "name", None) == "due":
            due_waiting.set()
            assert acknowledgement_committed.wait(timeout=5)
        with original_transaction() as unit:
            yield unit
        if getattr(role, "name", None) == "ack":
            acknowledgement_committed.set()

    monkeypatch.setattr(repository, "transaction", ordered_transaction)

    def run_due():
        role.name = "due"
        return coordinator.process_due_assignment(
            assignment,
            now + timedelta(seconds=60),
        )

    def run_ack():
        role.name = "ack"
        assert due_waiting.wait(timeout=5)
        return coordinator.acknowledge(acknowledgement, assignment, now)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="review") as executor:
        due_future = executor.submit(run_due)
        ack_future = executor.submit(run_ack)
        due_result = due_future.result(timeout=10)
        ack_result = ack_future.result(timeout=10)

    saved = repository.get_assignment(assignment.assignment_id)
    assert ack_result.deliveries == []
    assert due_result.deliveries == []
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    events = repository.list_events(mandate.mandate_id)
    assert [event.event_type for event in events].count("outreach.reminder_sent") == 0


def test_review_authenticated_ack_survives_a_reminder_winning_before_its_cas(
    directory, mandate, now, tmp_path, monkeypatch
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, EngagementType.ACKNOWLEDGE)
    _prepare(coordinator, repository, mandate, assignment, [], now)
    acknowledgement = _message(
        now,
        message_id="review-ack-after-reminder",
        text="ACK HW-2411",
    )
    original_transaction = repository.transaction
    acknowledgement_validated = threading.Event()
    reminder_committed = threading.Event()
    role = threading.local()

    @contextmanager
    def ordered_transaction():
        if getattr(role, "name", None) == "ack":
            attempt = getattr(role, "transaction_attempt", 0) + 1
            role.transaction_attempt = attempt
            if attempt == 1:
                acknowledgement_validated.set()
                assert reminder_committed.wait(timeout=5)
        with original_transaction() as unit:
            yield unit
        if getattr(role, "name", None) == "due":
            reminder_committed.set()

    monkeypatch.setattr(repository, "transaction", ordered_transaction)

    def run_ack():
        role.name = "ack"
        return coordinator.acknowledge(acknowledgement, assignment, now)

    def run_due():
        role.name = "due"
        assert acknowledgement_validated.wait(timeout=5)
        return coordinator.process_due_assignment(
            assignment,
            now + timedelta(seconds=60),
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="review-reverse") as executor:
        ack_future = executor.submit(run_ack)
        due_future = executor.submit(run_due)
        ack_result = ack_future.result(timeout=10)
        due_result = due_future.result(timeout=10)

    saved = repository.get_assignment(assignment.assignment_id)
    events = repository.list_events(mandate.mandate_id)
    assert len(due_result.deliveries) == 1
    assert ack_result.deliveries == []
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    assert [event.event_type for event in events].count("outreach.reminder_sent") == 1
    assert [event.event_type for event in events].count("stakeholder.acknowledged") == 1
    replay = coordinator.acknowledge(acknowledgement, assignment, now)
    assert replay.deliveries == []
    assert repository.list_events(mandate.mandate_id) == events


def test_review_authenticated_quick_ack_survives_reminder_and_sends_question_one_once(
    directory, mandate, now, tmp_path, monkeypatch
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, EngagementType.QUICK_RESPONSE)
    _prepare(
        coordinator,
        repository,
        mandate,
        assignment,
        ["Committed date?"],
        now,
    )
    acknowledgement = _message(
        now,
        message_id="review-quick-ack-after-reminder",
        text="ACK HW-2411",
    )
    original_transaction = repository.transaction
    acknowledgement_validated = threading.Event()
    reminder_committed = threading.Event()
    role = threading.local()

    @contextmanager
    def ordered_transaction():
        if getattr(role, "name", None) == "ack":
            attempt = getattr(role, "transaction_attempt", 0) + 1
            role.transaction_attempt = attempt
            if attempt == 1:
                acknowledgement_validated.set()
                assert reminder_committed.wait(timeout=5)
        with original_transaction() as unit:
            yield unit
        if getattr(role, "name", None) == "due":
            reminder_committed.set()

    monkeypatch.setattr(repository, "transaction", ordered_transaction)

    def run_ack():
        role.name = "ack"
        return coordinator.acknowledge(acknowledgement, assignment, now)

    def run_due():
        role.name = "due"
        assert acknowledgement_validated.wait(timeout=5)
        return coordinator.process_due_assignment(
            assignment,
            now + timedelta(seconds=60),
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="review-reverse") as executor:
        ack_future = executor.submit(run_ack)
        due_future = executor.submit(run_due)
        ack_result = ack_future.result(timeout=10)
        due_result = due_future.result(timeout=10)

    saved = repository.get_assignment(assignment.assignment_id)
    events = repository.list_events(mandate.mandate_id)
    assert len(due_result.deliveries) == 1
    assert [delivery.text for delivery in ack_result.deliveries] == [
        "Question 1 of 1:\nCommitted date?"
    ]
    assert saved is not None
    assert saved.state is StakeholderState.INTERVIEWING
    assert [event.event_type for event in events].count("outreach.reminder_sent") == 1
    assert [event.event_type for event in events].count("stakeholder.acknowledged") == 1
    replay = coordinator.acknowledge(acknowledgement, assignment, now)
    assert replay.deliveries == []
    assert repository.list_events(mandate.mandate_id) == events


@pytest.mark.parametrize(
    ("engagement_type", "questions", "expected_question"),
    [
        pytest.param(
            EngagementType.QUICK_RESPONSE,
            ["Committed date?"],
            "Question 1 of 1:\nCommitted date?",
            id="quick",
        ),
        pytest.param(
            EngagementType.STRUCTURED_INTERVIEW,
            ["Fact?", "Constraint?", "Commitment?"],
            "Question 1 of 3:\nFact?",
            id="structured",
        ),
    ],
)
def test_review_explicit_ack_retry_rejects_a_route_that_became_stale(
    directory,
    mandate,
    now,
    tmp_path,
    monkeypatch,
    engagement_type,
    questions,
    expected_question,
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, engagement_type)
    _prepare(coordinator, repository, mandate, assignment, questions, now)
    coordinator.process_due_assignment(assignment, now + timedelta(seconds=60))
    stale_primary_ack = _message(
        now,
        message_id=f"review-stale-route-{engagement_type.value}",
        text="ACK HW-2411",
    )
    original_transaction = repository.transaction
    acknowledgement_validated = threading.Event()
    alternate_committed = threading.Event()
    role = threading.local()

    @contextmanager
    def ordered_transaction():
        if getattr(role, "name", None) == "ack":
            attempt = getattr(role, "transaction_attempt", 0) + 1
            role.transaction_attempt = attempt
            if attempt == 1:
                acknowledgement_validated.set()
                assert alternate_committed.wait(timeout=5)
        with original_transaction() as unit:
            yield unit

    monkeypatch.setattr(repository, "transaction", ordered_transaction)

    def run_stale_ack():
        role.name = "ack"
        return coordinator.acknowledge(stale_primary_ack, assignment, now)

    def switch_to_alternate():
        role.name = "due"
        assert acknowledgement_validated.wait(timeout=5)
        result = coordinator.process_due_assignment(
            assignment,
            now + timedelta(seconds=90),
        )
        alternate_committed.set()
        return result

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="review-stale-route") as executor:
        ack_future = executor.submit(run_stale_ack)
        due_future = executor.submit(switch_to_alternate)
        stale_result = ack_future.result(timeout=10)
        due_result = due_future.result(timeout=10)

    after_stale = repository.get_assignment(assignment.assignment_id)
    assert after_stale is not None
    session = repository.get_interview(after_stale.interview_id)
    events = repository.list_events(mandate.mandate_id)
    assert len(due_result.deliveries) == 1
    assert stale_result.deliveries == []
    assert after_stale.state is StakeholderState.ALTERNATE_CHANNEL
    assert after_stale.active_route_index == 1
    assert session is not None
    assert session.current_route_id == "telegram-priya"
    assert session.current_conversation_id == "tg-priya"
    assert [event.event_type for event in events].count("stakeholder.acknowledged") == 0

    valid_alternate_ack = _message(
        now,
        message_id=f"review-active-route-{engagement_type.value}",
        text="ACK HW-2411",
        channel=Channel.TELEGRAM,
        sender="priya-telegram",
        conversation="tg-priya",
    )
    active_result = coordinator.acknowledge(valid_alternate_ack, after_stale, now)
    completed = repository.get_assignment(assignment.assignment_id)
    completed_session = repository.get_interview(after_stale.interview_id)
    completed_events = repository.list_events(mandate.mandate_id)
    assert [delivery.text for delivery in active_result.deliveries] == [expected_question]
    assert completed is not None
    assert completed.state is StakeholderState.INTERVIEWING
    assert completed_session is not None
    assert completed_session.current_route_id == "telegram-priya"
    assert completed_session.current_conversation_id == "tg-priya"
    assert (
        [event.event_type for event in completed_events].count("stakeholder.acknowledged")
        == 1
    )


@pytest.mark.parametrize(
    ("engagement_type", "questions"),
    [
        pytest.param(
            EngagementType.QUICK_RESPONSE,
            ["Committed date?"],
            id="quick",
        ),
        pytest.param(
            EngagementType.STRUCTURED_INTERVIEW,
            ["Fact?", "Constraint?", "Commitment?"],
            id="structured",
        ),
    ],
)
def test_review_explicit_ack_retry_rejects_assignment_changed_to_non_question_type(
    directory,
    mandate,
    now,
    tmp_path,
    monkeypatch,
    engagement_type,
    questions,
) -> None:
    repository = _file_repository(tmp_path)
    coordinator = _file_coordinator(directory, repository)
    assignment = _assignment(mandate, engagement_type)
    _prepare(coordinator, repository, mandate, assignment, questions, now)
    pending = repository.get_assignment(assignment.assignment_id)
    assert pending is not None
    changed_type = pending.model_copy(
        update={
            "engagement_type": EngagementType.ACKNOWLEDGE,
            "state": StakeholderState.FOLLOW_UP_DUE,
            "attempt_count": 2,
            "next_action_at": now + timedelta(seconds=90),
        }
    )
    acknowledgement = _message(
        now,
        message_id=f"review-type-change-{engagement_type.value}",
        text="ACK HW-2411",
    )
    original_transaction = repository.transaction
    acknowledgement_validated = threading.Event()
    type_change_committed = threading.Event()
    role = threading.local()

    @contextmanager
    def ordered_transaction():
        if getattr(role, "name", None) == "ack":
            attempt = getattr(role, "transaction_attempt", 0) + 1
            role.transaction_attempt = attempt
            if attempt == 1:
                acknowledgement_validated.set()
                assert type_change_committed.wait(timeout=5)
        with original_transaction() as unit:
            yield unit

    monkeypatch.setattr(repository, "transaction", ordered_transaction)

    def run_ack():
        role.name = "ack"
        return coordinator.acknowledge(acknowledgement, assignment, now)

    def change_assignment_type():
        assert acknowledgement_validated.wait(timeout=5)
        with original_transaction() as unit:
            changed = unit.compare_and_save_assignment(pending, changed_type)
        type_change_committed.set()
        return changed

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="review-type-change") as executor:
        ack_future = executor.submit(run_ack)
        type_future = executor.submit(change_assignment_type)
        ack_result = ack_future.result(timeout=10)
        type_changed = type_future.result(timeout=10)

    saved = repository.get_assignment(assignment.assignment_id)
    assert saved is not None
    session = repository.get_interview(saved.interview_id)
    events = repository.list_events(mandate.mandate_id)
    assert type_changed is True
    assert ack_result.deliveries == []
    assert saved.engagement_type is EngagementType.ACKNOWLEDGE
    assert saved.state is StakeholderState.FOLLOW_UP_DUE
    assert session is not None
    assert session.current_route_id == "email-priya"
    assert session.current_conversation_id == "mail-priya"
    assert [event.event_type for event in events].count("stakeholder.acknowledged") == 0


@pytest.mark.parametrize(
    ("engagement_type", "questions", "expects_interview"),
    [
        (EngagementType.INFORM, [], False),
        (EngagementType.ACKNOWLEDGE, [], False),
        (EngagementType.QUICK_RESPONSE, ["Fact?"], True),
        (
            EngagementType.STRUCTURED_INTERVIEW,
            ["Fact?", "Constraint?", "Commitment?"],
            True,
        ),
        (EngagementType.REVIEW_APPROVAL, [], False),
        (EngagementType.AVAILABILITY, [], False),
    ],
)
def test_prepare_start_releases_an_already_queued_preview_assignment(
    coordinator,
    mandate,
    now,
    engagement_type,
    questions,
    expects_interview,
) -> None:
    assignment = _assignment(
        mandate,
        engagement_type,
        state=StakeholderState.CONTACT_QUEUED,
    )

    prepared = coordinator.prepare_start(
        assignment,
        questions,
        mandate.token,
        mandate.objective,
        now,
    )

    assert prepared.assignment.state in {
        StakeholderState.DELIVERED,
        StakeholderState.AWAITING_ACKNOWLEDGEMENT,
    }
    assert (prepared.interview is not None) is expects_interview
    assert prepared.assignment.attempt_count == 1
    assert prepared.assignment.first_contact_at == now
