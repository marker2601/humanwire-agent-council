"""Top-level channel-neutral incoming message and due-work routing."""

from __future__ import annotations

from datetime import datetime

from humanwire.commands import (
    AcknowledgeCommand,
    AvailabilityCommand,
    CancelCommand,
    FreeTextCommand,
    MandateCommand,
    ProposalResponseCommand,
    StatusCommand,
    parse_command,
)
from humanwire.config import Settings
from humanwire.directory import AmbiguousPersonError, OrganizationDirectory, UnknownPersonError
from humanwire.domain import (
    DeliveryInstruction,
    DeliveryKind,
    IncomingMessage,
    MandateState,
    WorkflowResult,
)
from humanwire.evidence import EvidenceExtractor, shareable_evidence
from humanwire.meetings import MeetingCoordinator
from humanwire.messages import render_meeting_confirmation, render_proposal
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.services import MandateService, SynthesisService, _event
from humanwire.state_machine import MandateStateMachine


class HumanWireWorkflow:
    def __init__(self, directory: OrganizationDirectory, repository: SqlAlchemyHumanWireRepository, planner, evidence_extractor: EvidenceExtractor, settings: Settings) -> None:
        self.directory = directory
        self.repository = repository
        self.settings = settings
        self.mandates = MandateService(directory, repository, planner, evidence_extractor, settings)
        self.synthesis = SynthesisService(repository)

    def handle(self, message: IncomingMessage) -> WorkflowResult:
        command = parse_command(message.text)
        if isinstance(command, MandateCommand):
            return self.mandates.create(message, command)
        if isinstance(command, StatusCommand):
            return self._status(message, command.token)
        if isinstance(command, CancelCommand):
            return self._cancel(message, command.token)
        if isinstance(command, ProposalResponseCommand):
            return self._proposal(message, command)
        if isinstance(command, AvailabilityCommand):
            return self._availability(message, command)
        return self._interview(message, command)

    def process_due(self, now: datetime) -> WorkflowResult:
        deliveries: list[DeliveryInstruction] = []
        for assignment in self.repository.list_due_assignments(now):
            result = self.mandates.interviews.process_due_assignment(assignment, now)
            deliveries.extend(result.deliveries)
            self.synthesis.run(assignment.mandate_id, now)
        for mandate in self.repository.list_recent_mandates(1000):
            if mandate.expires_at <= now and mandate.state not in {MandateState.ALIGNED, MandateState.MEETING_READY, MandateState.PARTIAL, MandateState.CANCELLED, MandateState.EXPIRED}:
                expired = MandateStateMachine().transition(mandate, MandateState.EXPIRED, "deadline_elapsed", now)
                with self.repository.transaction() as unit:
                    unit.save_mandate(expired)
                    unit.append_event(mandate.mandate_id, _event("mandate.expired", now, f"expired:{mandate.mandate_id}", actor_id=mandate.initiator_id, previous_state=mandate.state.value, new_state="expired"))
        return WorkflowResult(deliveries=deliveries)

    def mark_delivery_result(self, instruction: DeliveryInstruction, succeeded: bool, now: datetime) -> WorkflowResult:
        if instruction.assignment_id is None:
            return WorkflowResult()
        delivery_id = instruction.message_id or f"instruction:{instruction.assignment_id}"
        if succeeded:
            self.mandates.interviews.mark_delivery_success(instruction.assignment_id, delivery_id, now)
        else:
            self.mandates.interviews.mark_delivery_failure(instruction.assignment_id, delivery_id, now)
        return WorkflowResult()

    def _interview(self, message: IncomingMessage, command: FreeTextCommand | AcknowledgeCommand) -> WorkflowResult:
        try:
            person = self.directory.person_for_sender(message)
        except (UnknownPersonError, AmbiguousPersonError):
            return self._reply(message, "Use /mandate to start a request.")
        candidates = []
        for mandate in self.repository.list_recent_mandates(1000):
            interview = self.repository.find_active_interview(mandate.mandate_id, person.person_id)
            if interview is not None and (interview.current_conversation_id in {None, message.conversation_id}):
                assignment = self.repository.get_assignment(interview.assignment_id)
                if assignment is not None:
                    candidates.append(assignment)
        if len(candidates) != 1:
            return self._reply(message, "Reply ACK <token> to select an active interview." if candidates else "Use /mandate to start a request.")
        assignment = candidates[0]
        result = self.mandates.interviews.acknowledge(message, assignment, message.received_at) if isinstance(command, AcknowledgeCommand) else self.mandates.interviews.record_answer(message, assignment, message.received_at)
        self.synthesis.run(assignment.mandate_id, message.received_at)
        return result

    def _status(self, message: IncomingMessage, token: str) -> WorkflowResult:
        mandate = self.repository.get_mandate_by_token(token)
        if mandate is None:
            return self._reply(message, "No mandate matches that token.")
        return self._reply(message, f"HumanWire {token}: {mandate.state.value}.")

    def _cancel(self, message: IncomingMessage, token: str) -> WorkflowResult:
        mandate = self.repository.get_mandate_by_token(token)
        if mandate is None:
            return self._reply(message, "No mandate matches that token.")
        try:
            sender = self.directory.person_for_sender(message)
        except (UnknownPersonError, AmbiguousPersonError):
            return self._reply(message, "Only the original initiator can cancel this mandate.")
        if sender.person_id != mandate.initiator_id:
            return self._reply(message, "Only the original initiator can cancel this mandate.")
        if mandate.state in {MandateState.ALIGNED, MandateState.MEETING_READY, MandateState.PARTIAL, MandateState.CANCELLED, MandateState.EXPIRED}:
            return self._reply(message, f"HumanWire {token}: {mandate.state.value}.")
        cancelled = MandateStateMachine().transition(mandate, MandateState.CANCELLED, "initiator_cancelled", message.received_at)
        with self.repository.transaction() as unit:
            unit.save_mandate(cancelled)
            unit.append_event(mandate.mandate_id, _event("mandate.cancelled", message.received_at, f"cancel:{mandate.mandate_id}:{message.message_id}", actor_id=sender.person_id, previous_state=mandate.state.value, new_state="cancelled"))
        return self._reply(message, f"HumanWire {token} is cancelled.")

    def _proposal(self, message: IncomingMessage, command: ProposalResponseCommand) -> WorkflowResult:
        mandate = self.repository.get_mandate_by_token(command.token)
        if mandate is None:
            return self._reply(message, "No mandate matches that token.")
        proposal = self.repository.get_active_proposal(mandate.mandate_id)
        if proposal is None:
            return self._reply(message, "There is no active proposal for that token.")
        try:
            person = self.directory.person_for_sender(message)
            from humanwire.alignment import (
                AlignmentReport,
                NegotiationCoordinator,
                NegotiationOutcome,
            )

            coordinator = NegotiationCoordinator(self.repository)
            coordinator.record_response(
                proposal,
                person.person_id,
                command.response,
                command.change_text,
                message.message_id,
                message.received_at,
            )
        except (UnknownPersonError, AmbiguousPersonError, ValueError):
            return self._reply(message, "That proposal response is not authorized or is no longer active.")
        outcome = coordinator.evaluate_round(proposal)
        if outcome is NegotiationOutcome.ALIGNED:
            aligned = MandateStateMachine().transition(
                mandate, MandateState.ALIGNED, "proposal_accepted", message.received_at
            )
            with self.repository.transaction() as unit:
                unit.save_mandate(aligned)
                unit.append_event(mandate.mandate_id, _event("mandate.aligned", message.received_at, f"proposal:aligned:{proposal.proposal_id}", actor_id=person.person_id, previous_state="negotiating", new_state="aligned"))
        elif outcome is NegotiationOutcome.NEXT_ROUND:
            report = AlignmentReport(mandate_id=mandate.mandate_id, issues=self.repository.list_issues(mandate.mandate_id), is_aligned=False)
            next_proposal = coordinator.create_proposal(mandate, report, 2, message.received_at)
            return WorkflowResult(deliveries=[DeliveryInstruction(kind=DeliveryKind.SEND_TO_CONVERSATION, text=render_proposal(mandate.token, next_proposal, shareable_evidence(self.repository.list_evidence(mandate.mandate_id))), mandate_token=mandate.token)])
        elif outcome is NegotiationOutcome.MEETING_REQUIRED:
            machine = MandateStateMachine()
            required = machine.transition(mandate, MandateState.MEETING_REQUIRED, "two_round_cap", message.received_at)
            scheduling = machine.transition(required, MandateState.SCHEDULING, "request_availability", message.received_at)
            with self.repository.transaction() as unit:
                unit.save_mandate(scheduling)
                unit.append_event(mandate.mandate_id, _event("mandate.meeting_required", message.received_at, f"meeting-required:{proposal.proposal_id}", actor_id=person.person_id, previous_state="negotiating", new_state="meeting_required"))
                unit.append_event(mandate.mandate_id, _event("mandate.scheduling", message.received_at, f"scheduling:{proposal.proposal_id}", actor_id=person.person_id, previous_state="meeting_required", new_state="scheduling"))
            return WorkflowResult(deliveries=[DeliveryInstruction(kind=DeliveryKind.SEND_TO_CONVERSATION, text=f"HUMANWIRE AVAILABILITY REQUEST · {mandate.token}\n\nReply AVAILABLE {mandate.token} <start>/<end> using ISO-8601 timestamps with offsets.", mandate_token=mandate.token)])
        return WorkflowResult()

    def _availability(self, message: IncomingMessage, command: AvailabilityCommand) -> WorkflowResult:
        mandate = self.repository.get_mandate_by_token(command.token)
        if mandate is None or mandate.state is not MandateState.SCHEDULING:
            return self._reply(message, "There is no active availability request for that token.")
        try:
            person = self.directory.person_for_sender(message)
        except (UnknownPersonError, AmbiguousPersonError):
            return self._reply(message, "Availability must come from a registered attendee.")
        self.repository.set_runtime_status(f"availability:{mandate.mandate_id}:{person.person_id}", json_windows(command), message.received_at)
        self.repository.append_event(mandate.mandate_id, _event("availability.recorded", message.received_at, f"availability:{mandate.mandate_id}:{person.person_id}:{message.message_id}", actor_id=person.person_id, channel=message.channel))
        return self._try_schedule(mandate, message.received_at)

    def _try_schedule(self, mandate, now: datetime) -> WorkflowResult:
        """Rebuild verification from durable availability, never object identity."""
        assignments = self.repository.list_assignments(mandate.mandate_id)
        from humanwire.alignment import AlignmentReport
        from humanwire.domain import AvailabilityWindow

        report = AlignmentReport(mandate_id=mandate.mandate_id, issues=self.repository.list_issues(mandate.mandate_id), is_aligned=False)
        coordinator = MeetingCoordinator(mandate.initiator_id)
        attendee_ids = coordinator.required_attendees(report, assignments, mandate.initiator_id)
        for attendee_id in attendee_ids:
            stored = self.repository.get_runtime_status(f"availability:{mandate.mandate_id}:{attendee_id}")
            if stored is None:
                return WorkflowResult()
            try:
                windows = [
                    AvailabilityWindow(start=datetime.fromisoformat(raw.split("/", 1)[0]), end=datetime.fromisoformat(raw.split("/", 1)[1]))
                    for raw in stored[0].split("|")
                ]
                coordinator.record_availability(attendee_id, windows)
            except (IndexError, ValueError):
                return WorkflowResult()
        slot = coordinator.find_overlap()
        if slot is None:
            return WorkflowResult()
        package = coordinator.build_package(mandate.plan, report, assignments, mandate.initiator_id, shareable_evidence(self.repository.list_evidence(mandate.mandate_id)), proposed_slot=slot, created_at=now)
        ready = MandateStateMachine().transition(mandate, MandateState.MEETING_READY, "verified_availability", now)
        with self.repository.transaction() as unit:
            unit.save_meeting_package(package)
            unit.save_mandate(ready)
            unit.append_event(mandate.mandate_id, _event("meeting.package_created", now, f"meeting:{package.meeting_id}", actor_id=mandate.initiator_id, metadata={"meeting_id": str(package.meeting_id)}))
            unit.append_event(mandate.mandate_id, _event("mandate.meeting_ready", now, f"meeting-ready:{package.meeting_id}", actor_id=mandate.initiator_id, previous_state="scheduling", new_state="meeting_ready"))
        return WorkflowResult(deliveries=[DeliveryInstruction(kind=DeliveryKind.SEND_TO_CONVERSATION, text=render_meeting_confirmation(mandate.token, package, coordinator=coordinator), mandate_token=mandate.token)])

    @staticmethod
    def _reply(message: IncomingMessage, text: str) -> WorkflowResult:
        return WorkflowResult(deliveries=[DeliveryInstruction(kind=DeliveryKind.REPLY_TO_MESSAGE, text=text, message_id=message.message_id, conversation_id=message.conversation_id)])


def json_windows(command: AvailabilityCommand) -> str:
    return "|".join(f"{window.start.isoformat()}/{window.end.isoformat()}" for window in command.windows)
