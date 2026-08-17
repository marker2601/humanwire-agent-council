from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from humanwire.cloud_dispatch import RunDispatchMessage
from humanwire.cloud_iam import cloud_iam_contract
from humanwire.cloud_observability import CloudLogEvent, log_cloud_event
from humanwire.cloud_store import CloudRunState, InMemoryRunRepository
from humanwire.cloud_worker import CloudRunWorker, WorkerDisposition, create_cloud_worker_app
from humanwire.google_submission_app import create_google_submission_app
from humanwire.logging_config import configure_logging
from tests.humanwire.studio_fixtures import launch_request

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
HOST = "humanwire-cloud.example.test"
ACTION = "cloud-action-token"
ALIAS = "coordination-cloud-hardening"
KEY = "dispatch-key-0000000000000901"
OWNER = "worker-owner-0000000000000901"


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def dispatch(self, run_alias: str, idempotency_key: str) -> None:
        self.calls.append((run_alias, idempotency_key))


def _cloud_request(**updates: object):
    return launch_request(agent_mode="google_adk", **updates)


def _post(client: TestClient, request):
    return client.post(
        "/api/runs",
        headers={
            "Origin": f"https://{HOST}",
            "X-HumanWire-Action": ACTION,
        },
        json=request.model_dump(mode="json"),
    )


def test_iam_contract_keeps_web_worker_and_push_authority_separate() -> None:
    contract = cloud_iam_contract()

    assert contract.web_roles == (
        "roles/datastore.user",
        "roles/pubsub.publisher",
    )
    assert contract.worker_roles == (
        "roles/aiplatform.user",
        "roles/datastore.user",
        "roles/logging.logWriter",
    )
    assert contract.push_roles == ("roles/run.invoker",)
    assert "roles/aiplatform.user" not in contract.web_roles
    assert "roles/pubsub.publisher" not in contract.worker_roles
    assert "allUsers" not in contract.model_dump_json()


def test_apps_expose_only_their_fixed_service_and_invocation_contracts() -> None:
    repository = InMemoryRunRepository()
    web = create_google_submission_app(
        repository,
        RecordingDispatcher(),
        action_token=ACTION,
        allowed_hosts={HOST},
        clock=lambda: NOW,
    )
    worker = create_cloud_worker_app(object(), allowed_hosts={"worker.example.test"})

    assert web.state.service_role == "web"
    assert web.state.requires_platform_authentication is False
    assert web.state.runtime_credentials_allowed is False
    assert worker.state.service_role == "worker"
    assert worker.state.requires_platform_authentication is True
    assert worker.state.browser_invocation_allowed is False


def test_cloud_logging_emits_only_fixed_allowlisted_metadata(capsys) -> None:
    configure_logging()
    logger = logging.getLogger("humanwire.cloud.hardening")

    log_cloud_event(
        CloudLogEvent.RUN_FAILED,
        state="failed",
        service_role="worker",
        logger=logger,
    )

    raw = capsys.readouterr().err
    payload = json.loads(raw)
    assert payload == {
        "timestamp": payload["timestamp"],
        "level": "INFO",
        "event": "cloud_runtime_event",
        "logger": "humanwire.cloud.hardening",
        "event_type": "cloud.run_failed",
        "state": "failed",
        "service_role": "worker",
    }
    assert "exception" not in raw.casefold()


@pytest.mark.parametrize(
    "objective",
    (
        "Coordinate private／秘密 before the launch decision.",
        "Coordinate password：private-value before approval.",
        "Coordinate ／confirm approve before the launch decision.",
        "Coordinate https：／／internal.example before approval.",
        "Coordinate AWS_ACCESS_KEY_ID AKIAABCDEFGHIJKLMNOP before approval.",
    ),
)
def test_unicode_normalized_private_requests_fail_as_invalid_before_storage(
    objective: str,
) -> None:
    repository = InMemoryRunRepository()
    dispatcher = RecordingDispatcher()
    app = create_google_submission_app(
        repository,
        dispatcher,
        action_token=ACTION,
        allowed_hosts={HOST},
        clock=lambda: NOW,
    )

    with TestClient(app, base_url=f"https://{HOST}") as client:
        response = _post(client, _cloud_request(objective=objective))

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}
    assert repository.active_run is None
    assert dispatcher.calls == []


def test_missing_google_runtime_fails_without_runner_or_standard_fallback() -> None:
    repository = InMemoryRunRepository()
    repository.create_run(
        _cloud_request(),
        run_alias=ALIAS,
        idempotency_key=KEY,
        now=NOW,
    )
    runner_calls: list[str] = []
    worker = CloudRunWorker(
        repository,
        decision_factory_builder=lambda: (_ for _ in ()).throw(
            ValueError("google_credentials_missing")
        ),
        runner=lambda *_args, **_kwargs: runner_calls.append("runner"),
        claim_owner_factory=lambda: OWNER,
        clock=lambda: NOW,
    )

    disposition = worker.handle(
        RunDispatchMessage(run_alias=ALIAS, idempotency_key=KEY)
    )

    snapshot = repository.load_snapshot(ALIAS)
    assert disposition is WorkerDisposition.ACCEPTED
    assert runner_calls == []
    assert snapshot.run_state == "failed"
    assert snapshot.downloads_ready is False
    assert "google_credentials_missing" not in snapshot.model_dump_json()


def test_lost_lease_never_binds_a_late_completed_worker() -> None:
    class LeaseLosingRepository(InMemoryRunRepository):
        def renew_claim(self, *args, **kwargs) -> bool:
            return False

    repository = LeaseLosingRepository()
    repository.create_run(
        _cloud_request(),
        run_alias=ALIAS,
        idempotency_key=KEY,
        now=NOW,
    )

    class Factory:
        model_identifier = "gemini-3.6-flash"

    def late_runner(_scenario, _output_path, _run_root, **_kwargs):
        time.sleep(0.25)

    worker = CloudRunWorker(
        repository,
        decision_factory_builder=Factory,
        runner=late_runner,
        claim_owner_factory=lambda: OWNER,
        clock=lambda: NOW,
        heartbeat_seconds=0.1,
    )

    disposition = worker.handle(
        RunDispatchMessage(run_alias=ALIAS, idempotency_key=KEY)
    )

    assert disposition is WorkerDisposition.RETRY
    assert repository.load_metadata(ALIAS).state is CloudRunState.RUNNING
    assert repository.load_snapshot(ALIAS).downloads_ready is False
