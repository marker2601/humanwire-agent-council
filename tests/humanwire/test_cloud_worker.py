from __future__ import annotations

import base64
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from humanwire.cloud_dispatch import RunDispatchMessage
from humanwire.cloud_store import CloudRunState, InMemoryRunRepository
from humanwire.cloud_worker import (
    CloudRunWorker,
    WorkerDisposition,
    create_cloud_worker_app,
)
from humanwire.google_config import GoogleAuthMode, GoogleRuntimeConfig
from humanwire.google_decision_engine import GoogleAdkPersonaDecisionEngineFactory
from humanwire.studio_models import StudioAgentMode
from humanwire.synthetic import generate_scenario
from tests.humanwire.studio_fixtures import launch_request

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
ALIAS = "coordination-cloud-worker"
KEY = "dispatch-key-0000000000000001"
OWNER = "worker-owner-000000000000001"


class DecisionFactory:
    model_identifier = "gemini-3.5-flash"


def request():
    return launch_request(agent_mode=StudioAgentMode.GOOGLE_ADK)


def queued_repository() -> InMemoryRunRepository:
    repository = InMemoryRunRepository()
    repository.create_run(request(), run_alias=ALIAS, idempotency_key=KEY, now=NOW)
    return repository


def message() -> RunDispatchMessage:
    return RunDispatchMessage(run_alias=ALIAS, idempotency_key=KEY)


def deterministic_runner(roots: list[Path], factories: list[object]):
    def run(scenario, output_path, run_root, **kwargs):
        roots.append(Path(run_root))
        factories.append(kwargs["decision_engine"])
        assert kwargs["model_decision_timeout_seconds"] == 60.0
        return generate_scenario(
            scenario,
            output_path,
            run_root,
            decision_engine=None,
            max_decision_workers=1,
            progress_observer=kwargs["progress_observer"],
            presentation_observer=kwargs["presentation_observer"],
            mandate_request=kwargs["mandate_request"],
            include_change_story=False,
            availability_date=kwargs["availability_date"],
            defer_authority_until_ready=True,
            include_conflict=kwargs["include_conflict"],
        )

    return run


def test_worker_claims_before_factory_runs_completes_and_cleans_up() -> None:
    repository = queued_repository()
    roots: list[Path] = []
    factories: list[object] = []
    factory = GoogleAdkPersonaDecisionEngineFactory(
        runtime=GoogleRuntimeConfig(
            model_id="gemini-3.5-flash",
            auth_mode=GoogleAuthMode.VERTEX_AI_ADC,
            project_id="humanwire-demo",
            location="us-central1",
        )
    )
    builder_states = []

    def build_factory():
        builder_states.append(repository.load_metadata(ALIAS).state)
        return factory

    worker = CloudRunWorker(
        repository,
        decision_factory_builder=build_factory,
        runner=deterministic_runner(roots, factories),
        claim_owner_factory=lambda: OWNER,
        clock=lambda: NOW,
    )

    result = worker.handle(message())

    assert result is WorkerDisposition.ACCEPTED
    assert builder_states == [CloudRunState.RUNNING]
    assert factories == [factory]
    assert roots and all(not root.exists() for root in roots)
    assert repository.load_metadata(ALIAS).state is CloudRunState.COMPLETE
    assert repository.load_snapshot(ALIAS).downloads_ready is True
    assert worker.handle(message()) is WorkerDisposition.ACCEPTED
    assert builder_states == [CloudRunState.RUNNING]


def test_healthy_duplicate_conflicts_before_factory_or_runner() -> None:
    repository = queued_repository()
    repository.claim_run(ALIAS, KEY, OWNER, now=NOW, lease_seconds=60)
    calls = []
    worker = CloudRunWorker(
        repository,
        decision_factory_builder=lambda: calls.append("factory"),
        runner=lambda *args, **kwargs: calls.append("runner"),
        claim_owner_factory=lambda: "worker-owner-000000000000002",
        clock=lambda: NOW,
    )

    assert worker.handle(message()) is WorkerDisposition.CONFLICT
    assert calls == []


def test_same_healthy_delivery_is_accepted_without_rerunning() -> None:
    repository = queued_repository()
    repository.claim_run(ALIAS, KEY, OWNER, now=NOW, lease_seconds=60)
    calls = []
    worker = CloudRunWorker(
        repository,
        decision_factory_builder=lambda: calls.append("factory"),
        runner=lambda *args, **kwargs: calls.append("runner"),
        claim_owner_factory=lambda: OWNER,
        clock=lambda: NOW,
    )

    assert worker.handle(message()) is WorkerDisposition.ACCEPTED
    assert calls == []


def test_unknown_or_mismatched_dispatch_is_irreparable_before_factory() -> None:
    repository = queued_repository()
    calls = []
    worker = CloudRunWorker(
        repository,
        decision_factory_builder=lambda: calls.append("factory"),
        runner=lambda *args, **kwargs: calls.append("runner"),
        claim_owner_factory=lambda: OWNER,
        clock=lambda: NOW,
    )

    mismatched = RunDispatchMessage(
        run_alias=ALIAS,
        idempotency_key="dispatch-key-0000000000000099",
    )
    unknown = RunDispatchMessage(
        run_alias="coordination-unknown-worker",
        idempotency_key=KEY,
    )

    assert worker.handle(mismatched) is WorkerDisposition.INVALID
    assert worker.handle(unknown) is WorkerDisposition.INVALID
    assert calls == []


def test_expired_claim_records_recovery_and_fails_without_replaying_authority() -> None:
    repository = queued_repository()
    repository.claim_run(ALIAS, KEY, OWNER, now=NOW, lease_seconds=30)
    calls = []
    worker = CloudRunWorker(
        repository,
        decision_factory_builder=lambda: calls.append("factory"),
        runner=lambda *args, **kwargs: calls.append("runner"),
        claim_owner_factory=lambda: "worker-owner-000000000000002",
        clock=lambda: NOW + timedelta(seconds=31),
    )

    assert worker.handle(message()) is WorkerDisposition.ACCEPTED
    snapshot = repository.load_snapshot(ALIAS)
    assert snapshot.run_state == "failed"
    assert snapshot.events[-1].effect == "inert"
    assert snapshot.events[-1].live_copy == "Worker recovery started."
    assert calls == []


def test_worker_heartbeats_and_joins_its_non_daemon_claim_thread() -> None:
    repository = queued_repository()
    roots = []

    def wait_then_retry(_scenario, _output_path, run_root, **_kwargs):
        roots.append(Path(run_root))
        time.sleep(0.25)
        raise RuntimeError("retry")

    worker = CloudRunWorker(
        repository,
        decision_factory_builder=DecisionFactory,
        runner=wait_then_retry,
        claim_owner_factory=lambda: OWNER,
        clock=lambda: NOW,
        heartbeat_seconds=0.1,
    )

    assert worker.handle(message()) is WorkerDisposition.RETRY
    assert repository.load_metadata(ALIAS).version >= 4
    assert roots and not roots[0].exists()
    assert not any(
        thread.name == "humanwire-cloud-claim" for thread in threading.enumerate()
    )


def test_transient_heartbeat_error_does_not_abandon_a_healthy_lease() -> None:
    repository = queued_repository()
    roots: list[Path] = []
    factories: list[object] = []
    original_renew = repository.renew_claim
    renew_attempts = 0

    def flaky_renew(*args, **kwargs):
        nonlocal renew_attempts
        renew_attempts += 1
        if renew_attempts == 1:
            raise RuntimeError("PRIVATE-FIRESTORE-CONTENTION")
        return original_renew(*args, **kwargs)

    repository.renew_claim = flaky_renew  # type: ignore[method-assign]
    run = deterministic_runner(roots, factories)

    def wait_then_complete(*args, **kwargs):
        time.sleep(0.25)
        return run(*args, **kwargs)

    worker = CloudRunWorker(
        repository,
        decision_factory_builder=DecisionFactory,
        runner=wait_then_complete,
        claim_owner_factory=lambda: OWNER,
        clock=lambda: NOW,
        heartbeat_seconds=0.1,
    )

    assert worker.handle(message()) is WorkerDisposition.ACCEPTED
    assert renew_attempts >= 2
    assert repository.load_metadata(ALIAS).state is CloudRunState.COMPLETE


def test_unexpected_worker_failure_is_retryable_and_private() -> None:
    repository = queued_repository()

    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE-WORKER-PATH/API-KEY")

    worker = CloudRunWorker(
        repository,
        decision_factory_builder=DecisionFactory,
        runner=fail,
        claim_owner_factory=lambda: OWNER,
        clock=lambda: NOW,
    )

    result = worker.handle(message())

    assert result is WorkerDisposition.RETRY
    assert "PRIVATE" not in repr(result)
    assert repository.load_metadata(ALIAS).state is CloudRunState.RUNNING


def test_domain_failure_publishes_fixed_terminal_state_without_exports() -> None:
    repository = queued_repository()

    def fail(*args, **kwargs):
        raise ValueError("PRIVATE-DOMAIN-DETAIL")

    worker = CloudRunWorker(
        repository,
        decision_factory_builder=DecisionFactory,
        runner=fail,
        claim_owner_factory=lambda: OWNER,
        clock=lambda: NOW,
    )

    assert worker.handle(message()) is WorkerDisposition.ACCEPTED
    snapshot = repository.load_snapshot(ALIAS)
    assert snapshot.run_state == "failed"
    assert snapshot.downloads_ready is False
    assert "PRIVATE" not in snapshot.model_dump_json()


class StubWorker:
    def __init__(self, disposition: WorkerDisposition) -> None:
        self.disposition = disposition
        self.messages = []

    def handle(self, dispatch: RunDispatchMessage) -> WorkerDisposition:
        self.messages.append(dispatch)
        return self.disposition


def push_body(dispatch: RunDispatchMessage | None = None, **updates: object) -> bytes:
    dispatch = dispatch or message()
    envelope: dict[str, object] = {
        "message": {
            "data": base64.b64encode(dispatch.to_bytes()).decode("ascii"),
            "messageId": "provider-message-1",
            "publishTime": "2026-08-16T12:00:00Z",
        },
        "subscription": "projects/humanwire-demo/subscriptions/humanwire-worker",
    }
    envelope.update(updates)
    return json.dumps(envelope, separators=(",", ":")).encode()


def post(client: TestClient, body: bytes, **headers: str):
    return client.post(
        "/internal/pubsub/runs",
        content=body,
        headers={
            "Host": "worker.example.test",
            "Content-Type": "application/json",
            **headers,
        },
    )


@pytest.mark.parametrize(
    ("disposition", "status"),
    [
        (WorkerDisposition.ACCEPTED, 204),
        (WorkerDisposition.CONFLICT, 409),
        (WorkerDisposition.INVALID, 400),
        (WorkerDisposition.RETRY, 503),
    ],
)
def test_worker_route_maps_only_fixed_safe_dispositions(disposition, status) -> None:
    worker = StubWorker(disposition)
    app = create_cloud_worker_app(worker, allowed_hosts={"worker.example.test"})

    response = post(TestClient(app), push_body())

    assert response.status_code == status
    assert worker.messages == [message()]
    assert response.headers["cache-control"] == "no-store"
    assert "content-disposition" not in response.headers
    assert app.state.requires_platform_authentication is True


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b"not-json",
        json.dumps(
            {
                "message": {"data": "%%%", "messageId": "provider-message-1"},
                "subscription": "projects/humanwire-demo/subscriptions/humanwire-worker",
            }
        ).encode(),
        push_body(extra="rejected"),
    ],
)
def test_worker_route_rejects_malformed_envelopes_before_execution(body: bytes) -> None:
    worker = StubWorker(WorkerDisposition.ACCEPTED)
    client = TestClient(
        create_cloud_worker_app(worker, allowed_hosts={"worker.example.test"})
    )

    response = post(client, body)

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_envelope"}
    assert worker.messages == []


def test_worker_route_rejects_wrong_host_origin_path_query_method_and_encoding() -> None:
    worker = StubWorker(WorkerDisposition.ACCEPTED)
    client = TestClient(
        create_cloud_worker_app(worker, allowed_hosts={"worker.example.test"})
    )
    body = push_body()

    responses = [
        client.post(
            "/internal/pubsub/runs",
            content=body,
            headers={"Host": "wrong.example.test", "Content-Type": "application/json"},
        ),
        post(client, body, Origin="https://worker.example.test"),
        client.post(
            "/internal/pubsub/runs?retry=1",
            content=body,
            headers={"Host": "worker.example.test", "Content-Type": "application/json"},
        ),
        client.put(
            "/internal/pubsub/runs",
            content=body,
            headers={"Host": "worker.example.test", "Content-Type": "application/json"},
        ),
        post(client, body, **{"Content-Encoding": "gzip"}),
    ]

    assert [response.status_code for response in responses] == [400, 403, 405, 405, 400]
    assert worker.messages == []
