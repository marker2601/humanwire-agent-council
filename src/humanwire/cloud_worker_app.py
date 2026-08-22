"""Canonical construction for the private Google Cloud worker service."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from humanwire.cloud_store import FirestoreRunRepository
from humanwire.cloud_worker import CloudRunWorker, create_cloud_worker_app
from humanwire.google_config import GoogleAuthMode, GoogleRuntimeConfig
from humanwire.google_decision_engine import GoogleAdkPersonaDecisionEngineFactory

_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,62}$")
_DATABASE = re.compile(r"^(?:\(default\)|[A-Za-z][A-Za-z0-9._~-]{0,62})$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


def _configuration(environment: Mapping[str, str]) -> tuple[str, str, str, GoogleRuntimeConfig]:
    try:
        project = environment.get("GOOGLE_CLOUD_PROJECT", "").strip()
        database = environment.get("HUMANWIRE_FIRESTORE_DATABASE", "(default)").strip()
        host = environment.get("HUMANWIRE_WORKER_HOST", "").strip().casefold()
        model = environment.get("HUMANWIRE_MODEL_ID", "gemini-3.5-flash").strip()
        location = environment.get("HUMANWIRE_GOOGLE_LOCATION", "global").strip()
        if (
            _PROJECT.fullmatch(project) is None
            or _DATABASE.fullmatch(database) is None
            or _HOST.fullmatch(host) is None
            or ".." in host
        ):
            raise ValueError
        runtime = GoogleRuntimeConfig(
            model_id=model,
            auth_mode=GoogleAuthMode.VERTEX_AI_ADC,
            project_id=project,
            location=location,
        )
    except (AttributeError, TypeError, ValueError):
        raise ValueError("cloud_worker_configuration_invalid") from None
    return project, database, host, runtime


def build_google_worker_app(
    environment: Mapping[str, str],
    *,
    firestore_client_factory: Callable[..., Any] | None = None,
):
    """Build the worker from ADC-backed, secret-free configuration only."""
    project, database, host, runtime = _configuration(environment)
    if firestore_client_factory is None:
        from google.cloud import firestore

        firestore_client_factory = firestore.Client
    client = None
    failed = False
    try:
        client = firestore_client_factory(project=project, database=database)
    except Exception:  # noqa: BLE001 - startup retains no provider details
        failed = True
    if failed or client is None:
        raise RuntimeError("cloud_worker_unavailable") from None
    repository = FirestoreRunRepository(client)
    worker = CloudRunWorker(
        repository,
        decision_factory_builder=lambda: GoogleAdkPersonaDecisionEngineFactory(
            runtime=runtime
        ),
    )
    app = create_cloud_worker_app(worker, allowed_hosts={host})
    app.state.worker = worker
    return app


__all__ = ["build_google_worker_app"]
