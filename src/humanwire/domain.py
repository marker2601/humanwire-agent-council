from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class Channel(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"


class Direction(StrEnum):
    DOWNWARD = "downward"
    LATERAL = "lateral"
    UPWARD = "upward"
    EXTERNAL = "external"


class MandateState(StrEnum):
    RECEIVED = "received"
    PLANNED = "planned"
    INTERVIEWING = "interviewing"
    SYNTHESIZING = "synthesizing"
    NEGOTIATING = "negotiating"
    ALIGNED = "aligned"
    MEETING_REQUIRED = "meeting_required"
    SCHEDULING = "scheduling"
    MEETING_READY = "meeting_ready"
    PARTIAL = "partial"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    DELIVERY_FAILED = "delivery_failed"


class StakeholderState(StrEnum):
    NOT_CONTACTED = "not_contacted"
    CONTACT_QUEUED = "contact_queued"
    DELIVERED = "delivered"
    AWAITING_ACKNOWLEDGEMENT = "awaiting_acknowledgement"
    ACKNOWLEDGED = "acknowledged"
    INTERVIEWING = "interviewing"
    COMPLETE = "complete"
    FOLLOW_UP_DUE = "follow_up_due"
    ALTERNATE_CHANNEL = "alternate_channel"
    DECLINED = "declined"
    UNREACHABLE = "unreachable"
    DELIVERY_FAILED = "delivery_failed"


class EvidenceType(StrEnum):
    FACT = "fact"
    CONSTRAINT = "constraint"
    CONCERN = "concern"
    PREFERENCE = "preference"
    COMMITMENT = "commitment"
    AVAILABILITY = "availability"
    DECISION = "decision"


class EvidenceVisibility(StrEnum):
    SHAREABLE = "shareable"
    ANONYMOUS = "anonymous"
    PRIVATE = "private"


class EvidenceStatus(StrEnum):
    ASSERTED = "asserted"
    CLARIFIED = "clarified"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    WITHDRAWN = "withdrawn"


class AlignmentIssueType(StrEnum):
    AGREEMENT = "agreement"
    CONTRADICTION = "contradiction"
    RESOURCE_CONFLICT = "resource_conflict"
    DEADLINE_CONFLICT = "deadline_conflict"
    MISSING_EVIDENCE = "missing_evidence"
    AUTHORITY_GAP = "authority_gap"
    HARD_CONSTRAINT = "hard_constraint"
    PRIVATE_BLOCKER = "private_blocker"


class ProposalResponseKind(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    CHANGE = "change"


class ProposalState(StrEnum):
    AWAITING_RESPONSES = "awaiting_responses"
    ALIGNED = "aligned"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


class DeliveryKind(StrEnum):
    REPLY_TO_MESSAGE = "reply_to_message"
    SEND_TO_CONVERSATION = "send_to_conversation"
    INITIATE_EMAIL = "initiate_email"


class ContactRoute(BaseModel):
    route_id: str
    channel: Channel
    sender_address: str
    recipient: str | None = None
    conversation_id: str | None = None
    preferred: bool = False

    @model_validator(mode="after")
    def has_delivery_destination(self) -> "ContactRoute":
        if self.channel is Channel.EMAIL and not self.recipient:
            raise ValueError("email contact routes require a recipient")
        if self.channel is Channel.TELEGRAM and not self.conversation_id:
            raise ValueError("telegram contact routes require a conversation_id")
        return self


class Person(BaseModel):
    person_id: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    role: str
    department: str
    timezone: str
    manager_id: str | None = None
    routes: list[ContactRoute] = Field(default_factory=list)


class IncomingMessage(BaseModel):
    message_id: str
    conversation_id: str
    connection_id: str
    channel: Channel
    sender_address: str
    sender_name: str | None = None
    subject: str | None = None
    text: str
    received_at: datetime


class PlannedStakeholder(BaseModel):
    person_ref: str
    reason: str
    direction: Direction
    required: bool = True
    questions: list[str] = Field(min_length=1, max_length=5)


class MandatePlan(BaseModel):
    objective: str
    required_decisions: list[str] = Field(min_length=1)
    stakeholders: list[PlannedStakeholder] = Field(min_length=1)
    deadline: datetime | None = None
    completion_conditions: list[str] = Field(min_length=1)


class Mandate(BaseModel):
    mandate_id: UUID
    token: str
    initiator_id: str
    origin_channel: Channel
    origin_conversation_id: str
    origin_message_id: str
    redacted_request: str
    objective: str
    plan: MandatePlan
    state: MandateState
    reason: str | None = None
    next_action_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    completed_at: datetime | None = None
    idempotency_key: str


class StakeholderAssignment(BaseModel):
    assignment_id: UUID
    mandate_id: UUID
    person_id: str
    department: str
    direction: Direction
    reason: str
    required: bool
    state: StakeholderState
    route_ids: list[str]
    active_route_index: int = 0
    attempt_count: int = 0
    interview_id: UUID | None = None
    first_contact_at: datetime | None = None
    last_delivery_at: datetime | None = None
    next_action_at: datetime | None = None
    acknowledged_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None


class InterviewSession(BaseModel):
    session_id: UUID
    mandate_id: UUID
    assignment_id: UUID
    questions: list[str] = Field(min_length=1, max_length=5)
    current_question_index: int = 0
    current_channel: Channel | None = None
    channel_history: list[Channel] = Field(default_factory=list)
    default_visibility: EvidenceVisibility = EvidenceVisibility.SHAREABLE
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class EvidenceItem(BaseModel):
    evidence_id: UUID
    mandate_id: UUID
    assignment_id: UUID
    stakeholder_id: str
    evidence_type: EvidenceType
    statement: str = Field(min_length=1, max_length=600)
    visibility: EvidenceVisibility
    status: EvidenceStatus
    source_message_id: str
    channel: Channel
    created_at: datetime
    related_decision: str | None = None
    deadline: datetime | None = None
    resource: str | None = None


class AlignmentIssue(BaseModel):
    issue_id: UUID
    mandate_id: UUID
    issue_type: AlignmentIssueType
    evidence_ids: list[UUID] = Field(default_factory=list)
    stakeholder_ids: list[str] = Field(default_factory=list)
    related_decision: str | None = None
    summary: str
    blocking: bool
    resolution: str | None = None


class Proposal(BaseModel):
    proposal_id: UUID
    mandate_id: UUID
    round_number: int = Field(ge=1, le=2)
    text: str = Field(min_length=1, max_length=600)
    issue_ids: list[UUID]
    required_respondent_ids: list[str]
    state: ProposalState = ProposalState.AWAITING_RESPONSES
    created_at: datetime
    expires_at: datetime


class ProposalResponse(BaseModel):
    response_id: UUID
    proposal_id: UUID
    stakeholder_id: str
    response: ProposalResponseKind
    change_text: str | None = Field(default=None, max_length=400)
    source_message_id: str
    created_at: datetime
    idempotency_key: str


class AvailabilityWindow(BaseModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def end_after_start(self) -> "AvailabilityWindow":
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("availability windows require timezone offsets")
        if self.end <= self.start:
            raise ValueError("availability end must be after start")
        return self


class MeetingPackage(BaseModel):
    meeting_id: UUID
    mandate_id: UUID
    purpose: str
    decision_owner_id: str
    required_attendee_ids: list[str]
    optional_attendee_ids: list[str] = Field(default_factory=list)
    proposed_start: datetime | None = None
    proposed_end: datetime | None = None
    timezone: str = "UTC"
    agreed_facts: list[str]
    open_decisions: list[str]
    agenda: list[str]
    pre_read_evidence_ids: list[UUID]
    calendar_written: bool = False
    created_at: datetime


class DomainEvent(BaseModel):
    event_type: str
    created_at: datetime
    idempotency_key: str
    actor_id: str | None = None
    assignment_id: UUID | None = None
    person_id: str | None = None
    department: str | None = None
    direction: Direction | None = None
    channel: Channel | None = None
    previous_state: str | None = None
    new_state: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeliveryInstruction(BaseModel):
    kind: DeliveryKind
    text: str
    mandate_token: str | None = None
    assignment_id: UUID | None = None
    message_id: str | None = None
    conversation_id: str | None = None
    recipient: str | None = None


class WorkflowResult(BaseModel):
    deliveries: list[DeliveryInstruction] = Field(default_factory=list)
