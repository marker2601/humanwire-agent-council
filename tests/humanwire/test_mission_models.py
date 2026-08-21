from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from humanwire.mission_models import (
    MissionActorType,
    MissionBlockedReason,
    MissionEvent,
    MissionMode,
    MissionParticipant,
    MissionRequest,
    MissionSnapshot,
    MissionState,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
ORG = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
WORKSPACE = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
MISSION = "mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"


def participant(
    actor_type: MissionActorType,
    *,
    participant_id: str = "participant-market-intelligence",
) -> MissionParticipant:
    return MissionParticipant(
        participant_id=participant_id,
        actor_type=actor_type,
        display_name="Market Intelligence",
        role="Market Intelligence AI",
        subject_id=None,
        response_required=True,
    )


def snapshot(
    *,
    mode: MissionMode = MissionMode.DEMO_RUN,
    state: MissionState = MissionState.READY,
    participants: tuple[MissionParticipant, ...] = (),
    events: tuple[MissionEvent, ...] = (),
    blocked_reason: MissionBlockedReason | None = None,
) -> MissionSnapshot:
    return MissionSnapshot(
        schema_version="humanwire.mission/v1",
        mission_id=MISSION,
        version=1,
        organization_id=ORG,
        workspace_id=WORKSPACE,
        mode=mode,
        state=state,
        objective="Approve the launch decision with current evidence.",
        urgency="standard",
        include_conflict=True,
        participants=participants,
        events=events,
        blocked_reason=blocked_reason,
        created_at=NOW,
        updated_at=NOW,
    )


def test_connected_mission_rejects_demo_stakeholders() -> None:
    with pytest.raises(ValueError, match="participant mode"):
        snapshot(
            mode=MissionMode.CONNECTED_ORGANIZATION,
            participants=(participant(MissionActorType.DEMO_STAKEHOLDER),),
        )


def test_demo_mission_rejects_human_members() -> None:
    human = MissionParticipant(
        participant_id="participant-human-owner",
        actor_type=MissionActorType.HUMAN_MEMBER,
        display_name="Avery Morgan",
        role="Decision owner",
        subject_id="sub_01HQ7XK9WPH4Y8ZQK3R2N1M6AA",
        response_required=True,
    )
    with pytest.raises(ValueError, match="participant mode"):
        snapshot(participants=(human,))


def test_demo_request_rejects_browser_supplied_subject_ids() -> None:
    with pytest.raises(ValidationError):
        MissionRequest.model_validate(
            {
                "mode": "demo_run",
                "objective": "Approve the launch decision with current evidence.",
                "subject_ids": ["sub_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"],
            }
        )


def test_blocked_reason_exists_exactly_for_blocked_state() -> None:
    with pytest.raises(ValueError, match="blocked reason"):
        snapshot(
            state=MissionState.BLOCKED,
            blocked_reason=None,
        )
    with pytest.raises(ValueError, match="blocked reason"):
        snapshot(
            state=MissionState.READY,
            blocked_reason=MissionBlockedReason.NO_CONSENTED_ROUTE,
        )


def test_event_ordinals_are_contiguous_and_unique() -> None:
    events = (
        MissionEvent(
            ordinal=1,
            kind="mission.created",
            stage="request",
            summary="Mission created.",
            participant_id=None,
            created_at=NOW,
        ),
        MissionEvent(
            ordinal=3,
            kind="mission.started",
            stage="planning",
            summary="Mission started.",
            participant_id=None,
            created_at=NOW,
        ),
    )
    with pytest.raises(ValueError, match="event ordinals"):
        snapshot(events=events)


def test_human_member_requires_a_subject_and_ai_actor_forbids_one() -> None:
    with pytest.raises(ValueError, match="human participant"):
        participant(MissionActorType.HUMAN_MEMBER)
    with pytest.raises(ValueError, match="AI participant"):
        MissionParticipant(
            participant_id="participant-risk-ai",
            actor_type=MissionActorType.AI_SPECIALIST,
            display_name="Risk and Compliance",
            role="Risk and Compliance AI",
            subject_id="sub_01HQ7XK9WPH4Y8ZQK3R2N1M6AA",
            response_required=False,
        )


def test_snapshot_rejects_naive_or_reverse_timestamps() -> None:
    values = snapshot().model_dump(mode="python")
    values["created_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        MissionSnapshot.model_validate(values)

    values = snapshot().model_dump(mode="python")
    values["updated_at"] = datetime(2026, 8, 21, 11, 59, tzinfo=UTC)
    with pytest.raises(ValueError, match="updated_at"):
        MissionSnapshot.model_validate(values)

