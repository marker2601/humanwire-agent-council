from __future__ import annotations

from datetime import UTC, datetime

import pytest

from humanwire.domain import Channel, ContactRoute, Direction, IncomingMessage, Person


def _person(
    person_id: str,
    display_name: str,
    role: str,
    department: str,
    *,
    manager_id: str | None = None,
    routes: list[ContactRoute] | None = None,
) -> Person:
    return Person(
        person_id=person_id,
        display_name=display_name,
        aliases=[display_name.split()[0]],
        role=role,
        department=department,
        timezone="America/Chicago",
        manager_id=manager_id,
        routes=routes or [],
    )


@pytest.fixture
def directory():
    from humanwire.directory import InitiatorPolicy, OrganizationDirectory, OrganizationDocument

    people = [
        _person("ceo", "Jordan Lee", "CEO", "Executive"),
        _person("coo", "Maya Chen", "COO", "Executive", manager_id="ceo"),
        _person("vp-support", "Nora Williams", "VP Support", "Support", manager_id="coo"),
        _person(
            "manager",
            "Arun Patel",
            "Support Manager",
            "Support",
            manager_id="vp-support",
            routes=[
                ContactRoute(
                    route_id="manager-telegram",
                    channel=Channel.TELEGRAM,
                    sender_address="manager-telegram",
                    conversation_id="tg-arun",
                    preferred=True,
                )
            ],
        ),
        _person("team-lead", "US Team Lead", "Team Lead", "Support", manager_id="manager"),
        _person(
            "vp-people",
            "Priya Raman",
            "VP People",
            "People",
            manager_id="coo",
            routes=[
                ContactRoute(
                    route_id="priya-telegram",
                    channel=Channel.TELEGRAM,
                    sender_address="priya-telegram",
                    conversation_id="tg-priya",
                ),
                ContactRoute(
                    route_id="priya-email",
                    channel=Channel.EMAIL,
                    sender_address="priya@example.test",
                    recipient="priya@example.test",
                    preferred=True,
                ),
            ],
        ),
        _person("cfo", "Taylor Kim", "CFO", "Finance", manager_id="ceo"),
    ]
    people[-1].aliases.append("Jordan")
    return OrganizationDirectory(
        OrganizationDocument(
            people=people,
            initiator_policies=[
                InitiatorPolicy(
                    person_id="manager",
                    allowed_directions={Direction.DOWNWARD, Direction.LATERAL, Direction.UPWARD},
                    allowed_departments={"Support", "People"},
                    max_upward_levels=1,
                )
            ],
        )
    )


@pytest.fixture
def telegram_message() -> IncomingMessage:
    return IncomingMessage(
        message_id="message-1",
        conversation_id="tg-arun",
        connection_id="connection-1",
        channel=Channel.TELEGRAM,
        sender_address="MANAGER-TELEGRAM",
        sender_name="Arun Patel",
        text="/mandate\nImprove support operations",
        received_at=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
    )


def test_classifies_downward_lateral_and_upward_routes(directory) -> None:
    assert directory.classify_direction("manager", "team-lead") is Direction.DOWNWARD
    assert directory.classify_direction("manager", "vp-people") is Direction.LATERAL
    assert directory.classify_direction("manager", "vp-support") is Direction.UPWARD


def test_policy_blocks_unapproved_target(directory) -> None:
    from humanwire.directory import UnauthorizedTargetError

    with pytest.raises(UnauthorizedTargetError):
        directory.validate_target("manager", "cfo", Direction.LATERAL)


def test_orders_preferred_then_alternate_deliverable_routes(directory) -> None:
    routes = directory.ordered_routes("vp-people")
    assert [route.channel for route in routes] == [Channel.EMAIL, Channel.TELEGRAM]
    assert routes[1].conversation_id == "tg-priya"


def test_matches_initiator_from_registered_sender(directory, telegram_message) -> None:
    assert directory.person_for_sender(telegram_message).person_id == "manager"
    assert directory.is_authorized_initiator(telegram_message) is True


def test_rejects_ambiguous_aliases_instead_of_selecting_a_person(directory) -> None:
    from humanwire.directory import AmbiguousPersonError

    with pytest.raises(AmbiguousPersonError):
        directory.resolve_person("Jordan")


def test_rejects_direction_mismatches_before_authority_checks(directory) -> None:
    from humanwire.directory import UnauthorizedTargetError

    with pytest.raises(UnauthorizedTargetError):
        directory.validate_target("manager", "team-lead", Direction.UPWARD)
