from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from humanwire.config import Settings
from humanwire.database import (
    DomainEventRecord,
    InterviewSessionRecord,
    MandateRecord,
    StakeholderAssignmentRecord,
    create_session_factory,
)
from humanwire.directory import InitiatorPolicy, OrganizationDirectory, OrganizationDocument
from humanwire.domain import (
    AlignmentIssue,
    AlignmentIssueType,
    Channel,
    ContactRoute,
    DeliveryKind,
    Direction,
    EngagementDecisionKind,
    EngagementType,
    EvidenceItem,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    IncomingMessage,
    MandatePlan,
    MandateState,
    Person,
    PlannedStakeholder,
    StakeholderAssignment,
    StakeholderState,
    WorkflowResult,
)
from humanwire.evidence import RuleBasedEvidenceExtractor
from humanwire.planning import ResolvedPlan
from humanwire.repository import RepositoryUnitOfWork, SqlAlchemyHumanWireRepository
from humanwire.workflow import HumanWireWorkflow


class DeterministicPlanner:
    def __init__(
        self,
        people: list[Person],
        *,
        question_count: int = 1,
        optional_people: set[str] | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        self.people = people
        self.question_count = question_count
        self.optional_people = optional_people or set()
        self.fallback_reason = fallback_reason

    def plan(self, text: str, initiator: Person) -> ResolvedPlan:
        del text, initiator
        return ResolvedPlan(
            plan=MandatePlan(
                objective="Coordinate launch coverage",
                required_decisions=["Approve coverage"],
                stakeholders=[
                    PlannedStakeholder(
                        person_ref=person.person_id,
                        reason=f"{person.display_name} input",
                        direction={
                            "team-lead": Direction.DOWNWARD,
                            "coo": Direction.UPWARD,
                        }.get(person.person_id, Direction.LATERAL),
                        required=person.person_id not in self.optional_people,
                        questions=[f"Question {index + 1}?" for index in range(self.question_count)],
                    )
                    for person in self.people
                ],
                completion_conditions=["All required interviews complete"],
            ),
            people=self.people,
            planner="rules" if self.fallback_reason else "deterministic",
            fallback_reason=self.fallback_reason,
        )


def _people(
    *,
    lead_email_thread: str | None = None,
    lead_secondary_email_thread: str | None = None,
) -> tuple[Person, list[Person]]:
    manager = Person(
        person_id="manager",
        display_name="Morgan Lee",
        role="Manager",
        department="Operations",
        timezone="UTC",
        routes=[
            ContactRoute(
                route_id="manager-tg",
                channel=Channel.TELEGRAM,
                sender_address="manager-chat",
                conversation_id="manager-conversation",
                preferred=True,
            )
        ],
    )
    people = [
        Person(
            person_id="team-lead",
            display_name="Riley Chen",
            role="Lead",
            department="Operations",
            timezone="UTC",
            manager_id="manager",
            routes=[
                ContactRoute(
                    route_id="lead-email",
                    channel=Channel.EMAIL,
                    sender_address="lead@example.test",
                    recipient="lead@example.test",
                    conversation_id=lead_email_thread,
                    preferred=True,
                ),
                ContactRoute(
                    route_id="lead-telegram",
                    channel=Channel.TELEGRAM,
                    sender_address="lead-chat",
                    conversation_id="lead-conversation",
                ),
            ],
        ),
        Person(
            person_id="vp-people",
            display_name="Avery Patel",
            role="VP",
            department="People",
            timezone="UTC",
            routes=[
                ContactRoute(
                    route_id="people-email",
                    channel=Channel.EMAIL,
                    sender_address="people@example.test",
                    recipient="people@example.test",
                    preferred=True,
                )
            ],
        ),
        Person(
            person_id="coo",
            display_name="Casey Nguyen",
            role="COO",
            department="Executive",
            timezone="UTC",
            routes=[
                ContactRoute(
                    route_id="coo-email",
                    channel=Channel.EMAIL,
                    sender_address="coo@example.test",
                    recipient="coo@example.test",
                    preferred=True,
                )
            ],
        ),
        Person(
            person_id="vp-support",
            display_name="Jordan Brooks",
            role="VP",
            department="Support",
            timezone="UTC",
            routes=[
                ContactRoute(
                    route_id="support-email",
                    channel=Channel.EMAIL,
                    sender_address="support@example.test",
                    recipient="support@example.test",
                    preferred=True,
                )
            ],
        ),
    ]
    if lead_secondary_email_thread is not None:
        lead = people[0]
        people[0] = lead.model_copy(
            update={
                "routes": [
                    lead.routes[0],
                    ContactRoute(
                        route_id="lead-email-secondary",
                        channel=Channel.EMAIL,
                        sender_address="lead-secondary@example.test",
                        recipient="lead-secondary@example.test",
                        conversation_id=lead_secondary_email_thread,
                    ),
                    *lead.routes[1:],
                ]
            }
        )
    return manager, people


def _directory(
    *,
    lead_email_thread: str | None = None,
    lead_secondary_email_thread: str | None = None,
) -> tuple[OrganizationDirectory, list[Person]]:
    manager, people = _people(
        lead_email_thread=lead_email_thread,
        lead_secondary_email_thread=lead_secondary_email_thread,
    )
    directory = OrganizationDirectory(
        OrganizationDocument(
            people=[manager, *people],
            initiator_policies=[
                InitiatorPolicy(
                    person_id="manager",
                    allowed_directions={Direction.DOWNWARD, Direction.LATERAL, Direction.UPWARD},
                    allowed_departments={"Operations", "People", "Executive", "Support"},
                )
            ],
        )
    )
    return directory, people


def _build_workflow(
    repository: SqlAlchemyHumanWireRepository,
    *,
    person_ids: tuple[str, ...] = ("team-lead",),
    question_count: int = 1,
    optional_people: set[str] | None = None,
    fallback_reason: str | None = None,
    lead_email_thread: str | None = None,
    lead_secondary_email_thread: str | None = None,
) -> HumanWireWorkflow:
    directory, all_people = _directory(
        lead_email_thread=lead_email_thread,
        lead_secondary_email_thread=lead_secondary_email_thread,
    )
    selected = [person for person in all_people if person.person_id in person_ids]
    return HumanWireWorkflow(
        directory,
        repository,
        DeterministicPlanner(
            selected,
            question_count=question_count,
            optional_people=optional_people,
            fallback_reason=fallback_reason,
        ),
        RuleBasedEvidenceExtractor(),
        Settings(),
    )


def _message_for(
    incoming_message_factory,
    person_id: str,
    text: str,
    *,
    message_id: str,
    conversation_id: str | None = None,
) -> IncomingMessage:
    routes = {
        "manager": (Channel.TELEGRAM, "manager-chat", "manager-conversation"),
        "team-lead": (Channel.EMAIL, "lead@example.test", "lead-thread"),
        "vp-people": (Channel.EMAIL, "people@example.test", "people-thread"),
    }
    channel, sender, default_conversation = routes[person_id]
    return incoming_message_factory(
        text=text,
        channel=channel,
        sender_address=sender,
        conversation_id=default_conversation if conversation_id is None else conversation_id,
        message_id=message_id,
    )


def _create_mandate(workflow, incoming_message_factory, *, message_id: str = "create-1"):
    result = workflow.handle(
        incoming_message_factory(
            text="/mandate\nCoordinate launch coverage",
            message_id=message_id,
        )
    )
    return workflow.repository.list_recent_mandates(1)[0], result


def _convert_assignment_to_engagement(
    repository: SqlAlchemyHumanWireRepository,
    mandate,
    engagement_type: EngagementType,
) -> StakeholderAssignment:
    assignment = repository.list_assignments(mandate.mandate_id)[0]
    converted = assignment.model_copy(
        update={
            "engagement_type": engagement_type,
            "response_required": True,
            "state": StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            "interview_id": None,
            "attempt_count": 1,
            "active_route_index": 0,
            "next_action_at": mandate.created_at + timedelta(minutes=1),
            "acknowledged_at": None,
            "completed_at": None,
            "failure_reason": None,
        }
    )
    repository.save_assignment(converted)
    return converted


def _move_to_scheduling(repository, mandate) -> None:
    with repository.transaction() as unit:
        unit.save_mandate(
            mandate.model_copy(update={"state": MandateState.SCHEDULING})
        )
        unit.add_issue(
            AlignmentIssue(
                issue_id=uuid4(),
                mandate_id=mandate.mandate_id,
                issue_type=AlignmentIssueType.HARD_CONSTRAINT,
                stakeholder_ids=["team-lead"],
                summary="A required stakeholder must join the meeting.",
                blocking=True,
            )
        )


def _complete_interview(
    workflow,
    incoming_message_factory,
    mandate,
    person_id: str,
    *,
    prefix: str,
    answer: str = "We cannot approve this yet.",
):
    workflow.handle(
        _message_for(
            incoming_message_factory,
            person_id,
            f"ACK {mandate.token}",
            message_id=f"{prefix}-ack",
        )
    )
    return workflow.handle(
        _message_for(
            incoming_message_factory,
            person_id,
            answer,
            message_id=f"{prefix}-answer",
        )
    )


def _event_types(repository, mandate_id) -> set[str]:
    return {event.event_type for event in repository.list_events(mandate_id)}


def _terminal_snapshot(repository, mandate):
    proposal = repository.get_active_proposal(mandate.mandate_id)
    return {
        "mandate": repository.get_mandate_by_token(mandate.token),
        "assignments": repository.list_assignments(mandate.mandate_id),
        "interviews": repository.list_interviews(mandate.mandate_id),
        "evidence": repository.list_evidence(mandate.mandate_id),
        "proposal": proposal,
        "proposal_responses": (
            repository.list_proposal_responses(proposal.proposal_id) if proposal else []
        ),
        "manager_availability": repository.get_runtime_status(
            f"availability:{mandate.mandate_id}:manager"
        ),
        "lead_availability": repository.get_runtime_status(
            f"availability:{mandate.mandate_id}:team-lead"
        ),
        "events": repository.list_events(mandate.mandate_id),
    }


def _correlation_snapshot(repository, mandates):
    mandate_ids = [mandate.mandate_id for mandate in mandates]
    proposals = {
        mandate_id: repository.get_active_proposal(mandate_id)
        for mandate_id in mandate_ids
    }
    return {
        "mandates": repository.list_recent_mandates(1000),
        "assignments": {
            mandate_id: repository.list_assignments(mandate_id)
            for mandate_id in mandate_ids
        },
        "interviews": {
            mandate_id: repository.list_interviews(mandate_id)
            for mandate_id in mandate_ids
        },
        "evidence": {
            mandate_id: repository.list_evidence(mandate_id)
            for mandate_id in mandate_ids
        },
        "proposals": proposals,
        "proposal_responses": {
            mandate_id: (
                repository.list_proposal_responses(proposal.proposal_id)
                if proposal is not None
                else []
            )
            for mandate_id, proposal in proposals.items()
        },
        "availability": {
            (mandate_id, person_id): repository.get_runtime_status(
                f"availability:{mandate_id}:{person_id}"
            )
            for mandate_id in mandate_ids
            for person_id in ("manager", "team-lead")
        },
        "events": {
            mandate_id: repository.list_events(mandate_id)
            for mandate_id in mandate_ids
        },
    }


def _late_terminal_message(workflow, incoming_message_factory, mandate, input_kind: str):
    if input_kind == "ack":
        return _message_for(
            incoming_message_factory,
            "team-lead",
            f"ACK {mandate.token}",
            message_id="late-terminal-ack",
        )
    if input_kind == "free_text":
        return _message_for(
            incoming_message_factory,
            "team-lead",
            "PRIVATE-LATE-SENTINEL must never become evidence.",
            message_id="late-terminal-answer",
        )

    _complete_interview(
        workflow,
        incoming_message_factory,
        mandate,
        "team-lead",
        prefix=f"late-{input_kind}",
    )
    if input_kind == "proposal":
        return _message_for(
            incoming_message_factory,
            "team-lead",
            f"ACCEPT {mandate.token}",
            message_id="late-terminal-proposal",
        )

    workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"REJECT {mandate.token}",
            message_id="late-availability-round-one",
        )
    )
    workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"REJECT {mandate.token}",
            message_id="late-availability-round-two",
        )
    )
    window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"
    return _message_for(
        incoming_message_factory,
        "manager",
        f"AVAILABLE {mandate.token} {window}",
        message_id="late-terminal-availability",
    )


@pytest.fixture
def repository() -> SqlAlchemyHumanWireRepository:
    return SqlAlchemyHumanWireRepository(create_session_factory("sqlite://"))


@pytest.fixture
def workflow(repository: SqlAlchemyHumanWireRepository) -> HumanWireWorkflow:
    return _build_workflow(
        repository,
        person_ids=("team-lead", "vp-people", "coo", "vp-support"),
    )


@pytest.fixture
def telegram_mandate(incoming_message_factory):
    return incoming_message_factory(text="/mandate\nCoordinate launch coverage with Riley, Avery, Casey, and Jordan")


def test_manager_mandate_creates_three_routes_and_real_deliveries(
    workflow, telegram_mandate, repository
) -> None:
    """Break caught: creation skips a direction, atomic state transition, or outreach."""
    result = workflow.handle(telegram_mandate)
    mandate = repository.list_recent_mandates(1)[0]
    assignments = repository.list_assignments(mandate.mandate_id)

    assert mandate.state is MandateState.INTERVIEWING
    assert {item.direction for item in assignments} == {Direction.DOWNWARD, Direction.LATERAL, Direction.UPWARD}
    assert len(result.deliveries) == 5
    assert {delivery.kind for delivery in result.deliveries[1:]} == {DeliveryKind.INITIATE_EMAIL}
    assert {event.new_state for event in repository.list_events(mandate.mandate_id)} >= {"received", "planned", "interviewing"}


def test_duplicate_incoming_mandate_returns_existing_state_without_second_outreach(
    workflow, telegram_mandate, repository
) -> None:
    """Break caught: a retry creates another mandate or repeats side effects."""
    first = workflow.handle(telegram_mandate)
    second = workflow.handle(telegram_mandate)

    assert len(repository.list_recent_mandates()) == 1
    assert len(first.deliveries) == 5
    assert second.deliveries == []


def test_required_stakeholder_without_route_is_atomically_partial_and_explained_to_initiator(
    repository, incoming_message_factory
) -> None:
    """Break caught: route exhaustion is hidden behind a generic creation acknowledgement."""
    manager, people = _people()
    missing = people[0].model_copy(update={"routes": [], "role": "Launch Lead"})
    directory = OrganizationDirectory(
        OrganizationDocument(
            people=[manager, missing],
            initiator_policies=[
                InitiatorPolicy(
                    person_id="manager",
                    allowed_directions={Direction.DOWNWARD},
                    allowed_departments={"Operations"},
                )
            ],
        )
    )
    workflow = HumanWireWorkflow(
        directory,
        repository,
        DeterministicPlanner([missing]),
        RuleBasedEvidenceExtractor(),
        Settings(),
    )
    message = incoming_message_factory(
        text="/mandate\nCoordinate launch coverage. PRIVATE-CREATION-SENTINEL",
        message_id="required-no-route",
    )

    first = workflow.handle(message)
    mandate = repository.list_recent_mandates(1)[0]
    assignment = repository.list_assignments(mandate.mandate_id)[0]
    events = repository.list_events(mandate.mandate_id)
    duplicate = workflow.handle(message)

    assert mandate.state is MandateState.PARTIAL
    assert assignment.state is StakeholderState.DELIVERY_FAILED
    assert assignment.failure_reason == "no_registered_route"
    assert {"stakeholder.delivery_failed", "mandate.partial"} <= {
        event.event_type for event in events
    }
    assert any(event.new_state == StakeholderState.DELIVERY_FAILED.value for event in events)
    assert len(first.deliveries) == 1
    assert first.deliveries[0].kind is DeliveryKind.SEND_TO_CONVERSATION
    assert first.deliveries[0].conversation_id == "manager-conversation"
    assert "Riley Chen" in first.deliveries[0].text
    assert "Launch Lead" in first.deliveries[0].text
    assert "missing" in first.deliveries[0].text.casefold()
    public_text = first.deliveries[0].text
    event_text = "\n".join(event.model_dump_json() for event in events)
    for private_value in (
        "PRIVATE-CREATION-SENTINEL",
        "manager-chat",
        "lead@example.test",
    ):
        assert private_value not in public_text
        assert private_value not in event_text
    assert duplicate.deliveries == []
    assert repository.list_events(mandate.mandate_id) == events


def test_model_planning_failure_uses_persisted_fallback_and_duplicate_is_inert(
    repository, incoming_message_factory
) -> None:
    """Break caught: planner fallback is silent or duplicate creation repeats outreach."""
    workflow = _build_workflow(repository, fallback_reason="provider_timeout")
    message = incoming_message_factory(
        text="/mandate\nCoordinate launch coverage",
        message_id="fallback-create",
    )

    first = workflow.handle(message)
    mandate = repository.list_recent_mandates(1)[0]
    events_before = repository.list_events(mandate.mandate_id)
    duplicate = workflow.handle(message)

    fallback = next(event for event in events_before if event.event_type == "model.fallback")
    assert mandate.objective == "Coordinate launch coverage"
    assert fallback.metadata == {"reason_code": "provider_timeout"}
    assert len(first.deliveries) == 2
    assert duplicate.deliveries == []
    assert len(repository.list_recent_mandates()) == 1
    assert repository.list_events(mandate.mandate_id) == events_before


def test_unknown_sender_cannot_create_mandate(workflow, incoming_message_factory, repository) -> None:
    """Break caught: unauthenticated senders gain mandate authority."""
    result = workflow.handle(incoming_message_factory(text="/mandate\nCoordinate Riley", sender_address="unknown"))

    assert result.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert repository.list_recent_mandates() == []


def test_late_reply_and_unknown_sender_fail_closed_without_mutation(
    repository, incoming_message_factory
) -> None:
    """Break caught: stale or unauthenticated free text becomes interview evidence."""
    workflow = _build_workflow(repository)
    mandate, _ = _create_mandate(workflow, incoming_message_factory)
    _complete_interview(
        workflow,
        incoming_message_factory,
        mandate,
        "team-lead",
        prefix="complete",
    )
    newer, _ = _create_mandate(workflow, incoming_message_factory, message_id="newer-create")
    events_before = repository.list_events(newer.mandate_id)
    evidence_before = repository.list_evidence(newer.mandate_id)
    assignment_before = repository.list_assignments(newer.mandate_id)[0]

    late = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            "A late answer must not count.",
            message_id="late-answer",
        )
    )
    unknown = workflow.handle(
        incoming_message_factory(
            text="An unknown answer must not count.",
            channel=Channel.EMAIL,
            sender_address="unknown@example.test",
            conversation_id="lead-thread",
            message_id="unknown-answer",
        )
    )

    assert late.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert unknown.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert repository.list_events(newer.mandate_id) == events_before
    assert repository.list_evidence(newer.mandate_id) == evidence_before
    assert repository.list_assignments(newer.mandate_id)[0] == assignment_before

    workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"ACK {newer.token}",
            message_id="newer-ack",
        )
    )
    legitimate = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            "The explicitly selected interview may continue.",
            message_id="newer-answer",
        )
    )

    assert repository.list_assignments(newer.mandate_id)[0].state is StakeholderState.COMPLETE
    assert len(repository.list_evidence(newer.mandate_id)) == 1
    assert legitimate.deliveries


def test_terminal_thread_requires_newer_interview_ack_before_tokenless_answer(
    repository, incoming_message_factory
) -> None:
    """Break caught: terminal thread history implicitly selects one newer unacknowledged interview."""
    workflow = _build_workflow(
        repository,
        lead_email_thread="lead-thread",
        lead_secondary_email_thread="lead-thread",
    )
    terminal, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="terminal-thread-create"
    )
    workflow.handle(
        incoming_message_factory(
            text=f"/cancel {terminal.token}",
            message_id="terminal-thread-cancel",
        )
    )
    newer, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="newer-thread-create"
    )
    newer_assignment = repository.list_assignments(newer.mandate_id)[0]
    newer_interview = repository.list_interviews(newer.mandate_id)[0]
    assert repository.get_mandate_by_token(terminal.token).state is MandateState.CANCELLED
    assert newer_assignment.state is StakeholderState.AWAITING_ACKNOWLEDGEMENT
    assert newer_interview.current_conversation_id == "lead-thread"
    assert newer_interview.acknowledged_at is None
    before = _correlation_snapshot(repository, (terminal, newer))

    guarded = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            "Tokenless text must not select the newer interview.",
            message_id="newer-thread-tokenless-before-ack",
        )
    )

    assert len(guarded.deliveries) == 1
    assert guarded.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert "ACK <token>" in guarded.deliveries[0].text
    assert _correlation_snapshot(repository, (terminal, newer)) == before

    selected = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"ACK {newer.token}",
            message_id="newer-thread-explicit-ack",
        )
    )
    continued = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            "The acknowledged exact-thread interview may continue.",
            message_id="newer-thread-tokenless-after-ack",
        )
    )

    assert selected.deliveries[0].text.startswith("Question 1 of 1")
    assert repository.list_assignments(newer.mandate_id)[0].state is StakeholderState.COMPLETE
    assert len(repository.list_evidence(newer.mandate_id)) == 1
    assert continued.deliveries


def test_explicit_ack_selects_newer_interview_across_registered_channels(
    repository, incoming_message_factory
) -> None:
    """Break caught: exact-thread history prevents a valid token from switching registered routes."""
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    terminal, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="cross-channel-terminal-create"
    )
    workflow.handle(
        incoming_message_factory(
            text=f"/cancel {terminal.token}",
            message_id="cross-channel-terminal-cancel",
        )
    )
    newer, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="cross-channel-newer-create"
    )

    selected = workflow.handle(
        incoming_message_factory(
            text=f"ACK {newer.token}",
            channel=Channel.TELEGRAM,
            sender_address="lead-chat",
            conversation_id="lead-conversation",
            message_id="cross-channel-newer-ack",
        )
    )
    continued = workflow.handle(
        incoming_message_factory(
            text="The explicitly selected Telegram interview may continue.",
            channel=Channel.TELEGRAM,
            sender_address="lead-chat",
            conversation_id="lead-conversation",
            message_id="cross-channel-newer-answer",
        )
    )

    session = repository.list_interviews(newer.mandate_id)[0]
    assert selected.deliveries[0].text.startswith("Question 1 of 1")
    assert session.current_route_id == "lead-telegram"
    assert session.current_conversation_id == "lead-conversation"
    assert session.acknowledged_at is not None
    assert repository.list_assignments(newer.mandate_id)[0].state is StakeholderState.COMPLETE
    assert len(repository.list_evidence(newer.mandate_id)) == 1
    assert continued.deliveries


@pytest.mark.parametrize(
    ("channel", "sender_address", "conversation_id"),
    [
        (Channel.EMAIL, "lead@example.test", "wrong-email-thread"),
        (Channel.EMAIL, "lead@example.test", ""),
        (Channel.TELEGRAM, "lead-chat", "wrong-telegram-group"),
        (Channel.TELEGRAM, "lead-chat", ""),
    ],
    ids=[
        "wrong-configured-email-thread",
        "missing-configured-email-thread",
        "wrong-configured-telegram-group",
        "missing-configured-telegram-group",
    ],
)
def test_configured_route_ack_requires_exact_conversation_without_mutation(
    repository,
    incoming_message_factory,
    channel: Channel,
    sender_address: str,
    conversation_id: str,
) -> None:
    """Break caught: valid sender/token binds and discloses into an unregistered conversation."""
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow, incoming_message_factory, message_id=f"configured-route-{channel.value}-create"
    )
    before = _correlation_snapshot(repository, (mandate,))

    result = workflow.handle(
        incoming_message_factory(
            text=f"ACK {mandate.token}",
            channel=channel,
            sender_address=sender_address,
            conversation_id=conversation_id,
            message_id=f"configured-route-{channel.value}-{conversation_id or 'missing'}-ack",
        )
    )

    assert result.deliveries == []
    assert _correlation_snapshot(repository, (mandate,)) == before


def test_terminal_history_on_another_registered_route_does_not_block_first_reply(
    repository, incoming_message_factory
) -> None:
    """Break caught: another registered address sharing a conversation overblocks correlation."""
    workflow = _build_workflow(
        repository,
        lead_email_thread="shared-thread",
        lead_secondary_email_thread="shared-thread",
    )
    terminal, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="other-route-terminal-create"
    )
    workflow.handle(
        incoming_message_factory(
            text=f"/cancel {terminal.token}",
            message_id="other-route-terminal-cancel",
        )
    )
    newer, created = _create_mandate(
        workflow, incoming_message_factory, message_id="other-route-newer-create"
    )
    primary = next(delivery for delivery in created.deliveries if delivery.assignment_id)
    alternate = workflow.mark_delivery_result(primary, False, newer.created_at)
    assert alternate.deliveries[0].recipient == "lead-secondary@example.test"
    assert repository.list_interviews(newer.mandate_id)[0].current_route_id == (
        "lead-email-secondary"
    )

    result = workflow.handle(
        incoming_message_factory(
            text="The secondary registered route may bind its own first reply.",
            channel=Channel.EMAIL,
            sender_address="lead-secondary@example.test",
            conversation_id="shared-thread",
            message_id="other-route-newer-answer",
        )
    )

    assert "ACK <token>" not in [delivery.text for delivery in result.deliveries]
    assert repository.list_assignments(newer.mandate_id)[0].state is StakeholderState.COMPLETE
    assert len(repository.list_evidence(newer.mandate_id)) == 1


def test_multiple_active_interviews_require_token_and_correct_ack_disambiguates(
    repository, incoming_message_factory
) -> None:
    """Break caught: ambiguous free text or a wrong ACK token mutates one active interview."""
    workflow = _build_workflow(repository)
    first, _ = _create_mandate(workflow, incoming_message_factory, message_id="first-create")
    second, _ = _create_mandate(workflow, incoming_message_factory, message_id="second-create")
    before = {
        assignment.mandate_id: assignment
        for mandate in (first, second)
        for assignment in repository.list_assignments(mandate.mandate_id)
    }
    events_before = {
        mandate.mandate_id: repository.list_events(mandate.mandate_id)
        for mandate in (first, second)
    }

    ambiguous = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            "This answer has no token.",
            message_id="ambiguous-answer",
        )
    )
    wrong = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            "ACK HW-FFFFFFFF",
            message_id="wrong-ack",
        )
    )
    assert "ACK <token>" in ambiguous.deliveries[0].text
    assert wrong.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    for mandate in (first, second):
        assert repository.list_assignments(mandate.mandate_id) == [before[mandate.mandate_id]]
        assert repository.list_events(mandate.mandate_id) == events_before[mandate.mandate_id]

    selected = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"ACK {first.token}",
            message_id="correct-ack",
        )
    )

    first_assignment = repository.list_assignments(first.mandate_id)[0]
    second_assignment = repository.list_assignments(second.mandate_id)[0]
    assert first_assignment.state is StakeholderState.INTERVIEWING
    assert second_assignment == before[second.mandate_id]
    assert selected.deliveries[0].text.startswith("Question 1 of 1")
    assert "stakeholder.acknowledged" in _event_types(repository, first.mandate_id)
    assert repository.list_evidence(first.mandate_id) == []
    assert repository.list_evidence(second.mandate_id) == []


def test_workflow_advances_answers_and_explicit_decline_produces_routed_partial(
    repository, incoming_message_factory
) -> None:
    """Break caught: answers skip questions or DECLINE is recorded as evidence/agreement."""
    workflow = _build_workflow(repository, question_count=2)
    mandate, _ = _create_mandate(workflow, incoming_message_factory)
    workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"ACK {mandate.token}",
            message_id="progress-ack",
        )
    )

    progressed = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            "The launch needs a checklist.",
            message_id="answer-one",
        )
    )
    declined = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"DECLINE {mandate.token}",
            message_id="explicit-decline",
        )
    )

    assignment = repository.list_assignments(mandate.mandate_id)[0]
    interview = repository.get_interview(assignment.interview_id)
    assert progressed.deliveries[0].text.startswith("Question 2 of 2")
    assert interview is not None and interview.current_question_index == 1
    assert assignment.state is StakeholderState.DECLINED
    assert repository.get_mandate_by_token(mandate.token).state is MandateState.PARTIAL
    assert len(repository.list_evidence(mandate.mandate_id)) == 1
    assert {"interview.answer_recorded", "stakeholder.declined", "mandate.partial"} <= _event_types(
        repository, mandate.mandate_id
    )
    assert len(declined.deliveries) == 1
    assert declined.deliveries[0].conversation_id == "manager-conversation"
    assert "missing" in declined.deliveries[0].text.casefold()


def test_completed_interview_enters_negotiating_with_routed_proposal_and_events(
    repository, incoming_message_factory
) -> None:
    """Break caught: completed but unresolved evidence skips durable negotiation."""
    workflow = _build_workflow(repository)
    mandate, _ = _create_mandate(workflow, incoming_message_factory)

    result = _complete_interview(
        workflow,
        incoming_message_factory,
        mandate,
        "team-lead",
        prefix="negotiate",
    )

    saved = repository.get_mandate_by_token(mandate.token)
    proposal = repository.get_active_proposal(mandate.mandate_id)
    assert saved is not None and saved.state is MandateState.NEGOTIATING
    assert proposal is not None and proposal.round_number == 1
    assert len(result.deliveries) == 1
    assert result.deliveries[0].recipient == "lead@example.test"
    assert result.deliveries[0].text.startswith("HUMANWIRE DRAFT PROPOSAL")
    assert {"mandate.synthesizing", "proposal.created", "mandate.negotiating"} <= _event_types(
        repository, mandate.mandate_id
    )


def test_authenticated_proposal_acceptance_persists_and_routes_public_alignment_brief(
    repository, incoming_message_factory
) -> None:
    """Break caught: proposal acceptance becomes ALIGNED without a durable routed brief."""
    workflow = _build_workflow(repository)
    mandate, _ = _create_mandate(workflow, incoming_message_factory)
    _complete_interview(
        workflow,
        incoming_message_factory,
        mandate,
        "team-lead",
        prefix="accept",
    )

    accepted = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"ACCEPT {mandate.token}",
            message_id="proposal-accepted",
        )
    )

    saved = repository.get_mandate_by_token(mandate.token)
    brief = repository.get_runtime_status(f"alignment-brief:{mandate.mandate_id}")
    assert saved is not None and saved.state is MandateState.ALIGNED
    assert brief is not None and brief[0].startswith("HUMANWIRE ALIGNMENT BRIEF")
    assert len(accepted.deliveries) == 2
    assert {delivery.recipient for delivery in accepted.deliveries if delivery.recipient} == {
        "lead@example.test"
    }
    assert any(
        delivery.conversation_id == "manager-conversation"
        for delivery in accepted.deliveries
    )
    assert {"proposal.response_recorded", "mandate.aligned", "alignment.brief_persisted"} <= (
        _event_types(repository, mandate.mandate_id)
    )


def test_proposals_require_all_authenticated_respondents_and_cap_after_round_two(
    repository, incoming_message_factory
) -> None:
    """Break caught: one response advances a multi-party round or round two exceeds its cap."""
    workflow = _build_workflow(repository, person_ids=("team-lead", "vp-people"))
    mandate, _ = _create_mandate(workflow, incoming_message_factory)
    _complete_interview(
        workflow,
        incoming_message_factory,
        mandate,
        "team-lead",
        prefix="lead",
    )
    proposal_result = _complete_interview(
        workflow,
        incoming_message_factory,
        mandate,
        "vp-people",
        prefix="people",
    )
    round_one = repository.get_active_proposal(mandate.mandate_id)
    assert round_one is not None and round_one.round_number == 1
    assert {delivery.recipient for delivery in proposal_result.deliveries} == {
        "lead@example.test",
        "people@example.test",
    }

    responses_before = repository.list_proposal_responses(round_one.proposal_id)
    wrong_token = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            "ACCEPT HW-FFFFFFFF",
            message_id="wrong-proposal-token",
        )
    )
    unauthorized = workflow.handle(
        _message_for(
            incoming_message_factory,
            "manager",
            f"ACCEPT {mandate.token}",
            message_id="unauthorized-proposal-sender",
        )
    )
    first_required = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"ACCEPT {mandate.token}",
            message_id="round-one-lead",
        )
    )

    assert wrong_token.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert unauthorized.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert repository.list_proposal_responses(round_one.proposal_id) != responses_before
    assert len(repository.list_proposal_responses(round_one.proposal_id)) == 1
    assert first_required.deliveries == []
    assert repository.get_active_proposal(mandate.mandate_id).proposal_id == round_one.proposal_id

    round_two_result = workflow.handle(
        _message_for(
            incoming_message_factory,
            "vp-people",
            f"CHANGE {mandate.token} Move the launch date",
            message_id="round-one-people",
        )
    )
    round_two = repository.get_active_proposal(mandate.mandate_id)
    assert round_two is not None and round_two.round_number == 2
    assert {delivery.recipient for delivery in round_two_result.deliveries} == {
        "lead@example.test",
        "people@example.test",
    }

    waiting = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"ACCEPT {mandate.token}",
            message_id="round-two-lead",
        )
    )
    capped = workflow.handle(
        _message_for(
            incoming_message_factory,
            "vp-people",
            f"REJECT {mandate.token}",
            message_id="round-two-people",
        )
    )

    assert waiting.deliveries == []
    assert repository.get_mandate_by_token(mandate.token).state is MandateState.SCHEDULING
    assert repository.get_active_proposal(mandate.mandate_id) is None
    assert {"mandate.meeting_required", "mandate.scheduling"} <= _event_types(
        repository, mandate.mandate_id
    )
    assert sum(
        event.event_type == "proposal.response_recorded"
        for event in repository.list_events(mandate.mandate_id)
    ) == 4
    assert {delivery.recipient for delivery in capped.deliveries if delivery.recipient} == set()
    assert any(delivery.conversation_id == "manager-conversation" for delivery in capped.deliveries)
    assert all("AVAILABLE" in delivery.text for delivery in capped.deliveries)


def test_workflow_routes_exact_decision_to_engagement_without_proposal_negotiation(
    repository, incoming_message_factory
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="decision-workflow-create"
    )
    assignment = _convert_assignment_to_engagement(
        repository, mandate, EngagementType.REVIEW_APPROVAL
    )

    result = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"DECIDE {mandate.token} CHANGE PRIVATE-WORKFLOW-CHANGE",
            message_id="decision-workflow-change",
        )
    )

    saved = repository.get_assignment(assignment.assignment_id)
    decisions = repository.list_engagement_decisions(mandate.mandate_id)
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    assert len(decisions) == 1
    assert decisions[0].response is EngagementDecisionKind.CHANGE
    assert decisions[0].change_text == "PRIVATE-WORKFLOW-CHANGE"
    assert repository.list_proposal_responses(uuid4()) == []
    public_text = "\n".join(delivery.text for delivery in result.deliveries)
    event_text = "\n".join(
        event.model_dump_json() for event in repository.list_events(mandate.mandate_id)
    )
    evidence_text = "\n".join(
        item.model_dump_json() for item in repository.list_evidence(mandate.mandate_id)
    )
    assert "PRIVATE-WORKFLOW-CHANGE" not in public_text
    assert "PRIVATE-WORKFLOW-CHANGE" not in event_text
    assert "PRIVATE-WORKFLOW-CHANGE" not in evidence_text


@pytest.mark.parametrize(
    ("person_id", "conversation_id"),
    [
        ("vp-people", "people-thread"),
        ("team-lead", "wrong-thread"),
    ],
    ids=["unrelated-person", "wrong-thread"],
)
def test_workflow_denies_unrelated_or_wrong_thread_decisions_without_disclosure(
    repository, incoming_message_factory, person_id, conversation_id
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow, incoming_message_factory, message_id=f"decision-deny-{person_id}"
    )
    assignment = _convert_assignment_to_engagement(
        repository, mandate, EngagementType.REVIEW_APPROVAL
    )
    before_assignment = repository.get_assignment(assignment.assignment_id)
    before_events = repository.list_events(mandate.mandate_id)

    result = workflow.handle(
        _message_for(
            incoming_message_factory,
            person_id,
            f"DECIDE {mandate.token} APPROVE",
            message_id=f"decision-denied-{person_id}-{conversation_id}",
            conversation_id=conversation_id,
        )
    )

    assert repository.get_assignment(assignment.assignment_id) == before_assignment
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert repository.list_evidence(mandate.mandate_id) == []
    assert repository.list_events(mandate.mandate_id) == before_events
    assert all(mandate.objective not in delivery.text for delivery in result.deliveries)


def test_workflow_ambiguous_and_terminal_decision_candidates_fail_closed(
    repository, incoming_message_factory
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="decision-ambiguous-create"
    )
    first = _convert_assignment_to_engagement(
        repository, mandate, EngagementType.REVIEW_APPROVAL
    )
    second = first.model_copy(update={"assignment_id": uuid4()})
    repository.add_assignment(second)
    before = repository.list_assignments(mandate.mandate_id)

    ambiguous = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"DECIDE {mandate.token} APPROVE",
            message_id="decision-ambiguous",
        )
    )

    assert ambiguous.deliveries == []
    assert repository.list_assignments(mandate.mandate_id) == before
    assert repository.list_engagement_decisions(mandate.mandate_id) == []

    cancelled = workflow.repository.get_mandate_by_token(mandate.token).model_copy(
        update={
            "state": MandateState.CANCELLED,
            "completed_at": mandate.created_at,
        }
    )
    with repository.transaction() as unit:
        unit.save_mandate(cancelled)
        unit.save_assignment(
            second.model_copy(update={"state": StakeholderState.UNREACHABLE})
        )
    terminal_before = repository.list_assignments(mandate.mandate_id)
    terminal = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"DECIDE {mandate.token} REJECT Too late",
            message_id="decision-terminal",
        )
    )

    assert repository.list_assignments(mandate.mandate_id) == terminal_before
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert all("Too late" not in delivery.text for delivery in terminal.deliveries)


@pytest.mark.parametrize(
    "engagement_type",
    [EngagementType.REVIEW_APPROVAL, EngagementType.AVAILABILITY],
)
@pytest.mark.parametrize("reply", ["ACK {token}", "I agree with this request."])
def test_workflow_ack_and_free_text_cannot_answer_explicit_engagements(
    repository, incoming_message_factory, engagement_type, reply
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow,
        incoming_message_factory,
        message_id=f"explicit-free-text-{engagement_type.value}",
    )
    assignment = _convert_assignment_to_engagement(
        repository, mandate, engagement_type
    )
    before_assignment = repository.get_assignment(assignment.assignment_id)
    before_events = repository.list_events(mandate.mandate_id)
    before_evidence = repository.list_evidence(mandate.mandate_id)

    result = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            reply.format(token=mandate.token),
            message_id=f"explicit-inert-{engagement_type.value}-{reply[:3]}",
        )
    )

    assert result.deliveries == []
    assert repository.get_assignment(assignment.assignment_id) == before_assignment
    assert repository.list_events(mandate.mandate_id) == before_events
    assert repository.list_evidence(mandate.mandate_id) == before_evidence
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:team-lead"
    ) is None


def test_workflow_routes_engagement_availability_before_scheduling_attendees(
    repository, incoming_message_factory
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="availability-engagement-create"
    )
    assignment = _convert_assignment_to_engagement(
        repository, mandate, EngagementType.AVAILABILITY
    )
    window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"

    result = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"AVAILABLE {mandate.token} {window}",
            message_id="availability-engagement-recorded",
        )
    )

    saved = repository.get_assignment(assignment.assignment_id)
    stored = repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:team-lead"
    )
    assert saved is not None
    assert saved.state is StakeholderState.COMPLETE
    assert stored is not None
    assert stored[0] == window
    assert repository.get_meeting_package(mandate.mandate_id) is None
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert not any(
        item.evidence_type is EvidenceType.DECISION
        for item in repository.list_evidence(mandate.mandate_id)
    )
    assert all("agreement" not in delivery.text.casefold() for delivery in result.deliveries)


def test_workflow_ambiguous_engagement_availability_cannot_replace_windows(
    repository, incoming_message_factory
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="availability-ambiguous-create"
    )
    first = _convert_assignment_to_engagement(
        repository, mandate, EngagementType.AVAILABILITY
    )
    repository.add_assignment(first.model_copy(update={"assignment_id": uuid4()}))
    before = repository.list_assignments(mandate.mandate_id)
    window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"

    result = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"AVAILABLE {mandate.token} {window}",
            message_id="availability-ambiguous",
        )
    )

    assert result.deliveries == []
    assert repository.list_assignments(mandate.mandate_id) == before
    assert repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:team-lead"
    ) is None
    assert not any(
        event.event_type == "availability.recorded"
        for event in repository.list_events(mandate.mandate_id)
    )


def test_availability_tokens_are_not_an_oracle_for_unauthorized_senders(
    repository, incoming_message_factory
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="availability-oracle-create"
    )
    _convert_assignment_to_engagement(
        repository, mandate, EngagementType.AVAILABILITY
    )
    ambiguous_peer = Person(
        person_id="ambiguous-peer",
        display_name="Ambiguous Peer",
        role="Peer",
        department="Support",
        timezone="UTC",
        routes=[
            ContactRoute(
                route_id="ambiguous-email",
                channel=Channel.EMAIL,
                sender_address="people@example.test",
                recipient="ambiguous@example.test",
            )
        ],
    )
    ambiguous_directory = OrganizationDirectory(
        workflow.directory.document.model_copy(
            update={"people": [*workflow.directory.document.people, ambiguous_peer]}
        )
    )
    workflow.directory = ambiguous_directory
    workflow.engagements.directory = ambiguous_directory
    window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"
    senders = [
        incoming_message_factory(
            text="placeholder",
            channel=Channel.EMAIL,
            sender_address="unknown@example.test",
            conversation_id="unknown-thread",
            message_id="oracle-unknown",
        ),
        _message_for(
            incoming_message_factory,
            "vp-people",
            "placeholder",
            message_id="oracle-ambiguous",
        ),
        incoming_message_factory(
            text="placeholder",
            channel=Channel.EMAIL,
            sender_address="support@example.test",
            conversation_id="support-thread",
            message_id="oracle-unrelated",
        ),
        _message_for(
            incoming_message_factory,
            "team-lead",
            "placeholder",
            message_id="oracle-wrong-route",
            conversation_id="wrong-thread",
        ),
    ]

    for label, token, state in (
        ("missing", "HW-NONE", MandateState.INTERVIEWING),
        ("active", mandate.token, MandateState.INTERVIEWING),
        ("scheduling", mandate.token, MandateState.SCHEDULING),
        ("terminal", mandate.token, MandateState.CANCELLED),
    ):
        current = repository.get_mandate_by_token(mandate.token)
        assert current is not None
        repository.save_mandate(current.model_copy(update={"state": state}))
        before = _terminal_snapshot(repository, mandate)
        outcomes = []
        for index, sender in enumerate(senders):
            incoming = sender.model_copy(
                update={
                    "text": f"AVAILABLE {token} {window}",
                    "message_id": f"{sender.message_id}-{label}-{index}",
                }
            )
            outcomes.append(workflow.handle(incoming))
        assert outcomes == [WorkflowResult()] * len(senders)
        assert _terminal_snapshot(repository, mandate) == before


def test_scheduling_availability_exact_duplicate_is_inert(
    repository, incoming_message_factory
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="schedule-exact-create"
    )
    _move_to_scheduling(repository, mandate)
    window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"
    message = _message_for(
        incoming_message_factory,
        "team-lead",
        f"AVAILABLE {mandate.token} {window}",
        message_id="schedule-exact-availability",
        conversation_id="lead-thread",
    )

    first = workflow.handle(message)
    before = _terminal_snapshot(repository, mandate)
    duplicate = workflow.handle(message)

    recorded = [
        event
        for event in repository.list_events(mandate.mandate_id)
        if event.event_type == "availability.recorded"
    ]
    assert first.deliveries == []
    assert duplicate.deliveries == []
    assert _terminal_snapshot(repository, mandate) == before
    assert len(recorded) == 1
    assert recorded[0].metadata == {"attempt_count": 1}


def test_scheduling_availability_same_count_conflicting_replay_is_inert(
    repository, incoming_message_factory
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="schedule-conflict-create"
    )
    _move_to_scheduling(repository, mandate)
    first_window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"
    message = _message_for(
        incoming_message_factory,
        "team-lead",
        f"AVAILABLE {mandate.token} {first_window}",
        message_id="schedule-conflicting-availability",
        conversation_id="lead-thread",
    )
    workflow.handle(message)
    before = _terminal_snapshot(repository, mandate)
    conflicting = message.model_copy(
        update={
            "text": (
                f"AVAILABLE {mandate.token} "
                "2026-08-13T15:00:00+00:00/2026-08-13T16:00:00+00:00"
            )
        }
    )

    result = workflow.handle(conflicting)

    assert result.deliveries == []
    assert _terminal_snapshot(repository, mandate) == before


def test_scheduling_availability_identity_includes_channel_connection_and_message(
    repository, incoming_message_factory
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow, incoming_message_factory, message_id="schedule-identity-create"
    )
    _move_to_scheduling(repository, mandate)
    message_id = "provider-message-reused-across-connections"
    email = incoming_message_factory(
        text=(
            f"AVAILABLE {mandate.token} "
            "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"
        ),
        channel=Channel.EMAIL,
        sender_address="lead@example.test",
        conversation_id="lead-thread",
        connection_id="email-connection",
        message_id=message_id,
    )
    telegram = incoming_message_factory(
        text=(
            f"AVAILABLE {mandate.token} "
            "2026-08-13T15:00:00+00:00/2026-08-13T16:00:00+00:00"
        ),
        channel=Channel.TELEGRAM,
        sender_address="lead-chat",
        conversation_id="lead-conversation",
        connection_id="telegram-connection",
        message_id=message_id,
    )

    workflow.handle(email)
    workflow.handle(telegram)

    recorded = [
        event
        for event in repository.list_events(mandate.mandate_id)
        if event.event_type == "availability.recorded"
    ]
    stored = repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:team-lead"
    )
    assert len(recorded) == 2
    assert len({event.idempotency_key for event in recorded}) == 2
    assert stored is not None and stored[0].startswith("2026-08-13T15:00:00+00:00")


def test_availability_email_and_telegram_routes_survive_workflow_restart(
    tmp_path, incoming_message_factory
) -> None:
    """Break caught: scheduling trusts a wrong thread or in-memory proof lost on restart."""
    database_url = f"sqlite:///{(tmp_path / 'restart.sqlite3').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(workflow, incoming_message_factory)
    assignment = repository.list_assignments(mandate.mandate_id)[0]
    with repository.transaction() as unit:
        unit.add_evidence(
            EvidenceItem(
                evidence_id=uuid4(),
                mandate_id=mandate.mandate_id,
                assignment_id=assignment.assignment_id,
                stakeholder_id=assignment.person_id,
                evidence_type=EvidenceType.CONSTRAINT,
                statement="PRIVATE-SENTINEL-42 private-contact@example.test",
                visibility=EvidenceVisibility.PRIVATE,
                status=EvidenceStatus.CONFIRMED,
                source_message_id="private-source",
                channel=Channel.EMAIL,
                created_at=mandate.created_at,
                related_decision="Approve coverage",
            )
        )
        unit.add_evidence(
            EvidenceItem(
                evidence_id=uuid4(),
                mandate_id=mandate.mandate_id,
                assignment_id=assignment.assignment_id,
                stakeholder_id=assignment.person_id,
                evidence_type=EvidenceType.CONSTRAINT,
                statement="We cannot launch until coverage is staffed.",
                visibility=EvidenceVisibility.SHAREABLE,
                status=EvidenceStatus.CONFIRMED,
                source_message_id="confirmed-blocker",
                channel=Channel.EMAIL,
                created_at=mandate.created_at,
                related_decision="Approve coverage",
            )
        )
    proposal_result = _complete_interview(
        workflow,
        incoming_message_factory,
        mandate,
        "team-lead",
        prefix="restart",
    )
    workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"REJECT {mandate.token}",
            message_id="restart-round-one",
        )
    )
    availability_request = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"REJECT {mandate.token}",
            message_id="restart-round-two",
        )
    )
    assert {delivery.recipient for delivery in availability_request.deliveries if delivery.recipient} == {
        "lead@example.test"
    }
    assert any(
        delivery.conversation_id == "manager-conversation"
        for delivery in availability_request.deliveries
    )

    window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"
    events_before = repository.list_events(mandate.mandate_id)
    wrong_email = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"AVAILABLE {mandate.token} {window}",
            message_id="wrong-email-thread",
            conversation_id="wrong-thread",
        )
    )
    missing_email = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"AVAILABLE {mandate.token} {window}",
            message_id="missing-email-thread",
            conversation_id="",
        )
    )
    wrong_telegram = workflow.handle(
        _message_for(
            incoming_message_factory,
            "manager",
            f"AVAILABLE {mandate.token} {window}",
            message_id="wrong-telegram-thread",
            conversation_id="wrong-manager-conversation",
        )
    )

    assert all(
        result == WorkflowResult()
        for result in (wrong_email, missing_email, wrong_telegram)
    )
    assert repository.list_events(mandate.mandate_id) == events_before
    assert repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:team-lead"
    ) is None
    assert repository.get_runtime_status(f"availability:{mandate.mandate_id}:manager") is None

    manager_recorded = workflow.handle(
        _message_for(
            incoming_message_factory,
            "manager",
            f"AVAILABLE {mandate.token} {window}",
            message_id="manager-availability",
        )
    )
    assert manager_recorded.deliveries == []
    assert repository.get_runtime_status(f"availability:{mandate.mandate_id}:manager") is not None
    assert repository.get_meeting_package(mandate.mandate_id) is None

    restarted_repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    restarted = _build_workflow(restarted_repository, lead_email_thread="lead-thread")
    ready = restarted.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"AVAILABLE {mandate.token} {window}",
            message_id="lead-availability-after-restart",
        )
    )

    saved = restarted_repository.get_mandate_by_token(mandate.token)
    package = restarted_repository.get_meeting_package(mandate.mandate_id)
    assert saved is not None and saved.state is MandateState.MEETING_READY
    assert package is not None
    assert package.required_attendee_ids == ["manager", "team-lead"]
    assert {delivery.recipient for delivery in ready.deliveries if delivery.recipient} == {
        "lead@example.test"
    }
    assert any(delivery.conversation_id == "manager-conversation" for delivery in ready.deliveries)
    assert all("PROPOSED MEETING" in delivery.text for delivery in ready.deliveries)
    assert {"availability.recorded", "meeting.package_created", "mandate.meeting_ready"} <= _event_types(
        restarted_repository, mandate.mandate_id
    )
    public_text = "\n".join(
        delivery.text
        for result in (proposal_result, availability_request, ready)
        for delivery in result.deliveries
    )
    event_text = "\n".join(
        event.model_dump_json()
        for event in restarted_repository.list_events(mandate.mandate_id)
    )
    for private_value in (
        "PRIVATE-SENTINEL-42",
        "private-contact@example.test",
        "lead@example.test",
        "manager-chat",
    ):
        assert private_value not in public_text
        assert private_value not in event_text


def test_process_due_merges_ladder_and_synthesis_deliveries_at_workflow_boundary(
    repository, incoming_message_factory, now
) -> None:
    """Break caught: due processing drops either its reminder or synthesis output."""
    workflow = _build_workflow(
        repository,
        person_ids=("team-lead", "vp-people"),
        optional_people={"vp-people"},
    )
    mandate, _ = _create_mandate(workflow, incoming_message_factory)
    assignments = {
        assignment.person_id: assignment
        for assignment in repository.list_assignments(mandate.mandate_id)
    }
    required = assignments["team-lead"].model_copy(
        update={"state": StakeholderState.COMPLETE, "next_action_at": None}
    )
    optional = assignments["vp-people"].model_copy(
        update={
            "state": StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            "attempt_count": 1,
            "next_action_at": now,
        }
    )
    with repository.transaction() as unit:
        unit.save_assignment(required)
        unit.save_assignment(optional)
        unit.add_evidence(
            EvidenceItem(
                evidence_id=uuid4(),
                mandate_id=mandate.mandate_id,
                assignment_id=required.assignment_id,
                stakeholder_id=required.person_id,
                evidence_type=EvidenceType.COMMITMENT,
                statement="I will support the coverage decision.",
                visibility=EvidenceVisibility.SHAREABLE,
                status=EvidenceStatus.CONFIRMED,
                source_message_id="confirmed-required",
                channel=Channel.EMAIL,
                created_at=now,
                related_decision="Approve coverage",
            )
        )

    result = workflow.process_due(now)

    assert repository.get_mandate_by_token(mandate.token).state is MandateState.ALIGNED
    assert repository.get_assignment(optional.assignment_id).attempt_count == 2
    assert {delivery.recipient for delivery in result.deliveries if delivery.recipient} == {
        "lead@example.test",
        "people@example.test",
    }
    assert any(delivery.conversation_id == "manager-conversation" for delivery in result.deliveries)
    assert any("Please acknowledge" in delivery.text for delivery in result.deliveries)
    assert any("ALIGNMENT BRIEF" in delivery.text for delivery in result.deliveries)
    assert {"outreach.reminder_sent", "mandate.aligned", "alignment.brief_persisted"} <= _event_types(
        repository, mandate.mandate_id
    )


def test_creation_transaction_rolls_back_every_row_when_event_persistence_fails(
    repository, incoming_message_factory, monkeypatch
) -> None:
    """Break caught: a failed creation event leaves an orphan mandate or assignment."""
    workflow = _build_workflow(repository)
    original = RepositoryUnitOfWork.add_interview

    def fail_after_interview_is_staged(self, interview):
        original(self, interview)
        raise RuntimeError("injected interview persistence failure")

    monkeypatch.setattr(RepositoryUnitOfWork, "add_interview", fail_after_interview_is_staged)
    with pytest.raises(RuntimeError, match="injected interview persistence failure"):
        workflow.handle(
            incoming_message_factory(
                text="/mandate\nCoordinate launch coverage",
                message_id="rollback-create",
            )
        )

    with repository._session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MandateRecord)) == 0
        assert session.scalar(select(func.count()).select_from(StakeholderAssignmentRecord)) == 0
        assert session.scalar(select(func.count()).select_from(InterviewSessionRecord)) == 0
        assert session.scalar(select(func.count()).select_from(DomainEventRecord)) == 0


def test_initiator_can_request_status(workflow, telegram_mandate, incoming_message_factory, repository) -> None:
    """Break caught: the owner cannot observe the durable mandate state."""
    workflow.handle(telegram_mandate)
    mandate = repository.list_recent_mandates(1)[0]
    events = repository.list_events(mandate.mandate_id)

    result = workflow.handle(
        incoming_message_factory(
            text=f"/status {mandate.token}",
            message_id="initiator-status",
        )
    )

    assert len(result.deliveries) == 1
    assert result.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert result.deliveries[0].conversation_id == "manager-conversation"
    assert mandate.token in result.deliveries[0].text
    assert MandateState.INTERVIEWING.value in result.deliveries[0].text
    assert repository.list_events(mandate.mandate_id) == events


def test_assigned_stakeholder_can_request_status(
    repository, incoming_message_factory
) -> None:
    """Break caught: an assigned registered stakeholder is incorrectly denied status."""
    workflow = _build_workflow(repository)
    mandate, _ = _create_mandate(workflow, incoming_message_factory)

    result = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"/status {mandate.token}",
            message_id="stakeholder-status",
        )
    )

    assert len(result.deliveries) == 1
    assert result.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert mandate.token in result.deliveries[0].text
    assert MandateState.INTERVIEWING.value in result.deliveries[0].text


def test_unrelated_registered_person_is_denied_status_without_disclosure(
    repository, incoming_message_factory
) -> None:
    """Break caught: any directory member can enumerate another mandate's state."""
    workflow = _build_workflow(repository)
    mandate, _ = _create_mandate(workflow, incoming_message_factory)
    before = _terminal_snapshot(repository, mandate)

    result = workflow.handle(
        _message_for(
            incoming_message_factory,
            "vp-people",
            f"/status {mandate.token}",
            message_id="unrelated-status",
        )
    )

    assert len(result.deliveries) == 1
    assert result.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert "not authorized" in result.deliveries[0].text.casefold()
    assert mandate.token not in result.deliveries[0].text
    assert mandate.objective not in result.deliveries[0].text
    assert MandateState.INTERVIEWING.value not in result.deliveries[0].text
    assert _terminal_snapshot(repository, mandate) == before


def test_initiator_cancellation_persists_event_routes_acknowledgement_and_duplicate_is_inert(
    workflow, telegram_mandate, incoming_message_factory, repository
) -> None:
    """Break caught: owner cancellation lacks an audit event or replays its acknowledgement."""
    workflow.handle(telegram_mandate)
    mandate = repository.list_recent_mandates(1)[0]
    cancel = incoming_message_factory(
        text=f"/cancel {mandate.token}",
        message_id="initiator-cancel",
    )

    first = workflow.handle(cancel)
    events = repository.list_events(mandate.mandate_id)
    duplicate = workflow.handle(cancel)

    assert repository.get_mandate_by_token(mandate.token).state is MandateState.CANCELLED
    cancelled = [event for event in events if event.event_type == "mandate.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].new_state == MandateState.CANCELLED.value
    assert len(first.deliveries) == 1
    assert first.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert first.deliveries[0].conversation_id == "manager-conversation"
    assert "cancelled" in first.deliveries[0].text.casefold()
    assert duplicate.deliveries == []
    assert repository.list_events(mandate.mandate_id) == events


def test_non_owner_cancellation_is_denied_without_mutation(
    repository, incoming_message_factory
) -> None:
    """Break caught: a registered stakeholder can cancel the owner's mandate."""
    workflow = _build_workflow(repository)
    mandate, _ = _create_mandate(workflow, incoming_message_factory)
    before = _terminal_snapshot(repository, mandate)

    result = workflow.handle(
        _message_for(
            incoming_message_factory,
            "team-lead",
            f"/cancel {mandate.token}",
            message_id="non-owner-cancel",
        )
    )

    assert len(result.deliveries) == 1
    assert result.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert "original initiator" in result.deliveries[0].text.casefold()
    assert _terminal_snapshot(repository, mandate) == before


def test_process_due_expires_mandate_once_and_never_resurrects_outreach(
    repository, incoming_message_factory
) -> None:
    """Break caught: reminders run before expiry or resume from a terminal mandate."""
    workflow = _build_workflow(repository)
    mandate, _ = _create_mandate(workflow, incoming_message_factory)
    assignments = repository.list_assignments(mandate.mandate_id)

    first = workflow.process_due(mandate.expires_at)
    events = repository.list_events(mandate.mandate_id)
    assignments_after_expiry = repository.list_assignments(mandate.mandate_id)
    second = workflow.process_due(mandate.expires_at)
    much_later = workflow.process_due(mandate.expires_at + Settings().mandate_timeout_seconds * timedelta(seconds=1))

    assert repository.get_mandate_by_token(mandate.token).state is MandateState.EXPIRED
    expired = [event for event in events if event.event_type == "mandate.expired"]
    assert len(expired) == 1
    assert expired[0].previous_state == MandateState.INTERVIEWING.value
    assert expired[0].new_state == MandateState.EXPIRED.value
    assert len(first.deliveries) == 1
    assert first.deliveries[0].kind is DeliveryKind.SEND_TO_CONVERSATION
    assert first.deliveries[0].conversation_id == "manager-conversation"
    assert "expired" in first.deliveries[0].text.casefold()
    assert assignments_after_expiry == assignments
    assert second.deliveries == []
    assert much_later.deliveries == []
    assert repository.list_assignments(mandate.mandate_id) == assignments
    assert repository.list_evidence(mandate.mandate_id) == []
    assert repository.list_events(mandate.mandate_id) == events


@pytest.mark.parametrize("terminal_state", ["cancelled", "expired"])
@pytest.mark.parametrize("input_kind", ["ack", "free_text", "proposal", "availability"])
def test_terminal_mandates_reject_late_inputs_without_mutation(
    repository, incoming_message_factory, terminal_state: str, input_kind: str
) -> None:
    """Break caught: a terminal mandate accepts a late human input and reopens durable work."""
    workflow = _build_workflow(repository)
    mandate, _ = _create_mandate(workflow, incoming_message_factory)
    late_message = _late_terminal_message(
        workflow, incoming_message_factory, mandate, input_kind
    )
    if terminal_state == "cancelled":
        workflow.handle(
            incoming_message_factory(
                text=f"/cancel {mandate.token}",
                message_id=f"terminal-{input_kind}-cancel",
            )
        )
        expected_state = MandateState.CANCELLED
        late_at = mandate.created_at + timedelta(seconds=1)
    else:
        workflow.process_due(mandate.expires_at)
        expected_state = MandateState.EXPIRED
        late_at = mandate.expires_at + timedelta(seconds=1)
    late_message = late_message.model_copy(update={"received_at": late_at})
    before = _terminal_snapshot(repository, mandate)

    result = workflow.handle(late_message)

    assert repository.get_mandate_by_token(mandate.token).state is expected_state
    if input_kind == "availability":
        assert result == WorkflowResult()
    else:
        assert len(result.deliveries) == 1
        assert result.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
        assert "PRIVATE-LATE-SENTINEL" not in result.deliveries[0].text
        assert "@example.test" not in result.deliveries[0].text
    assert _terminal_snapshot(repository, mandate) == before


def test_aligned_synthesis_persists_public_brief_and_routes_it_to_required_people(
    workflow, telegram_mandate, repository, now
) -> None:
    """Break caught: an aligned result is hidden or sent to an unrouted destination."""
    workflow.handle(telegram_mandate)
    mandate = repository.list_recent_mandates(1)[0]
    assignments = repository.list_assignments(mandate.mandate_id)
    with repository.transaction() as unit:
        for assignment in assignments:
            complete = assignment.model_copy(update={"state": StakeholderState.COMPLETE})
            unit.save_assignment(complete)
            unit.add_evidence(
                EvidenceItem(
                    evidence_id=uuid4(), mandate_id=mandate.mandate_id,
                    assignment_id=assignment.assignment_id, stakeholder_id=assignment.person_id,
                    evidence_type=EvidenceType.COMMITMENT,
                    statement="I will support the coverage decision.",
                    visibility=EvidenceVisibility.SHAREABLE, status=EvidenceStatus.CONFIRMED,
                    source_message_id=f"answer-{assignment.person_id}", channel=Channel.EMAIL,
                    created_at=now, related_decision="Approve coverage",
                )
            )
        unit.add_evidence(
            EvidenceItem(
                evidence_id=uuid4(),
                mandate_id=mandate.mandate_id,
                assignment_id=assignments[0].assignment_id,
                stakeholder_id=assignments[0].person_id,
                evidence_type=EvidenceType.FACT,
                statement="PRIVATE-BRIEF-SENTINEL private-brief@example.test",
                visibility=EvidenceVisibility.PRIVATE,
                status=EvidenceStatus.CONFIRMED,
                source_message_id="private-brief-source",
                channel=Channel.EMAIL,
                created_at=now,
            )
        )

    result = workflow.synthesis.run(mandate.mandate_id, now)

    assert repository.get_runtime_status(f"alignment-brief:{mandate.mandate_id}") is not None
    assert {delivery.recipient for delivery in result.deliveries if delivery.recipient} == {
        "lead@example.test", "people@example.test", "coo@example.test", "support@example.test"
    }
    assert any(delivery.conversation_id == "manager-conversation" for delivery in result.deliveries)
    assert any(event.event_type == "alignment.brief_persisted" for event in repository.list_events(mandate.mandate_id))
    public_text = "\n".join(delivery.text for delivery in result.deliveries)
    event_text = "\n".join(
        event.model_dump_json() for event in repository.list_events(mandate.mandate_id)
    )
    for private_value in (
        "PRIVATE-BRIEF-SENTINEL",
        "private-brief@example.test",
        "lead@example.test",
        "manager-chat",
    ):
        assert private_value not in public_text
        assert private_value not in event_text


def test_primary_delivery_failure_immediately_uses_the_next_registered_route(
    workflow, telegram_mandate, repository, now
) -> None:
    """Break caught: a failed primary delivery produces a reminder on that same route."""
    created = workflow.handle(telegram_mandate)
    primary = next(item for item in created.deliveries if item.assignment_id is not None and item.recipient == "lead@example.test")

    retry = workflow.mark_delivery_result(primary, succeeded=False, now=now)

    assert len(retry.deliveries) == 1
    assert retry.deliveries[0].conversation_id == "lead-conversation"
    assignment = repository.get_assignment(primary.assignment_id)
    assert assignment is not None
    assert assignment.active_route_index == 1
    assert {"outreach.delivery_failed", "outreach.alternate_sent"} <= _event_types(
        repository, assignment.mandate_id
    )


def test_final_delivery_failure_synthesizes_required_mandate_and_replay_is_inert(
    workflow, telegram_mandate, repository, now
) -> None:
    """Break caught: exhaustion passes an assignment UUID to mandate synthesis."""
    created = workflow.handle(telegram_mandate)
    primary = next(
        item
        for item in created.deliveries
        if item.assignment_id is not None and item.recipient == "lead@example.test"
    )
    alternate = workflow.mark_delivery_result(primary, succeeded=False, now=now).deliveries[0]
    mandate = repository.list_recent_mandates(1)[0]
    with repository.transaction() as unit:
        for assignment in repository.list_assignments(mandate.mandate_id):
            if assignment.assignment_id != primary.assignment_id:
                unit.save_assignment(
                    assignment.model_copy(update={"state": StakeholderState.COMPLETE})
                )

    exhausted = workflow.mark_delivery_result(alternate, succeeded=False, now=now)
    assignment = repository.get_assignment(primary.assignment_id)
    events = repository.list_events(mandate.mandate_id)
    replay = workflow.mark_delivery_result(alternate, succeeded=False, now=now)

    assert assignment is not None and assignment.state is StakeholderState.DELIVERY_FAILED
    assert repository.get_mandate_by_token(mandate.token).state is MandateState.PARTIAL
    assert {
        "outreach.delivery_failed",
        "outreach.alternate_sent",
        "stakeholder.delivery_failed",
        "mandate.partial",
    } <= {event.event_type for event in events}
    assert exhausted.deliveries
    assert replay.deliveries == []
    assert repository.list_events(mandate.mandate_id) == events
