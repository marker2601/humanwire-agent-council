"""Strict public contracts for one HumanWire coordination mission."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_ORGANIZATION_ID = rf"^org_{_ULID}$"
_WORKSPACE_ID = rf"^wrk_{_ULID}$"
_MISSION_ID = rf"^mis_{_ULID}$"
_SUBJECT_ID = rf"^sub_{_ULID}$"
_PARTICIPANT_ID = r"^[a-z][a-z0-9-]{2,63}$"
_EVENT_KIND = r"^[a-z][a-z0-9_.]{0,63}$"
_STAGE = r"^[a-z][a-z0-9_]{0,31}$"


class _MissionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MissionMode(StrEnum):
    DEMO_RUN = "demo_run"
    CONNECTED_ORGANIZATION = "connected_organization"


class MissionActorType(StrEnum):
    AI_SPECIALIST = "ai_specialist"
    DEMO_STAKEHOLDER = "demo_stakeholder"
    HUMAN_MEMBER = "human_member"


class MissionState(StrEnum):
    READY = "ready"
    RUNNING = "running"
    AWAITING_RESPONSE = "awaiting_response"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    FAILED = "failed"


class MissionBlockedReason(StrEnum):
    ORGANIZATION_NOT_READY = "organization_not_ready"
    NO_ELIGIBLE_PARTICIPANT = "no_eligible_participant"
    NO_CONSENTED_ROUTE = "no_consented_route"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERY_STATE_UNKNOWN = "delivery_state_unknown"


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _safe_display(value: str) -> str:
    if type(value) is not str:
        raise TypeError("display text is invalid")
    normalized = " ".join(value.split())
    if (
        not normalized
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        or any(character in "<>&\"'" for character in normalized)
    ):
        raise ValueError("display text is invalid")
    return normalized


class MissionRequest(_MissionModel):
    mode: MissionMode = Field(strict=False)
    objective: str = Field(min_length=12, max_length=1000)
    urgency: Literal["standard", "urgent"] = "standard"
    include_conflict: bool = True

    @field_validator("objective")
    @classmethod
    def objective_is_safe(cls, value: str) -> str:
        return _safe_display(value)


class MissionParticipant(_MissionModel):
    participant_id: str = Field(pattern=_PARTICIPANT_ID)
    actor_type: MissionActorType = Field(strict=False)
    display_name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)
    subject_id: str | None = Field(default=None, pattern=_SUBJECT_ID)
    response_required: bool

    @field_validator("display_name", "role")
    @classmethod
    def display_fields_are_safe(cls, value: str) -> str:
        return _safe_display(value)

    @model_validator(mode="after")
    def identity_matches_actor_type(self) -> Self:
        if self.actor_type is MissionActorType.HUMAN_MEMBER:
            if self.subject_id is None:
                raise ValueError("human participant requires a subject")
        elif self.subject_id is not None:
            raise ValueError("AI participant cannot bind a subject")
        return self


class MissionEvent(_MissionModel):
    ordinal: int = Field(ge=1, le=10_000)
    kind: str = Field(pattern=_EVENT_KIND)
    stage: str = Field(pattern=_STAGE)
    summary: str = Field(min_length=1, max_length=240)
    participant_id: str | None = Field(default=None, pattern=_PARTICIPANT_ID)
    created_at: datetime

    @field_validator("summary")
    @classmethod
    def summary_is_safe(cls, value: str) -> str:
        return _safe_display(value)

    @field_validator("created_at")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        return _aware_utc(value, "created_at")


class MissionSnapshot(_MissionModel):
    schema_version: Literal["humanwire.mission/v1"]
    mission_id: str = Field(pattern=_MISSION_ID)
    version: int = Field(ge=1, le=1_000_000)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    mode: MissionMode = Field(strict=False)
    state: MissionState = Field(strict=False)
    objective: str = Field(min_length=12, max_length=1000)
    urgency: Literal["standard", "urgent"]
    include_conflict: bool
    participants: tuple[MissionParticipant, ...] = Field(max_length=100)
    events: tuple[MissionEvent, ...] = Field(max_length=10_000)
    blocked_reason: MissionBlockedReason | None = Field(default=None, strict=False)
    created_at: datetime
    updated_at: datetime

    @field_validator("objective")
    @classmethod
    def objective_is_safe(cls, value: str) -> str:
        return _safe_display(value)

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime, info) -> datetime:
        return _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def is_one_consistent_mission(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        participant_ids = tuple(item.participant_id for item in self.participants)
        if len(set(participant_ids)) != len(participant_ids):
            raise ValueError("participant IDs must be unique")
        allowed = (
            {MissionActorType.AI_SPECIALIST, MissionActorType.DEMO_STAKEHOLDER}
            if self.mode is MissionMode.DEMO_RUN
            else {MissionActorType.AI_SPECIALIST, MissionActorType.HUMAN_MEMBER}
        )
        if any(item.actor_type not in allowed for item in self.participants):
            raise ValueError("participant mode is inconsistent")
        if [item.ordinal for item in self.events] != list(
            range(1, len(self.events) + 1)
        ):
            raise ValueError("event ordinals must be contiguous")
        known = set(participant_ids)
        if any(
            item.participant_id is not None and item.participant_id not in known
            for item in self.events
        ):
            raise ValueError("event participant is unknown")
        blocked = self.state is MissionState.BLOCKED
        if blocked != (self.blocked_reason is not None):
            raise ValueError("blocked reason must match blocked state")
        return self


def mission_id_is_valid(value: object) -> bool:
    return type(value) is str and re.fullmatch(_MISSION_ID, value) is not None


__all__ = [
    "MissionActorType",
    "MissionBlockedReason",
    "MissionEvent",
    "MissionMode",
    "MissionParticipant",
    "MissionRequest",
    "MissionSnapshot",
    "MissionState",
    "mission_id_is_valid",
]
