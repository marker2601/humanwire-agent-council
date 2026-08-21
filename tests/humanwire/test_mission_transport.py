from __future__ import annotations

from datetime import UTC, datetime

import pytest

from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
    OrganizationMembership,
)
from humanwire.domain import Channel
from humanwire.mission_models import (
    MissionActorType,
    MissionEvent,
    MissionMode,
    MissionParticipant,
    MissionSnapshot,
    MissionState,
)
from humanwire.mission_transport import (
    CaspianMissionTarget,
    CaspianMissionTransport,
    ConnectedMissionDispatcher,
    MissionDeliveryReceipt,
    MissionRoute,
    MissionTransportUnavailable,
    PreparedMissionOutreach,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
ORG = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
WORKSPACE = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
MISSION = "mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
SUBJECT = "sub_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"


class FixedRoutes:
    def __init__(self, routes: tuple[MissionRoute, ...]) -> None:
        self.routes = routes

    def consented_routes(self, _context, _subject_id) -> tuple[MissionRoute, ...]:
        return self.routes


class RecordingTransport:
    route_id = "provider-caspian"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def deliver(self, outreach):
        self.calls.append(outreach)
        if self.fail:
            raise RuntimeError("private provider failure alice@example.invalid")
        return MissionDeliveryReceipt(
            delivery_id="delivery-01",
            mission_id=outreach.mission_id,
            participant_id=outreach.participant_id,
            subject_id=outreach.subject_id,
            route_id=outreach.route_id,
            delivered_at=NOW,
        )


def context() -> DecisionOSContext:
    principal = DecisionOSPrincipal(
        uid="firebase-owner-01",
        email_verified=True,
        provider_ids=("google.com",),
    )
    return DecisionOSContext(
        principal=principal,
        membership=OrganizationMembership(
            organization_id=ORG,
            uid=principal.uid,
            role=DecisionOSRole.OWNER,
            status=MembershipStatus.ACTIVE,
        ),
    )


def participant(actor_type: MissionActorType) -> MissionParticipant:
    return MissionParticipant(
        participant_id=(
            "human-01hq7xk9wph4y8zqk3r2n1m6aa"
            if actor_type is MissionActorType.HUMAN_MEMBER
            else "demo-risk-stakeholder"
        ),
        actor_type=actor_type,
        display_name="Avery Morgan" if actor_type is MissionActorType.HUMAN_MEMBER else "Risk AI",
        role="Decision owner" if actor_type is MissionActorType.HUMAN_MEMBER else "Risk stakeholder",
        subject_id=SUBJECT if actor_type is MissionActorType.HUMAN_MEMBER else None,
        response_required=True,
    )


def snapshot(mode: MissionMode, person: MissionParticipant) -> MissionSnapshot:
    return MissionSnapshot(
        schema_version="humanwire.mission/v1",
        mission_id=MISSION,
        version=2,
        organization_id=ORG,
        workspace_id=WORKSPACE,
        mode=mode,
        state=MissionState.RUNNING,
        objective="Approve the launch decision with current evidence.",
        urgency="standard",
        include_conflict=True,
        participants=(person,),
        events=(
            MissionEvent(
                ordinal=1,
                kind="mission.created",
                stage="request",
                summary="Mission created.",
                participant_id=None,
                created_at=NOW,
            ),
        ),
        blocked_reason=None,
        created_at=NOW,
        updated_at=NOW,
    )


def route(*, consented: bool = True, active: bool = True) -> MissionRoute:
    return MissionRoute(
        route_id="route-email-owner",
        provider_route_id="provider-caspian",
        organization_id=ORG,
        subject_id=SUBJECT,
        channel=Channel.EMAIL,
        consented=consented,
        active=active,
    )


def test_demo_dispatch_performs_zero_transport_calls() -> None:
    transport = RecordingTransport()
    person = participant(MissionActorType.DEMO_STAKEHOLDER)
    outcome = ConnectedMissionDispatcher(
        routes=FixedRoutes((route(),)),
        transport=transport,
        clock=lambda: NOW,
    ).dispatch(context(), snapshot(MissionMode.DEMO_RUN, person), person)

    assert outcome.code == "demo_inert"
    assert transport.calls == []


def test_connected_dispatch_requires_exact_consented_route() -> None:
    transport = RecordingTransport()
    person = participant(MissionActorType.HUMAN_MEMBER)
    outcome = ConnectedMissionDispatcher(
        routes=FixedRoutes((route(consented=False),)),
        transport=transport,
        clock=lambda: NOW,
    ).dispatch(context(), snapshot(MissionMode.CONNECTED_ORGANIZATION, person), person)

    assert outcome.code == "no_consented_route"
    assert transport.calls == []


def test_connected_dispatch_requires_configured_provider() -> None:
    person = participant(MissionActorType.HUMAN_MEMBER)
    outcome = ConnectedMissionDispatcher(
        routes=FixedRoutes((route(),)),
        transport=None,
        clock=lambda: NOW,
    ).dispatch(context(), snapshot(MissionMode.CONNECTED_ORGANIZATION, person), person)

    assert outcome.code == "provider_not_configured"


def test_readiness_check_never_sends_and_returns_exact_blocker() -> None:
    person = participant(MissionActorType.HUMAN_MEMBER)
    transport = RecordingTransport()
    dispatcher = ConnectedMissionDispatcher(
        routes=FixedRoutes((route(consented=False),)),
        transport=transport,
        clock=lambda: NOW,
    )

    blocker = dispatcher.check_readiness(
        context(),
        snapshot(MissionMode.CONNECTED_ORGANIZATION, person),
        person,
    )

    assert blocker == "no_consented_route"
    assert transport.calls == []


def test_connected_dispatch_records_exact_adapter_result() -> None:
    transport = RecordingTransport()
    person = participant(MissionActorType.HUMAN_MEMBER)
    outcome = ConnectedMissionDispatcher(
        routes=FixedRoutes((route(),)),
        transport=transport,
        clock=lambda: NOW,
    ).dispatch(context(), snapshot(MissionMode.CONNECTED_ORGANIZATION, person), person)

    assert outcome.code == "delivered"
    assert outcome.delivery_id == "delivery-01"
    assert len(transport.calls) == 1
    assert transport.calls[0].route_id == "route-email-owner"
    assert "Approve the launch decision" in transport.calls[0].text


def test_provider_exception_becomes_unknown_without_private_content() -> None:
    transport = RecordingTransport(fail=True)
    person = participant(MissionActorType.HUMAN_MEMBER)
    outcome = ConnectedMissionDispatcher(
        routes=FixedRoutes((route(),)),
        transport=transport,
        clock=lambda: NOW,
    ).dispatch(context(), snapshot(MissionMode.CONNECTED_ORGANIZATION, person), person)

    assert outcome.code == "delivery_state_unknown"
    assert "alice@example.invalid" not in outcome.model_dump_json()


class RecordingCaspianClient:
    def __init__(self) -> None:
        self.initiated = []
        self.sent = []

    def initiate(self, connection_id: str, *, recipient: str, text: str):
        self.initiated.append((connection_id, recipient, text))
        return {"id": "provider-email-01"}

    def send_message(self, conversation_id: str, *, text: str):
        self.sent.append((conversation_id, text))
        return {"id": "provider-telegram-01"}


def outreach(channel: Channel, route_id: str) -> PreparedMissionOutreach:
    return PreparedMissionOutreach(
        mission_id=MISSION,
        organization_id=ORG,
        participant_id="human-01hq7xk9wph4y8zqk3r2n1m6aa",
        subject_id=SUBJECT,
        route_id=route_id,
        channel=channel,
        text="Please share the launch facts and constraints.",
        prepared_at=NOW,
    )


def test_caspian_transport_uses_existing_client_without_registering_a_handler() -> None:
    client = RecordingCaspianClient()
    transport = CaspianMissionTransport(
        client=client,
        email_connection_id="email-connection-01",
        targets=(
            CaspianMissionTarget.email(
                route_id="route-email-owner",
                recipient="alice@example.invalid",
            ),
            CaspianMissionTarget.telegram(
                route_id="route-telegram-owner",
                conversation_id="telegram-conversation-private-01",
            ),
        ),
        clock=lambda: NOW,
    )

    email = transport.deliver(outreach(Channel.EMAIL, "route-email-owner"))
    telegram = transport.deliver(outreach(Channel.TELEGRAM, "route-telegram-owner"))

    assert email.delivery_id == "provider-email-01"
    assert telegram.delivery_id == "provider-telegram-01"
    assert len(client.initiated) == 1
    assert len(client.sent) == 1
    assert not hasattr(client, "handlers")
    assert "alice@example.invalid" not in repr(transport)
    assert "telegram-conversation-private-01" not in repr(transport)


def test_caspian_transport_rejects_unknown_or_cross_channel_target() -> None:
    transport = CaspianMissionTransport(
        client=RecordingCaspianClient(),
        email_connection_id="email-connection-01",
        targets=(
            CaspianMissionTarget.email(
                route_id="route-email-owner",
                recipient="alice@example.invalid",
            ),
        ),
        clock=lambda: NOW,
    )

    with pytest.raises(MissionTransportUnavailable, match="mission_transport_unavailable"):
        transport.deliver(outreach(Channel.TELEGRAM, "route-email-owner"))
