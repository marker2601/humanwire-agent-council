"""Structural contracts shared by cloud adapters without importing provider SDKs."""

from __future__ import annotations

from typing import Protocol

from humanwire.cloud_store import (
    CloudClaimResult,
    CloudRunCreation,
    CloudRunMetadata,
    CloudTerminalBinding,
    CloudTimelineRecord,
)
from humanwire.studio_models import CoordinationRequest
from humanwire.studio_projection import StudioWorkspaceSnapshot


class RunRepository(Protocol):
    def create_run(
        self,
        request: CoordinationRequest,
        *,
        run_alias: str | None = None,
        idempotency_key: str | None = None,
        now=None,
    ) -> CloudRunCreation:
        """Create one durable queued run and its private dispatch binding."""
        raise NotImplementedError

    def load_metadata(self, run_alias: str) -> CloudRunMetadata:
        """Load validated operational metadata without a dispatch key."""
        raise NotImplementedError

    def load_snapshot(self, run_alias: str) -> StudioWorkspaceSnapshot:
        """Reconstruct the current immutable public prefix."""
        raise NotImplementedError

    def claim_run(self, *args, **kwargs) -> CloudClaimResult:
        """Claim a queued run or classify a bounded duplicate delivery."""
        raise NotImplementedError

    def append_timeline(
        self,
        run_alias: str,
        claim_owner: str,
        record: CloudTimelineRecord,
        *,
        now,
    ) -> bool:
        """Append one immutable synchronized public record."""
        raise NotImplementedError

    def finish_run(
        self,
        run_alias: str,
        claim_owner: str,
        binding: CloudTerminalBinding,
        *,
        now,
    ) -> bool:
        """Atomically bind a terminal run and release global ownership."""
        raise NotImplementedError


class RunDispatcher(Protocol):
    def dispatch(self, run_alias: str, idempotency_key: str) -> None:
        """Dispatch one safe run reference for asynchronous execution."""
        raise NotImplementedError


class ProgressPublisher(Protocol):
    def publish(self, snapshot: StudioWorkspaceSnapshot) -> None:
        """Publish one validated public snapshot without rewriting history."""
        raise NotImplementedError
