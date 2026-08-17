"""Structural contracts shared by cloud adapters without importing provider SDKs."""

from __future__ import annotations

from typing import Protocol

from humanwire.studio_models import CoordinationRequest
from humanwire.studio_projection import StudioWorkspaceSnapshot


class RunRepository(Protocol):
    def create_run(self, request: CoordinationRequest) -> str:
        """Create one durable queued run and return its safe alias."""
        raise NotImplementedError

    def load_snapshot(self, run_alias: str) -> StudioWorkspaceSnapshot:
        """Reconstruct the current immutable public prefix."""
        raise NotImplementedError


class RunDispatcher(Protocol):
    def dispatch(self, run_alias: str, idempotency_key: str) -> None:
        """Dispatch one safe run reference for asynchronous execution."""
        raise NotImplementedError


class ProgressPublisher(Protocol):
    def publish(self, snapshot: StudioWorkspaceSnapshot) -> None:
        """Publish one validated public snapshot without rewriting history."""
        raise NotImplementedError
