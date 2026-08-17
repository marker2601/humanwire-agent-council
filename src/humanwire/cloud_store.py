"""Durable run ownership and immutable public timeline repositories."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from humanwire.studio_models import (
    CoordinationRequest,
    StudioAgentMode,
    coordination_target_date,
)
from humanwire.studio_projection import (
    StudioConversationItem,
    StudioDataPoint,
    StudioLifecycle,
    StudioLifecycleStage,
    StudioOutcome,
    StudioTimelineEvent,
    StudioWorkspaceSnapshot,
    create_studio_progress,
)
from humanwire.synthetic import build_coordination_scenario

_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{15,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMELINE_ID_WIDTH = 8


class CloudStoreError(RuntimeError):
    """A fixed safe cloud-repository failure."""


class CloudActiveRunError(CloudStoreError):
    def __init__(self) -> None:
        super().__init__("active_run")


class CloudUnknownRunError(CloudStoreError):
    def __init__(self) -> None:
        super().__init__("run_not_found")


class CloudDivergenceError(CloudStoreError):
    pass


class CloudExpiredClaimError(CloudStoreError):
    def __init__(self) -> None:
        super().__init__("expired_claim_requires_recovery")


class CloudRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class CloudClaimStatus(StrEnum):
    CLAIMED = "claimed"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    TERMINAL = "terminal"
    RECOVERED = "recovered"


class _CloudModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CloudRunCreation(_CloudModel):
    run_alias: str = Field(pattern=_SAFE_ALIAS.pattern)
    idempotency_key: str = Field(pattern=_SAFE_OPAQUE.pattern)


class CloudClaimResult(_CloudModel):
    status: CloudClaimStatus
    lease_expires_at: datetime | None = None
    version: int = Field(ge=1)


class CloudTimelineRecord(_CloudModel):
    schema_version: Literal[1] = 1
    timeline_ordinal: int = Field(ge=1)
    record_hash: str = Field(pattern=_SHA256.pattern)
    event: StudioTimelineEvent
    conversations: tuple[StudioConversationItem, ...] = ()
    data_point: StudioDataPoint
    lifecycle: StudioLifecycle

    @classmethod
    def create(
        cls,
        *,
        event: StudioTimelineEvent,
        conversations: tuple[StudioConversationItem, ...],
        data_point: StudioDataPoint,
        lifecycle: StudioLifecycle,
    ) -> CloudTimelineRecord:
        payload = {
            "schema_version": 1,
            "timeline_ordinal": event.timeline_ordinal,
            "event": event.model_dump(mode="json"),
            "conversations": [item.model_dump(mode="json") for item in conversations],
            "data_point": data_point.model_dump(mode="json"),
            "lifecycle": lifecycle.model_dump(mode="json"),
        }
        return cls(
            schema_version=1,
            timeline_ordinal=event.timeline_ordinal,
            record_hash=_digest(payload),
            event=event,
            conversations=conversations,
            data_point=data_point,
            lifecycle=lifecycle,
        )

    @model_validator(mode="after")
    def is_synchronized_and_bound(self) -> Self:
        if self.event.timeline_ordinal != self.timeline_ordinal:
            raise ValueError("cloud event ordinal must match its timeline record")
        if self.data_point.event_ordinal != self.timeline_ordinal:
            raise ValueError("cloud data ordinal must match its timeline record")
        if self.data_point.effect != self.event.effect:
            raise ValueError("cloud data effect must match its event")
        if self.data_point.label != self.event.active_transition.generated_label:
            raise ValueError("cloud data label must match its event transition")
        if self.lifecycle.current is not self.event.stage:
            raise ValueError("cloud lifecycle must match its event stage")
        if any(item.event_ordinal != self.timeline_ordinal for item in self.conversations):
            raise ValueError("cloud conversations must share the timeline ordinal")
        if self.record_hash != _digest(_record_payload(self)):
            raise ValueError("cloud timeline record hash is invalid")
        return self


class CloudTerminalBinding(_CloudModel):
    state: Literal[CloudRunState.COMPLETE, CloudRunState.FAILED]
    outcome: StudioOutcome
    semantic_digest: str | None = Field(default=None, pattern=_SHA256.pattern)
    final_trace_digest: str | None = Field(default=None, pattern=_SHA256.pattern)
    transcript_digest: str | None = Field(default=None, pattern=_SHA256.pattern)
    json_digest: str | None = Field(default=None, pattern=_SHA256.pattern)
    csv_digest: str | None = Field(default=None, pattern=_SHA256.pattern)

    @model_validator(mode="after")
    def has_exact_terminal_evidence(self) -> Self:
        digests = (
            self.semantic_digest,
            self.final_trace_digest,
            self.transcript_digest,
            self.json_digest,
            self.csv_digest,
        )
        if self.state is CloudRunState.COMPLETE and any(item is None for item in digests):
            raise ValueError("complete cloud binding requires every evidence digest")
        if self.state is CloudRunState.FAILED and any(item is not None for item in digests):
            raise ValueError("failed cloud binding cannot expose evidence downloads")
        if self.state is CloudRunState.FAILED and self.outcome.state != "failed":
            raise ValueError("failed cloud binding requires a failed public outcome")
        return self


class CloudRunMetadata(_CloudModel):
    schema_version: Literal[1] = 1
    run_alias: str = Field(pattern=_SAFE_ALIAS.pattern)
    idempotency_key_hash: str = Field(pattern=_SHA256.pattern)
    request: CoordinationRequest
    agent_mode: Literal[StudioAgentMode.GOOGLE_ADK] = StudioAgentMode.GOOGLE_ADK
    model_id: Literal["gemini-3.6-flash"] = "gemini-3.6-flash"
    target_date: date
    state: CloudRunState
    lifecycle_stage: StudioLifecycleStage
    saved_ordinal: int = Field(ge=0)
    timeline_count: int = Field(ge=0)
    claim_owner: str | None = Field(default=None, pattern=_SAFE_OPAQUE.pattern)
    lease_expires_at: datetime | None = None
    version: int = Field(ge=1)
    outcome: StudioOutcome
    semantic_digest: str | None = Field(default=None, pattern=_SHA256.pattern)
    final_trace_digest: str | None = Field(default=None, pattern=_SHA256.pattern)
    transcript_digest: str | None = Field(default=None, pattern=_SHA256.pattern)
    json_digest: str | None = Field(default=None, pattern=_SHA256.pattern)
    csv_digest: str | None = Field(default=None, pattern=_SHA256.pattern)
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def has_consistent_state(self) -> Self:
        if self.request.agent_mode is not StudioAgentMode.GOOGLE_ADK:
            raise ValueError("cloud run requires the Google ADK mode")
        if self.saved_ordinal > self.timeline_count:
            raise ValueError("saved ordinal cannot exceed cloud timeline count")
        if self.state is CloudRunState.QUEUED and (
            self.claim_owner is not None or self.lease_expires_at is not None
        ):
            raise ValueError("queued cloud run cannot have a claim")
        if self.state is CloudRunState.RUNNING and (
            self.claim_owner is None or self.lease_expires_at is None
        ):
            raise ValueError("running cloud run requires a claim and lease")
        if self.state in {CloudRunState.COMPLETE, CloudRunState.FAILED} and (
            self.claim_owner is not None
            or self.lease_expires_at is not None
            or self.completed_at is None
        ):
            raise ValueError("terminal cloud run must release its claim")
        digests = (
            self.semantic_digest,
            self.final_trace_digest,
            self.transcript_digest,
            self.json_digest,
            self.csv_digest,
        )
        if self.state is CloudRunState.COMPLETE and any(item is None for item in digests):
            raise ValueError("complete cloud metadata requires every evidence digest")
        if self.state is CloudRunState.FAILED and (
            any(item is not None for item in digests) or self.outcome.state != "failed"
        ):
            raise ValueError("failed cloud metadata cannot expose completion evidence")
        if self.state in {CloudRunState.QUEUED, CloudRunState.RUNNING} and (
            any(item is not None for item in digests) or self.completed_at is not None
        ):
            raise ValueError("nonterminal cloud metadata cannot expose completion evidence")
        for timestamp in (
            self.created_at,
            self.started_at,
            self.updated_at,
            self.completed_at,
            self.lease_expires_at,
        ):
            if timestamp is not None and timestamp.utcoffset() is None:
                raise ValueError("cloud metadata timestamps require timezone offsets")
        return self


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("cloud document contains an unsupported value")


def _record_payload(record: CloudTimelineRecord) -> dict[str, object]:
    return record.model_dump(mode="json", exclude={"record_hash"})


def _safe_alias(value: str | None) -> str:
    alias = "coordination-" + secrets.token_hex(8) if value is None else value
    if not isinstance(alias, str) or _SAFE_ALIAS.fullmatch(alias) is None:
        raise ValueError("cloud run alias is invalid")
    return alias


def _safe_opaque(value: str, *, label: str) -> str:
    if not isinstance(value, str) or _SAFE_OPAQUE.fullmatch(value) is None:
        raise ValueError(f"cloud {label} is invalid")
    return value


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("cloud timestamps require a timezone offset")
    return value


def timeline_document_id(ordinal: int) -> str:
    """Return the exact bounded Firestore document ID for one timeline ordinal."""
    if type(ordinal) is not int or not 1 <= ordinal < 10**_TIMELINE_ID_WIDTH:
        raise ValueError("cloud timeline ordinal is invalid")
    return f"{ordinal:0{_TIMELINE_ID_WIDTH}d}"


def _initial_snapshot(request: CoordinationRequest, run_alias: str) -> StudioWorkspaceSnapshot:
    scenario = build_coordination_scenario(request, seed=7, scenario_id=run_alias)
    store, _ = create_studio_progress(request, scenario)
    return store.snapshot()


def _new_metadata(
    request: CoordinationRequest,
    run_alias: str,
    idempotency_key: str,
    now: datetime,
) -> CloudRunMetadata:
    request = CoordinationRequest.model_validate(request)
    if request.agent_mode is not StudioAgentMode.GOOGLE_ADK:
        raise ValueError("cloud run requires the Google ADK mode")
    initial = _initial_snapshot(request, run_alias)
    return CloudRunMetadata(
        run_alias=run_alias,
        idempotency_key_hash=_digest(idempotency_key),
        request=request,
        target_date=coordination_target_date(request, reference_date=now.date()),
        state=CloudRunState.QUEUED,
        lifecycle_stage=initial.lifecycle.current,
        saved_ordinal=0,
        timeline_count=0,
        version=1,
        outcome=initial.outcome,
        created_at=now,
        updated_at=now,
    )


def _binding_from_metadata(metadata: CloudRunMetadata) -> CloudTerminalBinding:
    if metadata.state not in {CloudRunState.COMPLETE, CloudRunState.FAILED}:
        raise ValueError("cloud run is not terminal")
    return CloudTerminalBinding(
        state=metadata.state,
        outcome=metadata.outcome,
        semantic_digest=metadata.semantic_digest,
        final_trace_digest=metadata.final_trace_digest,
        transcript_digest=metadata.transcript_digest,
        json_digest=metadata.json_digest,
        csv_digest=metadata.csv_digest,
    )


def _assert_claim(metadata: CloudRunMetadata, owner: str, now: datetime) -> None:
    if metadata.state is not CloudRunState.RUNNING or metadata.claim_owner != owner:
        raise CloudDivergenceError("claim_lost")
    if metadata.lease_expires_at is None or metadata.lease_expires_at <= now:
        raise CloudDivergenceError("claim_expired")


def _assert_active_document(data: dict[str, object] | None, run_alias: str) -> None:
    if data is None or data.get("run_alias") != run_alias:
        raise CloudDivergenceError("active_owner_lost")


def _assert_next_record(metadata: CloudRunMetadata, record: CloudTimelineRecord) -> None:
    if record.timeline_ordinal != metadata.timeline_count + 1:
        raise CloudDivergenceError("timeline_gap")
    persisted = record.event.persisted_ordinal
    if persisted is not None and persisted != metadata.saved_ordinal + 1:
        raise CloudDivergenceError("persisted_ordinal_gap")


def _metadata_after_append(
    metadata: CloudRunMetadata,
    record: CloudTimelineRecord,
    now: datetime,
) -> CloudRunMetadata:
    return metadata.model_copy(
        update={
            "timeline_count": record.timeline_ordinal,
            "saved_ordinal": (
                metadata.saved_ordinal
                if record.event.persisted_ordinal is None
                else record.event.persisted_ordinal
            ),
            "lifecycle_stage": record.lifecycle.current,
            "version": metadata.version + 1,
            "updated_at": now,
        }
    )


def _terminal_metadata(
    metadata: CloudRunMetadata,
    binding: CloudTerminalBinding,
    now: datetime,
) -> CloudRunMetadata:
    return metadata.model_copy(
        update={
            "state": binding.state,
            "claim_owner": None,
            "lease_expires_at": None,
            "version": metadata.version + 1,
            "outcome": binding.outcome,
            "semantic_digest": binding.semantic_digest,
            "final_trace_digest": binding.final_trace_digest,
            "transcript_digest": binding.transcript_digest,
            "json_digest": binding.json_digest,
            "csv_digest": binding.csv_digest,
            "updated_at": now,
            "completed_at": now,
        }
    )


def _dispatch_failed_metadata(
    metadata: CloudRunMetadata,
    now: datetime,
) -> CloudRunMetadata:
    return metadata.model_copy(
        update={
            "state": CloudRunState.FAILED,
            "version": metadata.version + 1,
            "outcome": StudioOutcome(
                state="failed",
                headline="Coordination stopped",
                summary="The saved workspace remains available for review.",
            ),
            "updated_at": now,
            "completed_at": now,
        }
    )


def _reconstruct_snapshot(
    metadata: CloudRunMetadata,
    records: tuple[CloudTimelineRecord, ...],
) -> StudioWorkspaceSnapshot:
    if [item.timeline_ordinal for item in records] != list(
        range(1, metadata.timeline_count + 1)
    ):
        raise CloudDivergenceError("timeline_prefix_invalid")
    initial = _initial_snapshot(metadata.request, metadata.run_alias)
    events = tuple(item.event for item in records)
    conversations = tuple(item for record in records for item in record.conversations)
    data_points = tuple(item.data_point for item in records)
    active = events[-1].active_transition if events else None
    active_persona = events[-1].affected_persona_id if events else None
    nodes = tuple(
        item.model_copy(update={"active": item.persona_id == active_persona})
        for item in initial.graph_nodes
    )
    edges = tuple(
        item.model_copy(
            update={
                "active": bool(
                    active
                    and item.source == active.source
                    and item.destination == active.destination
                )
            }
        )
        for item in initial.graph_edges
    )
    state = {
        CloudRunState.QUEUED: "starting",
        CloudRunState.RUNNING: "running",
        CloudRunState.COMPLETE: "complete",
        CloudRunState.FAILED: "failed",
    }[metadata.state]
    snapshot = StudioWorkspaceSnapshot(
        **initial.model_dump(
            exclude={
                "run_state",
                "lifecycle",
                "graph_nodes",
                "graph_edges",
                "events",
                "conversations",
                "data_points",
                "active_transition",
                "current_event_ordinal",
                "total_event_count",
                "outcome",
                "downloads_ready",
            }
        ),
        run_state=state,
        lifecycle=(records[-1].lifecycle if records else initial.lifecycle),
        graph_nodes=nodes,
        graph_edges=edges,
        events=events,
        conversations=conversations,
        data_points=data_points,
        active_transition=active,
        current_event_ordinal=len(records),
        total_event_count=len(records),
        outcome=metadata.outcome,
        downloads_ready=metadata.state is CloudRunState.COMPLETE,
    )
    snapshot._final_trace_sha256 = metadata.final_trace_digest
    snapshot._transcript_sha256 = metadata.transcript_digest
    return snapshot


class InMemoryRunRepository:
    """Thread-safe reference implementation for unit tests and local execution."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active_run: str | None = None
        self._runs: dict[str, CloudRunMetadata] = {}
        self._timeline: dict[str, dict[int, CloudTimelineRecord]] = {}

    @property
    def active_run(self) -> str | None:
        with self._lock:
            return self._active_run

    def create_run(
        self,
        request: CoordinationRequest,
        *,
        run_alias: str | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> CloudRunCreation:
        alias = _safe_alias(run_alias)
        key = _safe_opaque(
            idempotency_key or ("dispatch-" + secrets.token_hex(16)),
            label="idempotency key",
        )
        at = _aware(now or datetime.now(UTC))
        metadata = _new_metadata(request, alias, key, at)
        with self._lock:
            if self._active_run is not None:
                raise CloudActiveRunError()
            if alias in self._runs:
                raise CloudDivergenceError("run_alias_conflict")
            self._runs[alias] = metadata
            self._timeline[alias] = {}
            self._active_run = alias
        return CloudRunCreation(run_alias=alias, idempotency_key=key)

    def load_metadata(self, run_alias: str) -> CloudRunMetadata:
        alias = _safe_alias(run_alias)
        with self._lock:
            metadata = self._runs.get(alias)
            if metadata is None:
                raise CloudUnknownRunError()
            return metadata.model_copy(deep=True)

    def load_snapshot(self, run_alias: str) -> StudioWorkspaceSnapshot:
        alias = _safe_alias(run_alias)
        with self._lock:
            metadata = self._runs.get(alias)
            if metadata is None:
                raise CloudUnknownRunError()
            records = tuple(self._timeline[alias][item] for item in sorted(self._timeline[alias]))
            return _reconstruct_snapshot(metadata, records).model_copy(deep=True)

    def fail_queued_dispatch(
        self,
        run_alias: str,
        idempotency_key: str,
        *,
        now: datetime,
    ) -> bool:
        alias = _safe_alias(run_alias)
        key = _safe_opaque(idempotency_key, label="idempotency key")
        at = _aware(now)
        with self._lock:
            metadata = self._runs.get(alias)
            if metadata is None:
                raise CloudUnknownRunError()
            if metadata.idempotency_key_hash != _digest(key):
                raise CloudDivergenceError("idempotency_mismatch")
            if metadata.state is CloudRunState.FAILED:
                return False
            if metadata.state is not CloudRunState.QUEUED:
                raise CloudDivergenceError("dispatch_state_conflict")
            if self._active_run != alias:
                raise CloudDivergenceError("active_owner_lost")
            self._runs[alias] = _dispatch_failed_metadata(metadata, at)
            self._active_run = None
            return True

    def claim_run(
        self,
        run_alias: str,
        idempotency_key: str,
        claim_owner: str,
        *,
        now: datetime,
        lease_seconds: int,
        recovery_record: CloudTimelineRecord | None = None,
    ) -> CloudClaimResult:
        alias = _safe_alias(run_alias)
        key = _safe_opaque(idempotency_key, label="idempotency key")
        owner = _safe_opaque(claim_owner, label="claim owner")
        at = _aware(now)
        if not 1 <= lease_seconds <= 900:
            raise ValueError("cloud lease must be between 1 and 900 seconds")
        with self._lock:
            metadata = self._runs.get(alias)
            if metadata is None:
                raise CloudUnknownRunError()
            if metadata.idempotency_key_hash != _digest(key):
                raise CloudDivergenceError("idempotency_mismatch")
            if metadata.state in {CloudRunState.COMPLETE, CloudRunState.FAILED}:
                return CloudClaimResult(status=CloudClaimStatus.TERMINAL, version=metadata.version)
            lease_expires = at + timedelta(seconds=lease_seconds)
            if metadata.state is CloudRunState.QUEUED:
                claimed = metadata.model_copy(
                    update={
                        "state": CloudRunState.RUNNING,
                        "claim_owner": owner,
                        "lease_expires_at": lease_expires,
                        "version": metadata.version + 1,
                        "started_at": at,
                        "updated_at": at,
                    }
                )
                self._runs[alias] = claimed
                return CloudClaimResult(
                    status=CloudClaimStatus.CLAIMED,
                    lease_expires_at=lease_expires,
                    version=claimed.version,
                )
            assert metadata.lease_expires_at is not None
            if metadata.lease_expires_at > at:
                status = (
                    CloudClaimStatus.DUPLICATE
                    if metadata.claim_owner == owner
                    else CloudClaimStatus.CONFLICT
                )
                return CloudClaimResult(
                    status=status,
                    lease_expires_at=metadata.lease_expires_at,
                    version=metadata.version,
                )
            if recovery_record is None:
                raise CloudExpiredClaimError()
            _assert_next_record(metadata, recovery_record)
            if recovery_record.event.effect != "inert":
                raise CloudDivergenceError("recovery_record_must_be_inert")
            self._timeline[alias][recovery_record.timeline_ordinal] = recovery_record
            recovered = _metadata_after_append(metadata, recovery_record, at).model_copy(
                update={
                    "claim_owner": owner,
                    "lease_expires_at": lease_expires,
                    "version": metadata.version + 1,
                    "updated_at": at,
                }
            )
            self._runs[alias] = recovered
            return CloudClaimResult(
                status=CloudClaimStatus.RECOVERED,
                lease_expires_at=lease_expires,
                version=recovered.version,
            )

    def renew_claim(
        self,
        run_alias: str,
        claim_owner: str,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        alias = _safe_alias(run_alias)
        owner = _safe_opaque(claim_owner, label="claim owner")
        at = _aware(now)
        if not 1 <= lease_seconds <= 900:
            raise ValueError("cloud lease must be between 1 and 900 seconds")
        with self._lock:
            metadata = self._runs.get(alias)
            if metadata is None:
                raise CloudUnknownRunError()
            if (
                metadata.state is not CloudRunState.RUNNING
                or metadata.claim_owner != owner
                or metadata.lease_expires_at is None
                or metadata.lease_expires_at <= at
            ):
                return False
            self._runs[alias] = metadata.model_copy(
                update={
                    "lease_expires_at": at + timedelta(seconds=lease_seconds),
                    "version": metadata.version + 1,
                    "updated_at": at,
                }
            )
            return True

    def append_timeline(
        self,
        run_alias: str,
        claim_owner: str,
        record: CloudTimelineRecord,
        *,
        now: datetime,
    ) -> bool:
        alias = _safe_alias(run_alias)
        owner = _safe_opaque(claim_owner, label="claim owner")
        record = CloudTimelineRecord.model_validate(record)
        at = _aware(now)
        with self._lock:
            metadata = self._runs.get(alias)
            if metadata is None:
                raise CloudUnknownRunError()
            existing = self._timeline[alias].get(record.timeline_ordinal)
            if existing is not None:
                if existing.record_hash == record.record_hash:
                    return False
                raise CloudDivergenceError("timeline_divergence")
            _assert_claim(metadata, owner, at)
            _assert_next_record(metadata, record)
            self._timeline[alias][record.timeline_ordinal] = record
            self._runs[alias] = _metadata_after_append(metadata, record, at)
            return True

    def finish_run(
        self,
        run_alias: str,
        claim_owner: str,
        binding: CloudTerminalBinding,
        *,
        now: datetime,
    ) -> bool:
        alias = _safe_alias(run_alias)
        owner = _safe_opaque(claim_owner, label="claim owner")
        binding = CloudTerminalBinding.model_validate(binding)
        at = _aware(now)
        with self._lock:
            metadata = self._runs.get(alias)
            if metadata is None:
                raise CloudUnknownRunError()
            if metadata.state in {CloudRunState.COMPLETE, CloudRunState.FAILED}:
                if _binding_from_metadata(metadata) == binding:
                    return False
                raise CloudDivergenceError("terminal_divergence")
            _assert_claim(metadata, owner, at)
            if self._active_run != alias:
                raise CloudDivergenceError("active_owner_lost")
            self._runs[alias] = _terminal_metadata(metadata, binding, at)
            self._active_run = None
            return True


def _model_from_document(model_type, data: dict[str, object]):
    return model_type.model_validate_json(
        json.dumps(data, default=_json_default, separators=(",", ":"))
    )


def _metadata_document(metadata: CloudRunMetadata) -> dict[str, object]:
    data = metadata.model_dump(mode="json")
    for field in ("created_at", "started_at", "updated_at", "completed_at", "lease_expires_at"):
        data[field] = getattr(metadata, field)
    return data


def _record_document(record: CloudTimelineRecord, *, server_timestamp: object) -> dict[str, object]:
    return {**record.model_dump(mode="json"), "written_at": server_timestamp}


class FirestoreRunRepository:
    """Transactional Firestore implementation with the same safe semantics."""

    def __init__(
        self,
        client: Any,
        *,
        run_collection: str = "humanwire_runs",
        control_collection: str = "humanwire_control",
    ) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", run_collection):
            raise ValueError("cloud run collection is invalid")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", control_collection):
            raise ValueError("cloud control collection is invalid")
        self._client = client
        self._run_collection = run_collection
        self._control_collection = control_collection

    def _run_ref(self, alias: str):
        return self._client.collection(self._run_collection).document(alias)

    def _active_ref(self):
        return self._client.collection(self._control_collection).document("active_run")

    def create_run(
        self,
        request: CoordinationRequest,
        *,
        run_alias: str | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> CloudRunCreation:
        from google.cloud import firestore

        alias = _safe_alias(run_alias)
        key = _safe_opaque(
            idempotency_key or ("dispatch-" + secrets.token_hex(16)),
            label="idempotency key",
        )
        at = _aware(now or datetime.now(UTC))
        metadata = _new_metadata(request, alias, key, at)
        run_ref = self._run_ref(alias)
        active_ref = self._active_ref()

        @firestore.transactional
        def create(transaction):
            if active_ref.get(transaction=transaction).exists:
                raise CloudActiveRunError()
            if run_ref.get(transaction=transaction).exists:
                raise CloudDivergenceError("run_alias_conflict")
            document = _metadata_document(metadata)
            document["created_at"] = firestore.SERVER_TIMESTAMP
            document["updated_at"] = firestore.SERVER_TIMESTAMP
            transaction.create(run_ref, document)
            transaction.set(
                active_ref,
                {
                    "run_alias": alias,
                    "state": CloudRunState.QUEUED.value,
                    "owner_version": metadata.version,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )

        create(self._client.transaction())
        return CloudRunCreation(run_alias=alias, idempotency_key=key)

    def load_metadata(self, run_alias: str) -> CloudRunMetadata:
        alias = _safe_alias(run_alias)
        snapshot = self._run_ref(alias).get()
        if not snapshot.exists:
            raise CloudUnknownRunError()
        return _model_from_document(CloudRunMetadata, snapshot.to_dict())

    def load_snapshot(self, run_alias: str) -> StudioWorkspaceSnapshot:
        alias = _safe_alias(run_alias)
        metadata = self.load_metadata(alias)
        rows = self._run_ref(alias).collection("timeline").order_by("timeline_ordinal").stream()
        records = []
        for row in rows:
            data = row.to_dict()
            data.pop("written_at", None)
            records.append(_model_from_document(CloudTimelineRecord, data))
        return _reconstruct_snapshot(metadata, tuple(records))

    def fail_queued_dispatch(
        self,
        run_alias: str,
        idempotency_key: str,
        *,
        now: datetime,
    ) -> bool:
        from google.cloud import firestore

        alias = _safe_alias(run_alias)
        key = _safe_opaque(idempotency_key, label="idempotency key")
        at = _aware(now)
        run_ref = self._run_ref(alias)
        active_ref = self._active_ref()

        @firestore.transactional
        def fail_dispatch(transaction):
            run_row = run_ref.get(transaction=transaction)
            if not run_row.exists:
                raise CloudUnknownRunError()
            metadata = _model_from_document(CloudRunMetadata, run_row.to_dict())
            if metadata.idempotency_key_hash != _digest(key):
                raise CloudDivergenceError("idempotency_mismatch")
            if metadata.state is CloudRunState.FAILED:
                return False
            if metadata.state is not CloudRunState.QUEUED:
                raise CloudDivergenceError("dispatch_state_conflict")
            active_row = active_ref.get(transaction=transaction)
            _assert_active_document(
                active_row.to_dict() if active_row.exists else None,
                alias,
            )
            updated = _dispatch_failed_metadata(metadata, at)
            document = _metadata_document(updated)
            document["updated_at"] = firestore.SERVER_TIMESTAMP
            document["completed_at"] = firestore.SERVER_TIMESTAMP
            transaction.set(run_ref, document)
            transaction.delete(active_ref)
            return True

        return fail_dispatch(self._client.transaction())

    def claim_run(
        self,
        run_alias: str,
        idempotency_key: str,
        claim_owner: str,
        *,
        now: datetime,
        lease_seconds: int,
        recovery_record: CloudTimelineRecord | None = None,
    ) -> CloudClaimResult:
        from google.cloud import firestore

        alias = _safe_alias(run_alias)
        key = _safe_opaque(idempotency_key, label="idempotency key")
        owner = _safe_opaque(claim_owner, label="claim owner")
        at = _aware(now)
        if not 1 <= lease_seconds <= 900:
            raise ValueError("cloud lease must be between 1 and 900 seconds")
        run_ref = self._run_ref(alias)
        active_ref = self._active_ref()

        @firestore.transactional
        def claim(transaction):
            row = run_ref.get(transaction=transaction)
            if not row.exists:
                raise CloudUnknownRunError()
            metadata = _model_from_document(CloudRunMetadata, row.to_dict())
            if metadata.idempotency_key_hash != _digest(key):
                raise CloudDivergenceError("idempotency_mismatch")
            if metadata.state in {CloudRunState.COMPLETE, CloudRunState.FAILED}:
                return CloudClaimResult(
                    status=CloudClaimStatus.TERMINAL, version=metadata.version
                )
            active_row = active_ref.get(transaction=transaction)
            _assert_active_document(
                active_row.to_dict() if active_row.exists else None,
                alias,
            )
            lease_expires = at + timedelta(seconds=lease_seconds)
            if metadata.state is CloudRunState.QUEUED:
                updated = metadata.model_copy(
                    update={
                        "state": CloudRunState.RUNNING,
                        "claim_owner": owner,
                        "lease_expires_at": lease_expires,
                        "version": metadata.version + 1,
                        "started_at": at,
                        "updated_at": at,
                    }
                )
                document = _metadata_document(updated)
                document["started_at"] = firestore.SERVER_TIMESTAMP
                document["updated_at"] = firestore.SERVER_TIMESTAMP
                transaction.set(run_ref, document)
                transaction.set(
                    active_ref,
                    {
                        "run_alias": alias,
                        "state": CloudRunState.RUNNING.value,
                        "owner_version": updated.version,
                        "updated_at": firestore.SERVER_TIMESTAMP,
                    },
                )
                return CloudClaimResult(
                    status=CloudClaimStatus.CLAIMED,
                    lease_expires_at=lease_expires,
                    version=updated.version,
                )
            assert metadata.lease_expires_at is not None
            if metadata.lease_expires_at > at:
                return CloudClaimResult(
                    status=(
                        CloudClaimStatus.DUPLICATE
                        if metadata.claim_owner == owner
                        else CloudClaimStatus.CONFLICT
                    ),
                    lease_expires_at=metadata.lease_expires_at,
                    version=metadata.version,
                )
            if recovery_record is None:
                raise CloudExpiredClaimError()
            recovery = CloudTimelineRecord.model_validate(recovery_record)
            _assert_next_record(metadata, recovery)
            if recovery.event.effect != "inert":
                raise CloudDivergenceError("recovery_record_must_be_inert")
            timeline_ref = run_ref.collection("timeline").document(
                timeline_document_id(recovery.timeline_ordinal)
            )
            if timeline_ref.get(transaction=transaction).exists:
                raise CloudDivergenceError("timeline_divergence")
            updated = _metadata_after_append(metadata, recovery, at).model_copy(
                update={
                    "claim_owner": owner,
                    "lease_expires_at": lease_expires,
                    "version": metadata.version + 1,
                    "updated_at": at,
                }
            )
            transaction.create(
                timeline_ref,
                _record_document(recovery, server_timestamp=firestore.SERVER_TIMESTAMP),
            )
            document = _metadata_document(updated)
            document["updated_at"] = firestore.SERVER_TIMESTAMP
            transaction.set(run_ref, document)
            transaction.set(
                active_ref,
                {
                    "run_alias": alias,
                    "state": CloudRunState.RUNNING.value,
                    "owner_version": updated.version,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return CloudClaimResult(
                status=CloudClaimStatus.RECOVERED,
                lease_expires_at=lease_expires,
                version=updated.version,
            )

        return claim(self._client.transaction())

    def renew_claim(
        self,
        run_alias: str,
        claim_owner: str,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        from google.cloud import firestore

        alias = _safe_alias(run_alias)
        owner = _safe_opaque(claim_owner, label="claim owner")
        at = _aware(now)
        if not 1 <= lease_seconds <= 900:
            raise ValueError("cloud lease must be between 1 and 900 seconds")
        run_ref = self._run_ref(alias)
        active_ref = self._active_ref()

        @firestore.transactional
        def renew(transaction):
            row = run_ref.get(transaction=transaction)
            if not row.exists:
                raise CloudUnknownRunError()
            metadata = _model_from_document(CloudRunMetadata, row.to_dict())
            active_row = active_ref.get(transaction=transaction)
            _assert_active_document(
                active_row.to_dict() if active_row.exists else None,
                alias,
            )
            if (
                metadata.state is not CloudRunState.RUNNING
                or metadata.claim_owner != owner
                or metadata.lease_expires_at is None
                or metadata.lease_expires_at <= at
            ):
                return False
            updated = metadata.model_copy(
                update={
                    "lease_expires_at": at + timedelta(seconds=lease_seconds),
                    "version": metadata.version + 1,
                    "updated_at": at,
                }
            )
            document = _metadata_document(updated)
            document["updated_at"] = firestore.SERVER_TIMESTAMP
            transaction.set(run_ref, document)
            transaction.set(
                active_ref,
                {
                    "run_alias": alias,
                    "state": CloudRunState.RUNNING.value,
                    "owner_version": updated.version,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return True

        return renew(self._client.transaction())

    def append_timeline(
        self,
        run_alias: str,
        claim_owner: str,
        record: CloudTimelineRecord,
        *,
        now: datetime,
    ) -> bool:
        from google.cloud import firestore

        alias = _safe_alias(run_alias)
        owner = _safe_opaque(claim_owner, label="claim owner")
        record = CloudTimelineRecord.model_validate(record)
        at = _aware(now)
        run_ref = self._run_ref(alias)
        active_ref = self._active_ref()
        timeline_ref = run_ref.collection("timeline").document(
            timeline_document_id(record.timeline_ordinal)
        )

        @firestore.transactional
        def append(transaction):
            run_row = run_ref.get(transaction=transaction)
            if not run_row.exists:
                raise CloudUnknownRunError()
            metadata = _model_from_document(CloudRunMetadata, run_row.to_dict())
            existing_row = timeline_ref.get(transaction=transaction)
            if existing_row.exists:
                existing_data = existing_row.to_dict()
                if existing_data.get("record_hash") == record.record_hash:
                    return False
                raise CloudDivergenceError("timeline_divergence")
            active_row = active_ref.get(transaction=transaction)
            _assert_active_document(
                active_row.to_dict() if active_row.exists else None,
                alias,
            )
            _assert_claim(metadata, owner, at)
            _assert_next_record(metadata, record)
            updated = _metadata_after_append(metadata, record, at)
            transaction.create(
                timeline_ref,
                _record_document(record, server_timestamp=firestore.SERVER_TIMESTAMP),
            )
            document = _metadata_document(updated)
            document["updated_at"] = firestore.SERVER_TIMESTAMP
            transaction.set(run_ref, document)
            transaction.set(
                active_ref,
                {
                    "run_alias": alias,
                    "state": CloudRunState.RUNNING.value,
                    "owner_version": updated.version,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return True

        return append(self._client.transaction())

    def finish_run(
        self,
        run_alias: str,
        claim_owner: str,
        binding: CloudTerminalBinding,
        *,
        now: datetime,
    ) -> bool:
        from google.cloud import firestore

        alias = _safe_alias(run_alias)
        owner = _safe_opaque(claim_owner, label="claim owner")
        binding = CloudTerminalBinding.model_validate(binding)
        at = _aware(now)
        run_ref = self._run_ref(alias)
        active_ref = self._active_ref()

        @firestore.transactional
        def finish(transaction):
            run_row = run_ref.get(transaction=transaction)
            if not run_row.exists:
                raise CloudUnknownRunError()
            metadata = _model_from_document(CloudRunMetadata, run_row.to_dict())
            if metadata.state in {CloudRunState.COMPLETE, CloudRunState.FAILED}:
                if _binding_from_metadata(metadata) == binding:
                    return False
                raise CloudDivergenceError("terminal_divergence")
            _assert_claim(metadata, owner, at)
            active_row = active_ref.get(transaction=transaction)
            if not active_row.exists or active_row.to_dict().get("run_alias") != alias:
                raise CloudDivergenceError("active_owner_lost")
            updated = _terminal_metadata(metadata, binding, at)
            document = _metadata_document(updated)
            document["updated_at"] = firestore.SERVER_TIMESTAMP
            document["completed_at"] = firestore.SERVER_TIMESTAMP
            transaction.set(run_ref, document)
            transaction.delete(active_ref)
            return True

        return finish(self._client.transaction())
