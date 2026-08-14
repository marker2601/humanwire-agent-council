"""Allowlisted public labels for persisted HumanWire replay events."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict


class ReplayLabels(BaseModel):
    """The complete public label set for one replay row."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    stage: str
    source: str
    destination: str
    data_point: str


ReplayDefinition = tuple[str, str, str, str]

REPLAY_EVENT_EXPLANATIONS: Final[Mapping[str, ReplayDefinition]] = MappingProxyType(
    {
        "mandate.created": ("Mandate", "Mandate created", "HumanWire", "Decision Room"),
        "mandate.received": ("Mandate", "Mandate received", "HumanWire", "Decision Room"),
        "engagement.plan_previewed": ("Plan", "Plan previewed", "HumanWire", "Decision Room"),
        "engagement.plan_released": ("Plan", "Plan released", "HumanWire", "Decision Room"),
        "mandate.planned": ("Plan", "Plan prepared", "HumanWire", "Decision Room"),
        "mandate.interviewing": ("Outreach", "Coordination started", "HumanWire", "Decision Room"),
        "engagement.quick_response_sent": ("Outreach", "Outreach sent", "HumanWire", "person"),
        "engagement.structured_interview_sent": (
            "Outreach",
            "Interview requested",
            "HumanWire",
            "person",
        ),
        "engagement.acknowledgement_sent": (
            "Outreach",
            "Acknowledgement requested",
            "HumanWire",
            "person",
        ),
        "engagement.inform_delivered": ("Outreach", "Update delivered", "HumanWire", "person"),
        "engagement.structured_interview_reminder": (
            "Outreach",
            "Reminder sent",
            "HumanWire",
            "person",
        ),
        "engagement.structured_interview_alternate_selected": (
            "Outreach",
            "Alternate channel selected",
            "HumanWire",
            "person",
        ),
        "engagement.quick_response_completed": (
            "Response",
            "Response completed",
            "person",
            "HumanWire",
        ),
        "engagement.acknowledged": (
            "Response",
            "Acknowledgement received",
            "person",
            "HumanWire",
        ),
        "engagement.structured_interview_progressed": (
            "Response",
            "Interview progressed",
            "person",
            "HumanWire",
        ),
        "interview.answer_recorded": ("Response", "Answer recorded", "person", "HumanWire"),
        "interview.evidence_confirmed": ("Evidence", "Evidence confirmed", "person", "HumanWire"),
        "engagement.approval_pending": ("Decision", "Decision requested", "HumanWire", "person"),
        "engagement.override_recorded": ("Decision", "Decision updated", "HumanWire", "person"),
        "engagement.decision_recorded": ("Decision", "Decision recorded", "person", "HumanWire"),
        "proposal.response_recorded": (
            "Decision",
            "Proposal response recorded",
            "person",
            "HumanWire",
        ),
        "proposal.created": ("Proposal", "Proposal prepared", "HumanWire", "Decision Room"),
        "mandate.negotiating": ("Proposal", "Proposal review started", "HumanWire", "Decision Room"),
        "mandate.meeting_required": ("Scheduling", "Meeting required", "HumanWire", "Decision Room"),
        "mandate.scheduling": ("Scheduling", "Scheduling started", "HumanWire", "Decision Room"),
        "availability.recorded": ("Scheduling", "Availability recorded", "person", "HumanWire"),
        "meeting.package_created": ("Scheduling", "Meeting prepared", "HumanWire", "Decision Room"),
        "mandate.meeting_ready": ("Scheduling", "Meeting ready", "HumanWire", "Decision Room"),
        "mandate.aligned": ("Outcome", "Outcome recorded", "HumanWire", "Decision Room"),
        "mandate.partial": ("Outcome", "Partial outcome recorded", "HumanWire", "Decision Room"),
        "mandate.cancelled": ("Outcome", "Mandate cancelled", "HumanWire", "Decision Room"),
        "mandate.expired": ("Outcome", "Mandate expired", "HumanWire", "Decision Room"),
    }
)


def project_replay_labels(event_type: str, person_name: str | None) -> ReplayLabels:
    """Project only the fixed public labels supported by a persisted event type."""
    definition = REPLAY_EVENT_EXPLANATIONS.get(event_type)
    if definition is None:
        return ReplayLabels(
            stage="Saved event",
            source="HumanWire",
            destination="Decision Room",
            data_point="No public data point",
        )
    stage, data_point, raw_source, raw_destination = definition
    source = person_name if raw_source == "person" else raw_source
    destination = person_name if raw_destination == "person" else raw_destination
    if not source or not destination:
        return project_replay_labels("", None)
    return ReplayLabels(
        stage=stage,
        source=source,
        destination=destination,
        data_point=data_point,
    )
