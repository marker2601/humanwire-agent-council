from __future__ import annotations

from datetime import UTC, datetime

import pytest

from humanwire.mission_models import (
    MissionActorType,
    MissionEvent,
    MissionMode,
    MissionParticipant,
    MissionSnapshot,
    MissionState,
)
from humanwire.mission_projection import (
    MissionProjectionUnavailable,
    build_mission_projection,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
ORG = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
WORKSPACE = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
MISSION = "mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
SUBJECT = "sub_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"


def event(
    ordinal: int,
    kind: str,
    stage: str,
    summary: str,
    participant_id: str | None = None,
) -> MissionEvent:
    return MissionEvent(
        ordinal=ordinal,
        kind=kind,
        stage=stage,
        summary=summary,
        participant_id=participant_id,
        created_at=NOW,
    )


def demo_snapshot() -> MissionSnapshot:
    participants = (
        MissionParticipant(
            participant_id="ai-market-intelligence",
            actor_type=MissionActorType.AI_SPECIALIST,
            display_name="Market Intelligence",
            role="Market Intelligence AI",
            response_required=False,
        ),
        MissionParticipant(
            participant_id="demo-decision-owner",
            actor_type=MissionActorType.DEMO_STAKEHOLDER,
            display_name="Sofia Alvarez",
            role="Decision owner AI",
            response_required=True,
        ),
    )
    return MissionSnapshot(
        schema_version="humanwire.mission/v1",
        mission_id=MISSION,
        version=6,
        organization_id=ORG,
        workspace_id=WORKSPACE,
        mode=MissionMode.DEMO_RUN,
        state=MissionState.COMPLETE,
        objective="Approve the launch decision with current evidence.",
        urgency="standard",
        include_conflict=True,
        participants=participants,
        events=(
            event(1, "mission.created", "request", "Mission created."),
            event(2, "mission.started", "outreach", "Mission started."),
            event(
                3,
                "council.specialist_completed",
                "analysis",
                "Market Intelligence completed analysis.",
                "ai-market-intelligence",
            ),
            event(
                4,
                "council.completed",
                "synthesis",
                "Launch with a bounded pilot.",
            ),
            event(
                5,
                "stakeholder.response_recorded",
                "evidence",
                "AI stakeholder evidence recorded.",
                "demo-decision-owner",
            ),
            event(6, "decision_brief.ready", "decision", "Decision brief ready."),
        ),
        blocked_reason=None,
        created_at=NOW,
        updated_at=NOW,
    )


def test_projection_contains_mode_and_safe_actor_labels() -> None:
    projection = build_mission_projection(demo_snapshot())

    assert projection.mode_label == "Demo run"
    assert projection.stage == "decision"
    assert projection.recommendation_summary == "Launch with a bounded pilot."
    assert projection.next_action == "Review the decision brief."
    assert all(
        "fabricated" not in item.display_name.casefold()
        for item in projection.participants
    )
    assert {item.actor_label for item in projection.participants} == {
        "AI specialist",
        "AI stakeholder",
    }


def test_connected_projection_labels_human_and_waiting_action() -> None:
    human = MissionParticipant(
        participant_id="human-01hq7xk9wph4y8zqk3r2n1m6aa",
        actor_type=MissionActorType.HUMAN_MEMBER,
        display_name="Avery Morgan",
        role="Decision owner",
        subject_id=SUBJECT,
        response_required=True,
    )
    snapshot = demo_snapshot().model_copy(
        update={
            "mode": MissionMode.CONNECTED_ORGANIZATION,
            "state": MissionState.AWAITING_RESPONSE,
            "participants": (demo_snapshot().participants[0], human),
            "events": (
                event(1, "mission.created", "request", "Mission created."),
                event(2, "mission.started", "outreach", "Mission started."),
                event(
                    3,
                    "outreach.sent",
                    "outreach",
                    "Outreach sent through a consented route.",
                    human.participant_id,
                ),
            ),
        }
    )

    projection = build_mission_projection(snapshot)

    assert projection.mode_label == "Connected organization"
    assert projection.participants[1].actor_label == "Organization member"
    assert projection.delivery_status == "delivered"
    assert projection.next_action == "Waiting for an organization response."
    assert SUBJECT not in projection.model_dump_json()


@pytest.mark.parametrize(
    "private_value",
    [
        "alice@example.invalid",
        "telegram-conversation-private-01",
        "Bearer private-token-value",
        "Traceback provider-private-marker",
    ],
)
def test_projection_rejects_private_route_values(private_value: str) -> None:
    poisoned = demo_snapshot().model_copy(
        update={
            "events": (
                *demo_snapshot().events[:-1],
                event(6, "decision_brief.ready", "decision", private_value),
            )
        }
    )

    with pytest.raises(
        MissionProjectionUnavailable,
        match="mission_projection_unavailable",
    ):
        build_mission_projection(poisoned)


def test_projection_rejects_unknown_event_kind() -> None:
    poisoned = demo_snapshot().model_copy(
        update={
            "events": (
                *demo_snapshot().events[:-1],
                event(6, "provider.raw_payload", "decision", "Decision brief ready."),
            )
        }
    )

    with pytest.raises(MissionProjectionUnavailable):
        build_mission_projection(poisoned)


def test_projection_rejects_private_objective_before_serialization() -> None:
    poisoned = demo_snapshot().model_copy(
        update={"objective": "Ask alice@example.invalid to approve the launch."}
    )

    with pytest.raises(MissionProjectionUnavailable):
        build_mission_projection(poisoned)
