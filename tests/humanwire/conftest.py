from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from humanwire.domain import Channel, ContactRoute, IncomingMessage, Person

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

MANAGER = Person(
    person_id="manager",
    display_name="Morgan Lee",
    aliases=["Morgan"],
    role="Operations Manager",
    department="Operations",
    timezone="America/Chicago",
    routes=[
        ContactRoute(
            route_id="manager-telegram",
            channel=Channel.TELEGRAM,
            sender_address="manager-chat",
            conversation_id="manager-conversation",
            preferred=True,
        )
    ],
)
TEAM_LEAD = Person(
    person_id="team-lead",
    display_name="Riley Chen",
    aliases=["Riley"],
    role="Team Lead",
    department="Operations",
    timezone="America/Chicago",
    manager_id="manager",
    routes=[
        ContactRoute(
            route_id="team-lead-email",
            channel=Channel.EMAIL,
            sender_address="team-lead@example.test",
            recipient="team-lead@example.test",
            preferred=True,
        )
    ],
)
VP_PEOPLE = Person(
    person_id="vp-people",
    display_name="Avery Patel",
    aliases=["Avery"],
    role="VP People",
    department="People",
    timezone="America/Chicago",
    routes=[
        ContactRoute(
            route_id="vp-people-email",
            channel=Channel.EMAIL,
            sender_address="vp-people@example.test",
            recipient="vp-people@example.test",
            preferred=True,
        )
    ],
)
VP_SUPPORT = Person(
    person_id="vp-support",
    display_name="Jordan Brooks",
    aliases=["Jordan"],
    role="VP Support",
    department="Support",
    timezone="America/Chicago",
    manager_id="coo",
    routes=[
        ContactRoute(
            route_id="vp-support-email",
            channel=Channel.EMAIL,
            sender_address="vp-support@example.test",
            recipient="vp-support@example.test",
            preferred=True,
        )
    ],
)
COO = Person(
    person_id="coo",
    display_name="Casey Nguyen",
    aliases=["Casey"],
    role="Chief Operating Officer",
    department="Executive",
    timezone="America/Chicago",
    routes=[
        ContactRoute(
            route_id="coo-email",
            channel=Channel.EMAIL,
            sender_address="coo@example.test",
            recipient="coo@example.test",
            preferred=True,
        )
    ],
)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def manager() -> Person:
    return MANAGER


@pytest.fixture
def people() -> list[Person]:
    return [MANAGER, TEAM_LEAD, VP_PEOPLE, VP_SUPPORT, COO]


@pytest.fixture
def incoming_message_factory() -> Callable[..., IncomingMessage]:
    def factory(
        *,
        text: str,
        channel: Channel = Channel.TELEGRAM,
        sender_address: str = "manager-chat",
        sender_name: str | None = "Morgan Lee",
        message_id: str = "message-1",
        conversation_id: str = "manager-conversation",
        connection_id: str = "connection-1",
        subject: str | None = None,
        received_at: datetime = NOW,
    ) -> IncomingMessage:
        return IncomingMessage(
            message_id=message_id,
            conversation_id=conversation_id,
            connection_id=connection_id,
            channel=channel,
            sender_address=sender_address,
            sender_name=sender_name,
            subject=subject,
            text=text,
            received_at=received_at,
        )

    return factory
