"""Read-only, privacy-safe progress projections for synthetic HumanWire runs."""

from __future__ import annotations

import re
import threading
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from humanwire.persona_runtime import SyntheticGenerationMode, SyntheticProvenance
from humanwire.replay_projection import REPLAY_EVENT_EXPLANATIONS, project_replay_labels

_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_UUID_SHAPED_VALUE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_SAFE_LABEL = re.compile(r"^[A-Za-z][A-Za-z .,'-]{0,119}$")
_SAFE_ROLE = re.compile(r"^[A-Za-z][A-Za-z0-9 .,'&()/_-]{0,199}$")
_SAFE_STATE = re.compile(r"^[a-z][a-z_]{0,63}$")
_TERMINAL_ASSIGNMENT_STATES = frozenset(
    {"complete", "declined", "unreachable", "delivery_failed"}
)
_TERMINAL_MANDATE_STATES = frozenset(
    {"meeting_ready", "partial", "aligned", "cancelled", "expired"}
)
_CHANNEL_LABELS = {"email": "Email", "telegram": "Telegram"}
_DIRECTION_LABELS = {
    "upward": "Upward",
    "downward": "Downward",
    "lateral": "Lateral",
    "external": "External",
}
_INERT_DATA_POINTS = frozenset(
    {
        "No workflow data saved",
        "No response recorded",
        "No response before timeout",
        "Model response unavailable",
    }
)


class _StrictProgressModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def has_no_uuid_shaped_public_text(self):
        def strings(value: object):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    yield from strings(item)

        if any(_UUID_SHAPED_VALUE.search(value) for value in strings(self.model_dump())):
            raise ValueError("public progress models cannot contain UUID-shaped values")
        return self


class SyntheticRunState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class SyntheticRuntimeStatus(StrEnum):
    PERSISTED = "persisted"
    WAITING_FOR_AGENT = "waiting_for_agent"
    SYNTHETIC_SILENCE = "synthetic_silence"
    SYNTHETIC_TIMEOUT = "synthetic_timeout"
    MODEL_ERROR = "model_error"
    WORKFLOW_REJECTED = "workflow_rejected"
    TERMINAL_FAILURE = "terminal_failure"
    UNAVAILABLE = "unavailable"


class SyntheticProgressEvent(_StrictProgressModel):
    timeline_ordinal: int = Field(ge=1)
    persisted_ordinal: int | None = Field(default=None, ge=1)
    created_at: datetime
    story: Literal["primary", "change"]
    effect: Literal["persisted", "inert_attempt"]
    stage: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=120)
    channel: Literal["Email", "Telegram", "Internal"] | None = None
    direction: Literal["Upward", "Downward", "Lateral", "External"] | None = None
    data_point: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=200)
    highlight_target: str = Field(pattern=r"^(origin|none|persona-[1-9][0-9]*)$")
    persona_label: str | None = Field(default=None, max_length=120)
    contract: str | None = Field(default=None, max_length=64)


class SyntheticPersonaProgress(_StrictProgressModel):
    ordinal: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=200)
    contract: str | None = Field(default=None, max_length=64)
    status: str = Field(min_length=1, max_length=64)
    progress_current: int = Field(ge=0)
    progress_total: int = Field(ge=0)


class SyntheticAggregateCounts(_StrictProgressModel):
    personas: int = Field(ge=0)
    persisted_events: int = Field(ge=0)
    inert_attempts: int = Field(ge=0)
    complete_assignments: int = Field(ge=0)
    pending_assignments: int = Field(ge=0)
    terminal_mandates: int = Field(ge=0)


class SyntheticProgressSnapshot(_StrictProgressModel):
    schema_version: Literal["humanwire.synthetic-progress/v1"]
    provenance: SyntheticProvenance
    run_alias: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    scenario_label: str = Field(min_length=1, max_length=120)
    mode: SyntheticGenerationMode
    run_state: SyntheticRunState
    runtime_status: SyntheticRuntimeStatus
    active_persona_label: str | None = Field(default=None, max_length=120)
    active_contract: str | None = Field(default=None, max_length=64)
    saved_event_count: int = Field(ge=0)
    timeline_event_count: int = Field(ge=0)
    current_timeline_ordinal: int = Field(ge=0)
    current_persisted_ordinal: int = Field(ge=0)
    events: tuple[SyntheticProgressEvent, ...]
    personas: tuple[SyntheticPersonaProgress, ...]
    aggregate_counts: SyntheticAggregateCounts
    terminal_states: tuple[str, ...] = ()
    final_trace_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    _identity_seed: int | None = PrivateAttr(default=None)
    _transcript_sha256: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def has_consistent_public_progress(self):
        if self.final_trace_sha256 is not None and self.run_state is not SyntheticRunState.COMPLETE:
            raise ValueError("final trace is only valid for a complete synthetic run")
        persisted = tuple(event for event in self.events if event.effect == "persisted")
        if self.saved_event_count != len(persisted):
            raise ValueError("saved event count must match persisted progress events")
        if self.current_persisted_ordinal != len(persisted):
            raise ValueError("current persisted ordinal must match saved events")
        if self.timeline_event_count != len(self.events):
            raise ValueError("timeline event count must match progress events")
        if self.current_timeline_ordinal != len(self.events):
            raise ValueError("current timeline ordinal must match progress events")
        if [event.persisted_ordinal for event in persisted] != list(
            range(1, len(persisted) + 1)
        ):
            raise ValueError("persisted ordinals must be contiguous in displayed order")
        return self


class SyntheticEvidenceBundle(_StrictProgressModel):
    schema_version: Literal["humanwire.synthetic-evidence/v1"]
    provenance: SyntheticProvenance
    run_alias: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    scenario_label: str = Field(min_length=1, max_length=120)
    mode: SyntheticGenerationMode
    identity_seed: int = Field(ge=0, le=2_147_483_647)
    terminal_states: tuple[str, ...]
    aggregate_counts: SyntheticAggregateCounts
    events: tuple[SyntheticProgressEvent, ...]
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SyntheticPersonaView(Protocol):
    persona_id: str
    display_name: str
    role: str


class SyntheticScenarioView(Protocol):
    scenario_id: str
    identity_seed: int
    provenance: SyntheticProvenance
    personas: Sequence[SyntheticPersonaView]


class SyntheticProgressObserver(Protocol):
    def capture(
        self,
        repository: object,
        scenario: SyntheticScenarioView,
        *,
        mode: SyntheticGenerationMode,
        run_state: SyntheticRunState,
        runtime_status: SyntheticRuntimeStatus,
        active_persona_id: str | None = None,
        final_trace_sha256: str | None = None,
        transcript_sha256: str | None = None,
    ) -> None:
        raise NotImplementedError

    def mark_unavailable(self) -> None:
        raise NotImplementedError

    def record_inert_attempt(
        self,
        *,
        virtual_time: datetime,
        story: Literal["primary", "change"],
        persona_id: str | None,
        contract: str | None,
        runtime_status: SyntheticRuntimeStatus,
        data_point: str,
    ) -> None:
        raise NotImplementedError


class _RepositoryProgressView(Protocol):
    def list_recent_mandates(self, limit: int) -> Sequence[object]:
        raise NotImplementedError

    def list_assignments(self, mandate_id: object) -> Sequence[object]:
        raise NotImplementedError

    def list_events(self, mandate_id: object) -> Sequence[object]:
        raise NotImplementedError


@dataclass(frozen=True)
class _InertAttempt:
    virtual_time: datetime
    story: Literal["primary", "change"]
    persona_id: str | None
    contract: str | None
    runtime_status: SyntheticRuntimeStatus
    data_point: str
    local_ordinal: int


def _safe_alias(value: object) -> str:
    candidate = str(value or "")
    if _SAFE_ALIAS.fullmatch(candidate) and not _UUID_SHAPED_VALUE.search(candidate):
        return candidate
    return "synthetic-run"


def _safe_display_name(value: object) -> str:
    candidate = str(value or "")
    return candidate if _SAFE_LABEL.fullmatch(candidate) else "Synthetic persona"


def _safe_role(value: object) -> str:
    candidate = str(value or "")
    return candidate if _SAFE_ROLE.fullmatch(candidate) else "Role not listed"


def _safe_state(value: object, default: str) -> str:
    candidate = str(getattr(value, "value", value) or "")
    return candidate if _SAFE_STATE.fullmatch(candidate) else default


def _safe_contract(value: object) -> str | None:
    candidate = _safe_state(value, "")
    return candidate or None


def _safe_channel(value: object) -> str | None:
    return _CHANNEL_LABELS.get(_safe_state(value, ""))


def _safe_direction(value: object) -> str | None:
    return _DIRECTION_LABELS.get(_safe_state(value, ""))


def _story_for_index(index: int) -> Literal["primary", "change"]:
    return "primary" if index == 0 else "change"


def _event_description(
    stage: str,
    data_point: str,
    channel: str | None,
    direction: str | None,
) -> str:
    parts = [f"{stage}: {data_point}"]
    if channel:
        parts.append(channel)
    if direction:
        parts.append(direction)
    return " · ".join(parts)


def _scenario_personas(scenario: SyntheticScenarioView) -> tuple[object, ...]:
    return tuple(scenario.personas)


def _persona_matches(personas: Sequence[object], persona_id: object) -> list[object]:
    candidate = str(persona_id or "")
    return [item for item in personas if str(getattr(item, "persona_id", "")) == candidate]


def _progress_for_personas(
    personas: Sequence[object], assignments: Sequence[object]
) -> tuple[SyntheticPersonaProgress, ...]:
    rows: list[SyntheticPersonaProgress] = []
    for ordinal, persona in enumerate(personas, start=1):
        matching = [
            assignment
            for assignment in assignments
            if str(getattr(assignment, "person_id", "")) == str(getattr(persona, "persona_id", ""))
        ]
        if len(matching) == 1:
            assignment = matching[0]
            status = _safe_state(getattr(assignment, "state", None), "pending")
            contract = _safe_contract(getattr(assignment, "engagement_type", None))
            total = 1
            current = int(status == "complete")
        else:
            status = "pending"
            contract = None
            total = 0
            current = 0
        rows.append(
            SyntheticPersonaProgress(
                ordinal=ordinal,
                display_name=_safe_display_name(getattr(persona, "display_name", "")),
                role=_safe_role(getattr(persona, "role", "")),
                contract=contract,
                status=status,
                progress_current=current,
                progress_total=total,
            )
        )
    return tuple(rows)


def initial_progress(scenario: SyntheticScenarioView) -> SyntheticProgressSnapshot:
    """Return an immutable no-prediction snapshot before any repository effects exist."""
    personas = _scenario_personas(scenario)
    persona_rows = _progress_for_personas(personas, ())
    snapshot = SyntheticProgressSnapshot(
        schema_version="humanwire.synthetic-progress/v1",
        provenance=scenario.provenance,
        run_alias=_safe_alias(scenario.scenario_id),
        scenario_label="Synthetic coordination run",
        mode=SyntheticGenerationMode.DETERMINISTIC,
        run_state=SyntheticRunState.STARTING,
        runtime_status=SyntheticRuntimeStatus.PERSISTED,
        saved_event_count=0,
        timeline_event_count=0,
        current_timeline_ordinal=0,
        current_persisted_ordinal=0,
        events=(),
        personas=persona_rows,
        aggregate_counts=SyntheticAggregateCounts(
            personas=len(persona_rows),
            persisted_events=0,
            inert_attempts=0,
            complete_assignments=0,
            pending_assignments=0,
            terminal_mandates=0,
        ),
    )
    snapshot._identity_seed = scenario.identity_seed
    return snapshot


def evidence_bundle(snapshot: SyntheticProgressSnapshot) -> SyntheticEvidenceBundle | None:
    """Return the terminal public evidence view only after a final semantic trace exists."""
    if (
        snapshot.run_state is not SyntheticRunState.COMPLETE
        or snapshot.final_trace_sha256 is None
        or snapshot._identity_seed is None
    ):
        return None
    return SyntheticEvidenceBundle(
        schema_version="humanwire.synthetic-evidence/v1",
        provenance=snapshot.provenance,
        run_alias=snapshot.run_alias,
        scenario_label=snapshot.scenario_label,
        mode=snapshot.mode,
        identity_seed=snapshot._identity_seed,
        terminal_states=snapshot.terminal_states,
        aggregate_counts=snapshot.aggregate_counts,
        events=snapshot.events,
        trace_sha256=snapshot.final_trace_sha256,
    )


class SyntheticProgressStore:
    """Thread-safe storage that never shares a mutable snapshot with its callers."""

    def __init__(self, initial: SyntheticProgressSnapshot) -> None:
        self._lock = threading.Lock()
        self._snapshot = self._validated_copy(initial)

    def publish(self, snapshot: SyntheticProgressSnapshot) -> None:
        with self._lock:
            candidate = self._validated_copy(snapshot)
            self._assert_valid_transition(self._snapshot, candidate)
            self._snapshot = candidate

    def snapshot(self) -> SyntheticProgressSnapshot:
        with self._lock:
            return self._snapshot.model_copy(deep=True)

    def evidence_bundle(self) -> SyntheticEvidenceBundle | None:
        return evidence_bundle(self.snapshot())

    def final_evidence_binding(self) -> tuple[SyntheticEvidenceBundle, str] | None:
        """Return final safe evidence with its nonserialized transcript binding."""
        with self._lock:
            snapshot = self._snapshot.model_copy(deep=True)
        bundle = evidence_bundle(snapshot)
        if bundle is None or snapshot._transcript_sha256 is None:
            return None
        return bundle, snapshot._transcript_sha256

    @staticmethod
    def _validated_copy(snapshot: SyntheticProgressSnapshot) -> SyntheticProgressSnapshot:
        transcript_sha256 = snapshot._transcript_sha256
        if transcript_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", transcript_sha256
        ):
            raise ValueError("synthetic transcript binding must be a SHA-256 digest")
        if transcript_sha256 is not None and (
            snapshot.run_state is not SyntheticRunState.COMPLETE
            or snapshot.final_trace_sha256 is None
        ):
            raise ValueError("synthetic transcript binding requires completed finality")
        validated = SyntheticProgressSnapshot.model_validate(snapshot.model_dump())
        validated._identity_seed = snapshot._identity_seed
        validated._transcript_sha256 = transcript_sha256
        return validated.model_copy(deep=True)

    @staticmethod
    def _assert_valid_transition(
        previous: SyntheticProgressSnapshot,
        candidate: SyntheticProgressSnapshot,
    ) -> None:
        allowed_states = {
            SyntheticRunState.STARTING: {
                SyntheticRunState.STARTING,
                SyntheticRunState.RUNNING,
                SyntheticRunState.FAILED,
            },
            SyntheticRunState.RUNNING: {
                SyntheticRunState.RUNNING,
                SyntheticRunState.COMPLETE,
                SyntheticRunState.FAILED,
            },
            SyntheticRunState.COMPLETE: {SyntheticRunState.COMPLETE},
            SyntheticRunState.FAILED: {SyntheticRunState.FAILED},
        }
        if candidate.run_state not in allowed_states[previous.run_state]:
            raise ValueError("synthetic progress state cannot regress or skip finality")
        previous_persisted = tuple(
            event.model_dump(mode="json")
            for event in previous.events
            if event.effect == "persisted"
        )
        candidate_persisted = tuple(
            event.model_dump(mode="json")
            for event in candidate.events
            if event.effect == "persisted"
        )
        if candidate_persisted[: len(previous_persisted)] != previous_persisted:
            raise ValueError("synthetic progress must preserve the exact persisted prefix")
        if previous.final_trace_sha256 is not None and (
            candidate.final_trace_sha256 != previous.final_trace_sha256
        ):
            raise ValueError("synthetic progress final trace cannot change")
        if candidate._transcript_sha256 is not None and (
            candidate.run_state is not SyntheticRunState.COMPLETE
            or candidate.final_trace_sha256 is None
        ):
            raise ValueError("synthetic transcript binding requires completed finality")
        if previous._transcript_sha256 is not None and (
            candidate._transcript_sha256 != previous._transcript_sha256
        ):
            raise ValueError("synthetic transcript binding cannot change")


class RepositoryProgressObserver:
    """Project safe snapshots from repository reads without owning workflow authority."""

    def __init__(self, store: SyntheticProgressStore) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._inert_attempts: list[_InertAttempt] = []
        self._last_capture: tuple[
            _RepositoryProgressView,
            SyntheticScenarioView,
            SyntheticGenerationMode,
            SyntheticRunState,
            SyntheticRuntimeStatus,
            str | None,
            str | None,
            str | None,
        ] | None = None

    def snapshot(self) -> SyntheticProgressSnapshot:
        return self._store.snapshot()

    def evidence_bundle(self) -> SyntheticEvidenceBundle | None:
        return self._store.evidence_bundle()

    def capture(
        self,
        repository: object,
        scenario: SyntheticScenarioView,
        *,
        mode: SyntheticGenerationMode,
        run_state: SyntheticRunState,
        runtime_status: SyntheticRuntimeStatus,
        active_persona_id: str | None = None,
        final_trace_sha256: str | None = None,
        transcript_sha256: str | None = None,
    ) -> None:
        """Read persisted state and atomically publish its safe public projection."""
        typed_repository = repository  # structural protocol keeps synthetic.py out of this module
        with self._lock:
            self._last_capture = (
                typed_repository,
                scenario,
                mode,
                run_state,
                runtime_status,
                active_persona_id,
                final_trace_sha256,
                transcript_sha256,
            )
            self._publish_projection(*self._last_capture)

    def mark_unavailable(self) -> None:
        """Expose observer failure without changing or retrying the underlying workflow."""
        with self._lock:
            current = self._store.snapshot()
            self._store.publish(
                current.model_copy(update={"runtime_status": SyntheticRuntimeStatus.UNAVAILABLE})
            )

    def record_inert_attempt(
        self,
        *,
        virtual_time: datetime,
        story: Literal["primary", "change"],
        persona_id: str | None,
        contract: str | None,
        runtime_status: SyntheticRuntimeStatus,
        data_point: str,
    ) -> None:
        """Record a safe no-effect attempt without constructing a workflow envelope."""
        with self._lock:
            self._inert_attempts.append(
                _InertAttempt(
                    virtual_time=virtual_time,
                    story=story,
                    persona_id=persona_id,
                    contract=_safe_contract(contract),
                    runtime_status=runtime_status,
                    data_point=(
                        data_point if data_point in _INERT_DATA_POINTS else "No workflow data saved"
                    ),
                    local_ordinal=len(self._inert_attempts) + 1,
                )
            )
            if self._last_capture is not None:
                (
                    repository,
                    scenario,
                    mode,
                    run_state,
                    _status,
                    active_persona_id,
                    trace,
                    transcript_sha256,
                ) = self._last_capture
                self._last_capture = (
                    repository,
                    scenario,
                    mode,
                    run_state,
                    runtime_status,
                    active_persona_id,
                    trace,
                    transcript_sha256,
                )
                self._publish_projection(*self._last_capture)

    def _publish_projection(
        self,
        repository: _RepositoryProgressView,
        scenario: SyntheticScenarioView,
        mode: SyntheticGenerationMode,
        run_state: SyntheticRunState,
        runtime_status: SyntheticRuntimeStatus,
        active_persona_id: str | None,
        final_trace_sha256: str | None,
        transcript_sha256: str | None,
    ) -> None:
        personas = _scenario_personas(scenario)
        mandates = list(repository.list_recent_mandates(1000))
        mandates.sort(key=lambda item: (item.created_at, str(item.mandate_id)))
        all_assignments: list[object] = []
        persisted: list[tuple[datetime, int, int, SyntheticProgressEvent]] = []
        terminal_states: list[str] = []

        for mandate_index, mandate in enumerate(mandates):
            mandate_id = mandate.mandate_id
            assignments = list(repository.list_assignments(mandate_id))
            all_assignments.extend(assignments)
            assignment_counts = Counter(
                (
                    str(getattr(item, "mandate_id", "")),
                    str(getattr(item, "assignment_id", "")),
                    str(getattr(item, "person_id", "")),
                )
                for item in assignments
            )
            scenario_person_counts = Counter(
                str(getattr(item, "persona_id", "")) for item in personas
            )
            mandate_state = _safe_state(getattr(mandate, "state", None), "")
            if mandate_state in _TERMINAL_MANDATE_STATES:
                terminal_states.append(mandate_state)

            for saved_order, event in enumerate(repository.list_events(mandate_id), start=1):
                event_assignment_id = str(getattr(event, "assignment_id", "") or "")
                event_person_id = str(getattr(event, "person_id", "") or "")
                definition = REPLAY_EVENT_EXPLANATIONS.get(str(getattr(event, "event_type", "")))
                assignment_matches = [
                    item
                    for item in assignments
                    if (
                        str(getattr(item, "mandate_id", "")) == str(mandate_id)
                        and str(getattr(item, "assignment_id", "")) == event_assignment_id
                        and str(getattr(item, "person_id", "")) == event_person_id
                    )
                ]
                persona_matches = _persona_matches(personas, event_person_id)
                has_exact_person_binding = bool(
                    definition
                    and "person" in definition[2:]
                    and len(assignment_matches) == 1
                    and assignment_counts[
                        (str(mandate_id), event_assignment_id, event_person_id)
                    ]
                    == 1
                    and len(persona_matches) == 1
                    and scenario_person_counts[event_person_id] == 1
                )
                has_exact_mandate_binding = bool(
                    definition
                    and definition[2:] == ("HumanWire", "Decision Room")
                    and not event_assignment_id
                    and not event_person_id
                )
                bound_person = persona_matches[0] if has_exact_person_binding else None
                person_name = (
                    _safe_display_name(getattr(bound_person, "display_name", ""))
                    if bound_person is not None
                    else None
                )
                can_explain = has_exact_person_binding or has_exact_mandate_binding
                labels = project_replay_labels(
                    str(getattr(event, "event_type", "")) if can_explain else "",
                    person_name,
                )
                if has_exact_person_binding and bound_person is not None:
                    persona_ordinal = personas.index(bound_person) + 1
                    highlight_target = f"persona-{persona_ordinal}"
                    persona_label = person_name
                    contract = _safe_contract(
                        getattr(assignment_matches[0], "engagement_type", None)
                    )
                elif has_exact_mandate_binding:
                    highlight_target = "origin"
                    persona_label = None
                    contract = None
                else:
                    highlight_target = "none"
                    persona_label = None
                    contract = None
                projected_channel = (
                    _safe_channel(getattr(event, "channel", None))
                    if can_explain
                    else None
                )
                if projected_channel is None and has_exact_mandate_binding:
                    projected_channel = "Internal"
                projected_direction = (
                    _safe_direction(getattr(event, "direction", None))
                    if can_explain
                    else None
                )
                projected = SyntheticProgressEvent(
                    timeline_ordinal=1,
                    persisted_ordinal=None,
                    created_at=event.created_at,
                    story=_story_for_index(mandate_index),
                    effect="persisted",
                    stage=labels.stage,
                    source=labels.source,
                    destination=labels.destination,
                    channel=projected_channel,
                    direction=projected_direction,
                    data_point=labels.data_point,
                    description=_event_description(
                        labels.stage,
                        labels.data_point,
                        projected_channel,
                        projected_direction,
                    ),
                    highlight_target=highlight_target,
                    persona_label=persona_label,
                    contract=contract,
                )
                persisted.append((projected.created_at, mandate_index, saved_order, projected))

        persisted.sort(key=lambda item: item[:3])
        ordered_persisted = [
            (
                created_at,
                0,
                ordinal,
                event.model_copy(update={"persisted_ordinal": ordinal}),
            )
            for ordinal, (created_at, _mandate_index, _saved_order, event) in enumerate(
                persisted, start=1
            )
        ]

        inert = [
            (
                attempt.virtual_time,
                1,
                attempt.local_ordinal,
                SyntheticProgressEvent(
                    timeline_ordinal=1,
                    persisted_ordinal=None,
                    created_at=attempt.virtual_time,
                    story=attempt.story,
                    effect="inert_attempt",
                    stage="Attempt",
                    source="HumanWire",
                    destination="Decision Room",
                    data_point=attempt.data_point,
                    description=f"Attempt: {attempt.data_point}",
                    highlight_target="none",
                    contract=attempt.contract,
                ),
            )
            for attempt in self._inert_attempts
        ]
        combined = [*ordered_persisted, *inert]
        combined.sort(key=lambda item: item[:3])
        events = tuple(
            item[3].model_copy(update={"timeline_ordinal": index})
            for index, item in enumerate(combined, start=1)
        )
        persona_rows = _progress_for_personas(personas, all_assignments)
        active_personas = _persona_matches(personas, active_persona_id)
        active_persona = active_personas[0] if len(active_personas) == 1 else None
        active_assignments = [
            item
            for item in all_assignments
            if active_persona is not None
            and str(getattr(item, "person_id", ""))
            == str(getattr(active_persona, "persona_id", ""))
        ]
        active_contract = (
            _safe_contract(getattr(active_assignments[0], "engagement_type", None))
            if len(active_assignments) == 1
            else None
        )
        assignment_states = [
            _safe_state(getattr(item, "state", None), "pending") for item in all_assignments
        ]
        snapshot = SyntheticProgressSnapshot(
                schema_version="humanwire.synthetic-progress/v1",
                provenance=scenario.provenance,
                run_alias=_safe_alias(scenario.scenario_id),
                scenario_label="Synthetic coordination run",
                mode=mode,
                run_state=run_state,
                runtime_status=runtime_status,
                active_persona_label=(
                    _safe_display_name(getattr(active_persona, "display_name", ""))
                    if active_persona is not None
                    else None
                ),
                active_contract=active_contract,
                saved_event_count=len(ordered_persisted),
                timeline_event_count=len(events),
                current_timeline_ordinal=len(events),
                current_persisted_ordinal=len(ordered_persisted),
                events=events,
                personas=persona_rows,
                aggregate_counts=SyntheticAggregateCounts(
                    personas=len(persona_rows),
                    persisted_events=len(ordered_persisted),
                    inert_attempts=len(self._inert_attempts),
                    complete_assignments=assignment_states.count("complete"),
                    pending_assignments=sum(
                        state not in _TERMINAL_ASSIGNMENT_STATES for state in assignment_states
                    ),
                    terminal_mandates=len(terminal_states),
                ),
                terminal_states=tuple(terminal_states),
                final_trace_sha256=final_trace_sha256,
            )
        snapshot._identity_seed = scenario.identity_seed
        snapshot._transcript_sha256 = transcript_sha256
        self._store.publish(snapshot)
