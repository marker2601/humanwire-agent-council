"""Immutable, allowlisted product projection for the HumanWire studio."""

from __future__ import annotations

import re
import threading
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from humanwire.domain import AvailabilityWindow, Channel
from humanwire.persona_runtime import SyntheticIntent
from humanwire.replay_projection import project_replay_labels
from humanwire.studio_models import (
    CoordinationRequest,
    RequesterRole,
    TargetTiming,
    product_catalog,
)
from humanwire.synthetic_progress import (
    RepositoryProgressObserver,
    SyntheticProgressStore,
    SyntheticRunState,
    initial_progress,
)

_SAFE_ALIAS = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_SAFE_NODE_ID = r"^[a-z][a-z0-9-]{0,63}$"
_SAFE_PERSONA_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\b")
_HW_TOKEN = re.compile(r"\bHW-[A-F0-9]{8}\b", re.IGNORECASE)
_PRIVATE_KEY = re.compile(
    r"\b(?:sender_address|route_id|conversation_id|connection_id|message_id|"
    r"assignment_id|private_facts?|prompt|raw_payload)\b",
    re.IGNORECASE,
)
_PRIMARY_UI_WORD = re.compile(r"\b(?:proof|synthetic|simulated|fake)\b", re.IGNORECASE)
_WIRE_COMMAND = re.compile(
    r"/(?:mandate|go|confirm|decide|available|change)\b",
    re.IGNORECASE,
)
_CREDENTIAL = re.compile(
    r"\bbearer\s+\S+|"
    r"\b(?:[A-Za-z0-9]+[_-])*(?:(?:api|access|private|secret)[\s_-]?key"
    r"(?:[\s_-]?id)?|"
    r"authorization|password|credential|secret|database[_-]?(?:url|uri)|token)"
    r"[\"']?\s*(?:[:=]|\s)\s*[\"']?\S+",
    re.IGNORECASE,
)
_URI_COORDINATE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z][A-Za-z0-9+.-]{0,31}):(?:/{0,2})\S+",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"\bsk-[A-Za-z0-9_-]{8,}\b|\bAKIA[A-Z0-9]{8,}\b|"
    r"-{2,}BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-{2,}",
    re.IGNORECASE,
)
_DATABASE_ASSIGNMENT = re.compile(
    r"\b(?:server|host|database|dbname|user(?:name)?)\s*=\s*[^;\s]+(?:;|$)",
    re.IGNORECASE,
)
_SAFE_SLASH_TOKENS = frozenset({"go/no-go", "humanwire.studio/v1"})
_PATH_TOKEN_WRAPPERS = "\"'`()[]{}<>,.;:!?"

_REQUESTER_ROLE_LABELS = {
    RequesterRole.MANAGER: "Strategy manager",
    RequesterRole.EXECUTIVE: "Executive",
    RequesterRole.PROGRAM_LEAD: "Program lead",
    RequesterRole.TEAM_LEAD: "Team lead",
}
_TARGET_TIMING_LABELS = {
    TargetTiming.TOMORROW: "Tomorrow",
    TargetTiming.NEXT_BUSINESS_DAY: "Next business day",
}

_OUTBOUND_COPY = {
    "update": "HumanWire shared a coordination update.",
    "acknowledgement": "Please acknowledge this coordination update.",
    "quick_response": "Please share your response to the open coordination question.",
    "interview": "HumanWire requested a focused response to resolve the open question.",
    "evidence_confirmation": "Please confirm that the recorded evidence is accurate.",
    "evidence_confirmed": "HumanWire shared the confirmed evidence.",
    "approval_review": "Please review the decision proposal and record your decision.",
    "availability_request": "Please share availability for the required meeting.",
    "draft_proposal": "HumanWire shared the current decision proposal for review.",
    "alignment_brief": "HumanWire shared the current alignment summary.",
    "status": "HumanWire shared the current coordination status.",
    "meeting_reminder": "HumanWire shared a meeting reminder.",
    "partial": "HumanWire shared the current partial outcome.",
}
_AVAILABILITY_COPY = "Availability received for the requested window."
_DELIVERY_HEADINGS = (
    ("HUMANWIRE EVIDENCE CONFIRMATION", "evidence_confirmation"),
    ("HUMANWIRE EVIDENCE CONFIRMED", "evidence_confirmed"),
    ("HUMANWIRE AVAILABILITY REQUEST", "availability_request"),
    ("HUMANWIRE ACKNOWLEDGEMENT", "acknowledgement"),
    ("HUMANWIRE APPROVAL REVIEW", "approval_review"),
    ("HUMANWIRE QUICK RESPONSE", "quick_response"),
    ("HUMANWIRE DRAFT PROPOSAL", "draft_proposal"),
    ("HUMANWIRE ALIGNMENT BRIEF", "alignment_brief"),
    ("HUMANWIRE MEETING REMINDER", "meeting_reminder"),
    ("HUMANWIRE INTERVIEW", "interview"),
    ("HUMANWIRE UPDATE", "update"),
    ("HUMANWIRE STATUS", "status"),
    ("HUMANWIRE PARTIAL", "partial"),
)


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _contains_filesystem_path(text: str) -> bool:
    for raw_token in text.split():
        token = raw_token.strip(_PATH_TOKEN_WRAPPERS)
        if "/" not in token and "\\" not in token:
            continue
        if token.casefold() not in _SAFE_SLASH_TOKENS:
            return True
    return False


def _assert_product_safe(value: object, forbidden_texts: Sequence[str] = ()) -> None:
    normalized_forbidden = tuple(
        unicodedata.normalize("NFKC", secret).casefold()
        for secret in forbidden_texts
        if secret
    )
    for text in _strings(value):
        scan_text = unicodedata.normalize("NFKC", text)
        if (
            _UUID.search(scan_text)
            or _EMAIL.search(scan_text)
            or _HW_TOKEN.search(scan_text)
            or _PRIVATE_KEY.search(scan_text)
            or _PRIMARY_UI_WORD.search(scan_text)
            or _WIRE_COMMAND.search(scan_text)
            or _CREDENTIAL.search(scan_text)
            or _URI_COORDINATE.search(scan_text)
            or _SECRET_VALUE.search(scan_text)
            or _DATABASE_ASSIGNMENT.search(scan_text)
            or _contains_filesystem_path(scan_text)
            or any(secret in scan_text.casefold() for secret in normalized_forbidden)
        ):
            raise ValueError("studio projection text must be product-safe")


class _StudioProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def contains_only_product_safe_text(self) -> Self:
        _assert_product_safe(self.model_dump())
        return self


class StudioLifecycleStage(StrEnum):
    BRIEF = "brief"
    OUTREACH = "outreach"
    RESOLVE = "resolve"
    APPROVE = "approve"
    SCHEDULE = "schedule"


_STAGE_ORDER = tuple(StudioLifecycleStage)


class StudioLifecycle(_StudioProjection):
    current: StudioLifecycleStage
    stages: tuple[StudioLifecycleStage, ...]
    completed: tuple[StudioLifecycleStage, ...]


class StudioTransition(_StudioProjection):
    source: str = Field(pattern=_SAFE_NODE_ID)
    destination: str = Field(pattern=_SAFE_NODE_ID)
    source_label: str = Field(min_length=1, max_length=120)
    destination_label: str = Field(min_length=1, max_length=120)
    generated_label: str = Field(min_length=1, max_length=120)


class StudioTimelineEvent(_StudioProjection):
    timeline_ordinal: int = Field(ge=1)
    persisted_ordinal: int | None = Field(default=None, ge=1)
    created_at: datetime
    stage: StudioLifecycleStage
    effect: Literal["persisted", "inert"]
    active_transition: StudioTransition
    affected_persona_id: str | None = Field(default=None, pattern=_SAFE_PERSONA_ID)
    live_copy: str = Field(min_length=1, max_length=240)


class StudioConversationItem(_StudioProjection):
    ordinal: int = Field(ge=1)
    event_ordinal: int = Field(ge=1)
    created_at: datetime
    speaker: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)
    direction: Literal["from_humanwire", "to_humanwire", "system"]
    channel: Literal["Email", "Telegram", "Workspace"]
    text: str = Field(min_length=1, max_length=600)
    status: Literal["sent", "received", "no_response", "rejected"]


class StudioGraphNode(_StudioProjection):
    node_id: str = Field(pattern=_SAFE_NODE_ID)
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["request", "service", "gateway", "stakeholder", "artifact"]
    persona_id: str | None = Field(default=None, pattern=_SAFE_PERSONA_ID)
    role: str | None = Field(default=None, min_length=1, max_length=120)
    initials: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}$")
    active: bool = False


class StudioGraphEdge(_StudioProjection):
    source: str = Field(pattern=_SAFE_NODE_ID)
    destination: str = Field(pattern=_SAFE_NODE_ID)
    active: bool = False


class StudioDataPoint(_StudioProjection):
    event_ordinal: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=200)
    effect: Literal["persisted", "inert"]


class StudioOutcome(_StudioProjection):
    state: str = Field(pattern=r"^[a-z][a-z_]{0,63}$")
    headline: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=240)
    meeting_start: datetime | None = None
    meeting_end: datetime | None = None
    required_attendees: tuple[str, ...] = ()


class StudioWorkspaceSnapshot(_StudioProjection):
    schema_version: Literal["humanwire.studio/v1"]
    run_alias: str = Field(pattern=_SAFE_ALIAS)
    objective: str = Field(min_length=12, max_length=1000)
    requester_name: Literal["Alex Morgan"]
    requester_role_label: str = Field(min_length=1, max_length=120)
    target_timing_label: str = Field(min_length=1, max_length=120)
    run_state: Literal["starting", "running", "complete", "failed"]
    connection_label: Literal["Workspace channels", "Provider connected"]
    lifecycle: StudioLifecycle
    graph_nodes: tuple[StudioGraphNode, ...]
    graph_edges: tuple[StudioGraphEdge, ...]
    events: tuple[StudioTimelineEvent, ...]
    conversations: tuple[StudioConversationItem, ...]
    data_points: tuple[StudioDataPoint, ...]
    active_transition: StudioTransition | None
    current_event_ordinal: int = Field(ge=0)
    total_event_count: int = Field(ge=0)
    outcome: StudioOutcome
    downloads_ready: bool
    _final_trace_sha256: str | None = PrivateAttr(default=None)
    _transcript_sha256: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def has_synchronized_public_history(self) -> Self:
        count = len(self.events)
        if self.current_event_ordinal != count or self.total_event_count != count:
            raise ValueError("studio event counts must match the immutable timeline")
        if [item.timeline_ordinal for item in self.events] != list(range(1, count + 1)):
            raise ValueError("studio timeline ordinals must be contiguous")
        if any(
            (item.persisted_ordinal is not None) != (item.effect == "persisted")
            for item in self.events
        ):
            raise ValueError(
                "studio persisted ordinal must exist exactly for persisted events"
            )
        persisted = [item.persisted_ordinal for item in self.events if item.effect == "persisted"]
        if persisted != list(range(1, len(persisted) + 1)):
            raise ValueError("studio persisted ordinals must be contiguous")
        if [item.event_ordinal for item in self.data_points] != list(range(1, count + 1)):
            raise ValueError("studio data ordinals must exactly match timeline ordinals")
        if any(
            point.effect != event.effect
            for event, point in zip(self.events, self.data_points, strict=True)
        ):
            raise ValueError("studio data effect must match its timeline event effect")
        if any(
            point.label != event.active_transition.generated_label
            for event, point in zip(self.events, self.data_points, strict=True)
        ):
            raise ValueError(
                "studio data generated label must match its timeline transition"
            )
        if [item.ordinal for item in self.conversations] != list(
            range(1, len(self.conversations) + 1)
        ):
            raise ValueError("studio conversation ordinals must be contiguous")
        conversation_events = [item.event_ordinal for item in self.conversations]
        if conversation_events != sorted(conversation_events) or any(
            item < 1 or item > count for item in conversation_events
        ):
            raise ValueError("studio conversations must follow valid event ordinals")
        expected_active = self.events[-1].active_transition if self.events else None
        if self.active_transition != expected_active:
            raise ValueError("studio active transition must match the newest event")
        active_edges = [item for item in self.graph_edges if item.active]
        if (count == 0 and active_edges) or (count and len(active_edges) != 1):
            raise ValueError("studio workspace must expose exactly one active saved edge")
        if active_edges and (
            active_edges[0].source != expected_active.source
            or active_edges[0].destination != expected_active.destination
        ):
            raise ValueError("studio active edge must match the active transition")
        active_people = [item for item in self.graph_nodes if item.persona_id and item.active]
        expected_person = self.events[-1].affected_persona_id if self.events else None
        if len(active_people) != int(expected_person is not None) or (
            active_people and active_people[0].persona_id != expected_person
        ):
            raise ValueError("studio workspace must expose only the affected stakeholder")
        if self.lifecycle.stages != _STAGE_ORDER:
            raise ValueError("studio lifecycle stages must use the exact approved order")
        lifecycle_index = _STAGE_ORDER.index(self.lifecycle.current)
        if self.lifecycle.completed != _STAGE_ORDER[:lifecycle_index]:
            raise ValueError(
                "studio lifecycle completed stages must be the exact current-stage prefix"
            )
        current = StudioLifecycleStage.BRIEF
        for event in self.events:
            if event.effect == "inert" and event.stage != current:
                raise ValueError("inert studio events cannot advance lifecycle")
            if event.effect == "persisted":
                if _STAGE_ORDER.index(event.stage) < _STAGE_ORDER.index(current):
                    raise ValueError("studio lifecycle cannot regress")
                current = event.stage
        if self.lifecycle.current != current:
            raise ValueError("studio lifecycle must match the saved event history")
        if self.downloads_ready and self.run_state != "complete":
            raise ValueError("studio downloads require a complete run")
        return self


class StudioPresentationObserver(Protocol):
    def record_outbound(
        self,
        *,
        created_at: datetime,
        persona_id: str,
        channel: Channel,
        message_kind: str,
        safe_text: str,
    ) -> None:
        raise NotImplementedError

    def record_decision(
        self,
        *,
        created_at: datetime,
        persona_id: str,
        channel: Channel,
        intent: SyntheticIntent,
        safe_content: str,
    ) -> None:
        raise NotImplementedError


class _PersonaView(Protocol):
    persona_id: str
    display_name: str
    role: str
    private_facts: Sequence[str]


class _ScenarioView(Protocol):
    scenario_id: str
    identity_seed: int
    provenance: object
    personas: Sequence[_PersonaView]


@dataclass(frozen=True)
class _ProductIdentity:
    persona_id: str
    display_name: str
    role: str


class _RepositoryView(Protocol):
    def list_recent_mandates(self, limit: int) -> Sequence[object]: ...
    def list_events(self, mandate_id: object) -> Sequence[object]: ...
    def get_meeting_package(self, mandate_id: object) -> object | None: ...


@dataclass(frozen=True)
class _RawEvent:
    event_type: str
    created_at: datetime
    persona_id: str | None


@dataclass(frozen=True)
class _TimelineSource:
    effect: Literal["persisted", "inert"]
    created_at: datetime
    persisted_ordinal: int | None = None


@dataclass
class _PresentationRecord:
    created_at: datetime
    persona_id: str
    channel: Literal["Email", "Telegram"]
    direction: Literal["from_humanwire", "to_humanwire"]
    text: str
    status: Literal["sent", "received", "no_response", "rejected"]
    message_kind: str | None = None
    intent: SyntheticIntent | None = None
    event_ordinal: int | None = None


def project_delivery_presentation(text: str) -> tuple[str, str] | None:
    """Return an allowlisted kind and fixed product sentence for a delivery heading."""
    for heading, kind in _DELIVERY_HEADINGS:
        if heading in text:
            return kind, _OUTBOUND_COPY[kind]
    return None


def _project_availability_content(content: str) -> str:
    value = content.strip()
    if any(character.isspace() for character in value) or value.count("/") != 1:
        raise ValueError("studio availability content must be one ISO-8601 window")
    start_text, end_text = value.split("/", 1)
    try:
        AvailabilityWindow(
            start=datetime.fromisoformat(start_text),
            end=datetime.fromisoformat(end_text),
        )
    except ValueError as error:
        raise ValueError(
            "studio availability content must be one timezone-aware ISO-8601 window"
        ) from error
    return _AVAILABILITY_COPY


def _channel_label(channel: Channel) -> Literal["Email", "Telegram"]:
    return "Email" if channel is Channel.EMAIL else "Telegram"


def _node_id_for_persona(persona_id: str) -> str:
    candidate = persona_id.casefold().replace("_", "-")
    if re.fullmatch(_SAFE_NODE_ID, candidate):
        return f"person-{candidate}"[:64]
    raise ValueError("studio persona ID cannot be projected safely")


def _initials(name: str) -> str:
    return "".join(part[0] for part in name.split()[:2]).upper()


def _product_identities(
    request: CoordinationRequest,
) -> dict[str, _ProductIdentity]:
    catalog = {item.persona_id: item for item in product_catalog().stakeholders}
    identities = {
        "synthetic-manager": _ProductIdentity(
            persona_id="synthetic-manager",
            display_name=request.requester_name,
            role=_REQUESTER_ROLE_LABELS[request.requester_role],
        )
    }
    identities.update(
        {
            persona_id: _ProductIdentity(
                persona_id=persona_id,
                display_name=catalog[persona_id].display_name,
                role=catalog[persona_id].role,
            )
            for persona_id in request.participant_ids
        }
    )
    return identities


def _validate_scenario_identities(
    scenario: _ScenarioView,
    identities: Mapping[str, _ProductIdentity],
) -> None:
    scenario_people = {item.persona_id: item for item in scenario.personas}
    if len(scenario_people) != len(scenario.personas) or set(scenario_people) != set(identities):
        raise ValueError("studio scenario identities must match the product catalog")
    if any(
        scenario_people[persona_id].display_name != identity.display_name
        or scenario_people[persona_id].role != identity.role
        for persona_id, identity in identities.items()
    ):
        raise ValueError("studio scenario identities must match the product catalog")


def _graph_nodes(
    identities: Mapping[str, _ProductIdentity],
) -> tuple[StudioGraphNode, ...]:
    nodes = [
        StudioGraphNode(node_id="request", label="Request", kind="request"),
        StudioGraphNode(node_id="humanwire", label="HumanWire", kind="service"),
        StudioGraphNode(node_id="caspian-gateway", label="Caspian Gateway", kind="gateway"),
    ]
    nodes.extend(
        StudioGraphNode(
            node_id=_node_id_for_persona(persona.persona_id),
            label=persona.display_name,
            kind="stakeholder",
            persona_id=persona.persona_id,
            role=persona.role,
            initials=_initials(persona.display_name),
        )
        for persona in identities.values()
        if persona.persona_id != "synthetic-manager"
    )
    nodes.extend(
        StudioGraphNode(node_id=node_id, label=label, kind="artifact")
        for node_id, label in (
            ("conflict", "Conflict"),
            ("interview", "Targeted interview"),
            ("evidence", "Evidence"),
            ("proposal", "Decision proposal"),
            ("approval", "Approval"),
            ("availability", "Availability"),
            ("meeting", "Meeting package"),
        )
    )
    return tuple(nodes)


def _graph_pairs(nodes: Sequence[StudioGraphNode]) -> tuple[tuple[str, str], ...]:
    people = [item.node_id for item in nodes if item.persona_id]
    pairs = [("request", "humanwire"), ("humanwire", "caspian-gateway")]
    pairs.extend(("caspian-gateway", person) for person in people)
    for person in people:
        pairs.extend(
            (person, destination)
            for destination in ("humanwire", "interview", "evidence", "approval", "availability")
        )
    pairs.extend(
        (
            ("humanwire", "conflict"),
            ("humanwire", "interview"),
            ("humanwire", "evidence"),
            ("humanwire", "proposal"),
            ("humanwire", "approval"),
            ("humanwire", "availability"),
            ("humanwire", "meeting"),
            ("conflict", "interview"),
            ("interview", "evidence"),
            ("evidence", "proposal"),
            ("proposal", "approval"),
            ("approval", "availability"),
            ("availability", "meeting"),
        )
    )
    return tuple(dict.fromkeys(pairs))


def _timing_label(request: CoordinationRequest) -> str:
    if request.target_timing is TargetTiming.CUSTOM:
        if request.custom_date is None:
            raise ValueError("custom date is required")
        return request.custom_date.isoformat()
    return _TARGET_TIMING_LABELS[request.target_timing]


def _lifecycle(current: StudioLifecycleStage) -> StudioLifecycle:
    index = _STAGE_ORDER.index(current)
    return StudioLifecycle(
        current=current,
        stages=_STAGE_ORDER,
        completed=_STAGE_ORDER[:index],
    )


def _initial_snapshot(
    request: CoordinationRequest,
    scenario: _ScenarioView,
    identities: Mapping[str, _ProductIdentity],
) -> StudioWorkspaceSnapshot:
    nodes = _graph_nodes(identities)
    return StudioWorkspaceSnapshot(
        schema_version="humanwire.studio/v1",
        run_alias=scenario.scenario_id,
        objective=request.objective,
        requester_name=request.requester_name,
        requester_role_label=_REQUESTER_ROLE_LABELS[request.requester_role],
        target_timing_label=_timing_label(request),
        run_state="starting",
        connection_label=(
            "Provider connected"
            if bool(getattr(scenario.provenance, "live_provider_verified", False))
            else "Workspace channels"
        ),
        lifecycle=_lifecycle(StudioLifecycleStage.BRIEF),
        graph_nodes=nodes,
        graph_edges=tuple(
            StudioGraphEdge(source=source, destination=destination)
            for source, destination in _graph_pairs(nodes)
        ),
        events=(),
        conversations=(),
        data_points=(),
        active_transition=None,
        current_event_ordinal=0,
        total_event_count=0,
        outcome=StudioOutcome(
            state="coordinating",
            headline="Coordination is ready to start",
            summary="HumanWire will save each confirmed step as work progresses.",
        ),
        downloads_ready=False,
    )


class StudioProgressStore:
    """Thread-safe immutable store for the product-only workspace contract."""

    def __init__(
        self,
        initial: StudioWorkspaceSnapshot,
        *,
        forbidden_texts: Sequence[str] = (),
    ) -> None:
        self._lock = threading.Lock()
        self._forbidden_texts = tuple(item for item in forbidden_texts if item)
        self._snapshot = self._validated_copy(initial)

    def publish(self, snapshot: StudioWorkspaceSnapshot) -> None:
        with self._lock:
            candidate = self._validated_copy(snapshot)
            self._assert_transition(self._snapshot, candidate)
            self._snapshot = candidate

    def snapshot(self) -> StudioWorkspaceSnapshot:
        with self._lock:
            return self._snapshot.model_copy(deep=True)

    def publish_failed(self) -> None:
        """Publish one fixed safe failure without retaining private exception data."""
        with self._lock:
            if self._snapshot.run_state in {"complete", "failed"}:
                return
            candidate = self._snapshot.model_copy(
                update={
                    "run_state": "failed",
                    "downloads_ready": False,
                    "outcome": StudioOutcome(
                        state="failed",
                        headline="Coordination stopped",
                        summary="The saved workspace remains available for review.",
                    ),
                }
            )
            candidate._final_trace_sha256 = None
            candidate._transcript_sha256 = None
            validated = self._validated_copy(candidate)
            self._assert_transition(self._snapshot, validated)
            self._snapshot = validated

    def _validated_copy(self, snapshot: StudioWorkspaceSnapshot) -> StudioWorkspaceSnapshot:
        trace = snapshot._final_trace_sha256
        transcript = snapshot._transcript_sha256
        if trace is not None and not _SHA256.fullmatch(trace):
            raise ValueError("studio final trace binding must be a SHA-256 digest")
        if transcript is not None and not _SHA256.fullmatch(transcript):
            raise ValueError("studio transcript binding must be a SHA-256 digest")
        if (trace is not None or transcript is not None) and snapshot.run_state != "complete":
            raise ValueError("studio final binding requires a complete run")
        if snapshot.downloads_ready and (trace is None or transcript is None):
            raise ValueError("studio downloads require final trace and transcript binding")
        validated = StudioWorkspaceSnapshot.model_validate(snapshot.model_dump())
        _assert_product_safe(validated.model_dump(), self._forbidden_texts)
        validated._final_trace_sha256 = trace
        validated._transcript_sha256 = transcript
        return validated.model_copy(deep=True)

    @staticmethod
    def _assert_transition(
        previous: StudioWorkspaceSnapshot,
        candidate: StudioWorkspaceSnapshot,
    ) -> None:
        if previous.run_state in {"complete", "failed"}:
            if (
                candidate.model_dump(mode="json") != previous.model_dump(mode="json")
                or candidate._final_trace_sha256 != previous._final_trace_sha256
                or candidate._transcript_sha256 != previous._transcript_sha256
            ):
                raise ValueError("terminal studio snapshots are immutable")
            return
        allowed = {
            "starting": {"starting", "running", "failed"},
            "running": {"running", "complete", "failed"},
            "complete": {"complete"},
            "failed": {"failed"},
        }
        if candidate.run_state not in allowed[previous.run_state]:
            raise ValueError("studio run state cannot regress or skip finality")
        immutable_fields = (
            "schema_version",
            "run_alias",
            "objective",
            "requester_name",
            "requester_role_label",
            "target_timing_label",
            "connection_label",
        )
        if any(getattr(previous, field) != getattr(candidate, field) for field in immutable_fields):
            raise ValueError("studio run metadata is immutable")
        previous_nodes = tuple(item.model_dump(exclude={"active"}) for item in previous.graph_nodes)
        candidate_nodes = tuple(item.model_dump(exclude={"active"}) for item in candidate.graph_nodes)
        previous_edges = tuple((item.source, item.destination) for item in previous.graph_edges)
        candidate_edges = tuple((item.source, item.destination) for item in candidate.graph_edges)
        if previous_nodes != candidate_nodes or previous_edges != candidate_edges:
            raise ValueError("studio graph topology is immutable")
        for field in ("events", "conversations", "data_points"):
            old = tuple(item.model_dump(mode="json") for item in getattr(previous, field))
            new = tuple(item.model_dump(mode="json") for item in getattr(candidate, field))
            if new[: len(old)] != old:
                raise ValueError(f"studio {field} must preserve the exact published prefix")
        if previous._final_trace_sha256 is not None and (
            candidate._final_trace_sha256 != previous._final_trace_sha256
        ):
            raise ValueError("studio final trace binding cannot change")
        if previous._transcript_sha256 is not None and (
            candidate._transcript_sha256 != previous._transcript_sha256
        ):
            raise ValueError("studio transcript binding cannot change")


_DATA_LABELS = {
    "mandate.created": "Coordination request saved",
    "mandate.received": "Coordination request saved",
    "mandate.planned": "Coordination plan prepared",
    "engagement.plan_previewed": "Stakeholder plan prepared",
    "engagement.plan_released": "Stakeholder plan released",
    "mandate.interviewing": "Coordination started",
    "outreach.primary_sent": "Outreach sent",
    "outreach.delivery_confirmed": "Delivery confirmed",
    "outreach.reminder_sent": "Conflict identified",
    "outreach.alternate_sent": "Alternate channel selected",
    "stakeholder.acknowledged": "Acknowledgement received",
    "interview.answer_recorded": "Interview answer recorded",
    "interview.evidence_confirmed": "Evidence confirmed",
    "mandate.synthesizing": "Confirmed evidence assembled",
    "proposal.created": "Decision proposal prepared",
    "mandate.negotiating": "Proposal review started",
    "proposal.response_recorded": "Proposal response recorded",
    "engagement.decision_recorded": "Approval recorded",
    "mandate.meeting_required": "Approval complete",
    "mandate.scheduling": "Scheduling started",
    "availability.recorded": "Availability recorded",
    "meeting.package_created": "Meeting package created",
    "mandate.meeting_ready": "Meeting ready",
}


def _raw_events(repository: _RepositoryView) -> tuple[_RawEvent, ...]:
    rows: list[tuple[datetime, int, int, _RawEvent]] = []
    mandates = sorted(
        repository.list_recent_mandates(1000),
        key=lambda item: (item.created_at, str(item.mandate_id)),
    )
    for mandate_index, mandate in enumerate(mandates):
        for saved_order, event in enumerate(repository.list_events(mandate.mandate_id), 1):
            persona_id = str(getattr(event, "person_id", "") or "") or None
            row = _RawEvent(str(event.event_type), event.created_at, persona_id)
            rows.append((event.created_at, mandate_index, saved_order, row))
    rows.sort(key=lambda item: item[:3])
    return tuple(item[3] for item in rows)


_LIFECYCLE_BY_STAGE = {
    "Origin": StudioLifecycleStage.BRIEF,
    "Outreach": StudioLifecycleStage.OUTREACH,
    "Interview": StudioLifecycleStage.RESOLVE,
    "Evidence": StudioLifecycleStage.RESOLVE,
    "Negotiation": StudioLifecycleStage.RESOLVE,
    "Approval": StudioLifecycleStage.APPROVE,
    "Availability": StudioLifecycleStage.SCHEDULE,
    "Meeting": StudioLifecycleStage.SCHEDULE,
}

_ORIGIN_EVENT_TYPES = frozenset(
    {
        "mandate.created",
        "mandate.received",
        "mandate.planned",
        "engagement.plan_previewed",
        "engagement.plan_released",
    }
)
_OUTREACH_EVENT_TYPES = frozenset(
    {
        "mandate.interviewing",
        "outreach.primary_sent",
        "outreach.delivery_confirmed",
        "outreach.delivery_failed",
        "outreach.reminder_sent",
        "outreach.alternate_sent",
        "stakeholder.acknowledged",
        "engagement.quick_response_sent",
        "engagement.structured_interview_sent",
        "engagement.acknowledgement_sent",
        "engagement.inform_delivered",
        "engagement.structured_interview_reminder",
        "engagement.structured_interview_alternate_selected",
        "engagement.acknowledged",
    }
)
_INTERVIEW_EVENT_TYPES = frozenset(
    {
        "interview.answer_recorded",
        "engagement.quick_response_completed",
        "engagement.structured_interview_progressed",
    }
)
_EVIDENCE_EVENT_TYPES = frozenset(
    {"interview.evidence_confirmed", "mandate.synthesizing"}
)
_NEGOTIATION_EVENT_TYPES = frozenset({"proposal.created", "mandate.negotiating"})
_MEETING_EVENT_TYPES = frozenset({"meeting.package_created", "mandate.meeting_ready"})


def _event_phase(
    raw_type: str,
    persona_id: str | None,
    *,
    negotiation_started: bool,
    approval_started: bool,
) -> str | None:
    if raw_type in _ORIGIN_EVENT_TYPES:
        return "Origin"
    if raw_type in _OUTREACH_EVENT_TYPES:
        return "Outreach"
    if raw_type in _INTERVIEW_EVENT_TYPES:
        return "Interview"
    if raw_type in _EVIDENCE_EVENT_TYPES:
        return "Evidence"
    if raw_type in _NEGOTIATION_EVENT_TYPES:
        return "Negotiation"
    if raw_type == "proposal.response_recorded":
        return "Approval" if persona_id == "approval" else "Negotiation"
    if raw_type == "engagement.decision_recorded":
        return "Approval" if negotiation_started else "Outreach"
    if raw_type == "mandate.meeting_required":
        return "Approval"
    if raw_type == "mandate.scheduling":
        return "Availability"
    if raw_type == "availability.recorded":
        return "Availability" if approval_started else None
    if raw_type in _MEETING_EVENT_TYPES:
        return "Meeting"
    return None


def _transition_for(
    raw_type: str,
    label: str,
    persona_id: str | None,
    persona_name: str | None,
) -> StudioTransition:
    person_node = _node_id_for_persona(persona_id) if persona_id else None
    if raw_type in {"mandate.created", "mandate.received"}:
        source, destination = "request", "humanwire"
    elif raw_type.startswith("outreach.") and person_node:
        source, destination = "caspian-gateway", person_node
    elif raw_type == "interview.answer_recorded" and person_node:
        source, destination = person_node, "interview"
    elif raw_type == "interview.evidence_confirmed" and person_node:
        source, destination = person_node, "evidence"
    elif raw_type == "engagement.decision_recorded" and person_node:
        source, destination = person_node, "approval"
    elif raw_type == "availability.recorded" and person_node:
        source, destination = person_node, "availability"
    elif raw_type == "stakeholder.acknowledged" and person_node:
        source, destination = person_node, "humanwire"
    elif raw_type == "outreach.reminder_sent":
        source, destination = "humanwire", "conflict"
    elif raw_type == "mandate.synthesizing":
        source, destination = "evidence", "proposal"
    elif raw_type in {"proposal.created", "mandate.negotiating"}:
        source, destination = "humanwire", "proposal"
    elif raw_type == "proposal.response_recorded":
        source, destination = (
            (person_node, "approval") if person_node else ("proposal", "approval")
        )
    elif raw_type == "mandate.meeting_required":
        source, destination = "proposal", "approval"
    elif raw_type == "mandate.scheduling":
        source, destination = "approval", "availability"
    elif raw_type == "meeting.package_created":
        source, destination = "availability", "meeting"
    elif raw_type == "mandate.meeting_ready":
        source, destination = "humanwire", "meeting"
    else:
        source, destination = "humanwire", "caspian-gateway"
    labels = {
        "request": "Request",
        "humanwire": "HumanWire",
        "caspian-gateway": "Caspian Gateway",
        "conflict": "Conflict",
        "interview": "Targeted interview",
        "evidence": "Evidence",
        "proposal": "Decision proposal",
        "approval": "Approval",
        "availability": "Availability",
        "meeting": "Meeting package",
    }
    if person_node and persona_name:
        labels[person_node] = persona_name
    return StudioTransition(
        source=source,
        destination=destination,
        source_label=labels[source],
        destination_label=labels[destination],
        generated_label=label,
    )


def _expected_types(record: _PresentationRecord) -> frozenset[str]:
    if record.direction == "from_humanwire":
        if record.message_kind == "draft_proposal":
            return frozenset({"proposal.created"})
        if record.message_kind == "evidence_confirmation":
            return frozenset({"interview.answer_recorded"})
        if record.message_kind == "availability_request":
            return frozenset(
                {
                    "outreach.delivery_confirmed",
                    "mandate.meeting_required",
                    "mandate.scheduling",
                }
            )
        if record.message_kind == "approval_review":
            return frozenset({"outreach.delivery_confirmed", "proposal.created"})
        return frozenset(
            {"outreach.delivery_confirmed", "outreach.reminder_sent", "outreach.alternate_sent"}
        )
    return {
        SyntheticIntent.ACKNOWLEDGE: frozenset({"stakeholder.acknowledged"}),
        SyntheticIntent.ANSWER: frozenset({"interview.answer_recorded"}),
        SyntheticIntent.INTERVIEW_RESPONSE: frozenset({"interview.answer_recorded"}),
        SyntheticIntent.CONFIRM_EVIDENCE: frozenset({"interview.evidence_confirmed"}),
        SyntheticIntent.APPROVE: frozenset({"engagement.decision_recorded"}),
        SyntheticIntent.CHANGE: frozenset({"engagement.decision_recorded"}),
        SyntheticIntent.AVAILABILITY: frozenset({"availability.recorded"}),
        SyntheticIntent.ACCEPT_PROPOSAL: frozenset({"proposal.response_recorded"}),
        SyntheticIntent.CHANGE_PROPOSAL: frozenset({"proposal.response_recorded"}),
        SyntheticIntent.SILENCE: frozenset(),
        SyntheticIntent.ERROR: frozenset(),
    }[record.intent]


class StudioProgressObserver:
    """Join repository truth and safe presentation callbacks on one event ordinal."""

    def __init__(
        self,
        store: StudioProgressStore,
        delegate: RepositoryProgressObserver,
        request: CoordinationRequest,
        scenario: _ScenarioView,
        identities: Mapping[str, _ProductIdentity],
    ) -> None:
        self._store = store
        self._delegate = delegate
        self._request = request
        self._identities = dict(identities)
        self._run_alias = scenario.scenario_id
        self._connection_label: Literal["Workspace channels", "Provider connected"] = (
            "Provider connected"
            if bool(getattr(scenario.provenance, "live_provider_verified", False))
            else "Workspace channels"
        )
        self._private_fact_values = tuple(
            fact for person in scenario.personas for fact in person.private_facts
        )
        self._lock = threading.RLock()
        self._records: list[_PresentationRecord] = []
        self._last_repository: _RepositoryView | None = None
        self._timeline: list[_TimelineSource] = []
        self._persisted_seen = 0
        self._inert_seen = 0

    def snapshot(self) -> StudioWorkspaceSnapshot:
        return self._store.snapshot()

    def evidence_bundle(self):
        return self._delegate.evidence_bundle()

    def capture(self, repository: object, scenario: object, **state: object) -> None:
        with self._lock:
            self._delegate.capture(repository, scenario, **state)
            self._last_repository = repository
            self._publish(repository)

    def mark_unavailable(self) -> None:
        with self._lock:
            self._delegate.mark_unavailable()
            self._store.publish_failed()

    def record_inert_attempt(self, **attempt: object) -> None:
        with self._lock:
            self._delegate.record_inert_attempt(**attempt)
            if self._last_repository is not None:
                self._publish(self._last_repository)

    def record_outbound(
        self,
        *,
        created_at: datetime,
        persona_id: str,
        channel: Channel,
        message_kind: str,
        safe_text: str,
    ) -> None:
        with self._lock:
            if message_kind not in _OUTBOUND_COPY:
                raise ValueError("unknown studio outbound message kind")
            if safe_text != _OUTBOUND_COPY[message_kind]:
                raise ValueError("studio outbound text must use its fixed message kind copy")
            self._persona(persona_id)
            _assert_product_safe(safe_text, self._private_facts())
            self._records.append(
                _PresentationRecord(
                    created_at=created_at,
                    persona_id=persona_id,
                    channel=_channel_label(channel),
                    direction="from_humanwire",
                    text=safe_text,
                    status="sent",
                    message_kind=message_kind,
                )
            )

    def record_decision(
        self,
        *,
        created_at: datetime,
        persona_id: str,
        channel: Channel,
        intent: SyntheticIntent,
        safe_content: str,
    ) -> None:
        with self._lock:
            self._persona(persona_id)
            if intent is SyntheticIntent.SILENCE:
                text, status = "No response received.", "no_response"
            elif intent is SyntheticIntent.ERROR:
                text, status = "Response could not be accepted.", "rejected"
            elif intent is SyntheticIntent.AVAILABILITY:
                text = _project_availability_content(safe_content)
                _assert_product_safe(text, self._private_facts())
                status = "received"
            else:
                text = safe_content.strip()
                _assert_product_safe(text, self._private_facts())
                if not 1 <= len(text) <= 600:
                    raise ValueError("studio decision content must be bounded")
                status = "received"
            self._records.append(
                _PresentationRecord(
                    created_at=created_at,
                    persona_id=persona_id,
                    channel=_channel_label(channel),
                    direction="to_humanwire",
                    text=text,
                    status=status,
                    intent=intent,
                )
            )

    def _persona(self, persona_id: str) -> _ProductIdentity:
        persona = self._identities.get(persona_id)
        if persona is None:
            raise ValueError("studio presentation requires one known persona")
        return persona

    def _private_facts(self) -> tuple[str, ...]:
        return self._private_fact_values

    def _assign_records(
        self,
        raw: Sequence[_RawEvent],
    ) -> dict[int, str]:
        used = {
            item.event_ordinal
            for item in self._records
            if item.event_ordinal is not None and item.direction == "to_humanwire"
        }
        overrides: dict[int, str] = {
            item.event_ordinal: item.persona_id
            for item in self._records
            if item.event_ordinal is not None
            and item.direction == "to_humanwire"
            and item.persona_id != "synthetic-manager"
        }
        for record in self._records:
            if record.event_ordinal is not None:
                continue
            expected = _expected_types(record)
            candidates: list[int] = []
            if expected:
                for ordinal, event in enumerate(self._timeline, 1):
                    if (
                        (record.direction == "to_humanwire" and ordinal in used)
                        or event.effect != "persisted"
                    ):
                        continue
                    assert event.persisted_ordinal is not None
                    item = raw[event.persisted_ordinal - 1]
                    if item.event_type not in expected or item.created_at < record.created_at:
                        continue
                    if item.persona_id not in {None, record.persona_id}:
                        continue
                    candidates.append(ordinal)
            else:
                candidates = [
                    ordinal
                    for ordinal, event in enumerate(self._timeline, 1)
                    if ordinal not in used
                    and event.effect == "inert"
                    and event.created_at >= record.created_at
                ]
            if candidates:
                record.event_ordinal = candidates[0]
                if (
                    record.direction == "to_humanwire"
                    and record.persona_id != "synthetic-manager"
                ):
                    used.add(candidates[0])
                    overrides[candidates[0]] = record.persona_id
        return overrides

    def _publish(self, repository: object) -> None:
        progress = self._delegate.snapshot()
        typed_repository = repository
        raw = _raw_events(typed_repository)
        for persisted_ordinal in range(self._persisted_seen + 1, len(raw) + 1):
            self._timeline.append(
                _TimelineSource(
                    effect="persisted",
                    created_at=raw[persisted_ordinal - 1].created_at,
                    persisted_ordinal=persisted_ordinal,
                )
            )
        self._persisted_seen = len(raw)
        inert_events = tuple(item for item in progress.events if item.effect == "inert_attempt")
        for item in inert_events[self._inert_seen :]:
            self._timeline.append(_TimelineSource(effect="inert", created_at=item.created_at))
        self._inert_seen = len(inert_events)
        overrides = self._assign_records(raw)
        inbound_records = {
            item.event_ordinal: item
            for item in self._records
            if item.direction == "to_humanwire" and item.event_ordinal is not None
        }
        people = self._identities
        current_stage = StudioLifecycleStage.BRIEF
        negotiation_started = False
        approval_started = False
        proposal_count = 0
        events: list[StudioTimelineEvent] = []
        for timeline_ordinal, event in enumerate(self._timeline, 1):
            if event.effect == "persisted":
                assert event.persisted_ordinal is not None
                raw_event = raw[event.persisted_ordinal - 1]
                raw_type = raw_event.event_type
                persona_id = overrides.get(timeline_ordinal, raw_event.persona_id)
                inbound_record = inbound_records.get(timeline_ordinal)
                quick_response = (
                    raw_type == "interview.answer_recorded"
                    and inbound_record is not None
                    and inbound_record.intent is SyntheticIntent.ANSWER
                )
                phase = _event_phase(
                    raw_type,
                    persona_id,
                    negotiation_started=negotiation_started,
                    approval_started=approval_started,
                )
                if quick_response:
                    phase = "Outreach"
                if phase == "Negotiation":
                    negotiation_started = True
                elif phase == "Approval":
                    approval_started = True
                if phase is not None:
                    proposed_stage = _LIFECYCLE_BY_STAGE[phase]
                    if _STAGE_ORDER.index(proposed_stage) > _STAGE_ORDER.index(current_stage):
                        current_stage = proposed_stage
                replay = project_replay_labels(
                    raw_type,
                    people[persona_id].display_name if persona_id in people else None,
                )
                label = _DATA_LABELS.get(raw_type, replay.data_point)
                if quick_response:
                    label = "Response recorded"
                if raw_type == "proposal.created":
                    proposal_count += 1
                    if proposal_count > 1:
                        label = "Proposal revised"
                if label == "No public data point":
                    label = "Coordination record updated"
                transition = _transition_for(
                    raw_type,
                    label,
                    persona_id,
                    people[persona_id].display_name if persona_id in people else None,
                )
                if quick_response and persona_id in people:
                    transition = StudioTransition(
                        source=_node_id_for_persona(persona_id),
                        destination="humanwire",
                        source_label=people[persona_id].display_name,
                        destination_label="HumanWire",
                        generated_label=label,
                    )
                effect: Literal["persisted", "inert"] = "persisted"
                persisted_ordinal = event.persisted_ordinal
                live_copy = f"{label}."
            else:
                persona_id = overrides.get(timeline_ordinal)
                label = "No state change"
                transition = _transition_for("", label, persona_id, None)
                effect = "inert"
                persisted_ordinal = None
                live_copy = "No state change."
            events.append(
                StudioTimelineEvent(
                    timeline_ordinal=timeline_ordinal,
                    persisted_ordinal=persisted_ordinal,
                    created_at=event.created_at,
                    stage=current_stage,
                    effect=effect,
                    active_transition=transition,
                    affected_persona_id=persona_id,
                    live_copy=live_copy,
                )
            )
        assigned = tuple(item for item in self._records if item.event_ordinal is not None)
        conversations = tuple(
            StudioConversationItem(
                ordinal=index,
                event_ordinal=item.event_ordinal,
                created_at=item.created_at,
                speaker=(
                    "HumanWire"
                    if item.direction == "from_humanwire"
                    else self._persona(item.persona_id).display_name
                ),
                role=(
                    "Coordination service"
                    if item.direction == "from_humanwire"
                    else self._persona(item.persona_id).role
                ),
                direction=item.direction,
                channel=item.channel,
                text=item.text,
                status=item.status,
            )
            for index, item in enumerate(assigned, 1)
        )
        data_points = tuple(
            StudioDataPoint(
                event_ordinal=item.timeline_ordinal,
                label=item.active_transition.generated_label,
                summary=(
                    "Saved to the coordination record."
                    if item.effect == "persisted"
                    else "No state change"
                ),
                effect=item.effect,
            )
            for item in events
        )
        active = events[-1].active_transition if events else None
        initial_nodes = _graph_nodes(self._identities)
        active_persona = events[-1].affected_persona_id if events else None
        nodes = tuple(
            item.model_copy(update={"active": item.persona_id == active_persona})
            for item in initial_nodes
        )
        edges = tuple(
            StudioGraphEdge(
                source=source,
                destination=destination,
                active=bool(
                    active and source == active.source and destination == active.destination
                ),
            )
            for source, destination in _graph_pairs(nodes)
        )
        outcome = self._outcome(typed_repository, progress.run_state)
        snapshot = StudioWorkspaceSnapshot(
            schema_version="humanwire.studio/v1",
            run_alias=self._run_alias,
            objective=self._request.objective,
            requester_name=self._request.requester_name,
            requester_role_label=_REQUESTER_ROLE_LABELS[self._request.requester_role],
            target_timing_label=_timing_label(self._request),
            run_state=progress.run_state.value,
            connection_label=self._connection_label,
            lifecycle=_lifecycle(current_stage),
            graph_nodes=nodes,
            graph_edges=edges,
            events=tuple(events),
            conversations=conversations,
            data_points=data_points,
            active_transition=active,
            current_event_ordinal=len(events),
            total_event_count=len(events),
            outcome=outcome,
            downloads_ready=bool(
                progress.run_state is SyntheticRunState.COMPLETE
                and progress.final_trace_sha256
                and progress._transcript_sha256
            ),
        )
        snapshot._final_trace_sha256 = progress.final_trace_sha256
        snapshot._transcript_sha256 = progress._transcript_sha256
        self._store.publish(snapshot)

    def _outcome(
        self,
        repository: _RepositoryView,
        run_state: SyntheticRunState,
    ) -> StudioOutcome:
        mandates = sorted(
            repository.list_recent_mandates(1000),
            key=lambda item: (item.created_at, str(item.mandate_id)),
        )
        if not mandates:
            return StudioOutcome(
                state="coordinating",
                headline="Coordination is starting",
                summary="HumanWire is preparing the coordination plan.",
            )
        mandate = mandates[-1]
        state = str(getattr(mandate.state, "value", mandate.state))
        package = repository.get_meeting_package(mandate.mandate_id)
        if package is not None:
            names = {
                item.persona_id: item.display_name for item in self._identities.values()
            }
            return StudioOutcome(
                state="meeting_ready",
                headline="Meeting package ready",
                summary="The required attendees and meeting window are confirmed.",
                meeting_start=package.proposed_start,
                meeting_end=package.proposed_end,
                required_attendees=tuple(
                    names[item]
                    for item in package.required_attendee_ids
                    if item in names
                ),
            )
        if run_state is SyntheticRunState.FAILED:
            return StudioOutcome(
                state="failed",
                headline="Coordination stopped",
                summary="The saved workspace remains available for review.",
            )
        return StudioOutcome(
            state=state if re.fullmatch(r"[a-z][a-z_]{0,63}", state) else "coordinating",
            headline="Coordination in progress",
            summary="HumanWire is recording confirmed responses and decisions.",
        )


def create_studio_progress(
    request: CoordinationRequest,
    scenario: _ScenarioView,
) -> tuple[StudioProgressStore, StudioProgressObserver]:
    """Create the safe product store and its repository/presentation observer."""
    request = CoordinationRequest.model_validate(request)
    identities = _product_identities(request)
    _validate_scenario_identities(scenario, identities)
    initial = _initial_snapshot(request, scenario, identities)
    private_facts = tuple(fact for person in scenario.personas for fact in person.private_facts)
    store = StudioProgressStore(initial, forbidden_texts=private_facts)
    internal_store = SyntheticProgressStore(initial_progress(scenario))
    delegate = RepositoryProgressObserver(internal_store)
    return store, StudioProgressObserver(store, delegate, request, scenario, identities)
