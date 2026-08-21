from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from humanwire.council_gateway import CouncilGatewayResult
from humanwire.council_projection import build_council_projection
from humanwire.council_runtime import CouncilRunOutput
from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    DecisionWorkspace,
    MembershipStatus,
    OrganizationMembership,
    WorkspacePlaybook,
)
from humanwire.google_council import (
    CouncilExecutionEvent,
    CouncilExecutionStatus,
)
from humanwire.mission_models import (
    MissionActorType,
    MissionBlockedReason,
    MissionMode,
    MissionParticipant,
    MissionRequest,
    MissionState,
)
from humanwire.mission_service import MissionService, MissionServiceUnavailable
from humanwire.mission_store import InMemoryMissionRepository
from humanwire.mission_transport import (
    MissionDispatchOutcome,
    MissionInboundResponse,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
ORG = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
WORKSPACE = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
MISSION = "mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
SUBJECT = "sub_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
HUMAN = "human-01hq7xk9wph4y8zqk3r2n1m6aa"


class FixedIdentifiers:
    def mission_id(self) -> str:
        return MISSION


class FixedResolver:
    def __init__(self, participants: tuple[MissionParticipant, ...]) -> None:
        self.participants = participants
        self.calls = 0

    def resolve(self, _context, _workspace, _request):
        self.calls += 1
        return self.participants


class RecordingCouncil:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, _context, _workspace, objective, *, cancellation, on_event):
        assert cancellation.is_set() is False
        self.calls.append(objective)
        event = CouncilExecutionEvent(
            ordinal=1,
            specialist_id="market_intelligence",
            display_name="Market Intelligence",
            status=CouncilExecutionStatus.COMPLETED,
        )
        on_event(event)
        projection = build_council_projection(
            run_id="council_run_01",
            objective=objective,
            events=(event,),
        ).model_copy(
            update={
                "state": "human_approval_required",
                "recommendation_summary": "Launch with a bounded pilot.",
                "recommended_action": "Approve a limited launch.",
                "required_human_action": "Confirm the decision owner.",
                "recommendation_digest": "a" * 64,
            }
        )
        return CouncilRunOutput(
            run_id="council_run_01",
            projection=projection,
            gateway=CouncilGatewayResult(
                accepted=True,
                reason="accepted",
                recommendation_digest="a" * 64,
                requires_human_approval=True,
            ),
        )


class RecordingDispatcher:
    def __init__(self, readiness: str | None = None) -> None:
        self.readiness_code = readiness
        self.calls: list[str] = []

    def check_readiness(self, _context, _snapshot, participant):
        self.calls.append(f"ready:{participant.participant_id}")
        return self.readiness_code

    def dispatch(self, _context, snapshot, participant):
        self.calls.append(f"send:{participant.participant_id}")
        return MissionDispatchOutcome(
            code="delivered",
            delivery_id="delivery-01",
            route_id="route-email-owner",
            occurred_at=NOW,
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


def workspace() -> DecisionWorkspace:
    return DecisionWorkspace(
        workspace_id=WORKSPACE,
        organization_id=ORG,
        name="Launch decisions",
        playbook=WorkspacePlaybook.LAUNCH_DECISION,
        created_by_uid="firebase-owner-01",
    )


def participant(actor_type: MissionActorType) -> MissionParticipant:
    values = {
        MissionActorType.AI_SPECIALIST: (
            "ai-market-intelligence",
            "Market Intelligence",
            "Market Intelligence AI",
            None,
            False,
        ),
        MissionActorType.DEMO_STAKEHOLDER: (
            "demo-decision-owner",
            "Sofia Alvarez",
            "Decision owner AI",
            None,
            True,
        ),
        MissionActorType.HUMAN_MEMBER: (
            HUMAN,
            "Avery Morgan",
            "Decision owner",
            SUBJECT,
            True,
        ),
    }[actor_type]
    return MissionParticipant(
        participant_id=values[0],
        actor_type=actor_type,
        display_name=values[1],
        role=values[2],
        subject_id=values[3],
        response_required=values[4],
    )


def request(mode: MissionMode) -> MissionRequest:
    return MissionRequest(
        mode=mode,
        objective="Approve the launch decision with current evidence.",
        urgency="standard",
        include_conflict=True,
    )


def service_fixture(
    mode: MissionMode,
    *,
    readiness: str | None = None,
) -> tuple[MissionService, RecordingCouncil, RecordingDispatcher]:
    participants = (
        participant(MissionActorType.AI_SPECIALIST),
        participant(
            MissionActorType.DEMO_STAKEHOLDER
            if mode is MissionMode.DEMO_RUN
            else MissionActorType.HUMAN_MEMBER
        ),
    )
    council = RecordingCouncil()
    dispatcher = RecordingDispatcher(readiness)
    return (
        MissionService(
            repository=InMemoryMissionRepository(
                clock=lambda: NOW,
                identifiers=FixedIdentifiers(),
            ),
            resolver=FixedResolver(participants),
            council=council,
            dispatcher=dispatcher,
            clock=lambda: NOW,
        ),
        council,
        dispatcher,
    )


def test_demo_run_executes_council_and_never_dispatches_provider() -> None:
    service, council, dispatcher = service_fixture(MissionMode.DEMO_RUN)
    created = service.create(context(), workspace(), request(MissionMode.DEMO_RUN))
    observed = []

    completed = service.run(
        context(),
        workspace(),
        created.mission_id,
        cancellation=threading.Event(),
        on_event=observed.append,
    )

    assert completed.state is MissionState.COMPLETE
    assert council.calls == [created.objective]
    assert dispatcher.calls == []
    assert [event.kind for event in completed.events] == [
        "mission.created",
        "mission.started",
        "council.specialist_completed",
        "council.completed",
        "stakeholder.response_recorded",
        "decision_brief.ready",
    ]
    assert observed[-1].kind == "decision_brief.ready"


def test_connected_run_blocks_before_council_when_provider_is_not_ready() -> None:
    service, council, dispatcher = service_fixture(
        MissionMode.CONNECTED_ORGANIZATION,
        readiness="provider_not_configured",
    )
    created = service.create(
        context(),
        workspace(),
        request(MissionMode.CONNECTED_ORGANIZATION),
    )

    blocked = service.run(
        context(),
        workspace(),
        created.mission_id,
        cancellation=threading.Event(),
    )

    assert blocked.state is MissionState.BLOCKED
    assert blocked.blocked_reason is MissionBlockedReason.PROVIDER_NOT_CONFIGURED
    assert council.calls == []
    assert dispatcher.calls == [f"ready:{HUMAN}"]
    assert blocked.events[-1].kind == "outreach.blocked"


def test_authenticated_reply_updates_the_exact_connected_assignment() -> None:
    service, _, dispatcher = service_fixture(MissionMode.CONNECTED_ORGANIZATION)
    created = service.create(
        context(),
        workspace(),
        request(MissionMode.CONNECTED_ORGANIZATION),
    )
    waiting = service.run(
        context(),
        workspace(),
        created.mission_id,
        cancellation=threading.Event(),
    )

    updated = service.record_response(
        context(),
        workspace(),
        MissionInboundResponse(
            mission_id=waiting.mission_id,
            participant_id=HUMAN,
            response_kind="fact",
            safe_summary="Launch dependency confirmed.",
            received_at=NOW,
        ),
    )

    assert waiting.state is MissionState.AWAITING_RESPONSE
    assert dispatcher.calls == [f"ready:{HUMAN}", f"send:{HUMAN}"]
    assert updated.state is MissionState.COMPLETE
    assert updated.events[-2].kind == "response.recorded"
    assert updated.events[-2].participant_id == HUMAN
    assert updated.events[-1].kind == "decision_brief.ready"


def test_reply_cannot_target_a_demo_or_non_outstanding_participant() -> None:
    service, _, _ = service_fixture(MissionMode.CONNECTED_ORGANIZATION)
    created = service.create(
        context(),
        workspace(),
        request(MissionMode.CONNECTED_ORGANIZATION),
    )

    with pytest.raises(MissionServiceUnavailable, match="mission_unavailable"):
        service.record_response(
            context(),
            workspace(),
            MissionInboundResponse(
                mission_id=created.mission_id,
                participant_id=HUMAN,
                response_kind="fact",
                safe_summary="Private route must remain unavailable.",
                received_at=NOW,
            ),
        )
