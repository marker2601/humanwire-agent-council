"""Type-aware stakeholder outreach with interview delegation where questions are required."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from humanwire.commands import AcknowledgeCommand, parse_command
from humanwire.config import Settings
from humanwire.directory import AmbiguousPersonError, OrganizationDirectory, UnknownPersonError
from humanwire.domain import (
    Channel,
    ContactRoute,
    DeliveryInstruction,
    DeliveryKind,
    DomainEvent,
    EngagementType,
    IncomingMessage,
    InterviewSession,
    StakeholderAssignment,
    StakeholderState,
    WorkflowResult,
)
from humanwire.evidence import EvidenceExtractor
from humanwire.interviews import (
    InterviewCoordinator,
    _matches_current_outbound_attempt,
    _outbound_attempt,
)
from humanwire.messages import (
    render_acknowledgement_intro,
    render_channel_switch,
    render_inform_update,
    render_reminder,
    render_unreachable_notice,
)
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.state_machine import (
    ASSIGNMENT_TERMINAL_STATES,
    AssignmentCompletionProof,
    StakeholderStateMachine,
)


@dataclass(frozen=True)
class PreparedEngagement:
    assignment: StakeholderAssignment
    interview: InterviewSession | None
    events: tuple[DomainEvent, ...]
    delivery: DeliveryInstruction


class EngagementCoordinator:
    """Run the minimum persisted engagement required by each assignment."""

    def __init__(
        self,
        directory: OrganizationDirectory,
        repository: SqlAlchemyHumanWireRepository,
        state_machine: StakeholderStateMachine,
        evidence_extractor: EvidenceExtractor,
        settings: Settings,
    ) -> None:
        self.directory = directory
        self.repository = repository
        self.state_machine = state_machine
        self.settings = settings
        self.interviews = InterviewCoordinator(
            directory,
            repository,
            state_machine,
            evidence_extractor,
            settings,
        )

    def prepare_start(
        self,
        assignment: StakeholderAssignment,
        questions: list[str],
        token: str,
        summary: str,
        now: datetime,
    ) -> PreparedEngagement:
        engagement_type = assignment.engagement_type
        if engagement_type in {
            EngagementType.REVIEW_APPROVAL,
            EngagementType.AVAILABILITY,
        }:
            raise ValueError(f"{engagement_type.value} is implemented in Task 5")
        response_required = engagement_type is not EngagementType.INFORM
        if assignment.response_required is not response_required:
            raise ValueError(
                f"{engagement_type.value} requires response_required="
                f"{str(response_required).lower()}"
            )
        self._validate_questions(engagement_type, questions)
        if engagement_type in {
            EngagementType.QUICK_RESPONSE,
            EngagementType.STRUCTURED_INTERVIEW,
        }:
            updated, session, event, delivery = self.interviews.prepare_assignment_start(
                assignment,
                questions,
                token,
                summary,
                now,
            )
            return PreparedEngagement(updated, session, (event,), delivery)

        routes = self._assignment_routes(assignment)
        if not routes:
            raise ValueError("assignment has no registered route")
        route = routes[0]
        queued = self._transition(
            assignment,
            StakeholderState.CONTACT_QUEUED,
            "primary_outreach",
            now,
        )
        delivered = self._transition(
            queued,
            StakeholderState.DELIVERED,
            "primary_outreach",
            now,
        )
        if engagement_type is EngagementType.ACKNOWLEDGE:
            prepared_assignment = self._transition(
                delivered,
                StakeholderState.AWAITING_ACKNOWLEDGEMENT,
                "primary_outreach",
                now,
            )
            text = render_acknowledgement_intro(token, summary, assignment.reason)
            next_action_at = now + timedelta(seconds=self.settings.acknowledgement_seconds)
        else:
            prepared_assignment = delivered
            text = render_inform_update(token, summary, assignment.reason)
            next_action_at = None
        prepared_assignment = prepared_assignment.model_copy(
            update={
                "attempt_count": 1,
                "active_route_index": 0,
                "first_contact_at": now,
                "last_delivery_at": now,
                "next_action_at": next_action_at,
            }
        )
        delivery_source, delivery_metadata = _outbound_attempt(
            prepared_assignment,
            route,
            attempt=prepared_assignment.attempt_count,
            route_index=prepared_assignment.active_route_index,
        )
        event = self._event(
            "outreach.primary_sent",
            prepared_assignment,
            assignment,
            now,
            f"engagement:{assignment.assignment_id}:outreach.primary_sent:1",
            delivery_metadata,
            channel=route.channel,
        )
        return PreparedEngagement(
            prepared_assignment,
            None,
            (event,),
            self._route_delivery(
                route,
                text,
                prepared_assignment,
                message_id=delivery_source,
            ),
        )

    def persist_prepared(self, prepared: PreparedEngagement) -> None:
        """Persist a prepared assignment, optional session, and events as one unit."""
        with self.repository.transaction() as unit:
            unit.save_assignment(prepared.assignment)
            if prepared.interview is not None:
                unit.add_interview(prepared.interview)
            for event in prepared.events:
                unit.append_event(prepared.assignment.mandate_id, event)

    def process_due_assignment(
        self, assignment: StakeholderAssignment, now: datetime
    ) -> WorkflowResult:
        saved = self.repository.get_assignment(assignment.assignment_id)
        if saved is None or saved.state in ASSIGNMENT_TERMINAL_STATES:
            return WorkflowResult()
        if saved.engagement_type is EngagementType.INFORM:
            return WorkflowResult()
        if saved.engagement_type in {
            EngagementType.REVIEW_APPROVAL,
            EngagementType.AVAILABILITY,
        }:
            return WorkflowResult()
        if saved.engagement_type in {
            EngagementType.QUICK_RESPONSE,
            EngagementType.STRUCTURED_INTERVIEW,
        }:
            return self.interviews.process_due_assignment(saved, now)
        if saved.next_action_at is not None and saved.next_action_at > now:
            return WorkflowResult()

        routes = self._assignment_routes(saved)
        if not routes:
            return self._mark_unreachable(saved, now, "no_registered_route", delivery=True)
        if saved.attempt_count == 1:
            reminder = self._transition(
                saved,
                StakeholderState.FOLLOW_UP_DUE,
                "acknowledgement_reminder",
                now,
            ).model_copy(
                update={
                    "attempt_count": 2,
                    "last_delivery_at": now,
                    "next_action_at": now + timedelta(seconds=self.settings.reminder_seconds),
                }
            )
            route = routes[min(saved.active_route_index, len(routes) - 1)]
            delivery_source, delivery_metadata = _outbound_attempt(
                reminder,
                route,
                attempt=reminder.attempt_count,
                route_index=reminder.active_route_index,
            )
            reminder_event = self._event(
                "outreach.reminder_sent",
                reminder,
                saved,
                now,
                f"engagement:{saved.assignment_id}:outreach.reminder_sent:2",
                delivery_metadata,
                channel=route.channel,
            )
            if not self._save_assignment_events(saved, reminder, (reminder_event,)):
                return WorkflowResult()
            return WorkflowResult(
                deliveries=[
                    self._route_delivery(
                        route,
                        render_reminder(self._token(saved), saved.engagement_type),
                        reminder,
                        message_id=delivery_source,
                    )
                ]
            )
        if saved.attempt_count == 2:
            alternate_index = saved.active_route_index + 1
            if alternate_index >= len(routes):
                return self._mark_unreachable(
                    saved,
                    now,
                    "no_alternate_registered_route",
                    delivery=False,
                )
            route = routes[alternate_index]
            alternate = self._transition(
                saved,
                StakeholderState.ALTERNATE_CHANNEL,
                "alternate_outreach",
                now,
            ).model_copy(
                update={
                    "attempt_count": 3,
                    "active_route_index": alternate_index,
                    "last_delivery_at": now,
                    "next_action_at": now
                    + timedelta(seconds=self.settings.acknowledgement_seconds),
                }
            )
            delivery_source, delivery_metadata = _outbound_attempt(
                alternate,
                route,
                attempt=alternate.attempt_count,
                route_index=alternate.active_route_index,
            )
            alternate_event = self._event(
                "outreach.alternate_sent",
                alternate,
                saved,
                now,
                f"engagement:{saved.assignment_id}:outreach.alternate_sent:3",
                delivery_metadata,
                channel=route.channel,
            )
            if not self._save_assignment_events(saved, alternate, (alternate_event,)):
                return WorkflowResult()
            return WorkflowResult(
                deliveries=[
                    self._route_delivery(
                        route,
                        render_channel_switch(
                            self._token(saved), "", "", 0, saved.engagement_type
                        ),
                        alternate,
                        message_id=delivery_source,
                    )
                ]
            )
        return self._mark_unreachable(saved, now, "no_acknowledgement", delivery=False)

    def acknowledge(
        self,
        message: IncomingMessage,
        assignment: StakeholderAssignment,
        now: datetime,
    ) -> WorkflowResult:
        saved = self.repository.get_assignment(assignment.assignment_id)
        if saved is None or saved.state in ASSIGNMENT_TERMINAL_STATES:
            return WorkflowResult()
        if saved.engagement_type in {
            EngagementType.QUICK_RESPONSE,
            EngagementType.STRUCTURED_INTERVIEW,
        }:
            return self.interviews.acknowledge(message, saved, now)
        if saved.engagement_type is not EngagementType.ACKNOWLEDGE:
            return WorkflowResult()
        if not message.conversation_id.strip():
            return WorkflowResult()
        parsed = parse_command(message.text)
        if not isinstance(parsed, AcknowledgeCommand) or parsed.token != self._token(saved):
            return WorkflowResult()
        route = self._active_message_route(message, saved)
        if route is None:
            return WorkflowResult()
        key = f"engagement:{saved.assignment_id}:ack:{message.message_id}"
        if self._event_exists(saved, key):
            return WorkflowResult()

        acknowledged = saved
        if acknowledged.state in {
            StakeholderState.DELIVERED,
            StakeholderState.FOLLOW_UP_DUE,
            StakeholderState.ALTERNATE_CHANNEL,
        }:
            acknowledged = self._transition(
                acknowledged,
                StakeholderState.AWAITING_ACKNOWLEDGEMENT,
                "authenticated_acknowledgement_received",
                now,
            )
        if acknowledged.state is not StakeholderState.AWAITING_ACKNOWLEDGEMENT:
            return WorkflowResult()
        acknowledged = self._transition(
            acknowledged,
            StakeholderState.ACKNOWLEDGED,
            "stakeholder_acknowledged",
            now,
        ).model_copy(update={"acknowledged_at": now})
        completed = self._transition(
            acknowledged,
            StakeholderState.COMPLETE,
            "acknowledgement_complete",
            now,
            completion_proof=AssignmentCompletionProof.AUTHENTICATED_ACKNOWLEDGEMENT,
        )
        event = self._event(
            "stakeholder.acknowledged",
            completed,
            saved,
            now,
            key,
            {},
            channel=message.channel,
        )
        if not self._save_assignment_events(saved, completed, (event,)):
            return WorkflowResult()
        return WorkflowResult()

    def record_answer(
        self,
        message: IncomingMessage,
        assignment: StakeholderAssignment,
        now: datetime,
    ) -> WorkflowResult:
        saved = self.repository.get_assignment(assignment.assignment_id)
        if saved is None or saved.engagement_type not in {
            EngagementType.QUICK_RESPONSE,
            EngagementType.STRUCTURED_INTERVIEW,
        }:
            return WorkflowResult()
        return self.interviews.record_answer(message, saved, now)

    def mark_delivery_success(
        self, assignment_id: UUID, delivery_id: str, now: datetime
    ) -> None:
        assignment = self.repository.get_assignment(assignment_id)
        if assignment is None:
            raise KeyError(str(assignment_id))
        if assignment.engagement_type in {
            EngagementType.QUICK_RESPONSE,
            EngagementType.STRUCTURED_INTERVIEW,
        }:
            self.interviews.mark_delivery_success(assignment_id, delivery_id, now)
            return
        if assignment.engagement_type in {
            EngagementType.REVIEW_APPROVAL,
            EngagementType.AVAILABILITY,
        } or assignment.state in ASSIGNMENT_TERMINAL_STATES:
            return
        allowed_states = (
            {StakeholderState.DELIVERED, StakeholderState.ALTERNATE_CHANNEL}
            if assignment.engagement_type is EngagementType.INFORM
            else {
                StakeholderState.AWAITING_ACKNOWLEDGEMENT,
                StakeholderState.FOLLOW_UP_DUE,
                StakeholderState.ALTERNATE_CHANNEL,
            }
        )
        if assignment.state not in allowed_states:
            return
        routes = self._assignment_routes(assignment)
        if assignment.active_route_index >= len(routes):
            return
        route = routes[assignment.active_route_index]
        if not _matches_current_outbound_attempt(
            self.repository,
            assignment,
            route,
            delivery_id,
        ):
            return
        key = self._delivery_result_key(assignment_id, delivery_id)

        updated = assignment
        if assignment.engagement_type is EngagementType.INFORM:
            if updated.state is StakeholderState.ALTERNATE_CHANNEL:
                updated = self._transition(
                    updated,
                    StakeholderState.DELIVERED,
                    "alternate_delivery_confirmed",
                    now,
                )
            if updated.state is not StakeholderState.DELIVERED:
                return
            updated = self._transition(
                updated,
                StakeholderState.COMPLETE,
                "delivery_confirmed",
                now,
                completion_proof=AssignmentCompletionProof.DELIVERY_CONFIRMED,
            )
        event = self._event(
            "outreach.delivery_confirmed",
            updated,
            assignment,
            now,
            key,
            {"delivery_id": delivery_id, "outcome": "success"},
        )
        self._save_assignment_events(assignment, updated, (event,))

    def mark_delivery_failure(
        self, assignment_id: UUID, delivery_id: str, now: datetime
    ) -> WorkflowResult:
        assignment = self.repository.get_assignment(assignment_id)
        if assignment is None:
            raise KeyError(str(assignment_id))
        if assignment.engagement_type in {
            EngagementType.QUICK_RESPONSE,
            EngagementType.STRUCTURED_INTERVIEW,
        }:
            return self.interviews.mark_delivery_failure(assignment_id, delivery_id, now)
        if assignment.engagement_type in {
            EngagementType.REVIEW_APPROVAL,
            EngagementType.AVAILABILITY,
        } or assignment.state in ASSIGNMENT_TERMINAL_STATES:
            return WorkflowResult()
        allowed_states = (
            {StakeholderState.DELIVERED, StakeholderState.ALTERNATE_CHANNEL}
            if assignment.engagement_type is EngagementType.INFORM
            else {
                StakeholderState.AWAITING_ACKNOWLEDGEMENT,
                StakeholderState.FOLLOW_UP_DUE,
                StakeholderState.ALTERNATE_CHANNEL,
            }
        )
        if assignment.state not in allowed_states:
            return WorkflowResult()
        routes = self._assignment_routes(assignment)
        if assignment.active_route_index >= len(routes):
            return WorkflowResult()
        active_route = routes[assignment.active_route_index]
        if not _matches_current_outbound_attempt(
            self.repository,
            assignment,
            active_route,
            delivery_id,
        ):
            return WorkflowResult()
        key = self._delivery_result_key(assignment_id, delivery_id)
        next_index = assignment.active_route_index + 1
        failed_event = self._event(
            "outreach.delivery_failed",
            assignment,
            assignment,
            now,
            key,
            {"delivery_id": delivery_id, "outcome": "failure"},
        )
        if next_index < len(routes):
            route = routes[next_index]
            alternate_source = assignment
            if alternate_source.state is StakeholderState.ALTERNATE_CHANNEL:
                bridge = (
                    StakeholderState.DELIVERED
                    if assignment.engagement_type is EngagementType.INFORM
                    else StakeholderState.AWAITING_ACKNOWLEDGEMENT
                )
                alternate_source = self._transition(
                    alternate_source,
                    bridge,
                    "advance_registered_route",
                    now,
                )
            alternate = self._transition(
                alternate_source,
                StakeholderState.ALTERNATE_CHANNEL,
                "gateway_delivery_failed",
                now,
            ).model_copy(
                update={
                    "active_route_index": next_index,
                    "attempt_count": max(assignment.attempt_count + 1, 3),
                    "last_delivery_at": now,
                    "next_action_at": None
                    if assignment.engagement_type is EngagementType.INFORM
                    else now + timedelta(seconds=self.settings.acknowledgement_seconds),
                }
            )
            delivery_source, delivery_metadata = _outbound_attempt(
                alternate,
                route,
                attempt=alternate.attempt_count,
                route_index=alternate.active_route_index,
            )
            alternate_event = self._event(
                "outreach.alternate_sent",
                alternate,
                assignment,
                now,
                f"delivery:{assignment_id}:alternate:{delivery_id}",
                delivery_metadata,
                channel=route.channel,
            )
            if not self._save_assignment_events(
                assignment,
                alternate,
                (failed_event, alternate_event),
            ):
                return WorkflowResult()
            return WorkflowResult(
                deliveries=[
                    self._route_delivery(
                        route,
                        render_channel_switch(
                            self._token(assignment),
                            "",
                            "",
                            0,
                            assignment.engagement_type,
                        ),
                        alternate,
                        message_id=delivery_source,
                    )
                ]
            )

        failed = self._transition(
            assignment,
            StakeholderState.DELIVERY_FAILED,
            "registered_routes_exhausted",
            now,
        )
        terminal_event = self._event(
            "stakeholder.delivery_failed",
            failed,
            assignment,
            now,
            f"delivery:{assignment_id}:exhausted:{delivery_id}",
            {"reason_code": "registered_routes_exhausted"},
        )
        if not self._save_assignment_events(
            assignment,
            failed,
            (failed_event, terminal_event),
        ):
            return WorkflowResult()
        return WorkflowResult(deliveries=self._owner_notice(failed, delivery_failed=True))

    @staticmethod
    def _validate_questions(engagement_type: EngagementType, questions: list[str]) -> None:
        allowed = {
            EngagementType.INFORM: range(1),
            EngagementType.ACKNOWLEDGE: range(1),
            EngagementType.QUICK_RESPONSE: range(1, 3),
            EngagementType.STRUCTURED_INTERVIEW: range(3, 6),
        }[engagement_type]
        if len(questions) not in allowed:
            raise ValueError(
                f"{engagement_type.value} does not allow {len(questions)} questions"
            )

    def _mark_unreachable(
        self,
        assignment: StakeholderAssignment,
        now: datetime,
        reason: str,
        *,
        delivery: bool,
    ) -> WorkflowResult:
        target = StakeholderState.DELIVERY_FAILED if delivery else StakeholderState.UNREACHABLE
        updated = self._transition(assignment, target, reason, now)
        event_type = "stakeholder.delivery_failed" if delivery else "stakeholder.unreachable"
        event = self._event(
            event_type,
            updated,
            assignment,
            now,
            f"engagement:{assignment.assignment_id}:{event_type}:{assignment.attempt_count}",
            {"reason_code": reason},
        )
        if not self._save_assignment_events(assignment, updated, (event,)):
            return WorkflowResult()
        return WorkflowResult(deliveries=self._owner_notice(updated, delivery_failed=delivery))

    def _save_assignment_events(
        self,
        expected: StakeholderAssignment,
        updated: StakeholderAssignment,
        events: tuple[DomainEvent, ...],
    ) -> bool:
        try:
            with self.repository.transaction() as unit:
                if not unit.compare_and_save_assignment(expected, updated):
                    return False
                for event in events:
                    if not unit.append_event_once(updated.mandate_id, event):
                        raise ValueError("concurrent exact event already won")
        except ValueError:
            return False
        return True

    def _assignment_routes(self, assignment: StakeholderAssignment) -> list[ContactRoute]:
        allowed = set(assignment.route_ids)
        return [
            route
            for route in self.directory.ordered_routes(assignment.person_id)
            if route.route_id in allowed
        ]

    def _active_message_route(
        self,
        message: IncomingMessage,
        assignment: StakeholderAssignment,
    ) -> ContactRoute | None:
        try:
            person = self.directory.person_for_sender(message)
        except (AmbiguousPersonError, UnknownPersonError):
            return None
        if person.person_id.casefold() != assignment.person_id.casefold():
            return None
        routes = self._assignment_routes(assignment)
        if not routes:
            return None
        active = routes[min(assignment.active_route_index, len(routes) - 1)]
        if (
            active.channel is not message.channel
            or active.sender_address.casefold() != message.sender_address.casefold()
            or (
                active.conversation_id is not None
                and active.conversation_id != message.conversation_id
            )
        ):
            return None
        return active

    def _token(self, assignment: StakeholderAssignment) -> str:
        for mandate in self.repository.list_recent_mandates(limit=1000):
            if mandate.mandate_id == assignment.mandate_id:
                return mandate.token
        raise KeyError(str(assignment.mandate_id))

    def _owner_notice(
        self,
        assignment: StakeholderAssignment,
        *,
        delivery_failed: bool,
    ) -> list[DeliveryInstruction]:
        mandate = next(
            (
                item
                for item in self.repository.list_recent_mandates(limit=1000)
                if item.mandate_id == assignment.mandate_id
            ),
            None,
        )
        if mandate is None:
            return []
        try:
            owner_route = self.directory.ordered_routes(mandate.initiator_id)[0]
            stakeholder = self.directory.resolve_person(assignment.person_id)
        except (UnknownPersonError, IndexError):
            return []
        return [
            self._route_delivery(
                owner_route,
                render_unreachable_notice(
                    mandate.token,
                    stakeholder.display_name,
                    assignment.engagement_type,
                    delivery_failed=delivery_failed,
                ),
                assignment,
            )
        ]

    def _event_exists(self, assignment: StakeholderAssignment, key: str) -> bool:
        return any(
            event.idempotency_key == key
            for event in self.repository.list_events(assignment.mandate_id)
        )

    @staticmethod
    def _delivery_result_key(assignment_id: UUID, delivery_id: str) -> str:
        return f"delivery:{assignment_id}:result:{delivery_id}"

    def _transition(
        self,
        assignment: StakeholderAssignment,
        target: StakeholderState,
        reason: str,
        now: datetime,
        *,
        completion_proof: AssignmentCompletionProof | None = None,
    ) -> StakeholderAssignment:
        return self.state_machine.transition(
            assignment,
            target,
            reason,
            now,
            completion_proof=completion_proof,
        )

    @staticmethod
    def _event(
        event_type: str,
        assignment: StakeholderAssignment,
        previous: StakeholderAssignment,
        now: datetime,
        key: str,
        metadata: dict[str, int | str],
        *,
        channel: Channel | None = None,
    ) -> DomainEvent:
        return DomainEvent(
            event_type=event_type,
            created_at=now,
            idempotency_key=key,
            assignment_id=assignment.assignment_id,
            person_id=assignment.person_id,
            department=assignment.department,
            direction=assignment.direction,
            channel=channel,
            previous_state=previous.state.value,
            new_state=assignment.state.value,
            metadata=metadata,
        )

    @staticmethod
    def _route_delivery(
        route: ContactRoute,
        text: str,
        assignment: StakeholderAssignment,
        *,
        message_id: str | None = None,
    ) -> DeliveryInstruction:
        if route.channel is Channel.EMAIL:
            return DeliveryInstruction(
                kind=DeliveryKind.INITIATE_EMAIL,
                text=text,
                assignment_id=assignment.assignment_id,
                recipient=route.recipient,
                message_id=message_id,
            )
        return DeliveryInstruction(
            kind=DeliveryKind.SEND_TO_CONVERSATION,
            text=text,
            assignment_id=assignment.assignment_id,
            conversation_id=route.conversation_id,
            message_id=message_id,
        )
