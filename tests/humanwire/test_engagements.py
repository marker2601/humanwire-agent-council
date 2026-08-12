import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
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
    DomainEvent,
    EngagementType,
    IncomingMessage,
    Mandate,
    MandateState,
    Person,
    StakeholderAssignment,
    StakeholderState,
)
from humanwire.engagements import EngagementCoordinator
from humanwire.evidence import RuleBasedEvidenceExtractor
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
    "engagement_type",
    [EngagementType.REVIEW_APPROVAL, EngagementType.AVAILABILITY],
)
def test_unsupported_approval_and_availability_fail_closed_without_mutation(
    coordinator, repository, mandate, now, engagement_type
) -> None:
    assignment = _assignment(mandate, engagement_type)
    repository.add_mandate(mandate)
    repository.add_assignment(assignment)

    with pytest.raises(ValueError, match="Task 5"):
        coordinator.prepare_start(
            assignment,
            [],
            mandate.token,
            mandate.objective,
            now,
        )

    before = repository.get_assignment(assignment.assignment_id)
    ack = coordinator.acknowledge(
        _message(now, message_id="unsupported-ack", text="ACK HW-2411"), assignment, now
    )
    answer = coordinator.record_answer(
        _message(now, message_id="unsupported-answer", text="Approved"), assignment, now
    )
    assert ack.deliveries == []
    assert answer.deliveries == []
    assert repository.get_assignment(assignment.assignment_id) == before
    assert repository.list_events(mandate.mandate_id) == []


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
