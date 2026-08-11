from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from secondsignal.database import CaseEventRecord, CaseRecord, RuntimeStatusRecord
from secondsignal.domain import (
    CaseEvent,
    CaseState,
    Channel,
    RiskAssessment,
    VerificationCase,
    VerificationRoute,
)


class DuplicateCaseError(ValueError):
    """Raised when a case token or idempotency key already exists."""


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _record_from_case(case: VerificationCase) -> CaseRecord:
    return CaseRecord(
        case_id=str(case.case_id),
        token=case.token,
        reporter_address=case.reporter_address,
        origin_channel=case.origin_channel.value,
        origin_conversation_id=case.origin_conversation_id,
        origin_message_id=case.origin_message_id,
        redacted_message=case.redacted_message,
        claimed_identity_id=case.claimed_identity_id,
        claimed_identity_name=case.claimed_identity_name,
        risk=case.risk.model_dump(mode="json"),
        verification_route=(
            case.verification_route.model_dump(mode="json") if case.verification_route else None
        ),
        state=case.state.value,
        reason=case.reason,
        created_at=case.created_at,
        expires_at=case.expires_at,
        resolved_at=case.resolved_at,
        idempotency_key=case.idempotency_key,
    )


def _case_from_record(record: CaseRecord) -> VerificationCase:
    return VerificationCase(
        case_id=UUID(record.case_id),
        token=record.token,
        reporter_address=record.reporter_address,
        origin_channel=Channel(record.origin_channel),
        origin_conversation_id=record.origin_conversation_id,
        origin_message_id=record.origin_message_id,
        redacted_message=record.redacted_message,
        claimed_identity_id=record.claimed_identity_id,
        claimed_identity_name=record.claimed_identity_name,
        risk=RiskAssessment.model_validate(record.risk),
        verification_route=(
            VerificationRoute.model_validate(record.verification_route)
            if record.verification_route
            else None
        ),
        state=CaseState(record.state),
        reason=record.reason,
        created_at=_utc(record.created_at),
        expires_at=_utc(record.expires_at),
        resolved_at=_utc(record.resolved_at),
        idempotency_key=record.idempotency_key,
    )


def _copy_case_to_record(case: VerificationCase, record: CaseRecord) -> None:
    replacement = _record_from_case(case)
    for field in (
        "token",
        "reporter_address",
        "origin_channel",
        "origin_conversation_id",
        "origin_message_id",
        "redacted_message",
        "claimed_identity_id",
        "claimed_identity_name",
        "risk",
        "verification_route",
        "state",
        "reason",
        "created_at",
        "expires_at",
        "resolved_at",
        "idempotency_key",
    ):
        setattr(record, field, getattr(replacement, field))


class SqlAlchemyCaseRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add_case(self, case: VerificationCase) -> None:
        with self._session_factory() as session:
            session.add(_record_from_case(case))
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise DuplicateCaseError(case.token) from error

    def get_by_token(self, token: str) -> VerificationCase | None:
        with self._session_factory() as session:
            record = session.scalar(select(CaseRecord).where(CaseRecord.token == token))
            return _case_from_record(record) if record else None

    def get_by_idempotency_key(self, key: str) -> VerificationCase | None:
        with self._session_factory() as session:
            record = session.scalar(select(CaseRecord).where(CaseRecord.idempotency_key == key))
            return _case_from_record(record) if record else None

    def save_case(self, case: VerificationCase) -> None:
        with self._session_factory() as session:
            record = session.get(CaseRecord, str(case.case_id))
            if record is None:
                raise KeyError(str(case.case_id))
            _copy_case_to_record(case, record)
            session.commit()

    def append_event(self, case_id: UUID, event: CaseEvent) -> None:
        with self._session_factory() as session:
            session.add(
                CaseEventRecord(
                    case_id=str(case_id),
                    event_type=event.event_type,
                    created_at=event.created_at,
                    event_metadata=event.metadata,
                )
            )
            session.commit()

    def list_events(self, case_id: UUID) -> list[CaseEvent]:
        with self._session_factory() as session:
            records = session.scalars(
                select(CaseEventRecord)
                .where(CaseEventRecord.case_id == str(case_id))
                .order_by(CaseEventRecord.created_at, CaseEventRecord.event_id)
            ).all()
            return [
                CaseEvent(
                    event_type=record.event_type,
                    created_at=_utc(record.created_at),
                    metadata=record.event_metadata,
                )
                for record in records
            ]

    def list_recent(self, limit: int = 30) -> list[VerificationCase]:
        with self._session_factory() as session:
            records = session.scalars(
                select(CaseRecord).order_by(CaseRecord.created_at.desc()).limit(limit)
            ).all()
            return [_case_from_record(record) for record in records]

    def list_expired_pending(self, now: datetime) -> list[VerificationCase]:
        with self._session_factory() as session:
            records = session.scalars(
                select(CaseRecord)
                .where(
                    CaseRecord.state == CaseState.AWAITING_VERIFICATION.value,
                    CaseRecord.expires_at <= now,
                )
                .order_by(CaseRecord.expires_at)
            ).all()
            return [_case_from_record(record) for record in records]

    def set_runtime_status(self, key: str, value: str, updated_at: datetime) -> None:
        with self._session_factory() as session:
            record = session.get(RuntimeStatusRecord, key)
            if record is None:
                session.add(RuntimeStatusRecord(key=key, value=value, updated_at=updated_at))
            else:
                record.value = value
                record.updated_at = updated_at
            session.commit()

    def get_runtime_status(self, key: str) -> tuple[str, datetime] | None:
        with self._session_factory() as session:
            record = session.get(RuntimeStatusRecord, key)
            if record is None:
                return None
            updated_at = _utc(record.updated_at)
            assert updated_at is not None
            return record.value, updated_at
