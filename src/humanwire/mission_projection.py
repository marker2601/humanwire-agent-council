"""Strict browser-safe projection for one HumanWire mission."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from humanwire.mission_models import (
    MissionActorType,
    MissionBlockedReason,
    MissionEvent,
    MissionMode,
    MissionSnapshot,
    MissionState,
)
from humanwire.mission_store import _canonical

_EVENT_KINDS = frozenset(
    {
        "mission.created",
        "mission.started",
        "mission.failed",
        "council.specialist_started",
        "council.specialist_completed",
        "council.specialist_failed",
        "council.completed",
        "stakeholder.response_recorded",
        "outreach.sent",
        "outreach.blocked",
        "response.recorded",
        "decision_brief.ready",
    }
)
_PRIVATE_MARKERS = (
    "bearer ",
    "conversation_id",
    "conversation-private",
    "member_uid",
    "provider payload",
    "secret",
    "source identity",
    "token",
    "traceback",
)
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9.-]+\.[a-z]{2,}")


class MissionProjectionUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("mission_projection_unavailable")


class _ProjectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MissionParticipantProjection(_ProjectionModel):
    participant_id: str
    display_name: str
    role: str
    actor_label: Literal["AI specialist", "AI stakeholder", "Organization member"]
    response_required: bool


class MissionEventProjection(_ProjectionModel):
    ordinal: int
    kind: str
    stage: str
    summary: str
    participant_id: str | None
    created_at: str


class MissionProjection(_ProjectionModel):
    mission_id: str
    objective: str
    mode: MissionMode
    mode_label: Literal["Demo run", "Connected organization"]
    state: MissionState
    stage: str
    participants: tuple[MissionParticipantProjection, ...]
    events: tuple[MissionEventProjection, ...]
    next_action: str
    recommendation_summary: str | None
    delivery_status: str | None
    blocked_reason: MissionBlockedReason | None


def _public_text(value: str) -> bool:
    if type(value) is not str or _EMAIL.search(value) is not None:
        return False
    folded = value.casefold()
    return not any(marker in folded for marker in _PRIVATE_MARKERS)


def _actor_label(actor_type: MissionActorType) -> str:
    return {
        MissionActorType.AI_SPECIALIST: "AI specialist",
        MissionActorType.DEMO_STAKEHOLDER: "AI stakeholder",
        MissionActorType.HUMAN_MEMBER: "Organization member",
    }[actor_type]


def _next_action(snapshot: MissionSnapshot) -> str:
    if snapshot.state is MissionState.COMPLETE:
        return "Review the decision brief."
    if snapshot.state is MissionState.AWAITING_RESPONSE:
        return "Waiting for an organization response."
    if snapshot.state is MissionState.BLOCKED:
        return "Resolve the connected-mode readiness requirement."
    if snapshot.state is MissionState.FAILED:
        return "Review the saved activity and retry when ready."
    if snapshot.state is MissionState.RUNNING:
        return "HumanWire is coordinating the mission."
    return "Start the mission."


def _recommendation(events: tuple[MissionEvent, ...]) -> str | None:
    matches = tuple(item.summary for item in events if item.kind == "council.completed")
    return matches[-1] if matches else None


def _delivery(snapshot: MissionSnapshot) -> str | None:
    if any(item.kind == "response.recorded" for item in snapshot.events):
        return "response_recorded"
    if any(item.kind == "outreach.sent" for item in snapshot.events):
        return "delivered"
    if snapshot.blocked_reason is not None:
        return snapshot.blocked_reason.value
    return None


def build_mission_projection(snapshot: MissionSnapshot) -> MissionProjection:
    """Return a detached allowlisted view or fail closed on private/corrupt input."""

    try:
        canonical = _canonical(snapshot, MissionSnapshot)
        if not _public_text(canonical.objective) or any(
            item.kind not in _EVENT_KINDS
            or not _public_text(item.summary)
            or not _public_text(item.stage)
            for item in canonical.events
        ) or any(
            not _public_text(item.display_name) or not _public_text(item.role)
            for item in canonical.participants
        ):
            raise ValueError
        participants = tuple(
            MissionParticipantProjection(
                participant_id=item.participant_id,
                display_name=item.display_name,
                role=item.role,
                actor_label=_actor_label(item.actor_type),
                response_required=item.response_required,
            )
            for item in canonical.participants
        )
        events = tuple(
            MissionEventProjection(
                ordinal=item.ordinal,
                kind=item.kind,
                stage=item.stage,
                summary=item.summary,
                participant_id=item.participant_id,
                created_at=item.created_at.isoformat(),
            )
            for item in canonical.events
        )
        projection = MissionProjection(
            mission_id=canonical.mission_id,
            objective=canonical.objective,
            mode=canonical.mode,
            mode_label=(
                "Demo run"
                if canonical.mode is MissionMode.DEMO_RUN
                else "Connected organization"
            ),
            state=canonical.state,
            stage=canonical.events[-1].stage,
            participants=participants,
            events=events,
            next_action=_next_action(canonical),
            recommendation_summary=_recommendation(canonical.events),
            delivery_status=_delivery(canonical),
            blocked_reason=canonical.blocked_reason,
        )
        payload = BaseModel.model_dump_json(projection, warnings="error")
        if any(marker in payload.casefold() for marker in _PRIVATE_MARKERS):
            raise ValueError
    except Exception:  # noqa: BLE001 - private projection input fails closed
        raise MissionProjectionUnavailable() from None
    return projection


__all__ = [
    "MissionEventProjection",
    "MissionParticipantProjection",
    "MissionProjection",
    "MissionProjectionUnavailable",
    "build_mission_projection",
]
