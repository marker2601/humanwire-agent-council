"""Durable application services for the channel-neutral HumanWire workflow."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from humanwire.alignment import AlignmentEngine, NegotiationCoordinator
from humanwire.commands import MandateCommand
from humanwire.config import Settings
from humanwire.directory import (
    AmbiguousPersonError,
    OrganizationDirectory,
    UnknownPersonError,
)
from humanwire.domain import (
    Channel,
    DeliveryInstruction,
    DeliveryKind,
    DomainEvent,
    IncomingMessage,
    Mandate,
    MandateState,
    StakeholderAssignment,
    StakeholderState,
    WorkflowResult,
)
from humanwire.evidence import EvidenceExtractor, private_blocker_count, shareable_evidence
from humanwire.interviews import InterviewCoordinator
from humanwire.messages import render_alignment_brief, render_proposal
from humanwire.planning import MandatePlanner, PlanNeedsClarification
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.state_machine import MandateStateMachine, StakeholderStateMachine


def incoming_idempotency_key(message: IncomingMessage) -> str:
    """Return the durable identity of one received channel message."""
    source = f"{message.channel.value}|{message.message_id}|{message.connection_id}"
    return f"incoming:{hashlib.sha256(source.encode()).hexdigest()}"


def _event(
    event_type: str,
    now: datetime,
    key: str,
    *,
    actor_id: str | None = None,
    previous_state: str | None = None,
    new_state: str | None = None,
    channel: Channel | None = None,
    metadata: dict[str, object] | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        created_at=now,
        idempotency_key=key,
        actor_id=actor_id,
        previous_state=previous_state,
        new_state=new_state,
        channel=channel,
        metadata=metadata or {},
    )


class MandateService:
    """Creates a complete, persisted mandate before any delivery is attempted."""

    def __init__(
        self,
        directory: OrganizationDirectory,
        repository: SqlAlchemyHumanWireRepository,
        planner: MandatePlanner,
        evidence_extractor: EvidenceExtractor,
        settings: Settings,
    ) -> None:
        self.directory = directory
        self.repository = repository
        self.planner = planner
        self.settings = settings
        self.state_machine = MandateStateMachine()
        self.interviews = InterviewCoordinator(
            directory, repository, StakeholderStateMachine(), evidence_extractor, settings
        )

    def create(self, message: IncomingMessage, command: MandateCommand) -> WorkflowResult:
        key = incoming_idempotency_key(message)
        existing = self.repository.get_mandate_by_idempotency_key(key)
        if existing is not None:
            return WorkflowResult()
        try:
            initiator = self.directory.person_for_sender(message)
        except (UnknownPersonError, AmbiguousPersonError):
            return self._reply(message, "HumanWire can only accept mandates from a registered initiator.")
        if not self.directory.is_authorized_initiator(message):
            return self._reply(message, "You are not authorized to create a HumanWire mandate.")
        try:
            resolved = self.planner.plan(command.body, initiator)
        except PlanNeedsClarification as error:
            names = ", ".join(error.candidates[:5])
            suffix = f" Candidates: {names}." if names else ""
            return self._reply(message, f"I need clarification before contacting anyone.{suffix}")

        now = message.received_at.astimezone(UTC)
        token = f"HW-{hashlib.sha256(key.encode()).hexdigest()[:8].upper()}"
        mandate = Mandate(
            mandate_id=uuid4(), token=token, initiator_id=initiator.person_id,
            origin_channel=message.channel, origin_conversation_id=message.conversation_id,
            origin_message_id=message.message_id, redacted_request=command.body.strip(),
            objective=resolved.plan.objective, plan=resolved.plan, state=MandateState.RECEIVED,
            created_at=now, updated_at=now,
            expires_at=now + timedelta(seconds=self.settings.mandate_timeout_seconds),
            idempotency_key=key,
        )
        planned = self.state_machine.transition(mandate, MandateState.PLANNED, "plan_validated", now)
        interviewing = self.state_machine.transition(planned, MandateState.INTERVIEWING, "interviews_created", now)
        assignments: list[StakeholderAssignment] = []
        unavailable_required = False
        missing_required_people = []
        for stakeholder, person in zip(resolved.plan.stakeholders, resolved.people, strict=True):
            routes = self.directory.ordered_routes(person.person_id)
            state = StakeholderState.NOT_CONTACTED if routes else StakeholderState.DELIVERY_FAILED
            unavailable_required = unavailable_required or (stakeholder.required and not routes)
            if stakeholder.required and not routes:
                missing_required_people.append(person)
            assignments.append(
                StakeholderAssignment(
                    assignment_id=uuid4(), mandate_id=mandate.mandate_id, person_id=person.person_id,
                    department=person.department, direction=stakeholder.direction, reason=stakeholder.reason,
                    required=stakeholder.required, state=state, route_ids=[route.route_id for route in routes],
                    failure_reason="no_registered_route" if not routes else None,
                )
            )
        final_mandate = interviewing
        if unavailable_required:
            final_mandate = self.state_machine.transition(
                interviewing, MandateState.PARTIAL, "required_stakeholder_unreachable", now
            )
        prepared_outreach = []
        if not unavailable_required:
            prepared_outreach = [
                self.interviews.prepare_assignment_start(
                    assignment,
                    stakeholder.questions,
                    token,
                    mandate.objective,
                    now,
                )
                for assignment, stakeholder in zip(
                    assignments, resolved.plan.stakeholders, strict=True
                )
            ]
            assignments = [item[0] for item in prepared_outreach]
        with self.repository.transaction() as unit:
            unit.add_mandate(final_mandate)
            unit.append_event(mandate.mandate_id, _event("mandate.received", now, f"{key}:received", actor_id=initiator.person_id, new_state="received", channel=message.channel))
            unit.append_event(mandate.mandate_id, _event("mandate.planned", now, f"{key}:planned", actor_id=initiator.person_id, previous_state="received", new_state="planned"))
            unit.append_event(mandate.mandate_id, _event("mandate.interviewing", now, f"{key}:interviewing", actor_id=initiator.person_id, previous_state="planned", new_state="interviewing"))
            if resolved.fallback_reason:
                unit.append_event(mandate.mandate_id, _event("model.fallback", now, f"{key}:fallback", actor_id=initiator.person_id, metadata={"reason_code": resolved.fallback_reason}))
            for assignment in assignments:
                unit.add_assignment(assignment)
                if assignment.state is StakeholderState.DELIVERY_FAILED:
                    unit.append_event(mandate.mandate_id, _event("outreach.delivery_failed", now, f"assignment:{assignment.assignment_id}:no-route", actor_id=initiator.person_id, metadata={"assignment_id": str(assignment.assignment_id), "reason_code": "no_registered_route"}))
                    unit.append_event(
                        mandate.mandate_id,
                        DomainEvent(
                            event_type="stakeholder.delivery_failed",
                            created_at=now,
                            idempotency_key=f"assignment:{assignment.assignment_id}:delivery-failed:no-route",
                            actor_id=initiator.person_id,
                            assignment_id=assignment.assignment_id,
                            person_id=assignment.person_id,
                            department=assignment.department,
                            direction=assignment.direction,
                            previous_state=StakeholderState.NOT_CONTACTED.value,
                            new_state=StakeholderState.DELIVERY_FAILED.value,
                            metadata={"reason_code": "no_registered_route"},
                        ),
                    )
            for _, interview, event, _ in prepared_outreach:
                unit.add_interview(interview)
                unit.append_event(mandate.mandate_id, event)
            if unavailable_required:
                unit.append_event(mandate.mandate_id, _event("mandate.partial", now, f"{key}:partial", actor_id=initiator.person_id, previous_state="interviewing", new_state="partial", metadata={"reason_code": "required_stakeholder_unreachable"}))

        if unavailable_required:
            missing = ", ".join(
                f"{person.display_name} ({person.role})" for person in missing_required_people
            )
            text = (
                f"HUMANWIRE PARTIAL · {token}\n\n"
                f"Required response missing from: {missing}. "
                "No registered delivery route is available, so no agreement or approval was inferred."
            )
            deliveries = self._route_deliveries([initiator.person_id], text, token)
        else:
            deliveries = self._reply(message, f"HumanWire mandate {token} is recorded.").deliveries
        deliveries.extend(item[3] for item in prepared_outreach)
        return WorkflowResult(deliveries=deliveries)

    def _route_deliveries(
        self, person_ids: list[str], text: str, token: str
    ) -> list[DeliveryInstruction]:
        deliveries: list[DeliveryInstruction] = []
        for person_id in dict.fromkeys(person_ids):
            routes = self.directory.ordered_routes(person_id)
            if not routes:
                continue
            route = routes[0]
            if route.channel is Channel.EMAIL:
                deliveries.append(
                    DeliveryInstruction(
                        kind=DeliveryKind.INITIATE_EMAIL,
                        text=text,
                        mandate_token=token,
                        recipient=route.recipient,
                    )
                )
            else:
                deliveries.append(
                    DeliveryInstruction(
                        kind=DeliveryKind.SEND_TO_CONVERSATION,
                        text=text,
                        mandate_token=token,
                        conversation_id=route.conversation_id,
                    )
                )
        return deliveries

    @staticmethod
    def _reply(message: IncomingMessage, text: str) -> WorkflowResult:
        return WorkflowResult(deliveries=[DeliveryInstruction(kind=DeliveryKind.REPLY_TO_MESSAGE, text=text, message_id=message.message_id, conversation_id=message.conversation_id)])


class SynthesisService:
    """Runs only after authenticated interview evidence is complete and durable."""

    def __init__(
        self,
        directory: OrganizationDirectory,
        repository: SqlAlchemyHumanWireRepository,
        alignment_engine_factory: Callable[[UUID], AlignmentEngine] | None = None,
        negotiation_coordinator: NegotiationCoordinator | None = None,
    ) -> None:
        self.directory = directory
        self.repository = repository
        self.state_machine = MandateStateMachine()
        self.alignment_engine_factory = alignment_engine_factory or AlignmentEngine
        self.negotiation_coordinator = negotiation_coordinator or NegotiationCoordinator(repository)

    def run(self, mandate_id: UUID, now: datetime) -> WorkflowResult:
        mandate = next((item for item in self.repository.list_recent_mandates(1000) if item.mandate_id == mandate_id), None)
        if mandate is None or mandate.state is not MandateState.INTERVIEWING:
            return WorkflowResult()
        assignments = self.repository.list_assignments(mandate_id)
        required = [item for item in assignments if item.required]
        if not required or any(item.state not in {StakeholderState.COMPLETE, StakeholderState.DECLINED, StakeholderState.UNREACHABLE, StakeholderState.DELIVERY_FAILED} for item in required):
            return WorkflowResult()
        if any(item.state is not StakeholderState.COMPLETE for item in required):
            return self._partial(mandate, now)
        synthesizing = self.state_machine.transition(mandate, MandateState.SYNTHESIZING, "required_interviews_complete", now)
        evidence = self.repository.list_evidence(mandate_id)
        report = self.alignment_engine_factory(mandate_id).analyze(mandate.plan, shareable_evidence(evidence), assignments, private_blocker_count=private_blocker_count(evidence))
        with self.repository.transaction() as unit:
            unit.save_mandate(synthesizing)
            unit.append_event(mandate_id, _event("mandate.synthesizing", now, f"synthesis:{mandate_id}", actor_id=mandate.initiator_id, previous_state="interviewing", new_state="synthesizing"))
            for issue in report.issues:
                unit.add_issue(issue)
        if report.is_aligned:
            aligned = self.state_machine.transition(synthesizing, MandateState.ALIGNED, "alignment_complete", now)
            public_evidence = shareable_evidence(evidence)
            brief = render_alignment_brief(mandate.token, public_evidence)
            with self.repository.transaction() as unit:
                unit.save_mandate(aligned)
                unit.set_runtime_status(f"alignment-brief:{mandate_id}", brief, now)
                unit.append_event(mandate_id, _event("mandate.aligned", now, f"aligned:{mandate_id}", actor_id=mandate.initiator_id, previous_state="synthesizing", new_state="aligned"))
                unit.append_event(mandate_id, _event("alignment.brief_persisted", now, f"alignment-brief:{mandate_id}", actor_id=mandate.initiator_id))
            recipients = [mandate.initiator_id, *(item.person_id for item in required)]
            return WorkflowResult(deliveries=self._route_deliveries(recipients, brief, mandate.token))
        negotiating = self.state_machine.transition(synthesizing, MandateState.NEGOTIATING, "blocking_issues", now)
        coordinator = self.negotiation_coordinator
        proposal = coordinator.create_proposal(synthesizing, report, 1, now)
        with self.repository.transaction() as unit:
            unit.save_mandate(negotiating)
            unit.append_event(mandate_id, _event("mandate.negotiating", now, f"negotiating:{mandate_id}", actor_id=mandate.initiator_id, previous_state="synthesizing", new_state="negotiating"))
        text = render_proposal(mandate.token, proposal, shareable_evidence(evidence))
        return WorkflowResult(
            deliveries=self._route_deliveries(
                proposal.required_respondent_ids, text, mandate.token
            )
        )

    def _partial(self, mandate: Mandate, now: datetime) -> WorkflowResult:
        partial = self.state_machine.transition(mandate, MandateState.PARTIAL, "required_response_missing", now)
        missing_names = [
            self.directory.resolve_person(assignment.person_id).display_name
            for assignment in self.repository.list_assignments(mandate.mandate_id)
            if assignment.required and assignment.state is not StakeholderState.COMPLETE
        ]
        with self.repository.transaction() as unit:
            unit.save_mandate(partial)
            unit.append_event(mandate.mandate_id, _event("mandate.partial", now, f"partial:{mandate.mandate_id}", actor_id=mandate.initiator_id, previous_state="interviewing", new_state="partial", metadata={"reason_code": "required_response_missing"}))
        missing = ", ".join(missing_names) if missing_names else "a required stakeholder"
        text = (
            f"HUMANWIRE PARTIAL · {mandate.token}\n\n"
            f"Required response missing from: {missing}. "
            "No agreement or approval was inferred."
        )
        return WorkflowResult(
            deliveries=self._route_deliveries([mandate.initiator_id], text, mandate.token)
        )

    def _route_deliveries(
        self, person_ids: list[str], text: str, token: str
    ) -> list[DeliveryInstruction]:
        deliveries: list[DeliveryInstruction] = []
        for person_id in dict.fromkeys(person_ids):
            routes = self.directory.ordered_routes(person_id)
            if not routes:
                continue
            route = routes[0]
            if route.channel is Channel.EMAIL:
                deliveries.append(
                    DeliveryInstruction(
                        kind=DeliveryKind.INITIATE_EMAIL,
                        text=text,
                        mandate_token=token,
                        recipient=route.recipient,
                    )
                )
            else:
                deliveries.append(
                    DeliveryInstruction(
                        kind=DeliveryKind.SEND_TO_CONVERSATION,
                        text=text,
                        mandate_token=token,
                        conversation_id=route.conversation_id,
                    )
                )
        return deliveries
