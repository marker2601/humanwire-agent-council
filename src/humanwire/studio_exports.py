"""Allowlisted final evidence exports for the private coordination studio."""

from __future__ import annotations

import csv
import io

from pydantic import BaseModel, ConfigDict

from humanwire.studio_projection import (
    StudioConversationItem,
    StudioDataPoint,
    StudioGraphEdge,
    StudioGraphNode,
    StudioOutcome,
    StudioWorkspaceSnapshot,
)
from humanwire.studio_run import StudioFinalBinding

_STUDIO_CSV_FIELDS = (
    "timeline_ordinal",
    "persisted_ordinal",
    "effect",
    "created_at",
    "stage",
    "source",
    "destination",
    "channel",
    "direction",
    "stakeholder",
    "data_point",
    "summary",
)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


class _ExportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class StudioRequestSummary(_ExportModel):
    objective: str
    requester_name: str
    requester_role: str
    target_timing: str


class StudioEvidenceGraph(_ExportModel):
    nodes: tuple[StudioGraphNode, ...]
    edges: tuple[StudioGraphEdge, ...]


class StudioEvidenceEvent(_ExportModel):
    timeline_ordinal: int
    persisted_ordinal: int | None
    effect: str
    created_at: str
    stage: str
    source: str
    destination: str
    channel: str | None
    direction: str | None
    stakeholder: str | None
    data_point: str
    summary: str


class StudioProductEvidence(_ExportModel):
    schema_version: str
    run_alias: str
    request: StudioRequestSummary
    outcome: StudioOutcome
    graph: StudioEvidenceGraph
    events: tuple[StudioEvidenceEvent, ...]
    conversations: tuple[StudioConversationItem, ...]
    data_points: tuple[StudioDataPoint, ...]


def _conversation_by_event(
    snapshot: StudioWorkspaceSnapshot,
) -> dict[int, StudioConversationItem]:
    selected: dict[int, StudioConversationItem] = {}
    for item in snapshot.conversations:
        selected.setdefault(item.event_ordinal, item)
    return selected


def _stakeholder_labels(snapshot: StudioWorkspaceSnapshot) -> dict[str, str]:
    return {
        item.persona_id: item.label
        for item in snapshot.graph_nodes
        if item.persona_id is not None
    }


def product_evidence(binding: StudioFinalBinding) -> StudioProductEvidence:
    """Build one product-only export after checking the final bound timelines."""
    snapshot = binding.snapshot
    bundle = binding.evidence
    if (
        bundle.run_alias != snapshot.run_alias
        or bundle.trace_sha256 != snapshot._final_trace_sha256
    ):
        raise ValueError("final studio evidence binding is inconsistent")

    conversations = _conversation_by_event(snapshot)
    stakeholders = _stakeholder_labels(snapshot)
    events: list[StudioEvidenceEvent] = []
    for event, point in zip(snapshot.events, snapshot.data_points, strict=True):
        conversation = conversations.get(event.timeline_ordinal)
        events.append(
            StudioEvidenceEvent(
                timeline_ordinal=event.timeline_ordinal,
                persisted_ordinal=event.persisted_ordinal,
                effect=event.effect,
                created_at=event.created_at.isoformat(),
                stage=event.stage.value,
                source=event.active_transition.source_label,
                destination=event.active_transition.destination_label,
                channel=conversation.channel if conversation is not None else None,
                direction=conversation.direction if conversation is not None else None,
                stakeholder=(
                    stakeholders.get(event.affected_persona_id)
                    if event.affected_persona_id is not None
                    else None
                ),
                data_point=point.label,
                summary=point.summary,
            )
        )
    return StudioProductEvidence(
        schema_version="humanwire.studio-evidence/v1",
        run_alias=snapshot.run_alias,
        request=StudioRequestSummary(
            objective=snapshot.objective,
            requester_name=snapshot.requester_name,
            requester_role=snapshot.requester_role_label,
            target_timing=snapshot.target_timing_label,
        ),
        outcome=snapshot.outcome,
        graph=StudioEvidenceGraph(nodes=snapshot.graph_nodes, edges=snapshot.graph_edges),
        events=tuple(events),
        conversations=snapshot.conversations,
        data_points=snapshot.data_points,
    )


def _csv_cell(value: object) -> str:
    """Render one inert spreadsheet cell without control-character row injection."""
    if value is None:
        return ""
    if isinstance(value, bool):
        rendered = str(value).lower()
    else:
        rendered = str(value)
    formula_leading = rendered.startswith(_FORMULA_PREFIXES)
    rendered = rendered.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return "'" + rendered if formula_leading else rendered


def product_events_csv(evidence: StudioProductEvidence) -> str:
    """Render event rows in the exact stable studio CSV contract."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=_STUDIO_CSV_FIELDS,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for item in evidence.events:
        row = item.model_dump(mode="json")
        writer.writerow({field: _csv_cell(row[field]) for field in _STUDIO_CSV_FIELDS})
    return output.getvalue()
