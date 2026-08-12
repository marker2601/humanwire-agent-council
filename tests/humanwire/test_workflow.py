from uuid import uuid4

import pytest

from humanwire.config import Settings
from humanwire.database import create_session_factory
from humanwire.directory import InitiatorPolicy, OrganizationDirectory, OrganizationDocument
from humanwire.domain import (
    Channel,
    ContactRoute,
    DeliveryKind,
    Direction,
    EvidenceItem,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    MandatePlan,
    MandateState,
    Person,
    PlannedStakeholder,
    StakeholderState,
)
from humanwire.evidence import RuleBasedEvidenceExtractor
from humanwire.planning import ResolvedPlan
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.workflow import HumanWireWorkflow


class DeterministicPlanner:
    def __init__(self, people: list[Person]) -> None:
        self.people = people

    def plan(self, text: str, initiator: Person) -> ResolvedPlan:
        del text, initiator
        return ResolvedPlan(
            plan=MandatePlan(
                objective="Coordinate launch coverage",
                required_decisions=["Approve coverage"],
                stakeholders=[
                    PlannedStakeholder(
                        person_ref="team-lead", reason="Delivery input", direction=Direction.DOWNWARD,
                        questions=["What is needed?"],
                    ),
                    PlannedStakeholder(
                        person_ref="vp-people", reason="People input", direction=Direction.LATERAL,
                        questions=["What policy applies?"],
                    ),
                    PlannedStakeholder(
                        person_ref="coo", reason="Executive input", direction=Direction.UPWARD,
                        questions=["What decision is required?"],
                    ),
                    PlannedStakeholder(
                        person_ref="vp-support", reason="Support input", direction=Direction.LATERAL,
                        questions=["What support constraint applies?"],
                    ),
                ],
                completion_conditions=["All required interviews complete"],
            ),
            people=self.people,
            planner="deterministic",
        )


@pytest.fixture
def repository() -> SqlAlchemyHumanWireRepository:
    return SqlAlchemyHumanWireRepository(create_session_factory("sqlite://"))


@pytest.fixture
def workflow(repository: SqlAlchemyHumanWireRepository) -> HumanWireWorkflow:
    manager = Person(person_id="manager", display_name="Morgan Lee", role="Manager", department="Operations", timezone="UTC", routes=[ContactRoute(route_id="manager-tg", channel=Channel.TELEGRAM, sender_address="manager-chat", conversation_id="manager-conversation", preferred=True)])
    people = [
        Person(person_id="team-lead", display_name="Riley Chen", role="Lead", department="Operations", timezone="UTC", manager_id="manager", routes=[ContactRoute(route_id="lead-email", channel=Channel.EMAIL, sender_address="lead@example.test", recipient="lead@example.test", preferred=True)]),
        Person(person_id="vp-people", display_name="Avery Patel", role="VP", department="People", timezone="UTC", routes=[ContactRoute(route_id="people-email", channel=Channel.EMAIL, sender_address="people@example.test", recipient="people@example.test", preferred=True)]),
        Person(person_id="coo", display_name="Casey Nguyen", role="COO", department="Executive", timezone="UTC", routes=[ContactRoute(route_id="coo-email", channel=Channel.EMAIL, sender_address="coo@example.test", recipient="coo@example.test", preferred=True)]),
        Person(person_id="vp-support", display_name="Jordan Brooks", role="VP", department="Support", timezone="UTC", routes=[ContactRoute(route_id="support-email", channel=Channel.EMAIL, sender_address="support@example.test", recipient="support@example.test", preferred=True)]),
    ]
    directory = OrganizationDirectory(OrganizationDocument(
        people=[manager, *people],
        initiator_policies=[InitiatorPolicy(person_id="manager", allowed_directions={Direction.DOWNWARD, Direction.LATERAL, Direction.UPWARD}, allowed_departments={"Operations", "People", "Executive", "Support"})],
    ))
    return HumanWireWorkflow(directory, repository, DeterministicPlanner(people), RuleBasedEvidenceExtractor(), Settings())


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


def test_unknown_sender_cannot_create_mandate(workflow, incoming_message_factory, repository) -> None:
    """Break caught: unauthenticated senders gain mandate authority."""
    result = workflow.handle(incoming_message_factory(text="/mandate\nCoordinate Riley", sender_address="unknown"))

    assert result.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert repository.list_recent_mandates() == []


def test_only_originating_initiator_can_cancel(workflow, telegram_mandate, incoming_message_factory, repository) -> None:
    """Break caught: a stakeholder can cancel someone else's mandate."""
    workflow.handle(telegram_mandate)
    token = repository.list_recent_mandates(1)[0].token
    intruder = incoming_message_factory(text=f"/cancel {token}", sender_address="lead@example.test", channel=Channel.EMAIL, conversation_id="lead@example.test", message_id="intruder")

    workflow.handle(intruder)

    assert repository.get_mandate_by_token(token).state is MandateState.INTERVIEWING


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

    result = workflow.synthesis.run(mandate.mandate_id, now)

    assert repository.get_runtime_status(f"alignment-brief:{mandate.mandate_id}") is not None
    assert {delivery.recipient for delivery in result.deliveries if delivery.recipient} == {
        "lead@example.test", "people@example.test", "coo@example.test", "support@example.test"
    }
    assert any(delivery.conversation_id == "manager-conversation" for delivery in result.deliveries)
    assert any(event.event_type == "alignment.brief_persisted" for event in repository.list_events(mandate.mandate_id))
