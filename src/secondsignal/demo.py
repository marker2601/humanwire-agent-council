"""Safe, deterministic application factory for the public competition demo."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import FastAPI

from secondsignal.config import Settings
from secondsignal.database import create_session_factory
from secondsignal.domain import (
    CaseEvent,
    CaseState,
    Channel,
    RiskAssessment,
    VerificationCase,
    VerificationRoute,
)
from secondsignal.repository import SqlAlchemyCaseRepository
from secondsignal.web import create_app


def _risk(
    action: str,
    summary: str,
    signals: list[str],
    *,
    amount: float | None = None,
    financial: bool = False,
    credential: bool = False,
) -> RiskAssessment:
    return RiskAssessment(
        requested_action=action,
        amount=amount,
        currency="USD" if amount is not None else None,
        urgency="high",
        secrecy_requested="secrecy request" in signals,
        financial_action=financial,
        credential_request=credential,
        link_or_qr_request="untrusted link" in signals,
        risk_signals=signals,
        safe_summary=summary,
        analyzer="rules + Qwen2.5-7B-Instruct",
    )


def _case(
    *,
    number: int,
    token: str,
    name: str,
    origin: Channel,
    verification: Channel,
    state: CaseState,
    created_at: datetime,
    risk: RiskAssessment,
    reason: str | None = None,
) -> VerificationCase:
    identity_slug = name.lower().replace(" ", "-")
    resolved_at = None if state is CaseState.AWAITING_VERIFICATION else created_at + timedelta(minutes=1)
    return VerificationCase(
        case_id=UUID(int=number),
        token=token,
        reporter_address=f"demo-reporter-{number}",
        origin_channel=origin,
        origin_conversation_id=f"demo-conversation-{number}",
        origin_message_id=f"demo-message-{number}",
        redacted_message=risk.safe_summary,
        claimed_identity_id=identity_slug,
        claimed_identity_name=name,
        risk=risk,
        verification_route=VerificationRoute(
            channel=verification,
            sender_address=f"demo-{identity_slug}",
            recipient=f"demo-{identity_slug}",
        ),
        state=state,
        reason=reason,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=10),
        resolved_at=resolved_at,
        idempotency_key=f"public-demo-{number}",
    )


def _seed(repository: SqlAlchemyCaseRepository, now: datetime) -> None:
    cases = [
        _case(
            number=1,
            token="SS-7K4P2M",
            name="Asha Rao",
            origin=Channel.TELEGRAM,
            verification=Channel.EMAIL,
            state=CaseState.DENIED,
            created_at=now - timedelta(minutes=3),
            risk=_risk(
                "Purchase gift cards",
                "Purchase $500 in gift cards for an urgent client request",
                ["gift card request", "artificial urgency", "secrecy request"],
                amount=500,
                financial=True,
            ),
            reason="human_denied",
        ),
        _case(
            number=2,
            token="SS-3B9L7Q",
            name="Michael Chen",
            origin=Channel.EMAIL,
            verification=Channel.TELEGRAM,
            state=CaseState.VERIFIED,
            created_at=now - timedelta(minutes=7),
            risk=_risk(
                "Approve a wire transfer",
                "Approve an urgent $12,400 vendor wire transfer",
                ["payment request", "artificial urgency"],
                amount=12_400,
                financial=True,
            ),
            reason="human_approved",
        ),
        _case(
            number=3,
            token="SS-1H8R5T",
            name="Finance Operations",
            origin=Channel.TELEGRAM,
            verification=Channel.EMAIL,
            state=CaseState.AWAITING_VERIFICATION,
            created_at=now - timedelta(minutes=1),
            risk=_risk(
                "Change payroll details",
                "Change payroll deposit details before today's cutoff",
                ["account change", "artificial urgency"],
                financial=True,
            ),
        ),
        _case(
            number=4,
            token="SS-9G2D1V",
            name="Priya Nair",
            origin=Channel.EMAIL,
            verification=Channel.TELEGRAM,
            state=CaseState.VERIFIED,
            created_at=now - timedelta(minutes=18),
            risk=_risk(
                "Share a temporary access code",
                "Share a temporary administrative access code",
                ["credential request", "secrecy request"],
                credential=True,
            ),
            reason="human_approved",
        ),
        _case(
            number=5,
            token="SS-6M3F8J",
            name="IT Support",
            origin=Channel.TELEGRAM,
            verification=Channel.EMAIL,
            state=CaseState.EXPIRED,
            created_at=now - timedelta(minutes=34),
            risk=_risk(
                "Open a security reset page",
                "Open a password reset page and sign in",
                ["untrusted link", "credential request"],
                credential=True,
            ),
            reason="verification_timeout",
        ),
    ]

    final_events = {
        CaseState.VERIFIED: "case.verified",
        CaseState.DENIED: "case.denied",
        CaseState.EXPIRED: "case.expired",
    }
    for case in cases:
        repository.add_case(case)
        events = [
            ("case.created", case.created_at),
            ("case.analyzed", case.created_at + timedelta(seconds=1)),
            ("case.awaiting_verification", case.created_at + timedelta(seconds=2)),
            ("verification.requested", case.created_at + timedelta(seconds=3)),
        ]
        final_event = final_events.get(case.state)
        if final_event and case.resolved_at:
            events.append((final_event, case.resolved_at))
        for event_type, created_at in events:
            repository.append_event(
                case.case_id,
                CaseEvent(event_type=event_type, created_at=created_at),
            )

    for key, value in (
        ("channel.email", "ready"),
        ("channel.telegram", "ready"),
        ("listener.heartbeat", "alive"),
    ):
        repository.set_runtime_status(key, value, now)


def create_demo_app() -> FastAPI:
    """Create the read-only public demo without loading credentials or private data."""
    now = datetime.now(UTC)
    repository = SqlAlchemyCaseRepository(create_session_factory("sqlite://"))
    _seed(repository, now)
    settings = Settings(database_url="sqlite://", expiry_poll_seconds=5)
    return create_app(repository, settings, clock=lambda: now, demo_mode=True)
