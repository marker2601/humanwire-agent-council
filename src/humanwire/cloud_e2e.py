"""Fail-closed verification for the durable cloud authority story."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from humanwire.studio_exports import StudioProductEvidence, product_events_csv
from humanwire.studio_projection import StudioWorkspaceSnapshot


class CloudAuthorityProof(BaseModel):
    """Safe summary of one verified deterministic cloud run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_alias: str
    event_count: int = Field(ge=1)
    ordered_ordinals: tuple[int, ...] = Field(min_length=7, max_length=10)
    meeting_ordinal: int = Field(ge=1)
    terminal_state: Literal["meeting_ready"]


def _canonical_json(evidence: StudioProductEvidence) -> bytes:
    return json.dumps(
        evidence.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _unique_ordinal(labels: list[str], label: str) -> int:
    matches = [index for index, value in enumerate(labels, 1) if value == label]
    if len(matches) != 1:
        raise ValueError("cloud_authority_story_invalid")
    return matches[0]


def _first_ordinal(labels: list[str], label: str) -> int:
    try:
        return labels.index(label) + 1
    except ValueError:
        raise ValueError("cloud_authority_story_invalid") from None


def _validate_snapshot_export_parity(
    snapshot: StudioWorkspaceSnapshot,
    evidence: StudioProductEvidence,
) -> None:
    if (
        evidence.schema_version != "humanwire.studio-evidence/v1"
        or snapshot.schema_version != "humanwire.studio/v1"
        or evidence.run_alias != snapshot.run_alias
        or evidence.request.objective != snapshot.objective
        or evidence.request.requester_name != snapshot.requester_name
        or evidence.request.requester_role != snapshot.requester_role_label
        or evidence.request.target_timing != snapshot.target_timing_label
        or evidence.outcome != snapshot.outcome
        or evidence.graph.nodes != snapshot.graph_nodes
        or evidence.graph.edges != snapshot.graph_edges
        or evidence.conversations != snapshot.conversations
        or evidence.data_points != snapshot.data_points
        or len(evidence.events) != len(snapshot.events)
    ):
        raise ValueError("cloud_authority_story_invalid")

    conversations = {}
    for item in snapshot.conversations:
        conversations.setdefault(item.event_ordinal, item)
    stakeholder_labels = {
        item.persona_id: item.label
        for item in snapshot.graph_nodes
        if item.persona_id is not None
    }
    for exported, event, point in zip(
        evidence.events,
        snapshot.events,
        snapshot.data_points,
        strict=True,
    ):
        conversation = conversations.get(event.timeline_ordinal)
        if (
            exported.timeline_ordinal != event.timeline_ordinal
            or exported.persisted_ordinal != event.persisted_ordinal
            or exported.effect != event.effect
            or exported.created_at != event.created_at.isoformat()
            or exported.stage != event.stage.value
            or exported.source != event.active_transition.source_label
            or exported.destination != event.active_transition.destination_label
            or exported.channel
            != (conversation.channel if conversation is not None else None)
            or exported.direction
            != (conversation.direction if conversation is not None else None)
            or exported.stakeholder
            != (
                stakeholder_labels.get(event.affected_persona_id)
                if event.affected_persona_id is not None
                else None
            )
            or exported.data_point != point.label
            or exported.summary != point.summary
        ):
            raise ValueError("cloud_authority_story_invalid")


def verify_cloud_authority_story(
    snapshot_bytes: bytes,
    evidence_json_bytes: bytes,
    evidence_csv_bytes: bytes,
    *,
    expect_conflict: bool = True,
) -> CloudAuthorityProof:
    """Verify chronology, authority, and immutable export parity for one cloud run."""
    if type(expect_conflict) is not bool:
        raise ValueError("cloud_authority_story_invalid")
    if not all(
        isinstance(item, bytes) and 2 <= len(item) <= 8_000_000
        for item in (snapshot_bytes, evidence_json_bytes, evidence_csv_bytes)
    ):
        raise ValueError("cloud_authority_story_invalid")
    try:
        snapshot = StudioWorkspaceSnapshot.model_validate_json(snapshot_bytes)
        evidence = StudioProductEvidence.model_validate_json(evidence_json_bytes)
    except (ValidationError, ValueError, TypeError):
        raise ValueError("cloud_authority_story_invalid") from None

    if (
        snapshot.run_state != "complete"
        or not snapshot.downloads_ready
        or snapshot.outcome.state != "meeting_ready"
        or snapshot.current_event_ordinal != len(snapshot.events)
        or snapshot.total_event_count != len(snapshot.events)
        or [item.timeline_ordinal for item in snapshot.events]
        != list(range(1, len(snapshot.events) + 1))
        or [item.event_ordinal for item in snapshot.data_points]
        != list(range(1, len(snapshot.events) + 1))
    ):
        raise ValueError("cloud_authority_story_invalid")

    _validate_snapshot_export_parity(snapshot, evidence)
    if (
        evidence_json_bytes != _canonical_json(evidence)
        or evidence_csv_bytes != product_events_csv(evidence).encode("utf-8")
    ):
        raise ValueError("cloud_authority_story_invalid")

    labels = [item.label for item in snapshot.data_points]
    request_ordinal = _unique_ordinal(labels, "Coordination request saved")
    outreach_ordinal = _first_ordinal(labels, "Outreach sent")
    evidence_ordinal = _unique_ordinal(labels, "Confirmed evidence assembled")
    proposal_ordinal = _unique_ordinal(labels, "Decision proposal prepared")
    approval_ordinal = _unique_ordinal(labels, "Approval complete")
    availability_ordinal = _first_ordinal(labels, "Availability recorded")
    meeting_ordinal = _unique_ordinal(labels, "Meeting ready")

    ordered: tuple[int, ...]
    if expect_conflict:
        conflict_ordinal = _unique_ordinal(labels, "Conflict identified")
        interview_ordinal = _first_ordinal(labels, "Interview answer recorded")
        revision_ordinal = _unique_ordinal(labels, "Proposal revised")
        ordered = (
            request_ordinal,
            outreach_ordinal,
            conflict_ordinal,
            interview_ordinal,
            evidence_ordinal,
            proposal_ordinal,
            revision_ordinal,
            approval_ordinal,
            availability_ordinal,
            meeting_ordinal,
        )
        if not any(
            item.speaker == "Anika Rao"
            and conflict_ordinal < item.event_ordinal < evidence_ordinal
            and "rollback" in item.text.casefold()
            for item in snapshot.conversations
        ):
            raise ValueError("cloud_authority_story_invalid")
        if not any(
            conflict_ordinal < item.timeline_ordinal < evidence_ordinal
            and item.active_transition.destination_label == "Targeted interview"
            for item in snapshot.events
        ):
            raise ValueError("cloud_authority_story_invalid")
    else:
        ordered = (
            request_ordinal,
            outreach_ordinal,
            evidence_ordinal,
            proposal_ordinal,
            approval_ordinal,
            availability_ordinal,
            meeting_ordinal,
        )
        if (
            "Conflict identified" in labels
            or any(
                item.active_transition.destination_label == "Targeted interview"
                for item in snapshot.events
            )
            or any(
                "rollback" in item.text.casefold()
                for item in snapshot.conversations
            )
            or not any(
                item.role == "Risk & compliance lead"
                and item.direction == "to_humanwire"
                and item.text == "Acknowledged."
                for item in snapshot.conversations
            )
            or any(
                item.role == "Risk & compliance lead" and item.status == "rejected"
                for item in snapshot.conversations
            )
        ):
            raise ValueError("cloud_authority_story_invalid")

    if tuple(sorted(ordered)) != ordered or len(set(ordered)) != len(ordered):
        raise ValueError("cloud_authority_story_invalid")
    if meeting_ordinal != len(snapshot.events):
        raise ValueError("cloud_authority_story_invalid")
    if any(
        item.speaker == "Sofia Alvarez" and item.event_ordinal < proposal_ordinal
        for item in snapshot.conversations
    ):
        raise ValueError("cloud_authority_story_invalid")
    if any(
        item.speaker == "Daniel Brooks"
        and item.event_ordinal < approval_ordinal
        for item in snapshot.conversations
    ):
        raise ValueError("cloud_authority_story_invalid")

    return CloudAuthorityProof(
        run_alias=snapshot.run_alias,
        event_count=len(snapshot.events),
        ordered_ordinals=ordered,
        meeting_ordinal=meeting_ordinal,
        terminal_state="meeting_ready",
    )


__all__ = ["CloudAuthorityProof", "verify_cloud_authority_story"]
