"""Persisted, channel-neutral interview coordination and non-response escalation."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from humanwire.commands import AcknowledgeCommand, AvailabilityCommand, parse_command
from humanwire.config import Settings
from humanwire.directory import AmbiguousPersonError, OrganizationDirectory, UnknownPersonError
from humanwire.domain import (
    Channel,
    ContactRoute,
    DeliveryInstruction,
    DeliveryKind,
    DomainEvent,
    EngagementType,
    EvidenceVisibility,
    IncomingMessage,
    InterviewSession,
    StakeholderAssignment,
    StakeholderState,
    WorkflowResult,
)
from humanwire.evidence import EvidenceExtractor, confirm_drafts
from humanwire.messages import (
    render_channel_switch,
    render_interview_intro,
    render_question,
    render_reminder,
    render_unreachable_notice,
)
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.state_machine import (
    ASSIGNMENT_TERMINAL_STATES,
    AssignmentCompletionProof,
    StakeholderStateMachine,
)

_VISIBILITY_PREFIX = re.compile(r"^(SHAREABLE|ANONYMOUS|PRIVATE)\b[\s:—-]*", re.IGNORECASE)
_DECLINE = re.compile(r"^DECLINE\s+(HW-[A-Z0-9]{4,8})$", re.IGNORECASE)
_OUTREACH_SENT_EVENTS = frozenset(
    {"outreach.primary_sent", "outreach.reminder_sent", "outreach.alternate_sent"}
)
_AUTHENTICATED_INBOUND_CAS_ATTEMPTS = 3


def _route_fingerprint(route: ContactRoute) -> str:
    source = f"humanwire:route:v1:{route.route_id}".encode()
    return hashlib.sha256(source).hexdigest()[:32]


def _outbound_attempt(
    assignment: StakeholderAssignment,
    route: ContactRoute,
    *,
    attempt: int,
    route_index: int,
) -> tuple[str, dict[str, int | str]]:
    fingerprint = _route_fingerprint(route)
    source = hashlib.sha256(
        (
            f"humanwire:outbound:v1:{assignment.assignment_id}:"
            f"{attempt}:{route_index}:{fingerprint}"
        ).encode()
    ).hexdigest()[:48]
    delivery_id = hashlib.sha256(source.encode()).hexdigest()[:48]
    return source, {
        "attempt": attempt,
        "delivery_id": delivery_id,
        "route_fingerprint": fingerprint,
        "route_index": route_index,
    }


def _matches_current_outbound_attempt(
    repository: SqlAlchemyHumanWireRepository,
    assignment: StakeholderAssignment,
    route: ContactRoute,
    delivery_id: str,
) -> bool:
    result_key = f"delivery:{assignment.assignment_id}:result:{delivery_id}"
    for event in reversed(repository.list_events(assignment.mandate_id)):
        if event.idempotency_key == result_key:
            return False
        if (
            event.assignment_id == assignment.assignment_id
            and event.event_type in _OUTREACH_SENT_EVENTS
            and event.metadata.get("delivery_id") == delivery_id
            and event.metadata.get("attempt") == assignment.attempt_count
            and event.metadata.get("route_index") == assignment.active_route_index
            and event.metadata.get("route_fingerprint") == _route_fingerprint(route)
        ):
            return True
    return False


class InterviewCoordinator:
    """Coordinates one short interview session for each assigned stakeholder."""

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
        self.evidence_extractor = evidence_extractor
        self.settings = settings

    def start_assignment(
        self, assignment: StakeholderAssignment, questions: list[str], now: datetime
    ) -> WorkflowResult:
        """Create a bounded session once, then persist the initial outreach before returning it."""
        saved = self.repository.get_assignment(assignment.assignment_id)
        if saved is None:
            raise KeyError(str(assignment.assignment_id))
        if saved.interview_id is None:
            bounded_questions = questions[:5]
            if not bounded_questions:
                raise ValueError("interview questions are required")
            session = InterviewSession(
                session_id=uuid4(),
                mandate_id=saved.mandate_id,
                assignment_id=saved.assignment_id,
                questions=bounded_questions,
                started_at=now,
                updated_at=now,
            )
            updated = saved.model_copy(update={"interview_id": session.session_id})
            with self.repository.transaction() as unit:
                unit.add_interview(session)
                unit.save_assignment(updated)
            saved = updated
        return self.process_due_assignment(saved, now)

    def prepare_assignment_start(
        self,
        assignment: StakeholderAssignment,
        questions: list[str],
        token: str,
        summary: str,
        now: datetime,
    ) -> tuple[StakeholderAssignment, InterviewSession, DomainEvent, DeliveryInstruction]:
        """Prepare initial outreach records for a caller-owned creation transaction."""
        bounded_questions = questions[:5]
        if not bounded_questions:
            raise ValueError("interview questions are required")
        routes = self._assignment_routes(assignment)
        if not routes:
            raise ValueError("assignment has no registered route")
        session = InterviewSession(
            session_id=uuid4(),
            mandate_id=assignment.mandate_id,
            assignment_id=assignment.assignment_id,
            questions=bounded_questions,
            started_at=now,
            updated_at=now,
        )
        pending = assignment.model_copy(update={"interview_id": session.session_id})
        queued = self._transition(pending, StakeholderState.CONTACT_QUEUED, "primary_outreach", now)
        delivered = self._transition(queued, StakeholderState.DELIVERED, "primary_outreach", now)
        updated = self._transition(
            delivered,
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            "primary_outreach",
            now,
        ).model_copy(
            update={
                "attempt_count": 1,
                "active_route_index": 0,
                "first_contact_at": now,
                "last_delivery_at": now,
                "next_action_at": now + timedelta(seconds=self.settings.acknowledgement_seconds),
            }
        )
        route = routes[0]
        delivery_source, delivery_metadata = _outbound_attempt(
            updated,
            route,
            attempt=updated.attempt_count,
            route_index=updated.active_route_index,
        )
        active_session = self._activate_route(session, route, route.conversation_id, now)
        event = self._event(
            "outreach.primary_sent",
            updated,
            assignment,
            now,
            f"interview:{updated.assignment_id}:outreach.primary_sent:{updated.attempt_count}",
            delivery_metadata,
        )
        delivery = self._route_delivery(
            route,
            render_interview_intro(
                token,
                summary,
                assignment.reason,
                len(bounded_questions),
                assignment.engagement_type,
            ),
            updated,
            message_id=delivery_source,
        )
        return updated, active_session, event, delivery

    def process_due_assignment(self, assignment: StakeholderAssignment, now: datetime) -> WorkflowResult:
        """Advance exactly one persisted response-ladder step when an assignment is due."""
        saved = self.repository.get_assignment(assignment.assignment_id)
        if saved is None or saved.state in ASSIGNMENT_TERMINAL_STATES:
            return WorkflowResult()
        if saved.next_action_at is not None and saved.next_action_at > now:
            return WorkflowResult()

        routes = self._assignment_routes(saved)
        if not routes:
            failed = self._transition(saved, StakeholderState.DELIVERY_FAILED, "no_registered_route", now)
            self._save_assignment_event(
                failed,
                saved,
                "outreach.delivery_failed",
                now,
                {"reason_code": "no_registered_route"},
            )
            return WorkflowResult()

        if saved.attempt_count == 0:
            session = self._session(saved)
            route = routes[0]
            queued = self._transition(saved, StakeholderState.CONTACT_QUEUED, "primary_outreach", now)
            delivered = self._transition(queued, StakeholderState.DELIVERED, "primary_outreach", now)
            updated = self._transition(
                delivered,
                StakeholderState.AWAITING_ACKNOWLEDGEMENT,
                "primary_outreach",
                now,
            ).model_copy(
                update={
                    "attempt_count": 1,
                    "active_route_index": 0,
                    "first_contact_at": saved.first_contact_at or now,
                    "last_delivery_at": now,
                    "next_action_at": now + timedelta(seconds=self.settings.acknowledgement_seconds),
                }
            )
            updated_session = self._activate_route(session, route, route.conversation_id, now)
            delivery_source, delivery_metadata = _outbound_attempt(
                updated,
                route,
                attempt=updated.attempt_count,
                route_index=updated.active_route_index,
            )
            if not self._save_assignment_event(
                updated,
                saved,
                "outreach.primary_sent",
                now,
                delivery_metadata,
                session=updated_session,
            ):
                return WorkflowResult()
            return WorkflowResult(
                deliveries=[
                    self._route_delivery(
                        route,
                        render_interview_intro(
                            self._token(saved),
                            self._summary(saved),
                            saved.reason,
                            len(session.questions),
                            saved.engagement_type,
                        ),
                        saved,
                        message_id=delivery_source,
                    )
                ]
            )

        if saved.attempt_count == 1:
            updated = self._transition(
                saved, StakeholderState.FOLLOW_UP_DUE, "acknowledgement_reminder", now
            ).model_copy(
                update={
                    "attempt_count": 2,
                    "last_delivery_at": now,
                    "next_action_at": now + timedelta(seconds=self.settings.reminder_seconds),
                }
            )
            route = routes[min(saved.active_route_index, len(routes) - 1)]
            delivery_source, delivery_metadata = _outbound_attempt(
                updated,
                route,
                attempt=updated.attempt_count,
                route_index=updated.active_route_index,
            )
            if not self._save_assignment_event(
                updated,
                saved,
                "outreach.reminder_sent",
                now,
                delivery_metadata,
            ):
                return WorkflowResult()
            return WorkflowResult(
                deliveries=[
                    self._route_delivery(
                        route,
                        render_reminder(self._token(saved), saved.engagement_type),
                        saved,
                        message_id=delivery_source,
                    )
                ]
            )

        if saved.attempt_count == 2:
            alternate_index = saved.active_route_index + 1
            if alternate_index >= len(routes):
                unreachable = self._transition(
                    saved, StakeholderState.UNREACHABLE, "no_alternate_registered_route", now
                )
                if not self._save_assignment_event(
                    unreachable,
                    saved,
                    "stakeholder.unreachable",
                    now,
                    {"reason_code": "no_alternate_registered_route"},
                ):
                    return WorkflowResult()
                return WorkflowResult(deliveries=self._owner_notice(saved))
            route = routes[alternate_index]
            session = self._session(saved)
            updated = self._transition(
                saved, StakeholderState.ALTERNATE_CHANNEL, "alternate_outreach", now
            ).model_copy(
                update={
                    "attempt_count": 3,
                    "active_route_index": alternate_index,
                    "last_delivery_at": now,
                    "next_action_at": now + timedelta(seconds=self.settings.acknowledgement_seconds),
                }
            )
            updated_session = self._activate_route(session, route, route.conversation_id, now)
            delivery_source, delivery_metadata = _outbound_attempt(
                updated,
                route,
                attempt=updated.attempt_count,
                route_index=updated.active_route_index,
            )
            if not self._save_assignment_event(
                updated,
                saved,
                "outreach.alternate_sent",
                now,
                delivery_metadata,
                session=updated_session,
            ):
                return WorkflowResult()
            return WorkflowResult(
                deliveries=[
                    self._route_delivery(
                        route,
                        render_channel_switch(
                            self._token(saved),
                            self._summary(saved),
                            saved.reason,
                            len(session.questions),
                            saved.engagement_type,
                        ),
                        saved,
                        message_id=delivery_source,
                    )
                ]
            )

        unreachable = self._transition(saved, StakeholderState.UNREACHABLE, "no_acknowledgement", now)
        if not self._save_assignment_event(
            unreachable,
            saved,
            "stakeholder.unreachable",
            now,
            {"reason_code": "no_acknowledgement"},
        ):
            return WorkflowResult()
        return WorkflowResult(deliveries=self._owner_notice(saved))

    def acknowledge(
        self, message: IncomingMessage, assignment: StakeholderAssignment, now: datetime
    ) -> WorkflowResult:
        """Accept a correlated acknowledgement and resume the existing question on that channel."""
        if not message.conversation_id.strip():
            return WorkflowResult()
        parsed = parse_command(message.text)
        if not isinstance(parsed, AcknowledgeCommand) or parsed.token != self._token(assignment):
            return WorkflowResult()
        return self._acknowledge(
            message,
            assignment,
            now,
            send_question=True,
            retry_authenticated_cas_loss=True,
        )

    def record_answer(
        self, message: IncomingMessage, assignment: StakeholderAssignment, now: datetime
    ) -> WorkflowResult:
        """Persist only an authenticated answer; an answer can implicitly acknowledge the interview."""
        if not message.conversation_id.strip():
            return WorkflowResult()
        decline_match = _DECLINE.fullmatch(message.text.strip())
        if decline_match is not None:
            if decline_match.group(1).upper() != self._token(assignment):
                return WorkflowResult()
            return self.decline(message, assignment, now)
        parsed = parse_command(message.text)
        if isinstance(parsed, AcknowledgeCommand):
            return WorkflowResult()
        if isinstance(parsed, AvailabilityCommand) and parsed.token != self._token(assignment):
            return WorkflowResult()
        saved = self.repository.get_assignment(assignment.assignment_id)
        if saved is None or saved.state in ASSIGNMENT_TERMINAL_STATES:
            return WorkflowResult()
        route = self._message_route(message, saved)
        if route is None:
            return WorkflowResult()
        session = self._session(saved)
        explicit_token = self._contains_token(message.text, self._token(saved))
        initial_bind = self._can_bind_initial_conversation(session, saved, route, message)
        if (
            initial_bind
            and saved.interview_id is not None
            and not self.repository.bind_initial_interview_conversation(
                saved.assignment_id,
                saved.interview_id,
                route.route_id,
                message.conversation_id,
            )
        ):
            saved = self.repository.get_assignment(saved.assignment_id)
            if saved is None:
                return WorkflowResult()
            session = self._session(saved)
            if not self._is_active_correlation(session, route, message):
                return WorkflowResult()
        if (
            not self._is_active_correlation(session, route, message)
            and not initial_bind
            and not explicit_token
        ):
            return WorkflowResult()
        duplicate_key = f"interview:{saved.assignment_id}:answer:{message.message_id}"
        if any(event.idempotency_key == duplicate_key for event in self.repository.list_events(saved.mandate_id)):
            return WorkflowResult()
        if saved.state in {
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            StakeholderState.FOLLOW_UP_DUE,
            StakeholderState.ALTERNATE_CHANNEL,
        }:
            self._acknowledge(
                message,
                saved,
                now,
                send_question=False,
                retry_authenticated_cas_loss=False,
            )
            saved = self.repository.get_assignment(saved.assignment_id)
            if saved is None:
                return WorkflowResult()
        if saved.state is not StakeholderState.INTERVIEWING:
            return WorkflowResult()

        answer, visibility = self._answer_and_visibility(message.text)
        if not answer:
            return WorkflowResult()
        session = self._session(saved)
        if session.current_question_index >= len(session.questions):
            return WorkflowResult()
        evidence = confirm_drafts(
            self.evidence_extractor.extract(
                answer=answer,
                question=session.questions[session.current_question_index],
                mandate_id=saved.mandate_id,
                assignment_id=saved.assignment_id,
                stakeholder_id=saved.person_id,
                source_message_id=message.message_id,
                channel=message.channel,
                received_at=message.received_at,
                visibility=visibility,
            )
        )
        next_index = session.current_question_index + 1
        completed = next_index == len(session.questions)
        updated_session = session.model_copy(
            update={
                "current_question_index": next_index,
                "current_channel": message.channel,
                "current_route_id": route.route_id,
                "current_conversation_id": message.conversation_id,
                "channel_history": self._channel_history(session, message.channel),
                "updated_at": now,
                "completed_at": now if completed else None,
            }
        )
        updated_assignment = (
            self._transition(
                saved,
                StakeholderState.COMPLETE,
                "interview_complete",
                now,
                completion_proof=AssignmentCompletionProof.REQUIRED_RESPONSES_COMPLETE,
            )
            if completed
            else saved
        )
        event = self._event(
            "interview.answer_recorded",
            updated_assignment,
            saved,
            now,
            duplicate_key,
            {"question_index": session.current_question_index},
            channel=message.channel,
        )
        with self.repository.transaction() as unit:
            unit.save_interview(updated_session)
            unit.save_assignment(updated_assignment)
            for item in evidence:
                unit.add_evidence(item)
            unit.append_event(updated_assignment.mandate_id, event)
        if completed:
            return WorkflowResult()
        return WorkflowResult(
            deliveries=[self._reply(message, render_question(session.questions[next_index], next_index + 1, len(session.questions)), saved)]
        )

    def decline(
        self, message: IncomingMessage, assignment: StakeholderAssignment, now: datetime
    ) -> WorkflowResult:
        """Record only an explicit, authenticated decline; it never becomes interview evidence."""
        saved = self.repository.get_assignment(assignment.assignment_id)
        if saved is None or saved.state in ASSIGNMENT_TERMINAL_STATES:
            return WorkflowResult()
        if self._message_route(message, saved) is None:
            return WorkflowResult()
        key = f"interview:{saved.assignment_id}:decline:{message.message_id}"
        if any(event.idempotency_key == key for event in self.repository.list_events(saved.mandate_id)):
            return WorkflowResult()
        pending = saved
        if pending.state is StakeholderState.ALTERNATE_CHANNEL:
            pending = self._transition(
                pending, StakeholderState.AWAITING_ACKNOWLEDGEMENT, "alternate_decline", now
            )
        if pending.state is StakeholderState.FOLLOW_UP_DUE:
            pending = self._transition(
                pending, StakeholderState.AWAITING_ACKNOWLEDGEMENT, "follow_up_decline", now
            )
        if pending.state not in {
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            StakeholderState.ACKNOWLEDGED,
            StakeholderState.INTERVIEWING,
        }:
            return WorkflowResult()
        declined = self._transition(pending, StakeholderState.DECLINED, "explicit_decline", now)
        event = self._event(
            "stakeholder.declined",
            declined,
            saved,
            now,
            key,
            {"reason_code": "explicit_decline"},
            channel=message.channel,
        )
        with self.repository.transaction() as unit:
            unit.save_assignment(declined)
            unit.append_event(declined.mandate_id, event)
        return WorkflowResult()

    def mark_delivery_success(self, assignment_id: UUID, delivery_id: str, now: datetime) -> None:
        """Record a gateway-confirmed delivery without treating it as a human response."""
        assignment = self.repository.get_assignment(assignment_id)
        if assignment is None:
            raise KeyError(str(assignment_id))
        if assignment.state not in {
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            StakeholderState.FOLLOW_UP_DUE,
            StakeholderState.ALTERNATE_CHANNEL,
        }:
            return
        routes = self._assignment_routes(assignment)
        if assignment.active_route_index >= len(routes):
            return
        route = routes[assignment.active_route_index]
        if not _matches_current_outbound_attempt(
            self.repository, assignment, route, delivery_id
        ):
            return
        key = f"delivery:{assignment_id}:result:{delivery_id}"
        event = self._event(
            "outreach.delivery_confirmed",
            assignment,
            assignment,
            now,
            key,
            {"delivery_id": delivery_id, "outcome": "success"},
        )
        try:
            with self.repository.transaction() as unit:
                if not unit.compare_and_save_assignment(assignment, assignment):
                    return
                if not unit.append_event_once(assignment.mandate_id, event):
                    raise ValueError("concurrent exact delivery result already won")
        except ValueError:
            return

    def mark_delivery_failure(
        self, assignment_id: UUID, delivery_id: str, now: datetime
    ) -> WorkflowResult:
        """Advance one persisted retry-ladder step; gateway failure never implies silence."""
        assignment = self.repository.get_assignment(assignment_id)
        if assignment is None:
            raise KeyError(str(assignment_id))
        if assignment.state not in {
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            StakeholderState.FOLLOW_UP_DUE,
            StakeholderState.ALTERNATE_CHANNEL,
        }:
            return WorkflowResult()
        routes = self._assignment_routes(assignment)
        if assignment.active_route_index >= len(routes):
            return WorkflowResult()
        active_route = routes[assignment.active_route_index]
        if not _matches_current_outbound_attempt(
            self.repository, assignment, active_route, delivery_id
        ):
            return WorkflowResult()
        key = f"delivery:{assignment_id}:result:{delivery_id}"
        event = self._event(
            "outreach.delivery_failed",
            assignment,
            assignment,
            now,
            key,
            {"delivery_id": delivery_id, "outcome": "failure"},
        )
        next_index = assignment.active_route_index + 1
        if next_index < len(routes):
            route = routes[next_index]
            session = self._session(assignment)
            alternate = self._transition(
                assignment, StakeholderState.ALTERNATE_CHANNEL, "gateway_primary_failed", now
            ).model_copy(
                update={
                    "active_route_index": next_index,
                    "attempt_count": max(assignment.attempt_count + 1, 3),
                    "last_delivery_at": now,
                    "next_action_at": now + timedelta(seconds=self.settings.acknowledgement_seconds),
                }
            )
            updated_session = self._activate_route(session, route, route.conversation_id, now)
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
            )
            try:
                with self.repository.transaction() as unit:
                    if not unit.compare_and_save_assignment(assignment, alternate):
                        return WorkflowResult()
                    unit.save_interview(updated_session)
                    if not unit.append_event_once(alternate.mandate_id, event):
                        raise ValueError("concurrent exact delivery result already won")
                    if not unit.append_event_once(alternate.mandate_id, alternate_event):
                        raise ValueError("concurrent alternate outreach already won")
            except ValueError:
                return WorkflowResult()
            return WorkflowResult(
                deliveries=[
                    self._route_delivery(
                        route,
                        render_channel_switch(
                            self._token(assignment),
                            self._summary(assignment),
                            assignment.reason,
                            len(session.questions),
                            assignment.engagement_type,
                        ),
                        alternate,
                        message_id=delivery_source,
                    )
                ]
            )
        failed = self._transition(
            assignment, StakeholderState.DELIVERY_FAILED, "registered_routes_exhausted", now
        )
        terminal_event = self._event(
            "stakeholder.delivery_failed",
            failed,
            assignment,
            now,
            f"delivery:{assignment_id}:exhausted:{delivery_id}",
            {"reason_code": "registered_routes_exhausted"},
        )
        try:
            with self.repository.transaction() as unit:
                if not unit.compare_and_save_assignment(assignment, failed):
                    return WorkflowResult()
                if not unit.append_event_once(failed.mandate_id, event):
                    raise ValueError("concurrent exact delivery result already won")
                if not unit.append_event_once(failed.mandate_id, terminal_event):
                    raise ValueError("concurrent terminal delivery result already won")
        except ValueError:
            return WorkflowResult()
        return WorkflowResult(deliveries=self._owner_notice(failed))

    def _acknowledge(
        self,
        message: IncomingMessage,
        assignment: StakeholderAssignment,
        now: datetime,
        *,
        send_question: bool,
        retry_authenticated_cas_loss: bool,
    ) -> WorkflowResult:
        attempt_limit = (
            _AUTHENTICATED_INBOUND_CAS_ATTEMPTS
            if retry_authenticated_cas_loss
            else 1
        )
        retrying_after_cas_loss = False
        for _ in range(attempt_limit):
            saved = self.repository.get_assignment(assignment.assignment_id)
            if saved is None or saved.state in ASSIGNMENT_TERMINAL_STATES:
                return WorkflowResult()
            if retry_authenticated_cas_loss:
                if saved.engagement_type not in {
                    EngagementType.QUICK_RESPONSE,
                    EngagementType.STRUCTURED_INTERVIEW,
                }:
                    return WorkflowResult()
                parsed = parse_command(message.text)
                if (
                    not isinstance(parsed, AcknowledgeCommand)
                    or parsed.token != self._token(saved)
                ):
                    return WorkflowResult()
            route = self._message_route(message, saved)
            if route is None:
                return WorkflowResult()
            if retrying_after_cas_loss:
                routes = self._assignment_routes(saved)
                if (
                    saved.active_route_index >= len(routes)
                    or routes[saved.active_route_index].route_id != route.route_id
                ):
                    return WorkflowResult()
            key = f"interview:{saved.assignment_id}:ack:{message.message_id}"
            if any(
                event.idempotency_key == key
                for event in self.repository.list_events(saved.mandate_id)
            ):
                return WorkflowResult()
            acknowledged = saved
            if saved.state is StakeholderState.ALTERNATE_CHANNEL:
                acknowledged = self._transition(
                    acknowledged,
                    StakeholderState.AWAITING_ACKNOWLEDGEMENT,
                    "alternate_reply",
                    now,
                )
            if acknowledged.state is StakeholderState.DELIVERED:
                acknowledged = self._transition(
                    acknowledged,
                    StakeholderState.AWAITING_ACKNOWLEDGEMENT,
                    "delivered_reply",
                    now,
                )
            if acknowledged.state is StakeholderState.FOLLOW_UP_DUE:
                acknowledged = self._transition(
                    acknowledged,
                    StakeholderState.AWAITING_ACKNOWLEDGEMENT,
                    "follow_up_reply",
                    now,
                )
            if acknowledged.state is not StakeholderState.AWAITING_ACKNOWLEDGEMENT:
                return WorkflowResult()
            acknowledged = self._transition(
                acknowledged,
                StakeholderState.ACKNOWLEDGED,
                "stakeholder_acknowledged",
                now,
            )
            interviewing = self._transition(
                acknowledged,
                StakeholderState.INTERVIEWING,
                "interview_started",
                now,
            ).model_copy(update={"acknowledged_at": now, "next_action_at": None})
            session = self._session(saved)
            updated_session = session.model_copy(
                update={
                    "current_channel": message.channel,
                    "current_route_id": route.route_id,
                    "current_conversation_id": message.conversation_id,
                    "channel_history": self._channel_history(session, message.channel),
                    "acknowledged_at": now,
                    "updated_at": now,
                }
            )
            event = self._event(
                "stakeholder.acknowledged",
                interviewing,
                saved,
                now,
                key,
                {},
                channel=message.channel,
            )
            try:
                with self.repository.transaction() as unit:
                    if not unit.compare_and_save_assignment(saved, interviewing):
                        retrying_after_cas_loss = True
                        continue
                    unit.save_interview(updated_session)
                    if not unit.append_event_once(interviewing.mandate_id, event):
                        raise ValueError("concurrent exact acknowledgement already won")
            except ValueError:
                return WorkflowResult()
            if not send_question:
                return WorkflowResult()
            return WorkflowResult(
                deliveries=[
                    self._reply(
                        message,
                        render_question(
                            updated_session.questions[updated_session.current_question_index],
                            updated_session.current_question_index + 1,
                            len(updated_session.questions),
                        ),
                        interviewing,
                    )
                ]
            )
        return WorkflowResult()

    def _assignment_routes(self, assignment: StakeholderAssignment) -> list[ContactRoute]:
        allowed = set(assignment.route_ids)
        return [
            route for route in self.directory.ordered_routes(assignment.person_id) if route.route_id in allowed
        ]

    def _message_route(
        self, message: IncomingMessage, assignment: StakeholderAssignment
    ) -> ContactRoute | None:
        try:
            person = self.directory.person_for_sender(message)
        except (AmbiguousPersonError, UnknownPersonError):
            return None
        if person.person_id.casefold() != assignment.person_id.casefold():
            return None
        for route in self._assignment_routes(assignment):
            if (
                route.channel is message.channel
                and route.sender_address.casefold() == message.sender_address.casefold()
                and (
                    route.conversation_id is None
                    or route.conversation_id == message.conversation_id
                )
            ):
                return route
        return None

    def _session(self, assignment: StakeholderAssignment) -> InterviewSession:
        if assignment.interview_id is None:
            raise ValueError("assignment has no interview session")
        session = self.repository.get_interview(assignment.interview_id)
        if session is None:
            raise KeyError(str(assignment.interview_id))
        return session

    def _token(self, assignment: StakeholderAssignment) -> str:
        mandate = self.repository.get_mandate_by_token(self._mandate_token(assignment.mandate_id))
        if mandate is None:
            raise KeyError(str(assignment.mandate_id))
        return mandate.token

    def _summary(self, assignment: StakeholderAssignment) -> str:
        mandate = self.repository.get_mandate_by_token(self._mandate_token(assignment.mandate_id))
        if mandate is None:
            raise KeyError(str(assignment.mandate_id))
        return mandate.objective

    def _mandate_token(self, mandate_id: UUID) -> str:
        for mandate in self.repository.list_recent_mandates(limit=1000):
            if mandate.mandate_id == mandate_id:
                return mandate.token
        raise KeyError(str(mandate_id))

    def _owner_notice(self, assignment: StakeholderAssignment) -> list[DeliveryInstruction]:
        mandate = self.repository.get_mandate_by_token(self._mandate_token(assignment.mandate_id))
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
                    delivery_failed=assignment.state is StakeholderState.DELIVERY_FAILED,
                ),
                assignment,
            )
        ]

    @staticmethod
    def _answer_and_visibility(text: str) -> tuple[str, EvidenceVisibility]:
        match = _VISIBILITY_PREFIX.match(text.strip())
        if match is None:
            return text.strip(), EvidenceVisibility.SHAREABLE
        return text.strip()[match.end() :].strip(), EvidenceVisibility(match.group(1).lower())

    @staticmethod
    def _channel_history(session: InterviewSession, channel: Channel) -> list[Channel]:
        return session.channel_history if session.channel_history[-1:] == [channel] else [
            *session.channel_history,
            channel,
        ]

    def _activate_route(
        self,
        session: InterviewSession,
        route: ContactRoute,
        conversation_id: str | None,
        now: datetime,
    ) -> InterviewSession:
        return session.model_copy(
            update={
                "current_channel": route.channel,
                "current_route_id": route.route_id,
                "current_conversation_id": conversation_id,
                "channel_history": self._channel_history(session, route.channel),
                "updated_at": now,
            }
        )

    @staticmethod
    def _is_active_correlation(
        session: InterviewSession, route: ContactRoute, message: IncomingMessage
    ) -> bool:
        return (
            session.current_route_id == route.route_id
            and session.current_conversation_id is not None
            and session.current_conversation_id == message.conversation_id
        )

    @staticmethod
    def _can_bind_initial_conversation(
        session: InterviewSession,
        assignment: StakeholderAssignment,
        route: ContactRoute,
        message: IncomingMessage,
    ) -> bool:
        return (
            assignment.state
            in {StakeholderState.DELIVERED, StakeholderState.AWAITING_ACKNOWLEDGEMENT}
            and session.current_route_id == route.route_id
            and session.current_conversation_id is None
            and bool(message.conversation_id.strip())
        )

    @staticmethod
    def _contains_token(text: str, token: str) -> bool:
        return re.search(rf"(?<![A-Z0-9-]){re.escape(token)}(?![A-Z0-9-])", text.upper()) is not None

    def _save_assignment_event(
        self,
        updated: StakeholderAssignment,
        previous: StakeholderAssignment,
        event_type: str,
        now: datetime,
        metadata: dict[str, int | str],
        *,
        session: InterviewSession | None = None,
    ) -> bool:
        event = self._event(
            event_type,
            updated,
            previous,
            now,
            f"interview:{updated.assignment_id}:{event_type}:{updated.attempt_count}",
            metadata,
        )
        try:
            with self.repository.transaction() as unit:
                if not unit.compare_and_save_assignment(previous, updated):
                    return False
                if session is not None:
                    unit.save_interview(session)
                if not unit.append_event_once(updated.mandate_id, event):
                    raise ValueError("concurrent exact assignment event already won")
        except ValueError:
            return False
        return True

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
                mandate_token=None,
                assignment_id=assignment.assignment_id,
                message_id=message_id,
                recipient=route.recipient,
            )
        return DeliveryInstruction(
            kind=DeliveryKind.SEND_TO_CONVERSATION,
            text=text,
            assignment_id=assignment.assignment_id,
            message_id=message_id,
            conversation_id=route.conversation_id,
        )

    @staticmethod
    def _reply(
        message: IncomingMessage, text: str, assignment: StakeholderAssignment
    ) -> DeliveryInstruction:
        return DeliveryInstruction(
            kind=DeliveryKind.REPLY_TO_MESSAGE,
            text=text,
            assignment_id=assignment.assignment_id,
            message_id=message.message_id,
            conversation_id=message.conversation_id,
        )
