from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from secondsignal.database import create_session_factory
from secondsignal.domain import (
    CaseState,
    Channel,
    RiskAssessment,
    VerificationCase,
    VerificationRoute,
)
from secondsignal.repository import SqlAlchemyCaseRepository

NOW = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)


@pytest.fixture
def make_case() -> Callable[..., VerificationCase]:
    def factory(**updates) -> VerificationCase:
        values = {
            "case_id": uuid4(),
            "token": "SS-7K4P2M",
            "reporter_address": "reporter-tg",
            "origin_channel": Channel.TELEGRAM,
            "origin_conversation_id": "conv-reporter",
            "origin_message_id": "msg-report",
            "redacted_message": "Buy $500 in gift cards",
            "claimed_identity_id": "asha-rao",
            "claimed_identity_name": "Asha Rao",
            "risk": RiskAssessment(
                requested_action="Purchase gift cards",
                amount=500,
                currency="USD",
                urgency="high",
                secrecy_requested=True,
                financial_action=True,
                risk_signals=["gift card request", "artificial urgency"],
                safe_summary="Purchase $500 in gift cards",
                analyzer="rules",
            ),
            "verification_route": VerificationRoute(
                channel=Channel.EMAIL,
                sender_address="asha@example.com",
                recipient="asha@example.com",
            ),
            "state": CaseState.AWAITING_VERIFICATION,
            "created_at": NOW,
            "expires_at": NOW + timedelta(minutes=10),
            "idempotency_key": "idem-1",
        }
        values.update(updates)
        return VerificationCase(**values)

    return factory


@pytest.fixture
def sample_case(make_case) -> VerificationCase:
    return make_case()


@pytest.fixture
def repository() -> SqlAlchemyCaseRepository:
    return SqlAlchemyCaseRepository(create_session_factory("sqlite://"))
