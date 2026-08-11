from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class MandateRecord(Base):
    __tablename__ = "hw_mandates"

    mandate_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    initiator_id: Mapped[str] = mapped_column(String(128), index=True)
    origin_channel: Mapped[str] = mapped_column(String(32))
    origin_conversation_id: Mapped[str] = mapped_column(String(255))
    origin_message_id: Mapped[str] = mapped_column(String(255))
    redacted_request: Mapped[str] = mapped_column(Text)
    objective: Mapped[str] = mapped_column(Text)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)


class StakeholderAssignmentRecord(Base):
    __tablename__ = "hw_assignments"

    assignment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mandate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hw_mandates.mandate_id"), index=True
    )
    person_id: Mapped[str] = mapped_column(String(128), index=True)
    department: Mapped[str] = mapped_column(String(128))
    direction: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    required: Mapped[bool]
    state: Mapped[str] = mapped_column(String(40), index=True)
    route_ids: Mapped[list[str]] = mapped_column(JSON)
    active_route_index: Mapped[int] = mapped_column(Integer)
    attempt_count: Mapped[int] = mapped_column(Integer)
    interview_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    first_contact_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class InterviewSessionRecord(Base):
    __tablename__ = "hw_interviews"
    __table_args__ = (
        Index(
            "uq_hw_active_interview_stakeholder",
            "mandate_id",
            "stakeholder_person_id",
            unique=True,
            sqlite_where=text("completed_at IS NULL"),
        ),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mandate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hw_mandates.mandate_id"), index=True
    )
    assignment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hw_assignments.assignment_id"), unique=True, index=True
    )
    stakeholder_person_id: Mapped[str] = mapped_column(String(128), index=True)
    questions: Mapped[list[str]] = mapped_column(JSON)
    current_question_index: Mapped[int] = mapped_column(Integer)
    current_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel_history: Mapped[list[str]] = mapped_column(JSON)
    default_visibility: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceItemRecord(Base):
    __tablename__ = "hw_evidence"

    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mandate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hw_mandates.mandate_id"), index=True
    )
    assignment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hw_assignments.assignment_id"), index=True
    )
    stakeholder_id: Mapped[str] = mapped_column(String(128), index=True)
    evidence_type: Mapped[str] = mapped_column(String(40))
    statement: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    source_message_id: Mapped[str] = mapped_column(String(255))
    channel: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    related_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)


class AlignmentIssueRecord(Base):
    __tablename__ = "hw_issues"

    issue_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mandate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hw_mandates.mandate_id"), index=True
    )
    issue_type: Mapped[str] = mapped_column(String(40))
    evidence_ids: Mapped[list[str]] = mapped_column(JSON)
    stakeholder_ids: Mapped[list[str]] = mapped_column(JSON)
    related_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    blocking: Mapped[bool]
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProposalRecord(Base):
    __tablename__ = "hw_proposals"

    proposal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mandate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hw_mandates.mandate_id"), index=True
    )
    round_number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    issue_ids: Mapped[list[str]] = mapped_column(JSON)
    required_respondent_ids: Mapped[list[str]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ProposalResponseRecord(Base):
    __tablename__ = "hw_proposal_responses"

    response_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hw_proposals.proposal_id"), index=True
    )
    stakeholder_id: Mapped[str] = mapped_column(String(128), index=True)
    response: Mapped[str] = mapped_column(String(32))
    change_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_message_id: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)


class MeetingPackageRecord(Base):
    __tablename__ = "hw_meeting_packages"

    meeting_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mandate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hw_mandates.mandate_id"), unique=True, index=True
    )
    purpose: Mapped[str] = mapped_column(Text)
    decision_owner_id: Mapped[str] = mapped_column(String(128))
    required_attendee_ids: Mapped[list[str]] = mapped_column(JSON)
    optional_attendee_ids: Mapped[list[str]] = mapped_column(JSON)
    proposed_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proposed_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64))
    agreed_facts: Mapped[list[str]] = mapped_column(JSON)
    open_decisions: Mapped[list[str]] = mapped_column(JSON)
    agenda: Mapped[list[str]] = mapped_column(JSON)
    pre_read_evidence_ids: Mapped[list[str]] = mapped_column(JSON)
    calendar_written: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DomainEventRecord(Base):
    __tablename__ = "hw_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mandate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hw_mandates.mandate_id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    assignment_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    person_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    previous_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class RuntimeStatusRecord(Base):
    __tablename__ = "hw_runtime_status"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    engine_kwargs: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    if database_url == "sqlite://":
        engine_kwargs["poolclass"] = StaticPool

    engine = create_engine(database_url, **engine_kwargs)
    if database_url.startswith("sqlite"):
        _enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
