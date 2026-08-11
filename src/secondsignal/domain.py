from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Channel(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"


class CaseState(StrEnum):
    RECEIVED = "received"
    ANALYZED = "analyzed"
    AWAITING_VERIFICATION = "awaiting_verification"
    VERIFIED = "verified"
    DENIED = "denied"
    UNVERIFIED = "unverified"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    DELIVERY_FAILED = "delivery_failed"


class DeliveryKind(StrEnum):
    REPLY_TO_MESSAGE = "reply_to_message"
    SEND_TO_CONVERSATION = "send_to_conversation"
    INITIATE_EMAIL = "initiate_email"


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


class VerificationRoute(BaseModel):
    channel: Channel
    sender_address: str
    recipient: str | None = None
    conversation_id: str | None = None


class VerifiedIdentity(BaseModel):
    identity_id: str
    display_name: str
    aliases: list[str]
    routes: list[VerificationRoute]


class RiskAssessment(BaseModel):
    requested_action: str
    amount: float | None = None
    currency: str | None = None
    urgency: str = "unknown"
    secrecy_requested: bool = False
    financial_action: bool = False
    credential_request: bool = False
    link_or_qr_request: bool = False
    risk_signals: list[str] = Field(default_factory=list)
    safe_summary: str
    analyzer: str


class VerificationCase(BaseModel):
    case_id: UUID
    token: str
    reporter_address: str
    origin_channel: Channel
    origin_conversation_id: str
    origin_message_id: str
    redacted_message: str
    claimed_identity_id: str
    claimed_identity_name: str
    risk: RiskAssessment
    verification_route: VerificationRoute | None
    state: CaseState
    reason: str | None = None
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None = None
    idempotency_key: str


class CaseEvent(BaseModel):
    event_type: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeliveryInstruction(BaseModel):
    kind: DeliveryKind
    text: str
    case_token: str | None = None
    message_id: str | None = None
    conversation_id: str | None = None
    recipient: str | None = None


class WorkflowResult(BaseModel):
    deliveries: list[DeliveryInstruction] = Field(default_factory=list)
