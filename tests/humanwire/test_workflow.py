import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest

from humanwire.config import Settings
from humanwire.container import DueActionWorker
from humanwire.database import create_session_factory
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


class MixedEngagementPlanner:
    SPECS = (
        (
            "inform-person",
            "Awareness update for launch observers.",
            Direction.DOWNWARD,
            False,
            EngagementType.INFORM,
            [],
        ),
        (
            "ack-person",
            "Acknowledge executive sponsorship receipt.",
            Direction.UPWARD,
            True,
            EngagementType.ACKNOWLEDGE,
            [],
        ),
        (
            "quick-person",
            "Provide one focused launch fact.",
            Direction.DOWNWARD,
            True,
            EngagementType.QUICK_RESPONSE,
            ["Which date is viable?"],
        ),
        (
            "structured-person",
            "Gather related policy facts and constraints.",
            Direction.LATERAL,
            True,
            EngagementType.STRUCTURED_INTERVIEW,
            ["Which rule applies?", "What blocks launch?", "What can you commit?"],
        ),
        (
            "approval-person",
            "Approve the registered launch decision.",
            Direction.UPWARD,
            True,
            EngagementType.REVIEW_APPROVAL,
            [],
        ),
        (
            "availability-person",
            "Provide meeting availability.",
            Direction.LATERAL,
            True,
            EngagementType.AVAILABILITY,
            [],
        ),
    )

    def __init__(self, people: list[Person]) -> None:
        self.people = people

    def plan(self, text: str, initiator: Person) -> ResolvedPlan:
        del text, initiator
        people_by_id = {person.person_id: person for person in self.people}
        return ResolvedPlan(
            plan=MandatePlan(
                objective="Coordinate adaptive launch coverage",
                required_decisions=["Complete the registered launch mandate"],
                stakeholders=[
                    PlannedStakeholder(
                        person_ref=person_id,
                        reason=reason,
                        direction=direction,
                        required=required,
                        engagement_type=engagement_type,
                        response_required=engagement_type is not EngagementType.INFORM,
                        questions=questions,
                    )
                    for (
                        person_id,
                        reason,
                        direction,
                        required,
                        engagement_type,
                        questions,
                    ) in self.SPECS
                ],
                completion_conditions=["Every required contribution is recorded"],
            ),
            people=[people_by_id[spec[0]] for spec in self.SPECS],
            planner="deterministic",
        )


def _mixed_directory(
    *,
    missing_routes: set[str] | None = None,
    route_overrides: dict[str, list[ContactRoute]] | None = None,
) -> tuple[OrganizationDirectory, list[Person]]:
    missing_routes = missing_routes or set()
    route_overrides = route_overrides or {}
    manager = Person(
        person_id="manager",
        display_name="Morgan Lee",
        role="Operations Manager",
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
    rows = (
        ("inform-person", "Inez Ward", "Observers"),
        ("ack-person", "Noah Price", "Leadership"),
        ("quick-person", "Quinn Stone", "Delivery"),
        ("structured-person", "Priya Raman", "People"),
        ("approval-person", "Maya Brooks", "Executive"),
        ("availability-person", "Ari Lane", "Operations"),
    )
    people = []
    for person_id, display_name, department in rows:
        routes = []
        if person_id not in missing_routes:
            routes = [
                ContactRoute(
                    route_id=f"{person_id}-email-route",
                    channel=Channel.EMAIL,
                    sender_address=f"{person_id}@private.example.test",
                    recipient=f"{person_id}@private.example.test",
                    preferred=True,
                ),
                ContactRoute(
                    route_id=f"{person_id}-telegram-route",
                    channel=Channel.TELEGRAM,
                    sender_address=f"{person_id}-private-chat",
                    conversation_id=f"{person_id}-private-conversation",
                ),
            ]
        if person_id in route_overrides:
            routes = route_overrides[person_id]
        people.append(
            Person(
                person_id=person_id,
                display_name=display_name,
                role=f"{department} owner",
                department=department,
                timezone="UTC",
                routes=routes,
            )
        )
    return (
        OrganizationDirectory(
            OrganizationDocument(
                people=[manager, *people],
                initiator_policies=[
                    InitiatorPolicy(
                        person_id="manager",
                        allowed_directions={
                            Direction.DOWNWARD,
                            Direction.LATERAL,
                            Direction.UPWARD,
                        },
                        allowed_departments={department for _, _, department in rows},
                    )
                ],
            )
        ),
        people,
    )


def _build_mixed_workflow(
    repository: SqlAlchemyHumanWireRepository,
    *,
    settings: Settings | None = None,
    missing_routes: set[str] | None = None,
    route_overrides: dict[str, list[ContactRoute]] | None = None,
) -> HumanWireWorkflow:
    directory, people = _mixed_directory(
        missing_routes=missing_routes,
        route_overrides=route_overrides,
    )
    return HumanWireWorkflow(
        directory,
        repository,
        MixedEngagementPlanner(people),
        RuleBasedEvidenceExtractor(),
        settings
        or Settings(
            _env_file=None,
            engagement_preview_seconds=15,
            acknowledgement_seconds=60,
            reminder_seconds=30,
        ),
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
    message = incoming_message_factory(
        text="/mandate\nCoordinate launch coverage",
        message_id=message_id,
    )
    workflow.handle(message)
    mandate = workflow.repository.list_recent_mandates(1)[0]
    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": f"{message_id}-go",
            }
        )
    )
    return workflow.repository.get_mandate_by_token(mandate.token), released


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
        "release_outbox": repository.list_release_outbox(mandate.mandate_id),
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


def _mixed_preview(
    workflow: HumanWireWorkflow,
    incoming_message_factory,
    *,
    message_id: str = "mixed-preview-create",
    received_at=None,
):
    updates = {}
    if received_at is not None:
        updates["received_at"] = received_at
    message = incoming_message_factory(
        text=(
            "/mandate\nCoordinate adaptive launch coverage. "
            "PRIVATE-REQUEST-SENTINEL provider-body-sentinel"
        ),
        message_id=message_id,
        **updates,
    )
    result = workflow.handle(message)
    mandate = workflow.repository.list_recent_mandates(1)[0]
    return message, mandate, result


def _release_selected_mixed_delivery(
    database_url,
    incoming_message_factory,
    now,
    *,
    person_id,
    prefix,
    route_overrides=None,
):
    settings = Settings(
        _env_file=None,
        engagement_require_go=True,
        acknowledgement_seconds=60,
        reminder_seconds=30,
    )
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    workflow = _build_mixed_workflow(
        repository,
        settings=settings,
        route_overrides=route_overrides,
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"{prefix}-create",
        received_at=now,
    )
    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": f"{prefix}-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    selected = next(
        delivery
        for delivery in released.deliveries
        if repository.get_assignment(delivery.assignment_id).person_id == person_id
    )
    for delivery in released.deliveries:
        if delivery.assignment_id != selected.assignment_id:
            workflow.mark_delivery_result(
                delivery,
                succeeded=True,
                now=now + timedelta(seconds=2),
            )
    return settings, repository, workflow, mandate, selected


def test_mandate_previews_mixed_engagements_before_release_without_outreach(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(repository)

    message, mandate, result = _mixed_preview(
        workflow, incoming_message_factory, received_at=now
    )

    assignments = repository.list_assignments(mandate.mandate_id)
    by_person = {assignment.person_id: assignment for assignment in assignments}
    events = repository.list_events(mandate.mandate_id)
    assert mandate.state is MandateState.PLANNED
    assert mandate.next_action_at == now + timedelta(seconds=15)
    assert len(result.deliveries) == 1
    preview = result.deliveries[0]
    assert preview.kind is DeliveryKind.REPLY_TO_MESSAGE
    assert preview.message_id == message.message_id
    assert preview.conversation_id == message.conversation_id
    assert preview.assignment_id is None
    assert {assignment.state for assignment in assignments} == {
        StakeholderState.CONTACT_QUEUED
    }
    assert all(assignment.attempt_count == 0 for assignment in assignments)
    assert all(assignment.first_contact_at is None for assignment in assignments)
    assert all(assignment.last_delivery_at is None for assignment in assignments)
    assert all(assignment.next_action_at is None for assignment in assignments)
    assert all(assignment.interview_id is None for assignment in assignments)
    assert repository.list_interviews(mandate.mandate_id) == []
    assert repository.list_evidence(mandate.mandate_id) == []
    assert repository.list_engagement_decisions(mandate.mandate_id) == []
    assert [event.event_type for event in events] == [
        "mandate.received",
        "mandate.planned",
        "engagement.plan_previewed",
    ]
    assert [by_person[person_id].engagement_type for person_id, *_ in MixedEngagementPlanner.SPECS] == [
        EngagementType.INFORM,
        EngagementType.ACKNOWLEDGE,
        EngagementType.QUICK_RESPONSE,
        EngagementType.STRUCTURED_INTERVIEW,
        EngagementType.REVIEW_APPROVAL,
        EngagementType.AVAILABILITY,
    ]
    assert by_person["inform-person"].response_required is False
    assert all(
        by_person[person_id].response_required
        for person_id in by_person
        if person_id != "inform-person"
    )

    text = preview.text
    for expected in (
        "HUMANWIRE ENGAGEMENT PLAN",
        mandate.token,
        "Inez Ward",
        "Observers",
        "Downward",
        "Awareness update for launch observers.",
        "Inform",
        "Response required: No",
        "Noah Price",
        "Acknowledgement",
        "Quinn Stone",
        "Quick response",
        "Questions: 1",
        "Priya Raman",
        "Structured interview",
        "Questions: 3",
        "Maya Brooks",
        "Approval review",
        "Ari Lane",
        "Availability",
        "Primary Email",
        "Alternate Telegram",
        "15-second preview",
        f"GO {mandate.token}",
        f"ENGAGE {mandate.token} <person_id> <type>",
    ):
        assert expected in text
    assert text.count("Questions:") == 2
    for forbidden in (
        "PRIVATE-REQUEST-SENTINEL",
        "provider-body-sentinel",
        "@private.example.test",
        "private-conversation",
        "private-chat",
        "-email-route",
        "-telegram-route",
        "connection-1",
        "manager-chat",
    ):
        assert forbidden not in text
        assert forbidden not in "\n".join(event.model_dump_json() for event in events)


def test_require_go_preview_has_no_deadline_and_never_auto_releases(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(
            _env_file=None,
            engagement_preview_seconds=0,
            engagement_require_go=True,
        ),
    )
    _, mandate, created = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="strict-require-go-preview",
        received_at=now,
    )

    due = workflow.process_due(now + timedelta(hours=1))

    saved = repository.get_mandate_by_token(mandate.token)
    assert saved is not None and saved.state is MandateState.PLANNED
    assert saved.next_action_at is None
    assert "Explicit GO is required before outreach" in created.deliveries[0].text
    assert due.deliveries == []
    assert repository.list_interviews(mandate.mandate_id) == []
    assert not any(
        event.event_type == "engagement.plan_released"
        for event in repository.list_events(mandate.mandate_id)
    )


def test_due_release_starts_all_six_engagement_types_once_and_not_before_deadline(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(repository)
    _, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="due-release-preview",
        received_at=now,
    )

    before = workflow.process_due(now + timedelta(seconds=14, microseconds=999999))
    released = workflow.process_due(now + timedelta(seconds=15))
    replay = workflow.process_due(now + timedelta(seconds=15))

    saved = repository.get_mandate_by_token(mandate.token)
    assignments = repository.list_assignments(mandate.mandate_id)
    by_type = {assignment.engagement_type: assignment for assignment in assignments}
    interviews = repository.list_interviews(mandate.mandate_id)
    events = repository.list_events(mandate.mandate_id)
    assert before.deliveries == []
    assert saved is not None and saved.state is MandateState.INTERVIEWING
    assert saved.next_action_at is None
    assert len(released.deliveries) == 6
    assert replay.deliveries == []
    assert by_type[EngagementType.INFORM].state is StakeholderState.DELIVERED
    assert by_type[EngagementType.INFORM].next_action_at is None
    for engagement_type in EngagementType:
        assignment = by_type[engagement_type]
        assert assignment.attempt_count == 1
        assert assignment.first_contact_at == now + timedelta(seconds=15)
        assert assignment.last_delivery_at == now + timedelta(seconds=15)
        if engagement_type is not EngagementType.INFORM:
            assert assignment.state is StakeholderState.AWAITING_ACKNOWLEDGEMENT
    assert {interview.assignment_id for interview in interviews} == {
        by_type[EngagementType.QUICK_RESPONSE].assignment_id,
        by_type[EngagementType.STRUCTURED_INTERVIEW].assignment_id,
    }
    assert len(interviews) == 2
    assert [event.event_type for event in events].count("outreach.primary_sent") == 6
    assert [event.event_type for event in events].count("engagement.plan_released") == 1
    assert [event.event_type for event in events].count("mandate.interviewing") == 1


def test_authorized_go_releases_strict_preview_early_once(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    _, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="go-release-preview",
        received_at=now,
    )
    go = incoming_message_factory(
        text=f"GO {mandate.token}",
        message_id="go-release-command",
        received_at=now + timedelta(seconds=1),
    )

    first = workflow.handle(go)
    replay = workflow.handle(go)

    saved = repository.get_mandate_by_token(mandate.token)
    assert saved is not None and saved.state is MandateState.INTERVIEWING
    assert len(first.deliveries) == 6
    assert replay.deliveries == []
    assert len(repository.list_interviews(mandate.mandate_id)) == 2
    assert sum(
        event.event_type == "engagement.plan_released"
        for event in repository.list_events(mandate.mandate_id)
    ) == 1


@pytest.mark.parametrize(
    ("sender", "channel", "conversation", "token"),
    [
        ("unknown-chat", Channel.TELEGRAM, "manager-conversation", None),
        ("manager-chat", Channel.TELEGRAM, "wrong-conversation", None),
        ("manager-chat", Channel.EMAIL, "manager-conversation", None),
        ("manager-chat", Channel.TELEGRAM, "manager-conversation", "HW-NONE"),
    ],
    ids=["unknown-sender", "wrong-thread", "wrong-channel", "wrong-token"],
)
def test_go_release_requires_exact_initiator_origin_and_token_without_oracle(
    repository,
    incoming_message_factory,
    now,
    sender,
    channel,
    conversation,
    token,
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    _, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"go-auth-preview-{sender}-{conversation}",
        received_at=now,
    )
    before = _terminal_snapshot(repository, mandate)

    result = workflow.handle(
        incoming_message_factory(
            text=f"GO {token or mandate.token}",
            sender_address=sender,
            channel=channel,
            conversation_id=conversation,
            message_id=f"go-auth-command-{sender}-{conversation}",
            received_at=now + timedelta(seconds=1),
        )
    )

    assert result == WorkflowResult()
    assert _terminal_snapshot(repository, mandate) == before


def test_authorized_engage_updates_plan_assignment_and_safe_event_atomically(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    _, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="engage-preview-create",
        received_at=now,
    )
    command = incoming_message_factory(
        text=f"ENGAGE {mandate.token} inform-person ACKNOWLEDGE",
        message_id="engage-safe-command",
        received_at=now + timedelta(seconds=1),
    )

    result = workflow.handle(command)

    saved = repository.get_mandate_by_token(mandate.token)
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "inform-person"
    )
    stakeholder = next(
        item for item in saved.plan.stakeholders if item.person_ref == "inform-person"
    )
    events = [
        event
        for event in repository.list_events(mandate.mandate_id)
        if event.event_type == "engagement.override_recorded"
    ]
    assert saved is not None and saved.state is MandateState.PLANNED
    assert assignment.engagement_type is EngagementType.ACKNOWLEDGE
    assert assignment.response_required is True
    assert assignment.state is StakeholderState.CONTACT_QUEUED
    assert stakeholder.engagement_type is EngagementType.ACKNOWLEDGE
    assert stakeholder.response_required is True
    assert len(events) == 1
    assert events[0].person_id == "inform-person"
    assert events[0].metadata == {
        "old_engagement_type": "inform",
        "new_engagement_type": "acknowledge",
    }
    assert len(result.deliveries) == 1
    assert result.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert "Acknowledgement" in result.deliveries[0].text
    assert "@private.example.test" not in result.deliveries[0].text

    before_replay = _terminal_snapshot(repository, mandate)
    replay = workflow.handle(command)
    assert replay == WorkflowResult()
    assert _terminal_snapshot(repository, mandate) == before_replay

    reverse = workflow.handle(
        command.model_copy(
            update={
                "message_id": "engage-safe-sequential",
                "text": f"ENGAGE {mandate.token} inform-person INFORM",
                "received_at": now + timedelta(seconds=2),
            }
        )
    )
    assert len(reverse.deliveries) == 1
    assert sum(
        event.event_type == "engagement.override_recorded"
        for event in repository.list_events(mandate.mandate_id)
    ) == 2


@pytest.mark.parametrize(
    ("person_id", "requested_type", "sender", "channel", "conversation"),
    [
        ("approval-person", "inform", "manager-chat", Channel.TELEGRAM, "manager-conversation"),
        ("quick-person", "structured_interview", "manager-chat", Channel.TELEGRAM, "manager-conversation"),
        ("unknown-person", "inform", "manager-chat", Channel.TELEGRAM, "manager-conversation"),
        ("inform-person", "acknowledge", "unknown-chat", Channel.TELEGRAM, "manager-conversation"),
        ("inform-person", "acknowledge", "manager-chat", Channel.TELEGRAM, "wrong-thread"),
        ("inform-person", "acknowledge", "manager-chat", Channel.EMAIL, "manager-conversation"),
    ],
    ids=[
        "unsafe-authority-downgrade",
        "invalid-question-contract",
        "unknown-person",
        "wrong-initiator",
        "wrong-thread",
        "wrong-channel",
    ],
)
def test_engage_rejects_unsafe_or_unauthorized_override_without_mutation(
    repository,
    incoming_message_factory,
    now,
    person_id,
    requested_type,
    sender,
    channel,
    conversation,
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    _, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"engage-denied-preview-{person_id}-{requested_type}",
        received_at=now,
    )
    before = _terminal_snapshot(repository, mandate)

    result = workflow.handle(
        incoming_message_factory(
            text=f"ENGAGE {mandate.token} {person_id} {requested_type}",
            sender_address=sender,
            channel=channel,
            conversation_id=conversation,
            message_id=f"engage-denied-{person_id}-{requested_type}",
            received_at=now + timedelta(seconds=1),
        )
    )

    assert result == WorkflowResult()
    assert _terminal_snapshot(repository, mandate) == before


def test_engage_is_inert_after_release_cancel_or_expiry(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    _, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="engage-late-preview",
        received_at=now,
    )
    workflow.handle(
        incoming_message_factory(
            text=f"GO {mandate.token}",
            message_id="engage-late-go",
            received_at=now + timedelta(seconds=1),
        )
    )
    before = _terminal_snapshot(repository, mandate)

    late = workflow.handle(
        incoming_message_factory(
            text=f"ENGAGE {mandate.token} inform-person ACKNOWLEDGE",
            message_id="engage-late-command",
            received_at=now + timedelta(seconds=2),
        )
    )

    assert late == WorkflowResult()
    assert _terminal_snapshot(repository, mandate) == before


def test_required_missing_route_is_truthful_partial_and_never_releases_other_assignments(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        missing_routes={"quick-person"},
    )

    _, mandate, result = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="required-missing-route-preview",
        received_at=now,
    )
    due = workflow.process_due(now + timedelta(days=1))

    saved = repository.get_mandate_by_token(mandate.token)
    assignments = repository.list_assignments(mandate.mandate_id)
    missing = next(item for item in assignments if item.person_id == "quick-person")
    assert saved is not None and saved.state is MandateState.PARTIAL
    assert saved.next_action_at is None
    assert missing.state is StakeholderState.DELIVERY_FAILED
    assert missing.failure_reason == "no_registered_route"
    assert len(result.deliveries) == 1
    assert result.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert "Quinn Stone" in result.deliveries[0].text
    assert "No registered delivery route" in result.deliveries[0].text
    assert due.deliveries == []
    assert repository.list_interviews(mandate.mandate_id) == []
    assert all(item.attempt_count == 0 for item in assignments)
    assert not any(
        event.event_type == "engagement.plan_released"
        for event in repository.list_events(mandate.mandate_id)
    )


def test_optional_missing_route_is_explicit_and_does_not_block_valid_release(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        missing_routes={"inform-person"},
    )
    _, mandate, result = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="optional-missing-route-preview",
        received_at=now,
    )

    released = workflow.process_due(now + timedelta(seconds=15))

    saved = repository.get_mandate_by_token(mandate.token)
    assignments = repository.list_assignments(mandate.mandate_id)
    missing = next(item for item in assignments if item.person_id == "inform-person")
    assert saved is not None and saved.state is MandateState.INTERVIEWING
    assert missing.state is StakeholderState.DELIVERY_FAILED
    assert "Routes: Unavailable" in result.deliveries[0].text
    assert len(released.deliveries) == 5
    assert all(delivery.assignment_id != missing.assignment_id for delivery in released.deliveries)


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("person_id", "concurrent-person"),
        ("department", "Concurrent Department"),
        ("direction", Direction.EXTERNAL),
        ("reason", "Concurrent reason"),
        ("required", True),
        ("engagement_type", EngagementType.ACKNOWLEDGE),
        ("response_required", True),
        ("state", StakeholderState.NOT_CONTACTED),
        ("route_ids", ["concurrent-route"]),
        ("active_route_index", 1),
        ("attempt_count", 1),
        ("interview_id", "uuid"),
        ("first_contact_at", "timestamp"),
        ("last_delivery_at", "timestamp"),
        ("next_action_at", "timestamp"),
        ("acknowledged_at", "timestamp"),
        ("completed_at", "timestamp"),
        ("failure_reason", "concurrent_failure"),
    ],
)
def test_release_cas_guards_every_optional_failed_assignment_field(
    tmp_path,
    incoming_message_factory,
    now,
    monkeypatch,
    field,
    changed_value,
) -> None:
    database_url = f"sqlite:///{(tmp_path / f'optional-failed-{field}.db').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
        missing_routes={"inform-person"},
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"optional-failed-{field}-create",
        received_at=now,
    )
    expected = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "inform-person"
    )
    if changed_value == "timestamp":
        changed_value = now + timedelta(seconds=1)
    elif changed_value == "uuid":
        changed_value = uuid4()
    changed = expected.model_copy(update={field: changed_value})
    original_transaction = repository.transaction
    raced = False

    @contextmanager
    def mutate_skipped_assignment_before_release():
        nonlocal raced
        if not raced:
            raced = True
            concurrent = SqlAlchemyHumanWireRepository(
                create_session_factory(database_url)
            )
            concurrent.save_assignment(changed)
        with original_transaction() as unit:
            yield unit

    monkeypatch.setattr(repository, "transaction", mutate_skipped_assignment_before_release)

    result = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": f"optional-failed-{field}-go",
                "received_at": now + timedelta(seconds=2),
            }
        )
    )

    assert raced
    assert result == WorkflowResult()
    assert repository.get_mandate_by_token(mandate.token).state is MandateState.PLANNED
    assert repository.get_assignment(expected.assignment_id) == changed
    assert repository.list_interviews(mandate.mandate_id) == []
    assert not any(
        event.event_type == "engagement.plan_released"
        for event in repository.list_events(mandate.mandate_id)
    )


def test_file_restart_releases_persisted_preview_at_due_time(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'preview-restart.sqlite3').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    first = _build_mixed_workflow(repository)
    _, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id="preview-restart-create",
        received_at=now,
    )

    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(restarted_repository)
    released = restarted.process_due(now + timedelta(seconds=15))
    replay = restarted.process_due(now + timedelta(seconds=15))

    saved = restarted_repository.get_mandate_by_token(mandate.token)
    assert saved is not None and saved.state is MandateState.INTERVIEWING
    assert len(released.deliveries) == 6
    assert replay.deliveries == []
    assert len(restarted_repository.list_interviews(mandate.mandate_id)) == 2


def test_release_atomically_persists_safe_claimed_initial_outbox_rows(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="release-outbox-safe-create",
        received_at=now,
    )

    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "release-outbox-safe-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )

    rows = repository.list_release_outbox(mandate.mandate_id)
    assert len(rows) == 6
    assert {row.assignment_id for row in rows} == {
        delivery.assignment_id for delivery in released.deliveries
    }
    assert {row.state for row in rows} == {"claimed"}
    assert {row.attempt_count for row in rows} == {1}
    assert {row.route_index for row in rows} == {0}
    assert {row.delivery_id for row in rows} == {
        hashlib.sha256(delivery.message_id.encode()).hexdigest()[:48]
        for delivery in released.deliveries
    }
    serialized = "\n".join(repr(row) for row in rows)
    for forbidden in (
        "@private.example.test",
        "private-conversation",
        "PRIVATE-REQUEST-SENTINEL",
        "provider-body-sentinel",
        "Awareness update for launch observers",
    ):
        assert forbidden not in serialized


def test_restart_recovers_release_commit_after_crash_before_any_dispatch(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-outbox-crash.db').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    first = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id="release-outbox-crash-create",
        received_at=now,
    )
    initially_claimed = first.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "release-outbox-crash-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    initial_ids = {delivery.message_id for delivery in initially_claimed.deliveries}

    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(
        restarted_repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    before_lease = restarted.process_due(now + timedelta(seconds=30))
    recovered = restarted.process_due(now + timedelta(seconds=32))

    assert before_lease == WorkflowResult()
    assert len(recovered.deliveries) == 6
    assert {delivery.message_id for delivery in recovered.deliveries} == initial_ids
    for delivery in recovered.deliveries:
        restarted.mark_delivery_result(
            delivery,
            succeeded=True,
            now=now + timedelta(seconds=33),
        )
    assert restarted.process_due(now + timedelta(seconds=64)) == WorkflowResult()
    assert {
        row.state
        for row in restarted_repository.list_release_outbox(mandate.mandate_id)
    } == {"completed"}


def test_restart_reconstructs_initial_release_by_persisted_route_id_after_reorder(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-route-reorder.db').as_posix()}"
    settings = Settings(_env_file=None, engagement_require_go=True)
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    first = _build_mixed_workflow(repository, settings=settings)
    message, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id="release-route-reorder-create",
        received_at=now,
    )
    released = first.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "release-route-reorder-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    selected = next(
        delivery
        for delivery in released.deliveries
        if repository.get_assignment(delivery.assignment_id).person_id
        == "inform-person"
    )
    assert selected.kind is DeliveryKind.INITIATE_EMAIL
    assert selected.recipient == "inform-person@private.example.test"

    original_routes = _mixed_directory()[0].resolve_person("inform-person").routes
    reordered_routes = [
        original_routes[0].model_copy(update={"preferred": False}),
        original_routes[1].model_copy(update={"preferred": True}),
    ]
    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(
        restarted_repository,
        settings=settings,
        route_overrides={"inform-person": reordered_routes},
    )

    recovered = restarted.process_due(now + timedelta(seconds=32))

    selected_recovery = next(
        delivery
        for delivery in recovered.deliveries
        if delivery.assignment_id == selected.assignment_id
    )
    assert selected_recovery.message_id == selected.message_id
    assert selected_recovery.kind is DeliveryKind.INITIATE_EMAIL
    assert selected_recovery.recipient == "inform-person@private.example.test"
    assert selected_recovery.conversation_id is None
    restarted.mark_delivery_result(
        selected_recovery,
        succeeded=True,
        now=now + timedelta(seconds=33),
    )
    selected_row = next(
        row
        for row in restarted_repository.list_release_outbox(mandate.mandate_id)
        if row.assignment_id == selected.assignment_id
    )
    assert selected_row.state == "completed"


def test_restart_after_ack_deadline_recovers_primary_before_any_response_ladder(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-outbox-before-ladder.db').as_posix()}"
    settings = Settings(
        _env_file=None,
        engagement_require_go=True,
        acknowledgement_seconds=10,
        reminder_seconds=10,
    )
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    first = _build_mixed_workflow(repository, settings=settings)
    message, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id="release-outbox-before-ladder-create",
        received_at=now,
    )
    initially_claimed = first.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "release-outbox-before-ladder-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )

    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(restarted_repository, settings=settings)
    recovered = restarted.process_due(now + timedelta(seconds=32))

    assert len(recovered.deliveries) == 6
    assert {delivery.message_id for delivery in recovered.deliveries} == {
        delivery.message_id for delivery in initially_claimed.deliveries
    }
    assert not any(
        event.event_type in {"outreach.reminder_sent", "outreach.alternate_sent"}
        for event in restarted_repository.list_events(mandate.mandate_id)
    )


@pytest.mark.parametrize(
    ("person_id", "response"),
    [
        ("ack-person", "ack"),
        ("quick-person", "answer"),
        ("structured-person", "answer"),
        ("approval-person", "decision"),
        ("availability-person", "availability"),
    ],
)
def test_authenticated_inbound_retires_initial_claim_when_provider_callback_was_lost(
    tmp_path,
    incoming_message_factory,
    now,
    person_id,
    response,
) -> None:
    database_url = f"sqlite:///{(tmp_path / f'inbound-retires-{person_id}.db').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"inbound-retires-{person_id}-create",
        received_at=now,
    )
    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": f"inbound-retires-{person_id}-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    selected = next(
        delivery
        for delivery in released.deliveries
        if repository.get_assignment(delivery.assignment_id).person_id == person_id
    )
    for delivery in released.deliveries:
        if delivery.assignment_id != selected.assignment_id:
            workflow.mark_delivery_result(
                delivery,
                succeeded=True,
                now=now + timedelta(seconds=2),
            )
    inbound_text = {
        "ack": f"ACK {mandate.token}",
        "answer": "A correlated human answer after the provider callback was lost.",
        "decision": f"DECIDE {mandate.token} APPROVE",
        "availability": (
            f"AVAILABLE {mandate.token} "
            "2026-08-11T13:00:00+00:00/2026-08-11T14:00:00+00:00"
        ),
    }[response]

    workflow.handle(
        incoming_message_factory(
            text=inbound_text,
            channel=Channel.EMAIL,
            sender_address=f"{person_id}@private.example.test",
            conversation_id=f"{person_id}-reply-thread",
            message_id=f"inbound-retires-{person_id}-response",
            received_at=now + timedelta(seconds=3),
        )
    )

    row = next(
        item
        for item in repository.list_release_outbox(mandate.mandate_id)
        if item.assignment_id == selected.assignment_id and item.attempt_count == 1
    )
    assert row.state == "completed"
    assert workflow.process_due(now + timedelta(seconds=35)) == WorkflowResult()


def test_restart_recovers_only_uncompleted_release_entries_after_mid_batch_crash(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-outbox-mid-batch.db').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    first = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id="release-outbox-mid-batch-create",
        received_at=now,
    )
    released = first.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "release-outbox-mid-batch-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    completed_ids = {delivery.message_id for delivery in released.deliveries[:2]}
    for delivery in released.deliveries[:2]:
        first.mark_delivery_result(
            delivery,
            succeeded=True,
            now=now + timedelta(seconds=2),
        )

    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(
        restarted_repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    recovered = restarted.process_due(now + timedelta(seconds=32))

    assert len(recovered.deliveries) == 4
    assert completed_ids.isdisjoint(
        {delivery.message_id for delivery in recovered.deliveries}
    )
    assert sorted(
        row.state
        for row in restarted_repository.list_release_outbox(mandate.mandate_id)
    ) == ["claimed"] * 4 + ["completed"] * 2


def test_failed_initial_provider_attempt_completes_outbox_and_never_replays_primary(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-outbox-failure.db').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="release-outbox-failure-create",
        received_at=now,
    )
    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "release-outbox-failure-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    failed = released.deliveries[0]
    for delivery in released.deliveries[1:]:
        workflow.mark_delivery_result(
            delivery,
            succeeded=True,
            now=now + timedelta(seconds=2),
        )

    alternate = workflow.mark_delivery_result(
        failed,
        succeeded=False,
        now=now + timedelta(seconds=2),
    )
    replay = workflow.process_due(now + timedelta(seconds=32))

    assert len(alternate.deliveries) == 1
    assert alternate.deliveries[0].message_id != failed.message_id
    assert replay == WorkflowResult()
    row = next(
        item
        for item in repository.list_release_outbox(mandate.mandate_id)
        if item.assignment_id == failed.assignment_id
    )
    assert row.state == "completed"


def test_failed_primary_alternate_is_recovered_after_callback_dispatch_crash(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-outbox-alternate-crash.db').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="release-outbox-alternate-crash-create",
        received_at=now,
    )
    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "release-outbox-alternate-crash-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    failed = next(
        delivery
        for delivery in released.deliveries
        if repository.get_assignment(delivery.assignment_id).person_id
        == "structured-person"
    )
    for delivery in released.deliveries:
        if delivery.assignment_id != failed.assignment_id:
            workflow.mark_delivery_result(
                delivery,
                succeeded=True,
                now=now + timedelta(seconds=2),
            )
    alternate = workflow.mark_delivery_result(
        failed,
        succeeded=False,
        now=now + timedelta(seconds=2),
    )
    assert len(alternate.deliveries) == 1

    restarted = _build_mixed_workflow(
        SqlAlchemyHumanWireRepository(create_session_factory(database_url)),
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    assert restarted.process_due(now + timedelta(seconds=32)) == WorkflowResult()
    recovered = restarted.process_due(now + timedelta(seconds=34))

    assert len(recovered.deliveries) == 1
    assert recovered.deliveries[0].message_id == alternate.deliveries[0].message_id
    restarted.mark_delivery_result(
        recovered.deliveries[0],
        succeeded=True,
        now=now + timedelta(seconds=35),
    )
    assert restarted.process_due(now + timedelta(seconds=66)) == WorkflowResult()
    assert {
        row.state
        for row in repository.list_release_outbox(mandate.mandate_id)
        if row.assignment_id == failed.assignment_id
    } == {"completed"}


def test_restart_reconstructs_failed_primary_alternate_by_persisted_route_id(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'alternate-route-reorder.db').as_posix()}"
    settings = Settings(_env_file=None, engagement_require_go=True)
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    first = _build_mixed_workflow(repository, settings=settings)
    message, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id="alternate-route-reorder-create",
        received_at=now,
    )
    released = first.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "alternate-route-reorder-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    failed = next(
        delivery
        for delivery in released.deliveries
        if repository.get_assignment(delivery.assignment_id).person_id
        == "structured-person"
    )
    for delivery in released.deliveries:
        if delivery.assignment_id != failed.assignment_id:
            first.mark_delivery_result(
                delivery,
                succeeded=True,
                now=now + timedelta(seconds=2),
            )
    alternate = first.mark_delivery_result(
        failed,
        succeeded=False,
        now=now + timedelta(seconds=2),
    ).deliveries[0]
    assert alternate.kind is DeliveryKind.SEND_TO_CONVERSATION
    assert alternate.conversation_id == "structured-person-private-conversation"

    original_routes = _mixed_directory()[0].resolve_person("structured-person").routes
    reordered_routes = [
        original_routes[0].model_copy(update={"preferred": False}),
        original_routes[1].model_copy(update={"preferred": True}),
    ]
    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(
        restarted_repository,
        settings=settings,
        route_overrides={"structured-person": reordered_routes},
    )

    recovered = restarted.process_due(now + timedelta(seconds=34))

    assert len(recovered.deliveries) == 1
    assert recovered.deliveries[0].message_id == alternate.message_id
    assert recovered.deliveries[0].kind is DeliveryKind.SEND_TO_CONVERSATION
    assert (
        recovered.deliveries[0].conversation_id
        == "structured-person-private-conversation"
    )
    assert recovered.deliveries[0].recipient is None
    restarted.mark_delivery_result(
        recovered.deliveries[0],
        succeeded=True,
        now=now + timedelta(seconds=35),
    )
    alternate_row = next(
        row
        for row in restarted_repository.list_release_outbox(mandate.mandate_id)
        if row.assignment_id == failed.assignment_id and row.attempt_count > 1
    )
    assert alternate_row.state == "completed"


def test_restart_skips_failed_primary_alternate_when_saved_route_was_removed(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'alternate-route-removed.db').as_posix()}"
    settings = Settings(_env_file=None, engagement_require_go=True)
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    first = _build_mixed_workflow(repository, settings=settings)
    message, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id="alternate-route-removed-create",
        received_at=now,
    )
    released = first.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "alternate-route-removed-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    failed = next(
        delivery
        for delivery in released.deliveries
        if repository.get_assignment(delivery.assignment_id).person_id
        == "structured-person"
    )
    for delivery in released.deliveries:
        if delivery.assignment_id != failed.assignment_id:
            first.mark_delivery_result(
                delivery,
                succeeded=True,
                now=now + timedelta(seconds=2),
            )
    alternate = first.mark_delivery_result(
        failed,
        succeeded=False,
        now=now + timedelta(seconds=2),
    ).deliveries[0]
    before_recovery = repository.get_assignment(failed.assignment_id)
    before_events = repository.list_events(mandate.mandate_id)

    original_routes = _mixed_directory()[0].resolve_person("structured-person").routes
    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(
        restarted_repository,
        settings=settings,
        route_overrides={"structured-person": [original_routes[0]]},
    )

    recovered = restarted.process_due(now + timedelta(seconds=34))

    assert recovered == WorkflowResult()
    assert alternate.conversation_id not in repr(recovered)
    saved = restarted_repository.get_assignment(failed.assignment_id)
    assert saved == before_recovery
    assert restarted_repository.list_events(mandate.mandate_id) == before_events
    assert saved is not None
    assert saved.active_route_index == 1
    assert saved.route_ids[1] == "structured-person-telegram-route"


@pytest.mark.parametrize(
    ("person_id", "succeeded", "expected_state"),
    [
        pytest.param(
            "inform-person",
            True,
            StakeholderState.COMPLETE,
            id="generic-success",
        ),
        pytest.param(
            "inform-person",
            False,
            StakeholderState.DELIVERY_FAILED,
            id="generic-failure",
        ),
        pytest.param(
            "structured-person",
            True,
            StakeholderState.ALTERNATE_CHANNEL,
            id="interview-success",
        ),
        pytest.param(
            "structured-person",
            False,
            StakeholderState.DELIVERY_FAILED,
            id="interview-failure",
        ),
    ],
)
def test_restart_callback_correlates_recovered_alternate_when_primary_route_was_removed(
    tmp_path,
    incoming_message_factory,
    now,
    person_id,
    succeeded,
    expected_state,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'alternate-callback-route-gap.db').as_posix()}"
    settings, _repository, first, mandate, primary = _release_selected_mixed_delivery(
        database_url,
        incoming_message_factory,
        now,
        person_id=person_id,
        prefix=f"alternate-callback-{person_id}-{succeeded}",
    )
    alternate = first.mark_delivery_result(
        primary,
        succeeded=False,
        now=now + timedelta(seconds=2),
    ).deliveries[0]
    original_routes = _mixed_directory()[0].resolve_person(person_id).routes

    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(
        restarted_repository,
        settings=settings,
        route_overrides={person_id: [original_routes[1]]},
    )
    recovered = restarted.process_due(now + timedelta(seconds=34))
    recovered_alternate = next(
        delivery
        for delivery in recovered.deliveries
        if delivery.assignment_id == primary.assignment_id
    )
    assert recovered_alternate.message_id == alternate.message_id
    assert recovered_alternate.conversation_id == (
        f"{person_id}-private-conversation"
    )

    restarted.mark_delivery_result(
        recovered_alternate,
        succeeded=succeeded,
        now=now + timedelta(seconds=35),
    )

    saved = restarted_repository.get_assignment(primary.assignment_id)
    alternate_row = next(
        row
        for row in restarted_repository.list_release_outbox(mandate.mandate_id)
        if row.assignment_id == primary.assignment_id and row.attempt_count > 1
    )
    assert saved is not None
    assert saved.state is expected_state
    assert alternate_row.state == "completed"
    assert restarted.process_due(now + timedelta(seconds=36)) == WorkflowResult()


@pytest.mark.parametrize("person_id", ["ack-person", "structured-person"])
@pytest.mark.parametrize(
    "route_variant",
    ["missing", "duplicate", "persisted-duplicate", "out-of-bounds"],
)
def test_due_reminder_is_inert_when_exact_active_route_cannot_be_resolved(
    tmp_path,
    incoming_message_factory,
    now,
    person_id,
    route_variant,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'due-active-route-gap.db').as_posix()}"
    settings, repository, first, mandate, primary_delivery = (
        _release_selected_mixed_delivery(
            database_url,
            incoming_message_factory,
            now,
            person_id=person_id,
            prefix=f"due-active-{person_id}-{route_variant}",
        )
    )
    first.mark_delivery_result(
        primary_delivery,
        succeeded=True,
        now=now + timedelta(seconds=2),
    )
    persisted = repository.get_assignment(primary_delivery.assignment_id)
    assert persisted is not None
    primary, alternate = _mixed_directory()[0].resolve_person(person_id).routes
    if route_variant == "missing":
        current_routes = [alternate]
    elif route_variant == "duplicate":
        duplicate_primary = primary.model_copy(
            update={
                "sender_address": f"{person_id}-duplicate-email",
                "recipient": f"{person_id}-duplicate@example.test",
            }
        )
        current_routes = [primary, duplicate_primary, alternate]
    elif route_variant == "persisted-duplicate":
        persisted = persisted.model_copy(
            update={
                "route_ids": [
                    primary.route_id,
                    primary.route_id,
                    alternate.route_id,
                ]
            }
        )
        repository.save_assignment(persisted)
        current_routes = [primary, alternate]
    else:
        persisted = persisted.model_copy(update={"active_route_index": 2})
        repository.save_assignment(persisted)
        current_routes = [primary, alternate]

    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(
        restarted_repository,
        settings=settings,
        route_overrides={person_id: current_routes},
    )
    before_assignment = restarted_repository.get_assignment(
        primary_delivery.assignment_id
    )
    before_events = restarted_repository.list_events(mandate.mandate_id)
    before_interviews = restarted_repository.list_interviews(mandate.mandate_id)
    assert before_assignment is not None

    result = restarted.engagements.process_due_assignment(
        before_assignment,
        now + timedelta(seconds=61),
    )

    assert result == WorkflowResult()
    assert restarted_repository.get_assignment(primary_delivery.assignment_id) == (
        before_assignment
    )
    assert restarted_repository.list_events(mandate.mandate_id) == before_events
    assert restarted_repository.list_interviews(mandate.mandate_id) == before_interviews
    assert f"{person_id}-private-conversation" not in repr(result)
    assert f"{person_id}-duplicate@example.test" not in repr(result)


@pytest.mark.parametrize("person_id", ["ack-person", "structured-person"])
@pytest.mark.parametrize("route_variant", ["missing", "duplicate"])
def test_due_alternate_is_inert_when_exact_next_route_cannot_be_resolved(
    tmp_path,
    incoming_message_factory,
    now,
    person_id,
    route_variant,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'due-next-route-gap.db').as_posix()}"
    default_routes = _mixed_directory()[0].resolve_person(person_id).routes
    primary, middle = default_routes
    final = ContactRoute(
        route_id=f"{person_id}-final-route",
        channel=Channel.EMAIL,
        sender_address=f"{person_id}-final@example.test",
        recipient=f"{person_id}-final@example.test",
    )
    settings, repository, first, mandate, primary_delivery = (
        _release_selected_mixed_delivery(
            database_url,
            incoming_message_factory,
            now,
            person_id=person_id,
            prefix=f"due-next-{person_id}-{route_variant}",
            route_overrides={person_id: [primary, middle, final]},
        )
    )
    first.mark_delivery_result(
        primary_delivery,
        succeeded=True,
        now=now + timedelta(seconds=2),
    )
    pending = repository.get_assignment(primary_delivery.assignment_id)
    assert pending is not None
    reminder = first.engagements.process_due_assignment(
        pending,
        now + timedelta(seconds=61),
    )
    assert reminder.deliveries[0].recipient == f"{person_id}@private.example.test"

    if route_variant == "missing":
        current_routes = [primary, final]
    else:
        duplicate_middle = middle.model_copy(
            update={
                "sender_address": f"{person_id}-duplicate-chat",
                "conversation_id": f"{person_id}-duplicate-conversation",
            }
        )
        current_routes = [primary, middle, duplicate_middle, final]
    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(
        restarted_repository,
        settings=settings,
        route_overrides={person_id: current_routes},
    )
    before_assignment = restarted_repository.get_assignment(
        primary_delivery.assignment_id
    )
    before_events = restarted_repository.list_events(mandate.mandate_id)
    before_interviews = restarted_repository.list_interviews(mandate.mandate_id)
    assert before_assignment is not None
    assert before_assignment.attempt_count == 2

    result = restarted.engagements.process_due_assignment(
        before_assignment,
        now + timedelta(seconds=91),
    )

    assert result == WorkflowResult()
    assert restarted_repository.get_assignment(primary_delivery.assignment_id) == (
        before_assignment
    )
    assert restarted_repository.list_events(mandate.mandate_id) == before_events
    assert restarted_repository.list_interviews(mandate.mandate_id) == before_interviews
    assert f"{person_id}-final@example.test" not in repr(result)
    assert f"{person_id}-duplicate-conversation" not in repr(result)


def test_concurrent_restart_drains_claim_each_initial_release_entry_once(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-outbox-claims.db').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    first = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id="release-outbox-claims-create",
        received_at=now,
    )
    first.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "release-outbox-claims-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    workflows = [
        _build_mixed_workflow(
            SqlAlchemyHumanWireRepository(create_session_factory(database_url)),
            settings=Settings(_env_file=None, engagement_require_go=True),
        )
        for _ in range(4)
    ]

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(workflow.process_due, now + timedelta(seconds=32))
            for workflow in workflows
        ]
        results = [future.result(timeout=10) for future in futures]

    deliveries = [delivery for result in results for delivery in result.deliveries]
    assert len(deliveries) == 6
    assert len({delivery.message_id for delivery in deliveries}) == 6
    assert {
        row.state for row in repository.list_release_outbox(mandate.mandate_id)
    } == {"claimed"}


def test_reclaimed_later_batch_entry_fences_the_original_dispatch_owner(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-outbox-batch-fence.db').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    first = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id="release-outbox-batch-fence-create",
        received_at=now,
    )
    released = first.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "release-outbox-batch-fence-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    original_later = released.deliveries[-1]

    restarted = _build_mixed_workflow(
        SqlAlchemyHumanWireRepository(create_session_factory(database_url)),
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    recovered = restarted.process_due(now + timedelta(seconds=32))
    reclaimed_later = next(
        item
        for item in recovered.deliveries
        if item.assignment_id == original_later.assignment_id
    )

    assert original_later.dispatch_claim_id != reclaimed_later.dispatch_claim_id
    assert not first.renew_delivery_claim(
        original_later,
        now + timedelta(seconds=33),
    )
    assert restarted.renew_delivery_claim(
        reclaimed_later,
        now + timedelta(seconds=33),
    )


@pytest.mark.parametrize(
    ("person_id", "succeeded"),
    [
        ("inform-person", True),
        ("inform-person", False),
        ("quick-person", True),
        ("quick-person", False),
    ],
)
def test_stale_release_owner_callback_is_inert_after_reclaim(
    tmp_path,
    incoming_message_factory,
    now,
    person_id,
    succeeded,
) -> None:
    database_url = f"sqlite:///{(tmp_path / f'release-outbox-stale-{person_id}-{succeeded}.db').as_posix()}"
    first_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    first = _build_mixed_workflow(
        first_repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id=f"release-outbox-stale-{person_id}-{succeeded}-create",
        received_at=now,
    )
    released = first.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": f"release-outbox-stale-{person_id}-{succeeded}-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    stale = next(
        item
        for item in released.deliveries
        if first_repository.get_assignment(item.assignment_id).person_id == person_id
    )

    winner_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    winner = _build_mixed_workflow(
        winner_repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    reclaimed = winner.process_due(now + timedelta(seconds=32))
    current = next(
        item
        for item in reclaimed.deliveries
        if item.assignment_id == stale.assignment_id
    )
    before_assignment = winner_repository.get_assignment(stale.assignment_id)
    assert before_assignment is not None
    before_session = (
        winner_repository.get_interview(before_assignment.interview_id)
        if before_assignment.interview_id is not None
        else None
    )
    before_events = winner_repository.list_events(mandate.mandate_id)

    stale_result = first.mark_delivery_result(
        stale,
        succeeded=succeeded,
        now=now + timedelta(seconds=33),
    )

    assert stale_result == WorkflowResult()
    assert winner_repository.get_assignment(stale.assignment_id) == before_assignment
    if before_session is not None:
        assert winner_repository.get_interview(before_session.session_id) == before_session
    assert winner_repository.list_events(mandate.mandate_id) == before_events
    open_row = next(
        row
        for row in winner_repository.list_release_outbox(mandate.mandate_id)
        if row.assignment_id == stale.assignment_id
    )
    assert open_row.state == "claimed"
    assert open_row.claim_owner == current.dispatch_claim_id

    winner_result = winner.mark_delivery_result(
        current,
        succeeded=succeeded,
        now=now + timedelta(seconds=34),
    )

    completed_row = next(
        row
        for row in winner_repository.list_release_outbox(mandate.mandate_id)
        if row.assignment_id == stale.assignment_id
    )
    assert completed_row.state == "completed"
    assert len(winner_repository.list_events(mandate.mandate_id)) > len(before_events)
    if succeeded:
        assert winner_result == WorkflowResult()
    else:
        assert len(winner_result.deliveries) == 1


@pytest.mark.parametrize(
    ("person_id", "succeeded"),
    [
        ("inform-person", True),
        ("inform-person", False),
        ("quick-person", True),
        ("quick-person", False),
    ],
)
def test_durable_release_callback_without_claim_owner_is_inert(
    tmp_path,
    incoming_message_factory,
    now,
    person_id,
    succeeded,
) -> None:
    database_url = f"sqlite:///{(tmp_path / f'release-outbox-owner-required-{person_id}-{succeeded}.db').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"release-outbox-owner-required-{person_id}-{succeeded}-create",
        received_at=now,
    )
    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": f"release-outbox-owner-required-{person_id}-{succeeded}-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    current = next(
        item
        for item in released.deliveries
        if repository.get_assignment(item.assignment_id).person_id == person_id
    )
    without_owner = current.model_copy(update={"dispatch_claim_id": None})
    before_assignment = repository.get_assignment(current.assignment_id)
    assert before_assignment is not None
    before_session = (
        repository.get_interview(before_assignment.interview_id)
        if before_assignment.interview_id is not None
        else None
    )
    before_events = repository.list_events(mandate.mandate_id)

    missing_owner_result = workflow.mark_delivery_result(
        without_owner,
        succeeded=succeeded,
        now=now + timedelta(seconds=2),
    )

    assert missing_owner_result == WorkflowResult()
    assert repository.get_assignment(current.assignment_id) == before_assignment
    if before_session is not None:
        assert repository.get_interview(before_session.session_id) == before_session
    assert repository.list_events(mandate.mandate_id) == before_events
    open_row = next(
        row
        for row in repository.list_release_outbox(mandate.mandate_id)
        if row.assignment_id == current.assignment_id
    )
    assert open_row.state == "claimed"
    assert open_row.claim_owner == current.dispatch_claim_id

    owner_result = workflow.mark_delivery_result(
        current,
        succeeded=succeeded,
        now=now + timedelta(seconds=3),
    )

    completed_row = next(
        row
        for row in repository.list_release_outbox(mandate.mandate_id)
        if row.assignment_id == current.assignment_id
    )
    assert completed_row.state == "completed"
    assert len(repository.list_events(mandate.mandate_id)) > len(before_events)
    if succeeded:
        assert owner_result == WorkflowResult()
    else:
        assert len(owner_result.deliveries) == 1


def test_due_worker_drains_recovered_release_outbox_and_callbacks_stop_replay(
    tmp_path, incoming_message_factory, now
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-outbox-worker.db').as_posix()}"
    repository = SqlAlchemyHumanWireRepository(create_session_factory(database_url))
    first = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        first,
        incoming_message_factory,
        message_id="release-outbox-worker-create",
        received_at=now,
    )
    first.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "release-outbox-worker-go",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    restarted_repository = SqlAlchemyHumanWireRepository(
        create_session_factory(database_url)
    )
    restarted = _build_mixed_workflow(
        restarted_repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )

    class CallbackGateway:
        def __init__(self):
            self.deliveries = []

        def dispatch(self, delivery):
            self.deliveries.append(delivery)
            restarted.mark_delivery_result(
                delivery,
                succeeded=True,
                now=now + timedelta(seconds=32),
            )

    gateway = CallbackGateway()
    worker = DueActionWorker(
        restarted,
        gateway,
        restarted_repository,
        poll_seconds=60,
        clock=lambda: now + timedelta(seconds=32),
    )

    worker.run_once()
    worker.run_once()

    assert len(gateway.deliveries) == 6
    assert len({delivery.message_id for delivery in gateway.deliveries}) == 6
    assert {
        row.state
        for row in restarted_repository.list_release_outbox(mandate.mandate_id)
    } == {"completed"}


@pytest.mark.parametrize(
    ("failure_point", "assignment_loss_index"),
    [
        ("mandate_cas", None),
        ("assignment_cas", 0),
        ("assignment_cas", 2),
        ("assignment_cas", 5),
        ("interview_insert", None),
        ("outbox_insert", None),
        ("event_append", None),
    ],
)
def test_release_failure_rolls_back_the_complete_batch_without_delivery(
    repository,
    incoming_message_factory,
    now,
    monkeypatch,
    failure_point,
    assignment_loss_index,
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    _, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"release-rollback-{failure_point}-{assignment_loss_index}",
        received_at=now,
    )
    before = _terminal_snapshot(repository, mandate)

    if failure_point == "mandate_cas":
        monkeypatch.setattr(
            RepositoryUnitOfWork,
            "compare_and_save_mandate_if_unexpired",
            lambda self, expected, updated, at: False,
        )
    elif failure_point == "assignment_cas":
        original = RepositoryUnitOfWork.compare_and_save_assignment
        calls = 0

        def lose_selected_assignment(self, expected, updated):
            nonlocal calls
            index = calls
            calls += 1
            if index == assignment_loss_index:
                return False
            return original(self, expected, updated)

        monkeypatch.setattr(
            RepositoryUnitOfWork,
            "compare_and_save_assignment",
            lose_selected_assignment,
        )
    elif failure_point == "interview_insert":
        original = RepositoryUnitOfWork.add_interview

        def fail_interview(self, interview):
            original(self, interview)
            raise RuntimeError("injected release interview failure")

        monkeypatch.setattr(RepositoryUnitOfWork, "add_interview", fail_interview)
    elif failure_point == "outbox_insert":
        original = RepositoryUnitOfWork.add_release_outbox

        def fail_outbox(self, entry):
            original(self, entry)
            raise RuntimeError("injected release outbox failure")

        monkeypatch.setattr(RepositoryUnitOfWork, "add_release_outbox", fail_outbox)
    else:
        original = RepositoryUnitOfWork.append_event_once

        def fail_release_event(self, mandate_id, event):
            if event.event_type == "engagement.plan_released":
                raise RuntimeError("injected release event failure")
            return original(self, mandate_id, event)

        monkeypatch.setattr(RepositoryUnitOfWork, "append_event_once", fail_release_event)

    released = workflow.handle(
        incoming_message_factory(
            text=f"GO {mandate.token}",
            message_id=f"release-rollback-go-{failure_point}-{assignment_loss_index}",
            received_at=now + timedelta(seconds=1),
        )
    )

    assert released == WorkflowResult()
    assert _terminal_snapshot(repository, mandate) == before


def test_release_rejects_an_assignment_roster_that_no_longer_matches_the_plan(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    _, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="release-roster-mismatch-create",
        received_at=now,
    )
    assignment = repository.list_assignments(mandate.mandate_id)[0]
    repository.add_assignment(
        assignment.model_copy(
            update={
                "assignment_id": uuid4(),
                "person_id": "unexpected-person",
            }
        )
    )
    before = _terminal_snapshot(repository, mandate)

    released = workflow.handle(
        incoming_message_factory(
            text=f"GO {mandate.token}",
            message_id="release-roster-mismatch-go",
            received_at=now + timedelta(seconds=1),
        )
    )

    assert released == WorkflowResult()
    assert _terminal_snapshot(repository, mandate) == before


def test_expiration_is_processed_before_a_due_preview_release(
    repository, incoming_message_factory, now
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(
            _env_file=None,
            engagement_preview_seconds=15,
            mandate_timeout_seconds=10,
        ),
    )
    _, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="expire-before-release-preview",
        received_at=now,
    )

    result = workflow.process_due(now + timedelta(seconds=15))

    saved = repository.get_mandate_by_token(mandate.token)
    assert saved is not None and saved.state is MandateState.EXPIRED
    assert len(result.deliveries) == 1
    assert "EXPIRED" in result.deliveries[0].text
    assert repository.list_interviews(mandate.mandate_id) == []
    assert not any(
        event.event_type == "engagement.plan_released"
        for event in repository.list_events(mandate.mandate_id)
    )


def _file_mixed_race_workflow(tmp_path, name: str, settings: Settings):
    database_path = tmp_path / f"{name}.sqlite3"
    factory = create_session_factory(f"sqlite:///{database_path.as_posix()}")
    with factory.kw["bind"].begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA busy_timeout=5000")
    repository = SqlAlchemyHumanWireRepository(factory)
    return repository, _build_mixed_workflow(repository, settings=settings)


def _order_stale_transaction_race(
    repository,
    monkeypatch,
    *,
    winner: str,
    participant_count: int = 2,
):
    """Hold every loser after its aggregate read, then commit one named winner."""
    original_transaction = repository.transaction
    role = threading.local()
    condition = threading.Condition()
    losers_ready = 0
    winner_done = threading.Event()

    @contextmanager
    def ordered_transaction():
        nonlocal losers_ready
        name = getattr(role, "name", None)
        if name == winner:
            with condition:
                assert condition.wait_for(
                    lambda: losers_ready == participant_count - 1,
                    timeout=5,
                )
        else:
            with condition:
                losers_ready += 1
                condition.notify_all()
            assert winner_done.wait(timeout=5)
        try:
            with original_transaction() as unit:
                yield unit
        finally:
            if name == winner:
                winner_done.set()

    monkeypatch.setattr(repository, "transaction", ordered_transaction)
    return role


def _terminal_wins_before_next_transaction(
    repository,
    monkeypatch,
    mandate_id,
    terminal_state: MandateState,
    at,
):
    original_transaction = repository.transaction
    raced = False

    @contextmanager
    def terminal_first_transaction():
        nonlocal raced
        if not raced:
            raced = True
            concurrent = SqlAlchemyHumanWireRepository(repository._session_factory)
            current = next(
                item
                for item in concurrent.list_recent_mandates(1000)
                if item.mandate_id == mandate_id
            )
            concurrent.save_mandate(
                current.model_copy(
                    update={
                        "state": terminal_state,
                        "next_action_at": None,
                        "updated_at": at,
                        "completed_at": at,
                    }
                )
            )
        with original_transaction() as unit:
            yield unit

    monkeypatch.setattr(repository, "transaction", terminal_first_transaction)
    return lambda: raced


@pytest.mark.parametrize(
    "engagement_type",
    [
        EngagementType.ACKNOWLEDGE,
        EngagementType.QUICK_RESPONSE,
        EngagementType.STRUCTURED_INTERVIEW,
        EngagementType.REVIEW_APPROVAL,
        EngagementType.AVAILABILITY,
    ],
)
@pytest.mark.parametrize("stage", ["reminder", "alternate", "unreachable"])
@pytest.mark.parametrize(
    "terminal_state", [MandateState.CANCELLED, MandateState.EXPIRED]
)
def test_terminal_first_due_ladder_race_is_inert_for_every_engagement_family(
    tmp_path,
    incoming_message_factory,
    now,
    monkeypatch,
    engagement_type,
    stage,
    terminal_state,
) -> None:
    repository, workflow = _file_mixed_race_workflow(
        tmp_path,
        f"due-terminal-{terminal_state.value}-{engagement_type.value}-{stage}",
        Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"due-terminal-{terminal_state.value}-{engagement_type.value}-{stage}",
        received_at=now,
    )
    workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": f"due-terminal-go-{engagement_type.value}-{stage}",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    terminal_at = (
        mandate.expires_at
        if terminal_state is MandateState.EXPIRED
        else now + timedelta(seconds=2)
    )
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.engagement_type is engagement_type
    )
    stage_update = {
        "reminder": {
            "state": StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            "attempt_count": 1,
            "active_route_index": 0,
        },
        "alternate": {
            "state": StakeholderState.FOLLOW_UP_DUE,
            "attempt_count": 2,
            "active_route_index": 0,
        },
        "unreachable": {
            "state": StakeholderState.ALTERNATE_CHANNEL,
            "attempt_count": 3,
            "active_route_index": 1,
        },
    }[stage]
    due_assignment = assignment.model_copy(
        update=stage_update
        | {
            "last_delivery_at": terminal_at,
            "next_action_at": terminal_at,
        }
    )
    repository.save_assignment(due_assignment)
    events_before = repository.list_events(mandate.mandate_id)
    interviews_before = repository.list_interviews(mandate.mandate_id)
    raced = _terminal_wins_before_next_transaction(
        repository,
        monkeypatch,
        mandate.mandate_id,
        terminal_state,
        terminal_at,
    )

    result = workflow.engagements.process_due_assignment(due_assignment, terminal_at)

    assert raced()
    assert result == WorkflowResult()
    assert repository.get_mandate_by_token(mandate.token).state is terminal_state
    assert repository.get_assignment(due_assignment.assignment_id) == due_assignment
    assert repository.list_events(mandate.mandate_id) == events_before
    assert repository.list_interviews(mandate.mandate_id) == interviews_before


@pytest.mark.parametrize("engagement_type", list(EngagementType))
@pytest.mark.parametrize("succeeded", [True, False], ids=["success", "failure"])
@pytest.mark.parametrize(
    "terminal_state", [MandateState.CANCELLED, MandateState.EXPIRED]
)
def test_terminal_first_delivery_callback_race_is_inert_for_every_engagement_type(
    tmp_path,
    incoming_message_factory,
    now,
    monkeypatch,
    engagement_type,
    succeeded,
    terminal_state,
) -> None:
    repository, workflow = _file_mixed_race_workflow(
        tmp_path,
        f"callback-terminal-{terminal_state.value}-{engagement_type.value}-{succeeded}",
        Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"callback-terminal-create-{engagement_type.value}-{succeeded}",
        received_at=now,
    )
    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": f"callback-terminal-go-{engagement_type.value}-{succeeded}",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.engagement_type is engagement_type
    )
    delivery = next(
        item for item in released.deliveries if item.assignment_id == assignment.assignment_id
    )
    terminal_at = (
        mandate.expires_at
        if terminal_state is MandateState.EXPIRED
        else now + timedelta(seconds=2)
    )
    assignment_before = repository.get_assignment(assignment.assignment_id)
    events_before = repository.list_events(mandate.mandate_id)
    interviews_before = repository.list_interviews(mandate.mandate_id)
    raced = _terminal_wins_before_next_transaction(
        repository,
        monkeypatch,
        mandate.mandate_id,
        terminal_state,
        terminal_at,
    )

    result = workflow.mark_delivery_result(delivery, succeeded, terminal_at)

    assert raced()
    assert result == WorkflowResult()
    assert repository.get_mandate_by_token(mandate.token).state is terminal_state
    assert repository.get_assignment(assignment.assignment_id) == assignment_before
    assert repository.list_events(mandate.mandate_id) == events_before
    assert repository.list_interviews(mandate.mandate_id) == interviews_before


@pytest.mark.parametrize(
    ("engagement_type", "operation"),
    [
        (EngagementType.ACKNOWLEDGE, "ack"),
        (EngagementType.QUICK_RESPONSE, "ack"),
        (EngagementType.STRUCTURED_INTERVIEW, "ack"),
        (EngagementType.QUICK_RESPONSE, "answer"),
        (EngagementType.STRUCTURED_INTERVIEW, "answer"),
        (EngagementType.STRUCTURED_INTERVIEW, "decline"),
    ],
)
@pytest.mark.parametrize(
    "terminal_state", [MandateState.CANCELLED, MandateState.EXPIRED]
)
def test_terminal_first_authenticated_engagement_race_is_inert(
    tmp_path,
    incoming_message_factory,
    now,
    monkeypatch,
    engagement_type,
    operation,
    terminal_state,
) -> None:
    repository, workflow = _file_mixed_race_workflow(
        tmp_path,
        f"inbound-terminal-{terminal_state.value}-{engagement_type.value}-{operation}",
        Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"inbound-terminal-create-{engagement_type.value}-{operation}",
        received_at=now,
    )
    workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": f"inbound-terminal-go-{engagement_type.value}-{operation}",
                "received_at": now + timedelta(seconds=1),
            }
        )
    )
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.engagement_type is engagement_type
    )

    def stakeholder_message(text, message_id, received_at):
        return incoming_message_factory(
            text=text,
            channel=Channel.EMAIL,
            sender_address=f"{assignment.person_id}@private.example.test",
            conversation_id=f"{assignment.person_id}-reply-thread",
            message_id=message_id,
            received_at=received_at,
        )

    if operation == "answer":
        workflow.handle(
            stakeholder_message(
                f"ACK {mandate.token}",
                f"inbound-terminal-setup-ack-{engagement_type.value}",
                now + timedelta(seconds=2),
            )
        )
    terminal_at = (
        mandate.expires_at
        if terminal_state is MandateState.EXPIRED
        else now + timedelta(seconds=3)
    )
    text = {
        "ack": f"ACK {mandate.token}",
        "answer": "A correlated answer that must lose to terminal state.",
        "decline": f"DECLINE {mandate.token}",
    }[operation]
    inbound = stakeholder_message(
        text,
        f"inbound-terminal-{operation}-{engagement_type.value}-{terminal_state.value}",
        terminal_at,
    )
    assignment_before = repository.get_assignment(assignment.assignment_id)
    events_before = repository.list_events(mandate.mandate_id)
    interviews_before = repository.list_interviews(mandate.mandate_id)
    evidence_before = repository.list_evidence(mandate.mandate_id)
    raced = _terminal_wins_before_next_transaction(
        repository,
        monkeypatch,
        mandate.mandate_id,
        terminal_state,
        terminal_at,
    )

    result = workflow.handle(inbound)

    assert raced()
    assert result == WorkflowResult()
    assert repository.get_mandate_by_token(mandate.token).state is terminal_state
    assert repository.get_assignment(assignment.assignment_id) == assignment_before
    assert repository.list_events(mandate.mandate_id) == events_before
    assert repository.list_interviews(mandate.mandate_id) == interviews_before
    assert repository.list_evidence(mandate.mandate_id) == evidence_before


@pytest.mark.parametrize("iteration", range(2))
@pytest.mark.parametrize("winner", ["go", "due"])
def test_file_go_and_due_release_race_has_one_complete_winner(
    tmp_path, incoming_message_factory, now, monkeypatch, iteration, winner
) -> None:
    repository, workflow = _file_mixed_race_workflow(
        tmp_path,
        f"go-due-{winner}-{iteration}",
        Settings(_env_file=None, engagement_preview_seconds=15),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"go-due-create-{winner}-{iteration}",
        received_at=now,
    )
    role = _order_stale_transaction_race(
        repository,
        monkeypatch,
        winner=winner,
    )

    def run_go():
        role.name = "go"
        return workflow.handle(
            message.model_copy(
                update={
                    "text": f"GO {mandate.token}",
                    "message_id": f"go-due-go-{winner}-{iteration}",
                    "received_at": now + timedelta(seconds=1),
                }
            )
        )

    def run_due():
        role.name = "due"
        return workflow.process_due(now + timedelta(seconds=15))

    with ThreadPoolExecutor(max_workers=2) as executor:
        go_result = executor.submit(run_go)
        due_result = executor.submit(run_due)
        results = (go_result.result(timeout=10), due_result.result(timeout=10))

    assert sorted(len(result.deliveries) for result in results) == [0, 6]
    assert repository.get_mandate_by_token(mandate.token).state is MandateState.INTERVIEWING
    assert len(repository.list_interviews(mandate.mandate_id)) == 2
    assert sum(
        event.event_type == "engagement.plan_released"
        for event in repository.list_events(mandate.mandate_id)
    ) == 1


@pytest.mark.parametrize("iteration", range(2))
@pytest.mark.parametrize("winner", ["go", "cancel"])
def test_file_go_and_cancel_race_never_releases_a_cancelled_plan(
    tmp_path, incoming_message_factory, now, monkeypatch, iteration, winner
) -> None:
    repository, workflow = _file_mixed_race_workflow(
        tmp_path,
        f"go-cancel-{winner}-{iteration}",
        Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"go-cancel-create-{winner}-{iteration}",
        received_at=now,
    )
    role = _order_stale_transaction_race(
        repository,
        monkeypatch,
        winner=winner,
    )

    def run_go():
        role.name = "go"
        return workflow.handle(
            message.model_copy(
                update={
                    "text": f"GO {mandate.token}",
                    "message_id": f"go-cancel-go-{winner}-{iteration}",
                }
            )
        )

    def run_cancel():
        role.name = "cancel"
        return workflow.handle(
            message.model_copy(
                update={
                    "text": f"/cancel {mandate.token}",
                    "message_id": f"go-cancel-cancel-{winner}-{iteration}",
                }
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        go_result = executor.submit(run_go)
        cancel_result = executor.submit(run_cancel)
        results = (go_result.result(timeout=10), cancel_result.result(timeout=10))

    saved = repository.get_mandate_by_token(mandate.token)
    events = repository.list_events(mandate.mandate_id)
    if winner == "go":
        assert saved.state is MandateState.INTERVIEWING
        assert sorted(len(result.deliveries) for result in results) == [0, 6]
        assert len(repository.list_interviews(mandate.mandate_id)) == 2
        assert not any(event.event_type == "mandate.cancelled" for event in events)
    else:
        assert saved.state is MandateState.CANCELLED
        assert sorted(len(result.deliveries) for result in results) == [0, 1]
        assert repository.list_interviews(mandate.mandate_id) == []
        assert not any(event.event_type == "engagement.plan_released" for event in events)


@pytest.mark.parametrize("iteration", range(2))
@pytest.mark.parametrize("winner", ["release", "expiry"])
def test_file_release_and_expiry_race_preserves_one_whole_aggregate(
    tmp_path, incoming_message_factory, now, monkeypatch, iteration, winner
) -> None:
    repository, workflow = _file_mixed_race_workflow(
        tmp_path,
        f"release-expiry-{winner}-{iteration}",
        Settings(
            _env_file=None,
            engagement_require_go=True,
            mandate_timeout_seconds=30,
        ),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"release-expiry-create-{winner}-{iteration}",
        received_at=now,
    )
    role = _order_stale_transaction_race(
        repository,
        monkeypatch,
        winner=winner,
    )

    def run_release():
        role.name = "release"
        return workflow.handle(
            message.model_copy(
                update={
                    "text": f"GO {mandate.token}",
                    "message_id": f"release-expiry-go-{winner}-{iteration}",
                    "received_at": mandate.expires_at - timedelta(seconds=1),
                }
            )
        )

    def run_expiry():
        role.name = "expiry"
        return workflow.process_due(mandate.expires_at)

    with ThreadPoolExecutor(max_workers=2) as executor:
        release_result = executor.submit(run_release)
        expiry_result = executor.submit(run_expiry)
        results = (release_result.result(timeout=10), expiry_result.result(timeout=10))

    saved = repository.get_mandate_by_token(mandate.token)
    events = repository.list_events(mandate.mandate_id)
    if winner == "release":
        assert saved.state is MandateState.INTERVIEWING
        assert sorted(len(result.deliveries) for result in results) == [0, 6]
        assert len(repository.list_interviews(mandate.mandate_id)) == 2
        assert not any(event.event_type == "mandate.expired" for event in events)
    else:
        assert saved.state is MandateState.EXPIRED
        assert sorted(len(result.deliveries) for result in results) == [0, 1]
        assert repository.list_interviews(mandate.mandate_id) == []
        assert not any(event.event_type == "engagement.plan_released" for event in events)


@pytest.mark.parametrize("iteration", range(2))
@pytest.mark.parametrize("winner", ["override", "release"])
def test_file_override_and_release_race_never_exposes_a_half_updated_plan(
    tmp_path, incoming_message_factory, now, monkeypatch, iteration, winner
) -> None:
    repository, workflow = _file_mixed_race_workflow(
        tmp_path,
        f"override-release-{winner}-{iteration}",
        Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"override-release-create-{winner}-{iteration}",
        received_at=now,
    )
    role = _order_stale_transaction_race(
        repository,
        monkeypatch,
        winner=winner,
    )

    def run_override():
        role.name = "override"
        return workflow.handle(
            message.model_copy(
                update={
                    "text": f"ENGAGE {mandate.token} inform-person ACKNOWLEDGE",
                    "message_id": f"override-release-engage-{winner}-{iteration}",
                }
            )
        )

    def run_release():
        role.name = "release"
        return workflow.handle(
            message.model_copy(
                update={
                    "text": f"GO {mandate.token}",
                    "message_id": f"override-release-go-{winner}-{iteration}",
                }
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        override_result = executor.submit(run_override)
        release_result = executor.submit(run_release)
        results = (override_result.result(timeout=10), release_result.result(timeout=10))

    saved = repository.get_mandate_by_token(mandate.token)
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "inform-person"
    )
    planned = next(
        item
        for item in saved.plan.stakeholders
        if item.person_ref == "inform-person"
    )
    events = repository.list_events(mandate.mandate_id)
    assert assignment.engagement_type is planned.engagement_type
    assert assignment.response_required is planned.response_required
    if winner == "override":
        assert saved.state is MandateState.PLANNED
        assert assignment.engagement_type is EngagementType.ACKNOWLEDGE
        assert sorted(len(result.deliveries) for result in results) == [0, 1]
        assert repository.list_interviews(mandate.mandate_id) == []
        assert not any(event.event_type == "engagement.plan_released" for event in events)
    else:
        assert saved.state is MandateState.INTERVIEWING
        assert assignment.engagement_type is EngagementType.INFORM
        assert sorted(len(result.deliveries) for result in results) == [0, 6]
        assert not any(event.event_type == "engagement.override_recorded" for event in events)


@pytest.mark.parametrize("iteration", range(2))
def test_file_two_override_race_records_exactly_one_safe_change(
    tmp_path, incoming_message_factory, now, monkeypatch, iteration
) -> None:
    repository, workflow = _file_mixed_race_workflow(
        tmp_path,
        f"two-overrides-{iteration}",
        Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"two-overrides-create-{iteration}",
        received_at=now,
    )
    role = _order_stale_transaction_race(
        repository,
        monkeypatch,
        winner="first",
    )

    def run_override(name: str):
        role.name = name
        return workflow.handle(
            message.model_copy(
                update={
                    "text": f"ENGAGE {mandate.token} inform-person ACKNOWLEDGE",
                    "message_id": f"two-overrides-{name}-{iteration}",
                }
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run_override, "first")
        second = executor.submit(run_override, "second")
        results = (first.result(timeout=10), second.result(timeout=10))

    saved = repository.get_mandate_by_token(mandate.token)
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "inform-person"
    )
    override_events = [
        event
        for event in repository.list_events(mandate.mandate_id)
        if event.event_type == "engagement.override_recorded"
    ]
    assert saved.state is MandateState.PLANNED
    assert assignment.engagement_type is EngagementType.ACKNOWLEDGE
    assert sorted(len(result.deliveries) for result in results) == [0, 1]
    assert len(override_events) == 1
    assert override_events[0].metadata == {
        "old_engagement_type": "inform",
        "new_engagement_type": "acknowledge",
    }


@pytest.mark.parametrize("iteration", range(2))
def test_file_repeated_due_scan_race_releases_once(
    tmp_path, incoming_message_factory, now, monkeypatch, iteration
) -> None:
    repository, workflow = _file_mixed_race_workflow(
        tmp_path,
        f"repeated-due-{iteration}",
        Settings(_env_file=None, engagement_preview_seconds=0),
    )
    _, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id=f"repeated-due-create-{iteration}",
        received_at=now,
    )
    role = _order_stale_transaction_race(
        repository,
        monkeypatch,
        winner="due-0",
        participant_count=4,
    )

    def run_due(index: int):
        role.name = f"due-{index}"
        return workflow.process_due(now)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(run_due, index) for index in range(4)]
        results = [future.result(timeout=10) for future in futures]

    assert sorted(len(result.deliveries) for result in results) == [0, 0, 0, 6]
    assert repository.get_mandate_by_token(mandate.token).state is MandateState.INTERVIEWING
    assert len(repository.list_interviews(mandate.mandate_id)) == 2
    assert sum(
        event.event_type == "engagement.plan_released"
        for event in repository.list_events(mandate.mandate_id)
    ) == 1


def test_process_due_delegates_all_six_types_to_the_shared_coordinator(
    repository, incoming_message_factory, now, monkeypatch
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="shared-due-create",
        received_at=now,
    )
    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "shared-due-go",
            }
        )
    )
    for delivery in released.deliveries:
        delivery_id = hashlib.sha256(delivery.message_id.encode()).hexdigest()[:48]
        with repository.transaction() as unit:
            assert unit.complete_release_outbox(
                delivery.assignment_id,
                delivery_id,
                now,
                claim_owner=delivery.dispatch_claim_id,
            )
    assignments = repository.list_assignments(mandate.mandate_id)
    for assignment in assignments:
        repository.save_assignment(
            assignment.model_copy(update={"next_action_at": now})
        )
    delegated = []

    def capture_due(assignment, at):
        delegated.append((assignment.assignment_id, assignment.engagement_type, at))
        return WorkflowResult()

    monkeypatch.setattr(workflow.engagements, "process_due_assignment", capture_due)

    workflow.process_due(now)

    assert {item[0] for item in delegated} == {
        assignment.assignment_id for assignment in assignments
    }
    assert {item[1] for item in delegated} == set(EngagementType)
    assert {item[2] for item in delegated} == {now}


def test_delivery_callbacks_for_all_six_types_use_the_shared_coordinator(
    repository, incoming_message_factory, now, monkeypatch
) -> None:
    workflow = _build_mixed_workflow(
        repository,
        settings=Settings(_env_file=None, engagement_require_go=True),
    )
    message, mandate, _ = _mixed_preview(
        workflow,
        incoming_message_factory,
        message_id="shared-callback-create",
        received_at=now,
    )
    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "shared-callback-go",
            }
        )
    )
    delegated = []

    def capture_success(assignment_id, delivery_id, at, *, claim_owner=None):
        delegated.append((assignment_id, delivery_id, at, claim_owner))

    monkeypatch.setattr(workflow.engagements, "mark_delivery_success", capture_success)

    for delivery in released.deliveries:
        workflow.mark_delivery_result(delivery, succeeded=True, now=now)

    assignments = repository.list_assignments(mandate.mandate_id)
    assert {item[0] for item in delegated} == {
        assignment.assignment_id for assignment in assignments
    }
    assert all(item[1] for item in delegated)
    assert {item[2] for item in delegated} == {now}
    assert {item[3] for item in delegated} == {
        delivery.dispatch_claim_id for delivery in released.deliveries
    }


def test_manager_mandate_creates_three_routes_and_real_deliveries(
    workflow, telegram_mandate, repository
) -> None:
    """Break caught: creation skips a direction, atomic state transition, or outreach."""
    preview = workflow.handle(telegram_mandate)
    mandate = repository.list_recent_mandates(1)[0]
    released = workflow.handle(
        telegram_mandate.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "manager-mandate-go",
            }
        )
    )
    assignments = repository.list_assignments(mandate.mandate_id)

    assert repository.get_mandate_by_token(mandate.token).state is MandateState.INTERVIEWING
    assert {item.direction for item in assignments} == {Direction.DOWNWARD, Direction.LATERAL, Direction.UPWARD}
    assert len(preview.deliveries) == 1
    assert len(released.deliveries) == 4
    assert {delivery.kind for delivery in released.deliveries} == {DeliveryKind.INITIATE_EMAIL}
    assert {event.new_state for event in repository.list_events(mandate.mandate_id)} >= {"received", "planned", "interviewing"}


def test_duplicate_incoming_mandate_returns_existing_state_without_second_outreach(
    workflow, telegram_mandate, repository
) -> None:
    """Break caught: a retry creates another mandate or repeats side effects."""
    first = workflow.handle(telegram_mandate)
    second = workflow.handle(telegram_mandate)

    assert len(repository.list_recent_mandates()) == 1
    assert len(first.deliveries) == 1
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
    assert first.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
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
    assert len(first.deliveries) == 1
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


def test_scheduling_availability_different_count_conflicting_replay_is_inert(
    repository, incoming_message_factory
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow,
        incoming_message_factory,
        message_id="schedule-count-conflict-create",
    )
    _move_to_scheduling(repository, mandate)
    first_window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"
    message = _message_for(
        incoming_message_factory,
        "team-lead",
        f"AVAILABLE {mandate.token} {first_window}",
        message_id="schedule-count-conflicting-availability",
        conversation_id="lead-thread",
    )
    workflow.handle(message)
    before = _terminal_snapshot(repository, mandate)
    conflicting = message.model_copy(
        update={
            "text": (
                f"AVAILABLE {mandate.token} {first_window} "
                "2026-08-13T15:00:00+00:00/2026-08-13T16:00:00+00:00"
            )
        }
    )

    result = workflow.handle(conflicting)

    assert result == WorkflowResult()
    assert _terminal_snapshot(repository, mandate) == before


def test_scheduling_availability_changed_receipt_time_replay_is_inert(
    repository, incoming_message_factory
) -> None:
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow,
        incoming_message_factory,
        message_id="schedule-received-at-conflict-create",
    )
    _move_to_scheduling(repository, mandate)
    window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"
    message = _message_for(
        incoming_message_factory,
        "team-lead",
        f"AVAILABLE {mandate.token} {window}",
        message_id="schedule-received-at-conflicting-availability",
        conversation_id="lead-thread",
    )
    workflow.handle(message)
    before = _terminal_snapshot(repository, mandate)
    replay = message.model_copy(
        update={"received_at": message.received_at + timedelta(seconds=1)}
    )

    result = workflow.handle(replay)

    assert result == WorkflowResult()
    assert _terminal_snapshot(repository, mandate) == before


def test_file_scheduling_concurrent_conflicting_replay_preserves_first_windows(
    tmp_path, incoming_message_factory, monkeypatch
) -> None:
    database_path = tmp_path / "scheduling-conflicting-replay.sqlite3"
    factory = create_session_factory(f"sqlite:///{database_path.as_posix()}")
    with factory.kw["bind"].begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA busy_timeout=5000")
    repository = SqlAlchemyHumanWireRepository(factory)
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow,
        incoming_message_factory,
        message_id="schedule-concurrent-conflict-create",
    )
    _move_to_scheduling(repository, mandate)
    first_window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"
    first = _message_for(
        incoming_message_factory,
        "team-lead",
        f"AVAILABLE {mandate.token} {first_window}",
        message_id="schedule-concurrent-conflicting-availability",
        conversation_id="lead-thread",
    )
    conflicting = first.model_copy(
        update={
            "text": (
                f"AVAILABLE {mandate.token} "
                "2026-08-13T15:00:00+00:00/2026-08-13T16:00:00+00:00 "
                "2026-08-14T15:00:00+00:00/2026-08-14T16:00:00+00:00"
            )
        }
    )
    original_transaction = repository.transaction
    conflicting_validated = threading.Event()
    first_committed = threading.Event()
    role = threading.local()

    @contextmanager
    def first_wins_transaction():
        if getattr(role, "name", None) == "first":
            assert conflicting_validated.wait(timeout=5)
        elif getattr(role, "name", None) == "conflicting":
            conflicting_validated.set()
            assert first_committed.wait(timeout=5)
        with original_transaction() as unit:
            yield unit
        if getattr(role, "name", None) == "first":
            first_committed.set()

    monkeypatch.setattr(repository, "transaction", first_wins_transaction)

    def record_first():
        role.name = "first"
        return workflow.handle(first)

    def record_conflicting():
        role.name = "conflicting"
        return workflow.handle(conflicting)

    with ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="scheduling-conflict",
    ) as executor:
        first_future = executor.submit(record_first)
        conflicting_future = executor.submit(record_conflicting)
        results = [
            first_future.result(timeout=10),
            conflicting_future.result(timeout=10),
        ]

    recorded = [
        event
        for event in repository.list_events(mandate.mandate_id)
        if event.event_type == "availability.recorded"
    ]
    stored = repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:team-lead"
    )
    assert results == [WorkflowResult(), WorkflowResult()]
    assert stored is not None and stored[0] == first_window
    assert len(recorded) == 1
    assert recorded[0].metadata == {"attempt_count": 1}


@pytest.mark.parametrize("terminal_state", [MandateState.CANCELLED, MandateState.EXPIRED])
@pytest.mark.parametrize("boundary", ["before_input", "before_package"])
def test_file_final_scheduling_availability_cannot_resurrect_terminal_mandate(
    tmp_path,
    incoming_message_factory,
    monkeypatch,
    terminal_state,
    boundary,
) -> None:
    database_path = tmp_path / f"scheduling-{terminal_state.value}-{boundary}.sqlite3"
    factory = create_session_factory(f"sqlite:///{database_path.as_posix()}")
    with factory.kw["bind"].begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA busy_timeout=5000")
    repository = SqlAlchemyHumanWireRepository(factory)
    workflow = _build_workflow(repository, lead_email_thread="lead-thread")
    mandate, _ = _create_mandate(
        workflow,
        incoming_message_factory,
        message_id=f"scheduling-terminal-{terminal_state.value}-{boundary}-create",
    )
    _move_to_scheduling(repository, mandate)
    window = "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00"
    manager_message = _message_for(
        incoming_message_factory,
        "manager",
        f"AVAILABLE {mandate.token} {window}",
        message_id=f"scheduling-terminal-{terminal_state.value}-{boundary}-manager",
    )
    assert workflow.handle(manager_message) == WorkflowResult()

    final_message = _message_for(
        incoming_message_factory,
        "team-lead",
        f"AVAILABLE {mandate.token} {window}",
        message_id=f"scheduling-terminal-{terminal_state.value}-{boundary}-lead",
        conversation_id="lead-thread",
    )
    original_transaction = repository.transaction
    response_at_boundary = threading.Event()
    input_committed = threading.Event()
    terminal_committed = threading.Event()
    role = threading.local()

    @contextmanager
    def terminal_first_transaction():
        if getattr(role, "name", None) != "response":
            with original_transaction() as unit:
                yield unit
            return

        transaction_number = getattr(role, "transaction_number", 0) + 1
        role.transaction_number = transaction_number
        target_transaction = 1 if boundary == "before_input" else 2
        if transaction_number == target_transaction:
            if boundary == "before_package":
                assert input_committed.is_set()
            response_at_boundary.set()
            assert terminal_committed.wait(timeout=5)
        with original_transaction() as unit:
            yield unit
        if boundary == "before_package" and transaction_number == 1:
            input_committed.set()

    monkeypatch.setattr(repository, "transaction", terminal_first_transaction)

    def record_final_availability():
        role.name = "response"
        return workflow.handle(final_message)

    def commit_terminal_state():
        assert response_at_boundary.wait(timeout=5)
        current = repository.get_mandate_by_token(mandate.token)
        assert current is not None
        updates = {
            "state": terminal_state,
            "updated_at": final_message.received_at + timedelta(seconds=1),
            "completed_at": final_message.received_at + timedelta(seconds=1),
        }
        if terminal_state is MandateState.EXPIRED:
            updates["expires_at"] = final_message.received_at
        with original_transaction() as unit:
            unit.save_mandate(current.model_copy(update=updates))
        terminal_committed.set()

    with ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix=f"scheduling-terminal-{boundary}",
    ) as executor:
        response_future = executor.submit(record_final_availability)
        terminal_future = executor.submit(commit_terminal_state)
        result = response_future.result(timeout=10)
        terminal_future.result(timeout=10)

    saved = repository.get_mandate_by_token(mandate.token)
    events = repository.list_events(mandate.mandate_id)
    lead_input_events = [
        event
        for event in events
        if event.event_type == "availability.recorded"
        and event.actor_id == "team-lead"
    ]
    lead_status = repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:team-lead"
    )
    assert result == WorkflowResult()
    assert saved is not None and saved.state is terminal_state
    assert repository.get_meeting_package(mandate.mandate_id) is None
    assert not {
        "meeting.package_created",
        "mandate.meeting_ready",
    }.intersection(event.event_type for event in events)
    if boundary == "before_input":
        assert lead_status is None
        assert lead_input_events == []
    else:
        assert lead_status is not None and lead_status[0] == window
        assert len(lead_input_events) == 1

    before_replay = _terminal_snapshot(repository, mandate)
    replay = workflow.handle(final_message)

    assert replay == WorkflowResult()
    assert repository.get_meeting_package(mandate.mandate_id) is None
    assert _terminal_snapshot(repository, mandate) == before_replay


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
    mandate, released = _create_mandate(workflow, incoming_message_factory)
    for delivery in released.deliveries:
        workflow.mark_delivery_result(delivery, succeeded=True, now=now)
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


def test_release_transaction_rolls_back_every_row_when_interview_persistence_fails(
    repository, incoming_message_factory, monkeypatch
) -> None:
    """Break caught: a failed interview insert leaks part of a release batch."""
    workflow = _build_workflow(repository)
    message = incoming_message_factory(
        text="/mandate\nCoordinate launch coverage",
        message_id="rollback-create",
    )
    workflow.handle(message)
    mandate = repository.list_recent_mandates(1)[0]
    before = _terminal_snapshot(repository, mandate)
    original = RepositoryUnitOfWork.add_interview

    def fail_after_interview_is_staged(self, interview):
        original(self, interview)
        raise RuntimeError("injected interview persistence failure")

    monkeypatch.setattr(RepositoryUnitOfWork, "add_interview", fail_after_interview_is_staged)
    released = workflow.handle(
        message.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "rollback-release",
            }
        )
    )

    assert released == WorkflowResult()
    assert _terminal_snapshot(repository, mandate) == before


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
    assert MandateState.PLANNED.value in result.deliveries[0].text
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
    workflow.handle(
        telegram_mandate.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "aligned-synthesis-go",
            }
        )
    )
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
    workflow.handle(telegram_mandate)
    mandate = repository.list_recent_mandates(1)[0]
    created = workflow.handle(
        telegram_mandate.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "primary-failure-go",
            }
        )
    )
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
    workflow.handle(telegram_mandate)
    mandate = repository.list_recent_mandates(1)[0]
    created = workflow.handle(
        telegram_mandate.model_copy(
            update={
                "text": f"GO {mandate.token}",
                "message_id": "final-failure-go",
            }
        )
    )
    primary = next(
        item
        for item in created.deliveries
        if item.assignment_id is not None and item.recipient == "lead@example.test"
    )
    alternate = workflow.mark_delivery_result(primary, succeeded=False, now=now).deliveries[0]
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
