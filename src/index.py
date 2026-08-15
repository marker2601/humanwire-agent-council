"""Vercel entrypoint for the public HumanWire coordination product."""

from __future__ import annotations

import os
from pathlib import Path

from humanwire.studio_run import StudioRunManager
from humanwire.submission_app import create_submission_app


def _deployment_hosts() -> frozenset[str]:
    hosts = {"secondsignal.vercel.app"}
    for name in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        value = os.environ.get(name, "").strip().casefold()
        if value:
            hosts.add(value)
    return frozenset(hosts)


manager = StudioRunManager(
    workspace_root=Path("/tmp/humanwire-submission-runs"),
    seed=7,
    step_delay_ms=120,
)
app = create_submission_app(
    manager,
    action_token="humanwire-submission-action-v1",
    allowed_hosts=_deployment_hosts(),
)
