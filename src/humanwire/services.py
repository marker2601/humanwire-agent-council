"""Durable application services for the channel-neutral HumanWire workflow."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from humanwire.alignment import (
    AlignmentEngine,
    ContributionStatus,
    NegotiationCoordinator,
    contribution_status,
)
from humanwire.commands import EngageCommand, MandateCommand
from humanwire.config import Settings
from humanwire.directory import (
    AmbiguousPersonError,
    OrganizationDirectory,
    UnknownPersonError,
)
from humanwire.domain import (
    AvailabilityWindow,
    Channel,
    DeliveryInstruction,
    DeliveryKind,
    DomainEvent,
    EngagementType,
    IncomingMessage,
    Mandate,
    MandateState,
    PlannedStakeholder,
    StakeholderAssignment,
    StakeholderState,
    WorkflowResult,
)
from humanwire.engagement_policy import EngagementPolicy, EngagementPolicyError
from humanwire.engagements import EngagementCoordinator, PreparedEngagement
from humanwire.evidence import EvidenceExtractor, private_blocker_count, shareable_evidence
from humanwire.messages import (
    EngagementPreviewRow,
    render_alignment_brief,
    render_engagement_plan_preview,
    render_proposal,
)
from humanwire.planning import MandatePlanner, PlanNeedsClarification
from humanwire.repository import ReleaseOutboxEntry, SqlAlchemyHumanWireRepository
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
    """Persist, preview, safely override, and release one engagement aggregate."""

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
        self.engagement_policy = EngagementPolicy()
        self.engagements = EngagementCoordinator(
            directory, repository, StakeholderStateMachine(), evidence_extractor, settings
        )
        self.interviews = self.engagements.interviews

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
        planned = self.state_machine.transition(
            mandate,
            MandateState.PLANNED,
            "plan_validated",
            now,
        ).model_copy(
            update={
                "next_action_at": (
                    None
                    if self.settings.engagement_require_go
                    else now + timedelta(seconds=self.settings.engagement_preview_seconds)
                )
            }
        )
        assignments: list[StakeholderAssignment] = []
        missing_required_people = []
        for stakeholder, person in zip(resolved.plan.stakeholders, resolved.people, strict=True):
            routes = self.directory.ordered_routes(person.person_id)
            if stakeholder.required and not routes:
                missing_required_people.append(person)
            assignments.append(
                StakeholderAssignment(
                    assignment_id=uuid4(), mandate_id=mandate.mandate_id, person_id=person.person_id,
                    department=person.department, direction=stakeholder.direction, reason=stakeholder.reason,
                    required=stakeholder.required,
                    engagement_type=stakeholder.engagement_type,
                    response_required=stakeholder.response_required,
                    state=(
                        StakeholderState.CONTACT_QUEUED
                        if routes
                        else StakeholderState.DELIVERY_FAILED
                    ),
                    route_ids=[route.route_id for route in routes],
                    failure_reason="no_registered_route" if not routes else None,
                )
            )
        final_mandate = planned
        if missing_required_people:
            coordinating = self.state_machine.transition(
                planned,
                MandateState.INTERVIEWING,
                "required_stakeholder_unreachable",
                now,
            )
            final_mandate = self.state_machine.transition(
                coordinating,
                MandateState.PARTIAL,
                "required_stakeholder_unreachable",
                now,
            )
        with self.repository.transaction() as unit:
            unit.add_mandate(final_mandate)
            unit.append_event(mandate.mandate_id, _event("mandate.received", now, f"{key}:received", actor_id=initiator.person_id, new_state="received", channel=message.channel))
            unit.append_event(mandate.mandate_id, _event("mandate.planned", now, f"{key}:planned", actor_id=initiator.person_id, previous_state="received", new_state="planned"))
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
            if missing_required_people:
                unit.append_event(mandate.mandate_id, _event("mandate.partial", now, f"{key}:partial", actor_id=initiator.person_id, previous_state="planned", new_state="partial", metadata={"reason_code": "required_stakeholder_unreachable"}))
            else:
                unit.append_event(
                    mandate.mandate_id,
                    _event(
                        "engagement.plan_previewed",
                        now,
                        f"{key}:engagement-plan-previewed",
                        actor_id=initiator.person_id,
                        previous_state="planned",
                        new_state="planned",
                    ),
                )

        if missing_required_people:
            missing = ", ".join(
                f"{person.display_name} ({person.role})" for person in missing_required_people
            )
            text = (
                f"HUMANWIRE PARTIAL · {token}\n\n"
                f"Required response missing from: {missing}. "
                "No registered delivery route is available, so no agreement or approval was inferred."
            )
            return self._reply(message, text)
        return self._preview(message, planned, assignments)

    def release(self, token: str, now: datetime) -> WorkflowResult:
        """Atomically release the latest planned aggregate and emit only committed work."""
        mandate = self.repository.get_mandate_by_token(token)
        if (
            mandate is None
            or mandate.state is not MandateState.PLANNED
            or mandate.expires_at <= now
        ):
            return WorkflowResult()
        assignments = self.repository.list_assignments(mandate.mandate_id)
        assignments_by_person = {item.person_id: item for item in assignments}
        if (
            len(assignments_by_person) != len(assignments)
            or len(assignments) != len(mandate.plan.stakeholders)
        ):
            return WorkflowResult()

        prepared: list[tuple[StakeholderAssignment, PreparedEngagement]] = []
        guarded_failed: list[StakeholderAssignment] = []
        try:
            for stakeholder in mandate.plan.stakeholders:
                assignment = assignments_by_person.get(stakeholder.person_ref)
                if assignment is None:
                    return WorkflowResult()
                if assignment.state is StakeholderState.DELIVERY_FAILED:
                    if not self._truthful_optional_no_route(assignment, stakeholder):
                        return WorkflowResult()
                    guarded_failed.append(assignment)
                    continue
                if (
                    assignment.state is not StakeholderState.CONTACT_QUEUED
                    or not assignment.route_ids
                    or not self._assignment_matches_stakeholder(
                        assignment,
                        stakeholder,
                    )
                ):
                    return WorkflowResult()
                prepared.append(
                    (
                        assignment,
                        self.engagements.prepare_start(
                            assignment,
                            stakeholder.questions,
                            mandate.token,
                            mandate.objective,
                            now,
                        ),
                    )
                )
        except (KeyError, ValueError):
            return WorkflowResult()

        released = self.state_machine.transition(
            mandate,
            MandateState.INTERVIEWING,
            "engagement_plan_released",
            now,
        ).model_copy(update={"next_action_at": None})
        release_event = _event(
            "engagement.plan_released",
            now,
            f"engagement-release:{mandate.mandate_id}",
            actor_id=mandate.initiator_id,
            previous_state=MandateState.PLANNED.value,
            new_state=MandateState.INTERVIEWING.value,
        )
        coordinating_event = _event(
            "mandate.interviewing",
            now,
            f"mandate-coordinating:{mandate.mandate_id}",
            actor_id=mandate.initiator_id,
            previous_state=MandateState.PLANNED.value,
            new_state=MandateState.INTERVIEWING.value,
        )
        claim_owners = {
            item.assignment.assignment_id: str(uuid4()) for _, item in prepared
        }
        try:
            with self.repository.transaction() as unit:
                if not unit.compare_and_save_mandate_if_unexpired(
                    mandate,
                    released,
                    now,
                ):
                    raise ValueError("planned mandate snapshot changed")
                for expected, item in prepared:
                    if not unit.compare_and_save_assignment(
                        expected,
                        item.assignment,
                    ):
                        raise ValueError("planned assignment snapshot changed")
                    if item.interview is not None:
                        unit.add_interview(item.interview)
                    for event in item.events:
                        if not unit.append_event_once(mandate.mandate_id, event):
                            raise ValueError("prepared outreach already exists")
                    message_id = item.delivery.message_id
                    if message_id is None:
                        raise ValueError("prepared release delivery has no stable identity")
                    unit.add_release_outbox(
                        ReleaseOutboxEntry(
                            outbox_id=message_id,
                            mandate_id=mandate.mandate_id,
                            assignment_id=item.assignment.assignment_id,
                            delivery_id=hashlib.sha256(message_id.encode()).hexdigest()[:48],
                            attempt_count=item.assignment.attempt_count,
                            route_index=item.assignment.active_route_index,
                            state="claimed",
                            claim_owner=claim_owners[item.assignment.assignment_id],
                            claimed_at=now,
                            created_at=now,
                            completed_at=None,
                        )
                    )
                for failed in guarded_failed:
                    if not unit.compare_and_save_assignment(failed, failed):
                        raise ValueError("failed assignment snapshot changed")
                if not unit.append_event_once(mandate.mandate_id, release_event):
                    raise ValueError("engagement plan was already released")
                if not unit.append_event_once(mandate.mandate_id, coordinating_event):
                    raise ValueError("coordination state already exists")
        except Exception:  # noqa: BLE001 - never leak a partially staged delivery batch
            return WorkflowResult()
        return WorkflowResult(
            deliveries=[
                item.delivery.model_copy(
                    update={
                        "dispatch_claim_id": claim_owners[item.assignment.assignment_id]
                    }
                )
                for _, item in prepared
            ]
        )

    def recover_release_outbox(self, now: datetime) -> WorkflowResult:
        """Claim and reconstruct durable initial outreach after a worker restart."""
        deliveries = []
        for entry in self.repository.claim_release_outbox(now):
            delivery = self.engagements.reconstruct_initial_delivery(entry, now)
            if delivery is not None:
                deliveries.append(delivery)
        return WorkflowResult(deliveries=deliveries)

    def authorized_release(
        self,
        message: IncomingMessage,
        token: str,
    ) -> WorkflowResult:
        now = message.received_at.astimezone(UTC)
        mandate = self.repository.get_mandate_by_token(token)
        if not self._authorized_origin(message, mandate, now):
            return WorkflowResult()
        return self.release(token, now)

    def override(
        self,
        message: IncomingMessage,
        command: EngageCommand,
    ) -> WorkflowResult:
        now = message.received_at.astimezone(UTC)
        mandate = self.repository.get_mandate_by_token(command.token)
        if not self._authorized_origin(message, mandate, now):
            return WorkflowResult()
        assert mandate is not None
        assignments = self.repository.list_assignments(mandate.mandate_id)
        if (
            len(assignments) != len(mandate.plan.stakeholders)
            or len({item.person_id for item in assignments}) != len(assignments)
        ):
            return WorkflowResult()
        candidates = [
            item for item in assignments if item.person_id == command.person_id
        ]
        stakeholder_indexes = [
            index
            for index, stakeholder in enumerate(mandate.plan.stakeholders)
            if stakeholder.person_ref == command.person_id
        ]
        if len(candidates) != 1 or len(stakeholder_indexes) != 1:
            return WorkflowResult()
        assignment = candidates[0]
        stakeholder_index = stakeholder_indexes[0]
        stakeholder = mandate.plan.stakeholders[stakeholder_index]
        if (
            assignment.state is not StakeholderState.CONTACT_QUEUED
            or not self._assignment_matches_stakeholder(assignment, stakeholder)
        ):
            return WorkflowResult()
        try:
            requested = self.engagement_policy.validate_override(
                stakeholder,
                command.engagement_type,
            )
        except EngagementPolicyError:
            return WorkflowResult()
        if requested is stakeholder.engagement_type:
            return WorkflowResult()

        response_required = requested is not EngagementType.INFORM
        try:
            updated_stakeholder = PlannedStakeholder.model_validate(
                stakeholder.model_dump()
                | {
                    "engagement_type": requested,
                    "response_required": response_required,
                }
            )
        except ValueError:
            return WorkflowResult()
        stakeholders = list(mandate.plan.stakeholders)
        stakeholders[stakeholder_index] = updated_stakeholder
        updated_mandate = mandate.model_copy(
            update={
                "plan": mandate.plan.model_copy(
                    update={"stakeholders": stakeholders}
                ),
                "updated_at": now,
            }
        )
        updated_assignment = assignment.model_copy(
            update={
                "engagement_type": requested,
                "response_required": response_required,
            }
        )
        event = DomainEvent(
            event_type="engagement.override_recorded",
            created_at=now,
            idempotency_key=f"{incoming_idempotency_key(message)}:engagement-override",
            actor_id=mandate.initiator_id,
            assignment_id=assignment.assignment_id,
            person_id=assignment.person_id,
            department=assignment.department,
            direction=assignment.direction,
            channel=message.channel,
            previous_state=assignment.engagement_type.value,
            new_state=requested.value,
            metadata={
                "old_engagement_type": assignment.engagement_type.value,
                "new_engagement_type": requested.value,
            },
        )
        try:
            with self.repository.transaction() as unit:
                if not unit.compare_and_save_mandate_if_unexpired(
                    mandate,
                    updated_mandate,
                    now,
                ):
                    raise ValueError("planned mandate snapshot changed")
                if not unit.compare_and_save_assignment(
                    assignment,
                    updated_assignment,
                ):
                    raise ValueError("planned assignment snapshot changed")
                if not unit.append_event_once(mandate.mandate_id, event):
                    raise ValueError("engagement override was already recorded")
        except ValueError:
            return WorkflowResult()
        updated_assignments = [
            updated_assignment
            if item.assignment_id == assignment.assignment_id
            else item
            for item in assignments
        ]
        return self._preview(message, updated_mandate, updated_assignments)

    def _preview(
        self,
        message: IncomingMessage,
        mandate: Mandate,
        assignments: list[StakeholderAssignment],
    ) -> WorkflowResult:
        assignments_by_person = {item.person_id: item for item in assignments}
        rows = []
        for stakeholder in mandate.plan.stakeholders:
            assignment = assignments_by_person[stakeholder.person_ref]
            person = self.directory.resolve_person(stakeholder.person_ref)
            allowed_routes = set(assignment.route_ids)
            channels = tuple(
                route.channel
                for route in self.directory.ordered_routes(person.person_id)
                if route.route_id in allowed_routes
            )
            rows.append(
                EngagementPreviewRow(
                    person_id=person.person_id,
                    display_name=person.display_name,
                    department=person.department,
                    direction=stakeholder.direction,
                    reason=stakeholder.reason,
                    engagement_type=stakeholder.engagement_type,
                    response_required=stakeholder.response_required,
                    question_count=len(stakeholder.questions),
                    route_channels=channels,
                )
            )
        text = render_engagement_plan_preview(
            mandate.token,
            rows,
            release_at=mandate.next_action_at,
            preview_seconds=self.settings.engagement_preview_seconds,
            require_go=self.settings.engagement_require_go,
        )
        return self._reply(message, text)

    def _authorized_origin(
        self,
        message: IncomingMessage,
        mandate: Mandate | None,
        now: datetime,
    ) -> bool:
        if (
            mandate is None
            or mandate.state is not MandateState.PLANNED
            or mandate.expires_at <= now
            or message.channel is not mandate.origin_channel
            or message.conversation_id != mandate.origin_conversation_id
        ):
            return False
        try:
            sender = self.directory.person_for_sender(message)
        except (UnknownPersonError, AmbiguousPersonError):
            return False
        if sender.person_id != mandate.initiator_id:
            return False
        return any(
            route.channel is message.channel
            and route.sender_address.casefold() == message.sender_address.casefold()
            and (
                route.conversation_id is None
                or route.conversation_id == message.conversation_id
            )
            for route in self.directory.ordered_routes(sender.person_id)
        )

    @staticmethod
    def _assignment_matches_stakeholder(
        assignment: StakeholderAssignment,
        stakeholder: PlannedStakeholder,
    ) -> bool:
        return (
            assignment.person_id == stakeholder.person_ref
            and assignment.direction is stakeholder.direction
            and assignment.reason == stakeholder.reason
            and assignment.required is stakeholder.required
            and assignment.engagement_type is stakeholder.engagement_type
            and assignment.response_required is stakeholder.response_required
        )

    def _truthful_optional_no_route(
        self,
        assignment: StakeholderAssignment,
        stakeholder: PlannedStakeholder,
    ) -> bool:
        return (
            not assignment.required
            and self._assignment_matches_stakeholder(assignment, stakeholder)
            and assignment.state is StakeholderState.DELIVERY_FAILED
            and assignment.route_ids == []
            and assignment.active_route_index == 0
            and assignment.attempt_count == 0
            and assignment.interview_id is None
            and assignment.first_contact_at is None
            and assignment.last_delivery_at is None
            and assignment.next_action_at is None
            and assignment.acknowledged_at is None
            and assignment.completed_at is None
            and assignment.failure_reason == "no_registered_route"
            and not self.directory.ordered_routes(assignment.person_id)
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

    @staticmethod
    def _reply(message: IncomingMessage, text: str) -> WorkflowResult:
        return WorkflowResult(deliveries=[DeliveryInstruction(kind=DeliveryKind.REPLY_TO_MESSAGE, text=text, message_id=message.message_id, conversation_id=message.conversation_id)])


class _StaleSynthesisSnapshot(RuntimeError):
    """Raised internally so the unit of work rolls back a stale synthesis."""


class SynthesisService:
    """Projects a contribution aggregate only from a transaction-fenced snapshot."""

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
        evidence = self.repository.list_evidence(mandate_id)
        decisions = self.repository.list_engagement_decisions(mandate_id)
        availability = self._availability_snapshot(assignments)
        availability_assignment_ids = self._availability_assignment_ids(
            assignments, availability
        )
        statuses = {
            assignment.assignment_id: contribution_status(
                assignment,
                evidence=evidence,
                decisions=decisions,
                has_availability=(
                    assignment.assignment_id in availability_assignment_ids
                ),
            )
            for assignment in assignments
        }
        engine = self.alignment_engine_factory(mandate_id)
        valid_assignment_ids = engine._valid_assignment_ids(mandate.plan, assignments)
        eligible_evidence_ids = engine._eligible_contribution_evidence_ids(
            assignments,
            evidence,
            valid_assignment_ids,
        )
        complete_assignment_ids = {
            assignment.assignment_id
            for assignment in assignments
            if not statuses[assignment.assignment_id].blocking
            and assignment.state is StakeholderState.COMPLETE
        }
        relevant_evidence = [
            item
            for item in evidence
            if item.evidence_id in eligible_evidence_ids
            and item.assignment_id in complete_assignment_ids
        ]
        public_evidence = shareable_evidence(relevant_evidence)
        report = engine.analyze(
            mandate.plan,
            public_evidence,
            assignments,
            private_blocker_count=private_blocker_count(relevant_evidence),
            contribution_evidence=evidence,
            decisions=decisions,
            availability_assignment_ids=availability_assignment_ids,
        )
        if any(statuses[item.assignment_id].blocking for item in required):
            return self._partial(
                mandate,
                now,
                required,
                statuses,
                report.issues,
                assignments,
                evidence,
                decisions,
                availability,
            )
        synthesizing = self.state_machine.transition(mandate, MandateState.SYNTHESIZING, "required_contributions_complete", now)
        if report.is_aligned:
            aligned = self.state_machine.transition(synthesizing, MandateState.ALIGNED, "alignment_complete", now)
            brief = render_alignment_brief(mandate.token, public_evidence)
            try:
                with self.repository.transaction() as unit:
                    self._fence_snapshot(
                        unit,
                        mandate,
                        aligned,
                        now,
                        assignments,
                        evidence,
                        decisions,
                        availability,
                    )
                    for issue in report.issues:
                        unit.add_issue(issue)
                    unit.set_runtime_status(f"alignment-brief:{mandate_id}", brief, now)
                    unit.append_event(mandate_id, _event("mandate.synthesizing", now, f"synthesis:{mandate_id}", actor_id=mandate.initiator_id, previous_state="interviewing", new_state="synthesizing"))
                    unit.append_event(mandate_id, _event("mandate.aligned", now, f"aligned:{mandate_id}", actor_id=mandate.initiator_id, previous_state="synthesizing", new_state="aligned"))
                    unit.append_event(mandate_id, _event("alignment.brief_persisted", now, f"alignment-brief:{mandate_id}", actor_id=mandate.initiator_id))
            except _StaleSynthesisSnapshot:
                return WorkflowResult()
            recipients = [mandate.initiator_id, *(item.person_id for item in required)]
            return WorkflowResult(deliveries=self._route_deliveries(recipients, brief, mandate.token))
        negotiating = self.state_machine.transition(synthesizing, MandateState.NEGOTIATING, "blocking_issues", now)
        coordinator = self.negotiation_coordinator
        proposal = coordinator.prepare_proposal(synthesizing, report, 1, now)
        try:
            with self.repository.transaction() as unit:
                self._fence_snapshot(
                    unit,
                    mandate,
                    negotiating,
                    now,
                    assignments,
                    evidence,
                    decisions,
                    availability,
                )
                for issue in report.issues:
                    unit.add_issue(issue)
                unit.add_proposal(proposal)
                unit.append_event(mandate_id, _event("mandate.synthesizing", now, f"synthesis:{mandate_id}", actor_id=mandate.initiator_id, previous_state="interviewing", new_state="synthesizing"))
                unit.append_event(
                    mandate_id,
                    DomainEvent(
                        event_type="proposal.created",
                        created_at=now,
                        idempotency_key=f"proposal:create:{proposal.proposal_id}",
                        metadata={
                            "proposal_id": str(proposal.proposal_id),
                            "round_number": 1,
                        },
                    ),
                )
                unit.append_event(mandate_id, _event("mandate.negotiating", now, f"negotiating:{mandate_id}", actor_id=mandate.initiator_id, previous_state="synthesizing", new_state="negotiating"))
        except _StaleSynthesisSnapshot:
            return WorkflowResult()
        text = render_proposal(mandate.token, proposal, public_evidence)
        return WorkflowResult(
            deliveries=self._route_deliveries(
                proposal.required_respondent_ids, text, mandate.token
            )
        )

    def _partial(
        self,
        mandate: Mandate,
        now: datetime,
        required: list[StakeholderAssignment],
        statuses: dict[UUID, ContributionStatus],
        issues: list,
        assignments: list[StakeholderAssignment],
        evidence: list,
        decisions: list,
        availability: dict[str, tuple[str, datetime] | None],
    ) -> WorkflowResult:
        partial = self.state_machine.transition(mandate, MandateState.PARTIAL, "required_response_missing", now)
        missing_names = [
            self.directory.resolve_person(assignment.person_id).display_name
            for assignment in required
            if statuses[assignment.assignment_id].blocking
        ]
        try:
            with self.repository.transaction() as unit:
                self._fence_snapshot(
                    unit,
                    mandate,
                    partial,
                    now,
                    assignments,
                    evidence,
                    decisions,
                    availability,
                )
                for issue in issues:
                    unit.add_issue(issue)
                unit.append_event(mandate.mandate_id, _event("mandate.partial", now, f"partial:{mandate.mandate_id}", actor_id=mandate.initiator_id, previous_state="interviewing", new_state="partial", metadata={"reason_code": "required_response_missing"}))
        except _StaleSynthesisSnapshot:
            return WorkflowResult()
        missing = ", ".join(missing_names) if missing_names else "a required stakeholder"
        text = (
            f"HUMANWIRE PARTIAL · {mandate.token}\n\n"
            f"Required response missing from: {missing}. "
            "No agreement or approval was inferred."
        )
        return WorkflowResult(
            deliveries=self._route_deliveries([mandate.initiator_id], text, mandate.token)
        )

    def _availability_snapshot(
        self, assignments: list[StakeholderAssignment]
    ) -> dict[str, tuple[str, datetime] | None]:
        return {
            f"availability:{assignment.mandate_id}:{assignment.person_id}": (
                self.repository.get_runtime_status(
                    f"availability:{assignment.mandate_id}:{assignment.person_id}"
                )
            )
            for assignment in assignments
            if assignment.engagement_type is EngagementType.AVAILABILITY
        }

    @staticmethod
    def _availability_assignment_ids(
        assignments: list[StakeholderAssignment],
        availability: dict[str, tuple[str, datetime] | None],
    ) -> set[UUID]:
        valid: set[UUID] = set()
        for assignment in assignments:
            if assignment.engagement_type is not EngagementType.AVAILABILITY:
                continue
            stored = availability[
                f"availability:{assignment.mandate_id}:{assignment.person_id}"
            ]
            if (
                stored is None
                or assignment.completed_at is None
                or stored[1] != assignment.completed_at
            ):
                continue
            raw_windows = stored[0].split("|")
            if not raw_windows or any(not raw for raw in raw_windows):
                continue
            try:
                windows = [
                    AvailabilityWindow(
                        start=datetime.fromisoformat(raw.split("/", 1)[0]),
                        end=datetime.fromisoformat(raw.split("/", 1)[1]),
                    )
                    for raw in raw_windows
                ]
            except (IndexError, ValueError):
                continue
            if windows:
                valid.add(assignment.assignment_id)
        return valid

    @staticmethod
    def _fence_snapshot(
        unit,
        expected: Mandate,
        updated: Mandate,
        now: datetime,
        assignments: list[StakeholderAssignment],
        evidence: list,
        decisions: list,
        availability: dict[str, tuple[str, datetime] | None],
    ) -> None:
        if not unit.compare_and_save_mandate_if_unexpired(expected, updated, now):
            raise _StaleSynthesisSnapshot
        if not unit.contribution_snapshot_matches(
            expected.mandate_id,
            assignments=assignments,
            evidence=evidence,
            decisions=decisions,
            availability=availability,
        ):
            raise _StaleSynthesisSnapshot

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
