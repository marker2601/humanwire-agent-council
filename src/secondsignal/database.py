from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class CaseRecord(Base):
    __tablename__ = "verification_cases"

    case_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    reporter_address: Mapped[str] = mapped_column(String(320))
    origin_channel: Mapped[str] = mapped_column(String(32))
    origin_conversation_id: Mapped[str] = mapped_column(String(255))
    origin_message_id: Mapped[str] = mapped_column(String(255))
    redacted_message: Mapped[str] = mapped_column(Text)
    claimed_identity_id: Mapped[str] = mapped_column(String(100))
    claimed_identity_name: Mapped[str] = mapped_column(String(200))
    risk: Mapped[dict[str, Any]] = mapped_column(JSON)
    verification_route: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class CaseEventRecord(Base):
    __tablename__ = "case_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("verification_cases.case_id"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class RuntimeStatusRecord(Base):
    __tablename__ = "runtime_status"

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
