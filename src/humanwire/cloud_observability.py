"""Fixed structured operational events for the durable Google runtime."""

from __future__ import annotations

import logging
from enum import StrEnum


class CloudLogEvent(StrEnum):
    RUN_QUEUED = "cloud.run_queued"
    RUN_CLAIMED = "cloud.run_claimed"
    RUN_RECOVERED = "cloud.run_recovered"
    RUN_COMPLETED = "cloud.run_completed"
    RUN_FAILED = "cloud.run_failed"


_STATES = frozenset({"queued", "running", "complete", "failed"})
_SERVICE_ROLES = frozenset({"web", "worker"})


def log_cloud_event(
    event: CloudLogEvent,
    *,
    state: str,
    service_role: str,
    logger: logging.Logger | None = None,
) -> None:
    """Log one fixed event with no free-form request, provider, or exception data."""
    event = CloudLogEvent(event)
    if type(state) is not str or state not in _STATES:
        raise ValueError("cloud log state is invalid")
    if type(service_role) is not str or service_role not in _SERVICE_ROLES:
        raise ValueError("cloud log service role is invalid")
    selected = logger or logging.getLogger("humanwire.cloud")
    selected.info(
        "cloud_runtime_event",
        extra={
            "event_type": event.value,
            "state": state,
            "service_role": service_role,
        },
    )


__all__ = ["CloudLogEvent", "log_cloud_event"]
