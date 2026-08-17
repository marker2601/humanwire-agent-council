"""Immutable cloud publication and cold-instance export reconstruction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from humanwire.cloud_store import (
    CloudDivergenceError,
    CloudRunMetadata,
    CloudRunState,
    CloudTerminalBinding,
    CloudTimelineRecord,
)
from humanwire.studio_exports import (
    product_events_csv,
    product_evidence_from_snapshot,
)
from humanwire.studio_projection import (
    StudioLifecycle,
    StudioLifecycleStage,
    StudioProgressStore,
    StudioWorkspaceSnapshot,
)


class _RunRepository(Protocol):
    def load_metadata(self, run_alias: str) -> CloudRunMetadata: ...

    def load_snapshot(self, run_alias: str) -> StudioWorkspaceSnapshot: ...

    def append_timeline(
        self,
        run_alias: str,
        claim_owner: str,
        record: CloudTimelineRecord,
        *,
        now: datetime,
    ) -> bool: ...

    def finish_run(
        self,
        run_alias: str,
        claim_owner: str,
        binding: CloudTerminalBinding,
        *,
        now: datetime,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class CloudProductExports:
    json_bytes: bytes
    csv_bytes: bytes
    json_digest: str
    csv_digest: str


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _lifecycle(stage: StudioLifecycleStage) -> StudioLifecycle:
    stages = tuple(StudioLifecycleStage)
    index = stages.index(stage)
    return StudioLifecycle(
        current=stage,
        stages=stages,
        completed=stages[:index],
    )


def _exports(snapshot: StudioWorkspaceSnapshot) -> CloudProductExports:
    evidence = product_evidence_from_snapshot(snapshot)
    json_bytes = json.dumps(
        evidence.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    csv_bytes = product_events_csv(evidence).encode("utf-8")
    return CloudProductExports(
        json_bytes=json_bytes,
        csv_bytes=csv_bytes,
        json_digest=_sha256(json_bytes),
        csv_digest=_sha256(csv_bytes),
    )


class CloudProgressPublisher:
    """Append each synchronized product ordinal through one claimed repository."""

    def __init__(
        self,
        repository: _RunRepository,
        *,
        run_alias: str,
        claim_owner: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._run_alias = run_alias
        self._claim_owner = claim_owner
        self._clock = clock or (lambda: datetime.now(UTC))

    def publish(self, snapshot: StudioWorkspaceSnapshot) -> None:
        candidate = StudioWorkspaceSnapshot.model_validate(snapshot.model_dump())
        candidate._final_trace_sha256 = snapshot._final_trace_sha256
        candidate._transcript_sha256 = snapshot._transcript_sha256
        if candidate.run_alias != self._run_alias:
            raise CloudDivergenceError("run_alias_mismatch")
        metadata = self._repository.load_metadata(self._run_alias)
        if metadata.state in {CloudRunState.COMPLETE, CloudRunState.FAILED}:
            existing = self._repository.load_snapshot(self._run_alias)
            if (
                existing.model_dump(mode="json") == candidate.model_dump(mode="json")
                and existing._final_trace_sha256 == candidate._final_trace_sha256
                and existing._transcript_sha256 == candidate._transcript_sha256
            ):
                return
            raise CloudDivergenceError("terminal_divergence")
        existing = self._repository.load_snapshot(self._run_alias)
        validator = StudioProgressStore(existing)
        validator.publish(candidate)
        current_count = metadata.timeline_count
        if current_count != len(existing.events):
            raise CloudDivergenceError("timeline_prefix_invalid")
        new_conversations = candidate.conversations[len(existing.conversations) :]
        if any(item.event_ordinal <= current_count for item in new_conversations):
            raise CloudDivergenceError("published_record_is_immutable")
        if candidate.run_state in {"complete", "failed"}:
            durable_watermark = len(candidate.events)
        else:
            # Presentation callbacks may attach to the newest saved event on the next
            # capture. Keep that one ordinal staged until a later event or finality.
            conversation_watermark = max(
                (item.event_ordinal for item in candidate.conversations),
                default=0,
            )
            durable_watermark = max(current_count, conversation_watermark - 1)
        if durable_watermark < current_count:
            raise CloudDivergenceError("timeline_watermark_regressed")
        for event in candidate.events[current_count:durable_watermark]:
            ordinal = event.timeline_ordinal
            conversations = tuple(
                item for item in candidate.conversations if item.event_ordinal == ordinal
            )
            record = CloudTimelineRecord.create(
                event=event,
                conversations=conversations,
                data_point=candidate.data_points[ordinal - 1],
                lifecycle=_lifecycle(event.stage),
            )
            self._repository.append_timeline(
                self._run_alias,
                self._claim_owner,
                record,
                now=self._clock(),
            )

    def bind_completion(self, snapshot: StudioWorkspaceSnapshot) -> CloudProductExports:
        if (
            snapshot.run_state != "complete"
            or not snapshot.downloads_ready
            or snapshot._final_trace_sha256 is None
            or snapshot._transcript_sha256 is None
        ):
            raise CloudDivergenceError("completion_not_bound")
        self.publish(snapshot)
        artifacts = _exports(snapshot)
        self._repository.finish_run(
            self._run_alias,
            self._claim_owner,
            CloudTerminalBinding(
                state=CloudRunState.COMPLETE,
                outcome=snapshot.outcome,
                semantic_digest=snapshot._final_trace_sha256,
                final_trace_digest=snapshot._final_trace_sha256,
                transcript_digest=snapshot._transcript_sha256,
                json_digest=artifacts.json_digest,
                csv_digest=artifacts.csv_digest,
            ),
            now=self._clock(),
        )
        return artifacts

    def bind_failure(self, snapshot: StudioWorkspaceSnapshot) -> bool:
        if snapshot.run_state != "failed" or snapshot.downloads_ready:
            raise CloudDivergenceError("failure_not_bound")
        self.publish(snapshot)
        return self._repository.finish_run(
            self._run_alias,
            self._claim_owner,
            CloudTerminalBinding(
                state=CloudRunState.FAILED,
                outcome=snapshot.outcome,
            ),
            now=self._clock(),
        )


def bound_cloud_exports(
    repository: _RunRepository,
    run_alias: str,
) -> CloudProductExports:
    """Regenerate and verify bound exports from any cold web instance."""
    metadata = repository.load_metadata(run_alias)
    if metadata.state is not CloudRunState.COMPLETE:
        raise CloudDivergenceError("exports_not_bound")
    snapshot = repository.load_snapshot(run_alias)
    artifacts = _exports(snapshot)
    if (
        metadata.json_digest != artifacts.json_digest
        or metadata.csv_digest != artifacts.csv_digest
    ):
        raise CloudDivergenceError("export_digest_mismatch")
    return artifacts
