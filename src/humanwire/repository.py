import re
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from humanwire.database import (
    AlignmentIssueRecord,
    DomainEventRecord,
    EngagementDecisionRecord,
    EvidenceItemRecord,
    InterviewSessionRecord,
    MandateRecord,
    MeetingPackageRecord,
    ProposalRecord,
    ProposalResponseRecord,
    ReleaseOutboxRecord,
    RuntimeStatusRecord,
    StakeholderAssignmentRecord,
)
from humanwire.domain import (
    AlignmentIssue,
    AlignmentIssueType,
    Channel,
    Direction,
    DomainEvent,
    EngagementDecision,
    EngagementDecisionKind,
    EngagementType,
    EvidenceItem,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    InterviewSession,
    Mandate,
    MandatePlan,
    MandateState,
    MeetingPackage,
    Proposal,
    ProposalResponse,
    ProposalResponseKind,
    ProposalState,
    StakeholderAssignment,
    StakeholderState,
)
from humanwire.state_machine import ASSIGNMENT_TERMINAL_STATES


class DuplicateMandateError(ValueError):
    """Raised when a mandate token or idempotency key already exists."""


@dataclass(frozen=True)
class ReleaseOutboxEntry:
    """Privacy-safe durable identity for one initial release delivery."""

    outbox_id: str
    mandate_id: UUID
    assignment_id: UUID
    delivery_id: str
    attempt_count: int
    route_index: int
    state: str
    claim_owner: str | None
    claimed_at: datetime | None
    created_at: datetime
    completed_at: datetime | None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _copy_columns(source: Any, target: Any, excluded: set[str]) -> None:
    for column in source.__table__.columns:
        if column.key not in excluded:
            setattr(target, column.key, getattr(source, column.key))


def _mandate_record(value: Mandate) -> MandateRecord:
    return MandateRecord(
        mandate_id=str(value.mandate_id),
        token=value.token,
        initiator_id=value.initiator_id,
        origin_channel=value.origin_channel.value,
        origin_conversation_id=value.origin_conversation_id,
        origin_message_id=value.origin_message_id,
        redacted_request=value.redacted_request,
        objective=value.objective,
        plan=_json(value.plan),
        state=value.state.value,
        reason=value.reason,
        next_action_at=value.next_action_at,
        created_at=value.created_at,
        updated_at=value.updated_at,
        expires_at=value.expires_at,
        completed_at=value.completed_at,
        idempotency_key=value.idempotency_key,
    )


def _mandate_value(record: MandateRecord) -> Mandate:
    return Mandate(
        mandate_id=UUID(record.mandate_id),
        token=record.token,
        initiator_id=record.initiator_id,
        origin_channel=Channel(record.origin_channel),
        origin_conversation_id=record.origin_conversation_id,
        origin_message_id=record.origin_message_id,
        redacted_request=record.redacted_request,
        objective=record.objective,
        plan=MandatePlan.model_validate(record.plan),
        state=MandateState(record.state),
        reason=record.reason,
        next_action_at=_utc(record.next_action_at),
        created_at=_utc(record.created_at),
        updated_at=_utc(record.updated_at),
        expires_at=_utc(record.expires_at),
        completed_at=_utc(record.completed_at),
        idempotency_key=record.idempotency_key,
    )


def _assignment_record(value: StakeholderAssignment) -> StakeholderAssignmentRecord:
    return StakeholderAssignmentRecord(
        assignment_id=str(value.assignment_id),
        mandate_id=str(value.mandate_id),
        person_id=value.person_id,
        department=value.department,
        direction=value.direction.value,
        reason=value.reason,
        required=value.required,
        engagement_type=value.engagement_type.value,
        response_required=value.response_required,
        state=value.state.value,
        route_ids=value.route_ids,
        active_route_index=value.active_route_index,
        attempt_count=value.attempt_count,
        interview_id=str(value.interview_id) if value.interview_id else None,
        first_contact_at=value.first_contact_at,
        last_delivery_at=value.last_delivery_at,
        next_action_at=value.next_action_at,
        acknowledged_at=value.acknowledged_at,
        completed_at=value.completed_at,
        failure_reason=value.failure_reason,
    )


def _assignment_value(record: StakeholderAssignmentRecord) -> StakeholderAssignment:
    return StakeholderAssignment(
        assignment_id=UUID(record.assignment_id),
        mandate_id=UUID(record.mandate_id),
        person_id=record.person_id,
        department=record.department,
        direction=Direction(record.direction),
        reason=record.reason,
        required=record.required,
        engagement_type=EngagementType(record.engagement_type),
        response_required=record.response_required,
        state=StakeholderState(record.state),
        route_ids=record.route_ids,
        active_route_index=record.active_route_index,
        attempt_count=record.attempt_count,
        interview_id=UUID(record.interview_id) if record.interview_id else None,
        first_contact_at=_utc(record.first_contact_at),
        last_delivery_at=_utc(record.last_delivery_at),
        next_action_at=_utc(record.next_action_at),
        acknowledged_at=_utc(record.acknowledged_at),
        completed_at=_utc(record.completed_at),
        failure_reason=record.failure_reason,
    )


def _release_outbox_record(value: ReleaseOutboxEntry) -> ReleaseOutboxRecord:
    return ReleaseOutboxRecord(
        outbox_id=value.outbox_id,
        mandate_id=str(value.mandate_id),
        assignment_id=str(value.assignment_id),
        delivery_id=value.delivery_id,
        attempt_count=value.attempt_count,
        route_index=value.route_index,
        state=value.state,
        claim_owner=value.claim_owner,
        claimed_at=value.claimed_at,
        created_at=value.created_at,
        completed_at=value.completed_at,
    )


def _release_outbox_value(record: ReleaseOutboxRecord) -> ReleaseOutboxEntry:
    return ReleaseOutboxEntry(
        outbox_id=record.outbox_id,
        mandate_id=UUID(record.mandate_id),
        assignment_id=UUID(record.assignment_id),
        delivery_id=record.delivery_id,
        attempt_count=record.attempt_count,
        route_index=record.route_index,
        state=record.state,
        claim_owner=record.claim_owner,
        claimed_at=_utc(record.claimed_at),
        created_at=_utc(record.created_at),
        completed_at=_utc(record.completed_at),
    )


def _interview_record(
    value: InterviewSession, stakeholder_person_id: str
) -> InterviewSessionRecord:
    return InterviewSessionRecord(
        session_id=str(value.session_id),
        mandate_id=str(value.mandate_id),
        assignment_id=str(value.assignment_id),
        stakeholder_person_id=stakeholder_person_id,
        questions=value.questions,
        current_question_index=value.current_question_index,
        current_channel=value.current_channel.value if value.current_channel else None,
        current_route_id=value.current_route_id,
        current_conversation_id=value.current_conversation_id,
        channel_history=[channel.value for channel in value.channel_history],
        default_visibility=value.default_visibility.value,
        acknowledged_at=value.acknowledged_at,
        started_at=value.started_at,
        updated_at=value.updated_at,
        completed_at=value.completed_at,
    )


def _interview_value(record: InterviewSessionRecord) -> InterviewSession:
    return InterviewSession(
        session_id=UUID(record.session_id),
        mandate_id=UUID(record.mandate_id),
        assignment_id=UUID(record.assignment_id),
        questions=record.questions,
        current_question_index=record.current_question_index,
        current_channel=Channel(record.current_channel) if record.current_channel else None,
        current_route_id=record.current_route_id,
        current_conversation_id=record.current_conversation_id,
        channel_history=[Channel(item) for item in record.channel_history],
        default_visibility=EvidenceVisibility(record.default_visibility),
        acknowledged_at=_utc(record.acknowledged_at),
        started_at=_utc(record.started_at),
        updated_at=_utc(record.updated_at),
        completed_at=_utc(record.completed_at),
    )


def _evidence_record(value: EvidenceItem) -> EvidenceItemRecord:
    return EvidenceItemRecord(
        evidence_id=str(value.evidence_id),
        mandate_id=str(value.mandate_id),
        assignment_id=str(value.assignment_id),
        stakeholder_id=value.stakeholder_id,
        evidence_type=value.evidence_type.value,
        statement=value.statement,
        visibility=value.visibility.value,
        status=value.status.value,
        source_message_id=value.source_message_id,
        channel=value.channel.value,
        created_at=value.created_at,
        related_decision=value.related_decision,
        deadline=value.deadline,
        resource=value.resource,
    )


def _evidence_value(record: EvidenceItemRecord) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=UUID(record.evidence_id),
        mandate_id=UUID(record.mandate_id),
        assignment_id=UUID(record.assignment_id),
        stakeholder_id=record.stakeholder_id,
        evidence_type=EvidenceType(record.evidence_type),
        statement=record.statement,
        visibility=EvidenceVisibility(record.visibility),
        status=EvidenceStatus(record.status),
        source_message_id=record.source_message_id,
        channel=Channel(record.channel),
        created_at=_utc(record.created_at),
        related_decision=record.related_decision,
        deadline=_utc(record.deadline),
        resource=record.resource,
    )


def _issue_record(value: AlignmentIssue) -> AlignmentIssueRecord:
    return AlignmentIssueRecord(
        issue_id=str(value.issue_id),
        mandate_id=str(value.mandate_id),
        issue_type=value.issue_type.value,
        evidence_ids=[str(item) for item in value.evidence_ids],
        stakeholder_ids=value.stakeholder_ids,
        related_decision=value.related_decision,
        summary=value.summary,
        blocking=value.blocking,
        resolution=value.resolution,
    )


def _issue_value(record: AlignmentIssueRecord) -> AlignmentIssue:
    return AlignmentIssue(
        issue_id=UUID(record.issue_id),
        mandate_id=UUID(record.mandate_id),
        issue_type=AlignmentIssueType(record.issue_type),
        evidence_ids=[UUID(item) for item in record.evidence_ids],
        stakeholder_ids=record.stakeholder_ids,
        related_decision=record.related_decision,
        summary=record.summary,
        blocking=record.blocking,
        resolution=record.resolution,
    )


def _proposal_record(value: Proposal) -> ProposalRecord:
    return ProposalRecord(
        proposal_id=str(value.proposal_id),
        mandate_id=str(value.mandate_id),
        round_number=value.round_number,
        text=value.text,
        issue_ids=[str(item) for item in value.issue_ids],
        required_respondent_ids=value.required_respondent_ids,
        state=value.state.value,
        created_at=value.created_at,
        expires_at=value.expires_at,
    )


def _proposal_value(record: ProposalRecord) -> Proposal:
    return Proposal(
        proposal_id=UUID(record.proposal_id),
        mandate_id=UUID(record.mandate_id),
        round_number=record.round_number,
        text=record.text,
        issue_ids=[UUID(item) for item in record.issue_ids],
        required_respondent_ids=record.required_respondent_ids,
        state=ProposalState(record.state),
        created_at=_utc(record.created_at),
        expires_at=_utc(record.expires_at),
    )


def _response_record(value: ProposalResponse) -> ProposalResponseRecord:
    return ProposalResponseRecord(
        response_id=str(value.response_id),
        proposal_id=str(value.proposal_id),
        stakeholder_id=value.stakeholder_id,
        response=value.response.value,
        change_text=value.change_text,
        source_message_id=value.source_message_id,
        created_at=value.created_at,
        idempotency_key=value.idempotency_key,
    )


def _response_value(record: ProposalResponseRecord) -> ProposalResponse:
    return ProposalResponse(
        response_id=UUID(record.response_id),
        proposal_id=UUID(record.proposal_id),
        stakeholder_id=record.stakeholder_id,
        response=ProposalResponseKind(record.response),
        change_text=record.change_text,
        source_message_id=record.source_message_id,
        created_at=_utc(record.created_at),
        idempotency_key=record.idempotency_key,
    )


def _engagement_decision_record(value: EngagementDecision) -> EngagementDecisionRecord:
    return EngagementDecisionRecord(
        decision_id=str(value.decision_id),
        mandate_id=str(value.mandate_id),
        assignment_id=str(value.assignment_id),
        stakeholder_id=value.stakeholder_id,
        response=value.response.value,
        change_text=value.change_text,
        source_message_id=value.source_message_id,
        created_at=value.created_at,
        idempotency_key=value.idempotency_key,
    )


def _engagement_decision_value(record: EngagementDecisionRecord) -> EngagementDecision:
    return EngagementDecision(
        decision_id=UUID(record.decision_id),
        mandate_id=UUID(record.mandate_id),
        assignment_id=UUID(record.assignment_id),
        stakeholder_id=record.stakeholder_id,
        response=EngagementDecisionKind(record.response),
        change_text=record.change_text,
        source_message_id=record.source_message_id,
        created_at=_utc(record.created_at),
        idempotency_key=record.idempotency_key,
    )


def _engagement_decision_exists_or_conflicts(
    session: Session, decision: EngagementDecision
) -> bool:
    by_assignment = session.scalar(
        select(EngagementDecisionRecord).where(
            EngagementDecisionRecord.assignment_id == str(decision.assignment_id)
        )
    )
    by_idempotency_key = session.scalar(
        select(EngagementDecisionRecord).where(
            EngagementDecisionRecord.idempotency_key == decision.idempotency_key
        )
    )
    if by_assignment is None and by_idempotency_key is None:
        return False
    same_record = (
        by_assignment is not None
        and by_idempotency_key is not None
        and by_assignment.decision_id == by_idempotency_key.decision_id
    )
    if same_record and _engagement_decision_value(by_assignment) == decision:
        return True
    if by_idempotency_key is not None:
        raise ValueError("engagement decision idempotency key conflicts")
    raise ValueError("assignment already has a decision")


def _ensure_sqlite_write_transaction(session: Session) -> None:
    connection = session.connection()
    if connection.dialect.name != "sqlite":
        return
    driver_connection = connection.connection.driver_connection
    if not driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def _package_record(value: MeetingPackage) -> MeetingPackageRecord:
    return MeetingPackageRecord(
        meeting_id=str(value.meeting_id),
        mandate_id=str(value.mandate_id),
        purpose=value.purpose,
        decision_owner_id=value.decision_owner_id,
        required_attendee_ids=value.required_attendee_ids,
        optional_attendee_ids=value.optional_attendee_ids,
        proposed_start=value.proposed_start,
        proposed_end=value.proposed_end,
        timezone=value.timezone,
        agreed_facts=value.agreed_facts,
        open_decisions=value.open_decisions,
        agenda=value.agenda,
        pre_read_evidence_ids=[str(item) for item in value.pre_read_evidence_ids],
        calendar_written=value.calendar_written,
        created_at=value.created_at,
    )


def _package_value(record: MeetingPackageRecord) -> MeetingPackage:
    return MeetingPackage(
        meeting_id=UUID(record.meeting_id),
        mandate_id=UUID(record.mandate_id),
        purpose=record.purpose,
        decision_owner_id=record.decision_owner_id,
        required_attendee_ids=record.required_attendee_ids,
        optional_attendee_ids=record.optional_attendee_ids,
        proposed_start=_utc(record.proposed_start),
        proposed_end=_utc(record.proposed_end),
        timezone=record.timezone,
        agreed_facts=record.agreed_facts,
        open_decisions=record.open_decisions,
        agenda=record.agenda,
        pre_read_evidence_ids=[UUID(item) for item in record.pre_read_evidence_ids],
        calendar_written=record.calendar_written,
        created_at=_utc(record.created_at),
    )


_EVENT_BOOLEAN_KEYS = frozenset({"calendar_written", "fallback", "safe"})
_EVENT_INTEGER_KEYS = frozenset(
    {
        "attempt",
        "attempt_count",
        "duration_ms",
        "question_index",
        "round_number",
        "route_index",
    }
)
_EVENT_IDENTIFIER_KEYS = frozenset(
    {
        "actor_id",
        "analyzer",
        "assignment_id",
        "delivery_id",
        "error_code",
        "evidence_id",
        "issue_id",
        "meeting_id",
        "message_id",
        "model",
        "new_engagement_type",
        "old_engagement_type",
        "outcome",
        "person_id",
        "proposal_id",
        "reason_code",
        "route_fingerprint",
        "route_id",
        "status",
    }
)
_EVENT_IDENTIFIER_LIST_KEYS = frozenset(
    {"assignment_ids", "evidence_ids", "issue_ids", "person_ids", "route_ids"}
)
_EVENT_CONTAINER_KEYS = frozenset({"references"})
_OPAQUE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_EMAIL_DESTINATION = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE_DESTINATION = re.compile(r"(?:\+\d[\d(). -]{6,}\d|\d{3}[(). -]+\d{3}[(). -]+\d{4}|\d{10,})")
_CHAT_DESTINATION_PREFIXES = ("@", "tg://", "telegram://", "http://t.me/", "https://t.me/")


def _validate_opaque_identifier(value: Any) -> None:
    if not isinstance(value, str) or not _OPAQUE_IDENTIFIER.fullmatch(value):
        raise ValueError("event metadata must not contain a full destination")
    lowered = value.lower()
    if (
        _EMAIL_DESTINATION.search(value)
        or _PHONE_DESTINATION.fullmatch(value)
        or lowered.startswith(_CHAT_DESTINATION_PREFIXES)
    ):
        raise ValueError("event metadata must not contain a full destination")


def _validate_event_metadata(metadata: dict[str, Any]) -> None:
    for key, value in metadata.items():
        if key in _EVENT_BOOLEAN_KEYS:
            if not isinstance(value, bool):
                raise ValueError("event metadata must contain only approved safe identifiers")
        elif key in _EVENT_INTEGER_KEYS:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("event metadata must contain only approved safe identifiers")
        elif key in _EVENT_IDENTIFIER_KEYS:
            _validate_opaque_identifier(value)
        elif key in _EVENT_IDENTIFIER_LIST_KEYS:
            if not isinstance(value, list):
                raise ValueError("event metadata must contain only approved safe identifiers")
            for item in value:
                _validate_opaque_identifier(item)
        elif key in _EVENT_CONTAINER_KEYS:
            if isinstance(value, dict):
                _validate_event_metadata(value)
            elif isinstance(value, list):
                for item in value:
                    if not isinstance(item, dict):
                        raise TypeError(
                            "event metadata must contain only approved safe identifiers"
                        )
                    _validate_event_metadata(item)
            else:
                raise ValueError("event metadata must contain only approved safe identifiers")
        else:
            raise ValueError(
                "event metadata must not contain a full destination or unapproved field"
            )


def _event_record(mandate_id: UUID, value: DomainEvent) -> DomainEventRecord:
    _validate_event_metadata(value.metadata)
    return DomainEventRecord(
        mandate_id=str(mandate_id),
        event_type=value.event_type,
        created_at=value.created_at,
        idempotency_key=value.idempotency_key,
        actor_id=value.actor_id,
        assignment_id=str(value.assignment_id) if value.assignment_id else None,
        person_id=value.person_id,
        department=value.department,
        direction=value.direction.value if value.direction else None,
        channel=value.channel.value if value.channel else None,
        previous_state=value.previous_state,
        new_state=value.new_state,
        event_metadata=value.metadata,
    )


def _event_value(record: DomainEventRecord) -> DomainEvent:
    return DomainEvent(
        event_type=record.event_type,
        created_at=_utc(record.created_at),
        idempotency_key=record.idempotency_key,
        actor_id=record.actor_id,
        assignment_id=UUID(record.assignment_id) if record.assignment_id else None,
        person_id=record.person_id,
        department=record.department,
        direction=Direction(record.direction) if record.direction else None,
        channel=Channel(record.channel) if record.channel else None,
        previous_state=record.previous_state,
        new_state=record.new_state,
        metadata=record.event_metadata,
    )


class RepositoryUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_mandate(self, mandate: Mandate) -> None:
        self._session.add(_mandate_record(mandate))
        # Other aggregate records use a database foreign key but deliberately
        # have no ORM relationship.  Flush the parent inside the caller's unit
        # of work so an event and its state change remain one transaction.
        self._session.flush()

    def save_mandate(self, mandate: Mandate) -> None:
        record = self._session.get(MandateRecord, str(mandate.mandate_id))
        if record is None:
            raise KeyError(str(mandate.mandate_id))
        _copy_columns(_mandate_record(mandate), record, {"mandate_id"})

    def guard_mandate_state_if_unexpired(
        self,
        mandate_id: UUID,
        expected_state: MandateState,
        now: datetime,
    ) -> bool:
        """Acquire the write turn only while a mandate remains in the expected state."""
        result = self._session.execute(
            update(MandateRecord)
            .where(
                MandateRecord.mandate_id == str(mandate_id),
                MandateRecord.state == expected_state.value,
                MandateRecord.expires_at > now,
            )
            .values(state=expected_state.value)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def compare_and_save_mandate_if_unexpired(
        self,
        expected: Mandate,
        updated: Mandate,
        now: datetime,
    ) -> bool:
        """Save one mandate while its persisted aggregate snapshot is exact and live."""
        return self._compare_and_save_mandate(
            expected,
            updated,
            now=now,
            require_unexpired=True,
        )

    def compare_and_save_mandate(
        self,
        expected: Mandate,
        updated: Mandate,
    ) -> bool:
        """Save one mandate only while its complete mutable snapshot remains exact."""
        return self._compare_and_save_mandate(
            expected,
            updated,
            now=None,
            require_unexpired=False,
        )

    def _compare_and_save_mandate(
        self,
        expected: Mandate,
        updated: Mandate,
        *,
        now: datetime | None,
        require_unexpired: bool,
    ) -> bool:
        if expected.mandate_id != updated.mandate_id:
            raise ValueError("mandate compare-and-save requires one mandate")
        replacement = _mandate_record(updated)
        values = {
            column.key: getattr(replacement, column.key)
            for column in replacement.__table__.columns
            if column.key != "mandate_id"
        }
        predicates = [
            MandateRecord.mandate_id == str(expected.mandate_id),
            MandateRecord.token == expected.token,
            MandateRecord.initiator_id == expected.initiator_id,
            MandateRecord.origin_channel == expected.origin_channel.value,
            MandateRecord.origin_conversation_id == expected.origin_conversation_id,
            MandateRecord.origin_message_id == expected.origin_message_id,
            MandateRecord.redacted_request == expected.redacted_request,
            MandateRecord.objective == expected.objective,
            MandateRecord.plan == _json(expected.plan),
            MandateRecord.state == expected.state.value,
            MandateRecord.reason == expected.reason,
            MandateRecord.next_action_at == expected.next_action_at,
            MandateRecord.created_at == expected.created_at,
            MandateRecord.updated_at == expected.updated_at,
            MandateRecord.expires_at == expected.expires_at,
            MandateRecord.completed_at == expected.completed_at,
            MandateRecord.idempotency_key == expected.idempotency_key,
        ]
        if require_unexpired:
            assert now is not None
            predicates.append(MandateRecord.expires_at > now)
        result = self._session.execute(
            update(MandateRecord)
            .where(*predicates)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def add_assignment(self, assignment: StakeholderAssignment) -> None:
        self._session.add(_assignment_record(assignment))

    def save_assignment(self, assignment: StakeholderAssignment) -> None:
        record = self._session.get(StakeholderAssignmentRecord, str(assignment.assignment_id))
        if record is None:
            raise KeyError(str(assignment.assignment_id))
        _copy_columns(_assignment_record(assignment), record, {"assignment_id"})

    def compare_and_save_assignment(
        self,
        expected: StakeholderAssignment,
        updated: StakeholderAssignment,
    ) -> bool:
        """Save only while the persisted lifecycle snapshot still matches the caller's read."""
        if expected.assignment_id != updated.assignment_id:
            raise ValueError("assignment compare-and-save requires one assignment")
        replacement = _assignment_record(updated)
        values = {
            column.key: getattr(replacement, column.key)
            for column in replacement.__table__.columns
            if column.key != "assignment_id"
        }
        result = self._session.execute(
            update(StakeholderAssignmentRecord)
            .where(
                StakeholderAssignmentRecord.assignment_id == str(expected.assignment_id),
                StakeholderAssignmentRecord.mandate_id == str(expected.mandate_id),
                StakeholderAssignmentRecord.person_id == expected.person_id,
                StakeholderAssignmentRecord.department == expected.department,
                StakeholderAssignmentRecord.direction == expected.direction.value,
                StakeholderAssignmentRecord.reason == expected.reason,
                StakeholderAssignmentRecord.required == expected.required,
                StakeholderAssignmentRecord.engagement_type
                == expected.engagement_type.value,
                StakeholderAssignmentRecord.response_required
                == expected.response_required,
                StakeholderAssignmentRecord.state == expected.state.value,
                StakeholderAssignmentRecord.route_ids == expected.route_ids,
                StakeholderAssignmentRecord.attempt_count == expected.attempt_count,
                StakeholderAssignmentRecord.active_route_index
                == expected.active_route_index,
                StakeholderAssignmentRecord.interview_id
                == (str(expected.interview_id) if expected.interview_id else None),
                StakeholderAssignmentRecord.first_contact_at
                == expected.first_contact_at,
                StakeholderAssignmentRecord.last_delivery_at
                == expected.last_delivery_at,
                StakeholderAssignmentRecord.next_action_at == expected.next_action_at,
                StakeholderAssignmentRecord.acknowledged_at
                == expected.acknowledged_at,
                StakeholderAssignmentRecord.completed_at == expected.completed_at,
                StakeholderAssignmentRecord.failure_reason == expected.failure_reason,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def compare_and_save_assignment_if_mandate_active(
        self,
        expected: StakeholderAssignment,
        updated: StakeholderAssignment,
        now: datetime,
    ) -> bool:
        """Save an exact assignment only while its mandate is coordinating and live."""
        if (
            expected.assignment_id != updated.assignment_id
            or expected.mandate_id != updated.mandate_id
        ):
            raise ValueError("mandate-coupled compare-and-save requires one assignment")
        replacement = _assignment_record(updated)
        values = {
            column.key: getattr(replacement, column.key)
            for column in replacement.__table__.columns
            if column.key != "assignment_id"
        }
        active_mandate = exists().where(
            MandateRecord.mandate_id == StakeholderAssignmentRecord.mandate_id,
            MandateRecord.mandate_id == str(expected.mandate_id),
            MandateRecord.state == MandateState.INTERVIEWING.value,
            MandateRecord.expires_at > now,
        )
        result = self._session.execute(
            update(StakeholderAssignmentRecord)
            .where(
                StakeholderAssignmentRecord.assignment_id
                == str(expected.assignment_id),
                StakeholderAssignmentRecord.mandate_id == str(expected.mandate_id),
                StakeholderAssignmentRecord.person_id == expected.person_id,
                StakeholderAssignmentRecord.department == expected.department,
                StakeholderAssignmentRecord.direction == expected.direction.value,
                StakeholderAssignmentRecord.reason == expected.reason,
                StakeholderAssignmentRecord.required == expected.required,
                StakeholderAssignmentRecord.engagement_type
                == expected.engagement_type.value,
                StakeholderAssignmentRecord.response_required
                == expected.response_required,
                StakeholderAssignmentRecord.route_ids == expected.route_ids,
                StakeholderAssignmentRecord.state == expected.state.value,
                StakeholderAssignmentRecord.attempt_count == expected.attempt_count,
                StakeholderAssignmentRecord.active_route_index
                == expected.active_route_index,
                StakeholderAssignmentRecord.interview_id
                == (str(expected.interview_id) if expected.interview_id else None),
                StakeholderAssignmentRecord.first_contact_at
                == expected.first_contact_at,
                StakeholderAssignmentRecord.last_delivery_at
                == expected.last_delivery_at,
                StakeholderAssignmentRecord.next_action_at
                == expected.next_action_at,
                StakeholderAssignmentRecord.acknowledged_at
                == expected.acknowledged_at,
                StakeholderAssignmentRecord.completed_at == expected.completed_at,
                StakeholderAssignmentRecord.failure_reason == expected.failure_reason,
                active_mandate,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def add_release_outbox(self, entry: ReleaseOutboxEntry) -> None:
        """Stage one safe initial-delivery claim inside the release transaction."""
        if (
            entry.state != "claimed"
            or entry.claimed_at is None
            or entry.claim_owner is None
        ):
            raise ValueError("released delivery must enter the outbox as an owned claim")
        self._session.add(_release_outbox_record(entry))

    def complete_release_outbox(
        self,
        assignment_id: UUID,
        delivery_id: str,
        now: datetime,
        *,
        claim_owner: str | None = None,
    ) -> bool:
        """Fence a durable callback, while allowing attempts with no durable row."""
        if claim_owner is not None:
            result = self._session.execute(
                update(ReleaseOutboxRecord)
                .where(
                    ReleaseOutboxRecord.assignment_id == str(assignment_id),
                    ReleaseOutboxRecord.delivery_id == delivery_id,
                    ReleaseOutboxRecord.state == "claimed",
                    ReleaseOutboxRecord.claim_owner == claim_owner,
                )
                .values(state="completed", completed_at=now)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                return True
        durable_row_exists = self._session.scalar(
            select(ReleaseOutboxRecord.outbox_id).where(
                ReleaseOutboxRecord.assignment_id == str(assignment_id),
                ReleaseOutboxRecord.delivery_id == delivery_id,
            )
        )
        return durable_row_exists is None

    def complete_current_release_outbox(
        self,
        assignment: StakeholderAssignment,
        now: datetime,
    ) -> bool:
        """Retire the open attempt proven delivered by authenticated inbound work."""
        result = self._session.execute(
            update(ReleaseOutboxRecord)
            .where(
                ReleaseOutboxRecord.mandate_id == str(assignment.mandate_id),
                ReleaseOutboxRecord.assignment_id == str(assignment.assignment_id),
                ReleaseOutboxRecord.attempt_count == assignment.attempt_count,
                ReleaseOutboxRecord.route_index == assignment.active_route_index,
                ReleaseOutboxRecord.state.in_(["pending", "claimed"]),
            )
            .values(state="completed", completed_at=now)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount > 0

    def claim_release_outbox(
        self,
        now: datetime,
        *,
        claim_owner: str,
        lease_seconds: int,
        limit: int,
    ) -> list[ReleaseOutboxEntry]:
        """Atomically claim eligible initial attempts, including expired leases."""
        lease_cutoff = now - timedelta(seconds=lease_seconds)
        terminal_assignments = [
            state.value for state in ASSIGNMENT_TERMINAL_STATES
        ]
        eligible_ids = (
            select(ReleaseOutboxRecord.outbox_id)
            .join(
                StakeholderAssignmentRecord,
                ReleaseOutboxRecord.assignment_id
                == StakeholderAssignmentRecord.assignment_id,
            )
            .join(
                MandateRecord,
                ReleaseOutboxRecord.mandate_id == MandateRecord.mandate_id,
            )
            .where(
                (
                    (ReleaseOutboxRecord.state == "pending")
                    | (
                        (ReleaseOutboxRecord.state == "claimed")
                        & (ReleaseOutboxRecord.claimed_at < lease_cutoff)
                    )
                ),
                MandateRecord.state == MandateState.INTERVIEWING.value,
                MandateRecord.expires_at > now,
                StakeholderAssignmentRecord.mandate_id
                == ReleaseOutboxRecord.mandate_id,
                StakeholderAssignmentRecord.attempt_count
                == ReleaseOutboxRecord.attempt_count,
                StakeholderAssignmentRecord.active_route_index
                == ReleaseOutboxRecord.route_index,
                StakeholderAssignmentRecord.state.not_in(terminal_assignments),
            )
            .order_by(
                ReleaseOutboxRecord.created_at,
                ReleaseOutboxRecord.outbox_id,
            )
            .limit(limit)
        )
        statement = (
            update(ReleaseOutboxRecord)
            .where(ReleaseOutboxRecord.outbox_id.in_(eligible_ids))
            .values(state="claimed", claim_owner=claim_owner, claimed_at=now)
            .returning(ReleaseOutboxRecord)
            .execution_options(synchronize_session=False)
        )
        records = self._session.scalars(statement).all()
        return sorted(
            (_release_outbox_value(record) for record in records),
            key=lambda item: (item.created_at, item.outbox_id),
        )

    def renew_release_outbox_claim(
        self,
        outbox_id: str,
        assignment_id: UUID,
        claim_owner: str,
        now: datetime,
    ) -> bool:
        """Renew one exact dispatch fence only while its aggregate remains live."""
        eligible = exists().where(
            StakeholderAssignmentRecord.assignment_id == str(assignment_id),
            StakeholderAssignmentRecord.assignment_id
            == ReleaseOutboxRecord.assignment_id,
            StakeholderAssignmentRecord.mandate_id == ReleaseOutboxRecord.mandate_id,
            StakeholderAssignmentRecord.attempt_count
            == ReleaseOutboxRecord.attempt_count,
            StakeholderAssignmentRecord.active_route_index
            == ReleaseOutboxRecord.route_index,
            StakeholderAssignmentRecord.state.not_in(
                [state.value for state in ASSIGNMENT_TERMINAL_STATES]
            ),
            exists().where(
                MandateRecord.mandate_id == ReleaseOutboxRecord.mandate_id,
                MandateRecord.state == MandateState.INTERVIEWING.value,
                MandateRecord.expires_at > now,
            ),
        )
        result = self._session.execute(
            update(ReleaseOutboxRecord)
            .where(
                ReleaseOutboxRecord.outbox_id == outbox_id,
                ReleaseOutboxRecord.assignment_id == str(assignment_id),
                ReleaseOutboxRecord.state == "claimed",
                ReleaseOutboxRecord.claim_owner == claim_owner,
                eligible,
            )
            .values(claimed_at=now)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def add_interview(self, interview: InterviewSession) -> None:
        assignment = self._session.get(StakeholderAssignmentRecord, str(interview.assignment_id))
        if assignment is None:
            raise KeyError(str(interview.assignment_id))
        existing = self._session.scalar(
            select(InterviewSessionRecord).where(
                InterviewSessionRecord.mandate_id == str(interview.mandate_id),
                InterviewSessionRecord.stakeholder_person_id == assignment.person_id,
                InterviewSessionRecord.completed_at.is_(None),
            )
        )
        if existing is not None:
            raise ValueError("stakeholder already has an active interview")
        self._session.add(_interview_record(interview, assignment.person_id))

    def save_interview(self, interview: InterviewSession) -> None:
        record = self._session.get(InterviewSessionRecord, str(interview.session_id))
        if record is None:
            raise KeyError(str(interview.session_id))
        assignment = self._session.get(StakeholderAssignmentRecord, str(interview.assignment_id))
        if assignment is None:
            raise KeyError(str(interview.assignment_id))
        _copy_columns(_interview_record(interview, assignment.person_id), record, {"session_id"})

    def compare_and_save_interview_if_mandate_active(
        self,
        expected: InterviewSession,
        updated: InterviewSession,
        now: datetime,
    ) -> bool:
        """Save one exact session only while its mandate is coordinating and live."""
        if (
            expected.session_id != updated.session_id
            or expected.mandate_id != updated.mandate_id
            or expected.assignment_id != updated.assignment_id
        ):
            raise ValueError("mandate-coupled interview CAS requires one session")
        assignment = self._session.get(
            StakeholderAssignmentRecord,
            str(expected.assignment_id),
        )
        if assignment is None or assignment.mandate_id != str(expected.mandate_id):
            return False
        replacement = _interview_record(updated, assignment.person_id)
        values = {
            column.key: getattr(replacement, column.key)
            for column in replacement.__table__.columns
            if column.key != "session_id"
        }
        active_mandate = exists().where(
            MandateRecord.mandate_id == InterviewSessionRecord.mandate_id,
            MandateRecord.mandate_id == str(expected.mandate_id),
            MandateRecord.state == MandateState.INTERVIEWING.value,
            MandateRecord.expires_at > now,
        )
        result = self._session.execute(
            update(InterviewSessionRecord)
            .where(
                InterviewSessionRecord.session_id == str(expected.session_id),
                InterviewSessionRecord.mandate_id == str(expected.mandate_id),
                InterviewSessionRecord.assignment_id == str(expected.assignment_id),
                InterviewSessionRecord.stakeholder_person_id == assignment.person_id,
                InterviewSessionRecord.questions == expected.questions,
                InterviewSessionRecord.current_question_index
                == expected.current_question_index,
                InterviewSessionRecord.current_channel
                == (
                    expected.current_channel.value
                    if expected.current_channel is not None
                    else None
                ),
                InterviewSessionRecord.current_route_id == expected.current_route_id,
                InterviewSessionRecord.current_conversation_id
                == expected.current_conversation_id,
                InterviewSessionRecord.channel_history
                == [channel.value for channel in expected.channel_history],
                InterviewSessionRecord.default_visibility
                == expected.default_visibility.value,
                InterviewSessionRecord.acknowledged_at == expected.acknowledged_at,
                InterviewSessionRecord.started_at == expected.started_at,
                InterviewSessionRecord.updated_at == expected.updated_at,
                InterviewSessionRecord.completed_at == expected.completed_at,
                active_mandate,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def add_evidence(self, evidence: EvidenceItem) -> None:
        self._session.add(_evidence_record(evidence))

    def add_issue(self, issue: AlignmentIssue) -> None:
        self._session.add(_issue_record(issue))

    def add_proposal(self, proposal: Proposal) -> None:
        self._session.add(_proposal_record(proposal))

    def get_proposal(self, proposal_id: UUID) -> Proposal | None:
        record = self._session.get(ProposalRecord, str(proposal_id))
        return _proposal_value(record) if record else None

    def save_proposal(self, proposal: Proposal) -> None:
        record = self._session.get(ProposalRecord, str(proposal.proposal_id))
        if record is None:
            raise KeyError(str(proposal.proposal_id))
        _copy_columns(_proposal_record(proposal), record, {"proposal_id"})

    def add_proposal_response(self, response: ProposalResponse) -> None:
        self._session.add(_response_record(response))

    def list_proposal_responses(self, proposal_id: UUID) -> list[ProposalResponse]:
        records = self._session.scalars(
            select(ProposalResponseRecord)
            .where(ProposalResponseRecord.proposal_id == str(proposal_id))
            .order_by(ProposalResponseRecord.receipt_order)
        ).all()
        return [_response_value(record) for record in records]

    def add_engagement_decision(self, decision: EngagementDecision) -> None:
        if _engagement_decision_exists_or_conflicts(self._session, decision):
            return
        _ensure_sqlite_write_transaction(self._session)
        try:
            with self._session.begin_nested():
                self._session.add(_engagement_decision_record(decision))
                self._session.flush()
        except IntegrityError:
            self._session.expire_all()
            if _engagement_decision_exists_or_conflicts(self._session, decision):
                return
            raise ValueError("engagement decision conflicts") from None

    def get_engagement_decision(self, assignment_id: UUID) -> EngagementDecision | None:
        record = self._session.scalar(
            select(EngagementDecisionRecord).where(
                EngagementDecisionRecord.assignment_id == str(assignment_id)
            )
        )
        return _engagement_decision_value(record) if record else None

    def list_engagement_decisions(self, mandate_id: UUID) -> list[EngagementDecision]:
        records = self._session.scalars(
            select(EngagementDecisionRecord)
            .where(EngagementDecisionRecord.mandate_id == str(mandate_id))
            .order_by(
                EngagementDecisionRecord.created_at,
                EngagementDecisionRecord.decision_id,
            )
        ).all()
        return [_engagement_decision_value(record) for record in records]

    def save_meeting_package(self, package: MeetingPackage) -> None:
        record = self._session.get(MeetingPackageRecord, str(package.meeting_id))
        replacement = _package_record(package)
        if record is None:
            self._session.add(replacement)
        else:
            _copy_columns(replacement, record, {"meeting_id"})

    def append_event(self, mandate_id: UUID, event: DomainEvent) -> None:
        self._session.add(_event_record(mandate_id, event))

    def append_event_once(self, mandate_id: UUID, event: DomainEvent) -> bool:
        """Append exactly once; an exact concurrent duplicate is inert."""
        record = _event_record(mandate_id, event)
        values = {
            "mandate_id": record.mandate_id,
            "event_type": record.event_type,
            "created_at": record.created_at,
            "idempotency_key": record.idempotency_key,
            "actor_id": record.actor_id,
            "assignment_id": record.assignment_id,
            "person_id": record.person_id,
            "department": record.department,
            "direction": record.direction,
            "channel": record.channel,
            "previous_state": record.previous_state,
            "new_state": record.new_state,
            "event_metadata": record.event_metadata,
        }
        bind = self._session.get_bind()
        if bind.dialect.name == "sqlite":
            statement = (
                sqlite_insert(DomainEventRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[DomainEventRecord.idempotency_key])
            )
            result = self._session.execute(statement)
            if result.rowcount == 1:
                return True
        else:
            try:
                with self._session.begin_nested():
                    self._session.execute(insert(DomainEventRecord).values(**values))
                return True
            except IntegrityError:
                self._session.expire_all()
        existing = self._session.scalar(
            select(DomainEventRecord).where(
                DomainEventRecord.idempotency_key == event.idempotency_key
            )
        )
        if (
            existing is not None
            and existing.mandate_id == str(mandate_id)
            and _event_value(existing) == event
        ):
            return False
        raise ValueError(f"event idempotency key conflicts: {event.idempotency_key}")

    def set_runtime_status(self, key: str, value: str, updated_at: datetime) -> None:
        record = self._session.get(RuntimeStatusRecord, key)
        if record is None:
            self._session.add(RuntimeStatusRecord(key=key, value=value, updated_at=updated_at))
        else:
            record.value = value
            record.updated_at = updated_at


class SqlAlchemyHumanWireRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @contextmanager
    def transaction(self) -> Generator[RepositoryUnitOfWork, None, None]:
        with self._session_factory() as session:
            try:
                yield RepositoryUnitOfWork(session)
                session.commit()
            except Exception:
                session.rollback()
                raise

    def _write(self, operation: Any, *args: Any) -> None:
        with self.transaction() as unit:
            operation(unit, *args)

    def add_mandate(self, mandate: Mandate) -> None:
        try:
            self._write(RepositoryUnitOfWork.add_mandate, mandate)
        except IntegrityError as error:
            raise DuplicateMandateError(mandate.token) from error

    def save_mandate(self, mandate: Mandate) -> None:
        self._write(RepositoryUnitOfWork.save_mandate, mandate)

    def get_mandate_by_token(self, token: str) -> Mandate | None:
        with self._session_factory() as session:
            record = session.scalar(select(MandateRecord).where(MandateRecord.token == token))
            return _mandate_value(record) if record else None

    def get_mandate(self, mandate_id: UUID) -> Mandate | None:
        with self._session_factory() as session:
            record = session.get(MandateRecord, str(mandate_id))
            return _mandate_value(record) if record else None

    def get_mandate_by_idempotency_key(self, key: str) -> Mandate | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(MandateRecord).where(MandateRecord.idempotency_key == key)
            )
            return _mandate_value(record) if record else None

    def list_recent_mandates(self, limit: int = 30) -> list[Mandate]:
        with self._session_factory() as session:
            records = session.scalars(
                select(MandateRecord).order_by(MandateRecord.created_at.desc()).limit(limit)
            ).all()
            return [_mandate_value(record) for record in records]

    def add_assignment(self, assignment: StakeholderAssignment) -> None:
        self._write(RepositoryUnitOfWork.add_assignment, assignment)

    def save_assignment(self, assignment: StakeholderAssignment) -> None:
        self._write(RepositoryUnitOfWork.save_assignment, assignment)

    def get_assignment(self, assignment_id: UUID) -> StakeholderAssignment | None:
        with self._session_factory() as session:
            record = session.get(StakeholderAssignmentRecord, str(assignment_id))
            return _assignment_value(record) if record else None

    def list_assignments(self, mandate_id: UUID) -> list[StakeholderAssignment]:
        with self._session_factory() as session:
            records = session.scalars(
                select(StakeholderAssignmentRecord)
                .where(StakeholderAssignmentRecord.mandate_id == str(mandate_id))
                .order_by(StakeholderAssignmentRecord.assignment_id)
            ).all()
            return [_assignment_value(record) for record in records]

    def add_interview(self, interview: InterviewSession) -> None:
        try:
            self._write(RepositoryUnitOfWork.add_interview, interview)
        except IntegrityError as error:
            raise ValueError("stakeholder already has an active interview") from error

    def save_interview(self, interview: InterviewSession) -> None:
        try:
            self._write(RepositoryUnitOfWork.save_interview, interview)
        except IntegrityError as error:
            raise ValueError("stakeholder already has an active interview") from error

    def get_interview(self, session_id: UUID) -> InterviewSession | None:
        with self._session_factory() as session:
            record = session.get(InterviewSessionRecord, str(session_id))
            return _interview_value(record) if record else None

    def bind_initial_interview_conversation(
        self,
        assignment_id: UUID,
        session_id: UUID,
        route_id: str,
        conversation_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Atomically bind one initial conversation without replacing an existing correlation."""
        if not conversation_id.strip():
            return False
        at = now or datetime.now(UTC)
        initial_assignments = (
            select(StakeholderAssignmentRecord.assignment_id)
            .join(
                MandateRecord,
                StakeholderAssignmentRecord.mandate_id == MandateRecord.mandate_id,
            )
            .where(
                StakeholderAssignmentRecord.assignment_id == str(assignment_id),
                StakeholderAssignmentRecord.state.in_(
                    [
                        StakeholderState.DELIVERED.value,
                        StakeholderState.AWAITING_ACKNOWLEDGEMENT.value,
                    ]
                ),
                MandateRecord.state == MandateState.INTERVIEWING.value,
                MandateRecord.expires_at > at,
            )
        )
        with self._session_factory() as session:
            result = session.execute(
                update(InterviewSessionRecord)
                .where(
                    InterviewSessionRecord.session_id == str(session_id),
                    InterviewSessionRecord.assignment_id == str(assignment_id),
                    InterviewSessionRecord.current_route_id == route_id,
                    InterviewSessionRecord.current_conversation_id.is_(None),
                    InterviewSessionRecord.assignment_id.in_(initial_assignments),
                )
                .values(current_conversation_id=conversation_id)
            )
            session.commit()
            return result.rowcount == 1

    def find_active_interview(self, mandate_id: UUID, person_id: str) -> InterviewSession | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(InterviewSessionRecord)
                .join(
                    StakeholderAssignmentRecord,
                    InterviewSessionRecord.assignment_id
                    == StakeholderAssignmentRecord.assignment_id,
                )
                .where(
                    InterviewSessionRecord.mandate_id == str(mandate_id),
                    StakeholderAssignmentRecord.person_id == person_id,
                    InterviewSessionRecord.completed_at.is_(None),
                )
                .order_by(InterviewSessionRecord.started_at)
            )
            return _interview_value(record) if record else None

    def list_interviews(self, mandate_id: UUID) -> list[InterviewSession]:
        with self._session_factory() as session:
            records = session.scalars(
                select(InterviewSessionRecord)
                .where(InterviewSessionRecord.mandate_id == str(mandate_id))
                .order_by(InterviewSessionRecord.started_at, InterviewSessionRecord.session_id)
            ).all()
            return [_interview_value(record) for record in records]

    def add_evidence(self, evidence: EvidenceItem) -> None:
        self._write(RepositoryUnitOfWork.add_evidence, evidence)

    def list_evidence(self, mandate_id: UUID) -> list[EvidenceItem]:
        with self._session_factory() as session:
            records = session.scalars(
                select(EvidenceItemRecord)
                .where(EvidenceItemRecord.mandate_id == str(mandate_id))
                .order_by(EvidenceItemRecord.created_at, EvidenceItemRecord.evidence_id)
            ).all()
            return [_evidence_value(record) for record in records]

    def add_issue(self, issue: AlignmentIssue) -> None:
        self._write(RepositoryUnitOfWork.add_issue, issue)

    def list_issues(self, mandate_id: UUID) -> list[AlignmentIssue]:
        with self._session_factory() as session:
            records = session.scalars(
                select(AlignmentIssueRecord)
                .where(AlignmentIssueRecord.mandate_id == str(mandate_id))
                .order_by(AlignmentIssueRecord.issue_id)
            ).all()
            return [_issue_value(record) for record in records]

    def add_proposal(self, proposal: Proposal) -> None:
        self._write(RepositoryUnitOfWork.add_proposal, proposal)

    def get_proposal(self, proposal_id: UUID) -> Proposal | None:
        with self._session_factory() as session:
            record = session.get(ProposalRecord, str(proposal_id))
            return _proposal_value(record) if record else None

    def save_proposal(self, proposal: Proposal) -> None:
        self._write(RepositoryUnitOfWork.save_proposal, proposal)

    def get_active_proposal(self, mandate_id: UUID) -> Proposal | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(ProposalRecord)
                .where(
                    ProposalRecord.mandate_id == str(mandate_id),
                    ProposalRecord.state == ProposalState.AWAITING_RESPONSES.value,
                )
                .order_by(ProposalRecord.round_number.desc())
            )
            return _proposal_value(record) if record else None

    def add_proposal_response(self, response: ProposalResponse) -> None:
        self._write(RepositoryUnitOfWork.add_proposal_response, response)

    def list_proposal_responses(self, proposal_id: UUID) -> list[ProposalResponse]:
        with self._session_factory() as session:
            records = session.scalars(
                select(ProposalResponseRecord)
                .where(ProposalResponseRecord.proposal_id == str(proposal_id))
                .order_by(ProposalResponseRecord.receipt_order)
            ).all()
            return [_response_value(record) for record in records]

    def add_engagement_decision(self, decision: EngagementDecision) -> None:
        try:
            self._write(RepositoryUnitOfWork.add_engagement_decision, decision)
        except IntegrityError as error:
            with self._session_factory() as session:
                if _engagement_decision_exists_or_conflicts(session, decision):
                    return
            raise ValueError("engagement decision conflicts") from error

    def get_engagement_decision(self, assignment_id: UUID) -> EngagementDecision | None:
        with self._session_factory() as session:
            return RepositoryUnitOfWork(session).get_engagement_decision(assignment_id)

    def list_engagement_decisions(self, mandate_id: UUID) -> list[EngagementDecision]:
        with self._session_factory() as session:
            return RepositoryUnitOfWork(session).list_engagement_decisions(mandate_id)

    def save_meeting_package(self, package: MeetingPackage) -> None:
        self._write(RepositoryUnitOfWork.save_meeting_package, package)

    def get_meeting_package(self, mandate_id: UUID) -> MeetingPackage | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(MeetingPackageRecord).where(
                    MeetingPackageRecord.mandate_id == str(mandate_id)
                )
            )
            return _package_value(record) if record else None

    def append_event(self, mandate_id: UUID, event: DomainEvent) -> None:
        try:
            self._write(RepositoryUnitOfWork.append_event, mandate_id, event)
        except IntegrityError as error:
            raise ValueError(f"duplicate event idempotency key: {event.idempotency_key}") from error

    def list_events(self, mandate_id: UUID) -> list[DomainEvent]:
        with self._session_factory() as session:
            records = session.scalars(
                select(DomainEventRecord)
                .where(DomainEventRecord.mandate_id == str(mandate_id))
                .order_by(DomainEventRecord.created_at, DomainEventRecord.event_id)
            ).all()
            return [_event_value(record) for record in records]

    def list_due_assignments(self, now: datetime) -> list[StakeholderAssignment]:
        terminal = [state.value for state in ASSIGNMENT_TERMINAL_STATES]
        with self._session_factory() as session:
            records = session.scalars(
                select(StakeholderAssignmentRecord)
                .where(
                    StakeholderAssignmentRecord.next_action_at.is_not(None),
                    StakeholderAssignmentRecord.next_action_at <= now,
                    StakeholderAssignmentRecord.state.not_in(terminal),
                )
                .order_by(
                    StakeholderAssignmentRecord.next_action_at,
                    StakeholderAssignmentRecord.assignment_id,
                )
            ).all()
            return [_assignment_value(record) for record in records]

    def list_due_mandates(self, now: datetime) -> list[Mandate]:
        with self._session_factory() as session:
            records = session.scalars(
                select(MandateRecord)
                .where(
                    MandateRecord.state == MandateState.PLANNED.value,
                    MandateRecord.next_action_at.is_not(None),
                    MandateRecord.next_action_at <= now,
                    MandateRecord.expires_at > now,
                )
                .order_by(MandateRecord.next_action_at, MandateRecord.mandate_id)
            ).all()
            return [_mandate_value(record) for record in records]

    def list_release_outbox(self, mandate_id: UUID) -> list[ReleaseOutboxEntry]:
        with self._session_factory() as session:
            records = session.scalars(
                select(ReleaseOutboxRecord)
                .where(ReleaseOutboxRecord.mandate_id == str(mandate_id))
                .order_by(
                    ReleaseOutboxRecord.created_at,
                    ReleaseOutboxRecord.outbox_id,
                )
            ).all()
            return [_release_outbox_value(record) for record in records]

    def claim_release_outbox(
        self,
        now: datetime,
        *,
        lease_seconds: int = 30,
        limit: int = 1000,
    ) -> list[ReleaseOutboxEntry]:
        claim_owner = str(uuid4())
        with self.transaction() as unit:
            return unit.claim_release_outbox(
                now,
                claim_owner=claim_owner,
                lease_seconds=lease_seconds,
                limit=limit,
            )

    def renew_release_outbox_claim(
        self,
        outbox_id: str,
        assignment_id: UUID,
        claim_owner: str,
        now: datetime,
    ) -> bool:
        with self.transaction() as unit:
            return unit.renew_release_outbox_claim(
                outbox_id,
                assignment_id,
                claim_owner,
                now,
            )

    def has_open_release_outbox(self, assignment_id: UUID) -> bool:
        with self._session_factory() as session:
            return (
                session.scalar(
                    select(ReleaseOutboxRecord.outbox_id).where(
                        ReleaseOutboxRecord.assignment_id == str(assignment_id),
                        ReleaseOutboxRecord.state.in_(["pending", "claimed"]),
                    )
                )
                is not None
            )

    def set_runtime_status(self, key: str, value: str, updated_at: datetime) -> None:
        self._write(RepositoryUnitOfWork.set_runtime_status, key, value, updated_at)

    def get_runtime_status(self, key: str) -> tuple[str, datetime] | None:
        with self._session_factory() as session:
            record = session.get(RuntimeStatusRecord, key)
            if record is None:
                return None
            updated_at = _utc(record.updated_at)
            assert updated_at is not None
            return record.value, updated_at
