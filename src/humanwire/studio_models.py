"""Product-facing request and catalog models for HumanWire coordination runs."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class _StudioModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RequesterRole(StrEnum):
    MANAGER = "manager"
    EXECUTIVE = "executive"
    PROGRAM_LEAD = "program_lead"
    TEAM_LEAD = "team_lead"


class TargetTiming(StrEnum):
    TOMORROW = "tomorrow"
    NEXT_BUSINESS_DAY = "next_business_day"
    CUSTOM = "custom"


class StudioAgentMode(StrEnum):
    STANDARD = "standard"
    MODEL_ASSISTED = "model_assisted"


class StakeholderCard(_StudioModel):
    persona_id: str = Field(pattern=_SAFE_ID)
    display_name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=200)
    engagement_label: str = Field(min_length=1, max_length=80)


class CoordinationTemplate(_StudioModel):
    template_id: str = Field(pattern=_SAFE_ID)
    title: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=12, max_length=1000)
    requester_role: RequesterRole = Field(strict=False)
    participant_ids: tuple[str, ...] = Field(min_length=3, max_length=8)
    target_timing: TargetTiming = Field(strict=False)
    include_conflict: bool = True


class StudioCatalog(_StudioModel):
    stakeholders: tuple[StakeholderCard, ...] = Field(min_length=1, max_length=8)
    templates: tuple[CoordinationTemplate, ...] = Field(min_length=1, max_length=3)


class CoordinationRequest(_StudioModel):
    template_id: str | None = Field(default=None, pattern=_SAFE_ID)
    objective: str = Field(min_length=12, max_length=1000)
    requester_name: Literal["Alex Morgan"] = "Alex Morgan"
    requester_role: RequesterRole = Field(strict=False)
    participant_ids: tuple[str, ...] = Field(min_length=3, max_length=8, strict=False)
    target_timing: TargetTiming = Field(strict=False)
    custom_date: date | None = None
    include_conflict: bool = True
    agent_mode: StudioAgentMode = Field(default=StudioAgentMode.STANDARD, strict=False)

    @model_validator(mode="after")
    def has_valid_participants_and_timing(self) -> Self:
        allowed = {person.persona_id for person in product_catalog().stakeholders}
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValueError("participant IDs must be unique")
        if not set(self.participant_ids) <= allowed:
            raise ValueError("participant IDs must come from the product catalog")
        if (self.target_timing is TargetTiming.CUSTOM) != (self.custom_date is not None):
            raise ValueError("custom timing requires exactly one custom date")
        return self


_STAKEHOLDERS = (
    StakeholderCard(
        persona_id="inform",
        display_name="Maya Chen",
        role="Executive sponsor",
        engagement_label="Inform",
    ),
    StakeholderCard(
        persona_id="ack",
        display_name="Nora Jensen",
        role="Communications lead",
        engagement_label="Acknowledge",
    ),
    StakeholderCard(
        persona_id="quick-a",
        display_name="Priya Shah",
        role="Product lead",
        engagement_label="Quick response",
    ),
    StakeholderCard(
        persona_id="quick-b",
        display_name="Marcus Reed",
        role="Engineering lead",
        engagement_label="Quick response",
    ),
    StakeholderCard(
        persona_id="structured",
        display_name="Anika Rao",
        role="Risk & compliance lead",
        engagement_label="Structured interview",
    ),
    StakeholderCard(
        persona_id="approval",
        display_name="Sofia Alvarez",
        role="Approval owner",
        engagement_label="Review and approval",
    ),
    StakeholderCard(
        persona_id="availability",
        display_name="Daniel Brooks",
        role="Operations lead",
        engagement_label="Availability",
    ),
    StakeholderCard(
        persona_id="approval-change",
        display_name="Elena Torres",
        role="Business owner",
        engagement_label="Change authority",
    ),
)

_TEMPLATES = (
    CoordinationTemplate(
        template_id="launch-decision",
        title="Launch decision",
        objective="Set up a decision meeting tomorrow to approve the launch plan.",
        requester_role=RequesterRole.MANAGER,
        participant_ids=(
            "inform",
            "ack",
            "quick-a",
            "quick-b",
            "structured",
            "approval",
            "availability",
        ),
        target_timing=TargetTiming.TOMORROW,
        include_conflict=True,
    ),
    CoordinationTemplate(
        template_id="cross-team-conflict",
        title="Resolve a cross-team conflict",
        objective=(
            "Resolve the launch-readiness disagreement between Product, "
            "Engineering, and Risk."
        ),
        requester_role=RequesterRole.PROGRAM_LEAD,
        participant_ids=("quick-a", "quick-b", "structured", "approval"),
        target_timing=TargetTiming.NEXT_BUSINESS_DAY,
        include_conflict=True,
    ),
    CoordinationTemplate(
        template_id="executive-decision-review",
        title="Executive decision review",
        objective="Prepare the minimum evidence needed for an executive go/no-go review.",
        requester_role=RequesterRole.EXECUTIVE,
        participant_ids=("inform", "quick-a", "quick-b", "structured", "approval"),
        target_timing=TargetTiming.NEXT_BUSINESS_DAY,
        include_conflict=True,
    ),
)

_PRODUCT_CATALOG = StudioCatalog(stakeholders=_STAKEHOLDERS, templates=_TEMPLATES)


def product_catalog() -> StudioCatalog:
    """Return the fixed professional catalog shown by the studio product."""
    return _PRODUCT_CATALOG
