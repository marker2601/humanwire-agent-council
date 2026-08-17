from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime

import pytest

from humanwire.cloud_progress import CloudProgressPublisher, bound_cloud_exports
from humanwire.cloud_store import CloudDivergenceError, CloudRunState, InMemoryRunRepository
from humanwire.studio_models import StudioAgentMode
from humanwire.studio_projection import create_studio_progress
from humanwire.synthetic import build_coordination_scenario, generate_scenario
from tests.humanwire.studio_fixtures import launch_request

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
KEY = "dispatch-key-0000000000000001"
OWNER = "worker-owner-000000000000001"
ALIAS = "coordination-cloud-progress"


class CapturingPublisher:
    def __init__(self) -> None:
        self.snapshots = []

    def publish(self, snapshot) -> None:
        self.snapshots.append(snapshot.model_copy(deep=True))


def cloud_request():
    return launch_request(agent_mode=StudioAgentMode.GOOGLE_ADK)


def claimed_repository() -> InMemoryRunRepository:
    repository = InMemoryRunRepository()
    repository.create_run(
        cloud_request(), run_alias=ALIAS, idempotency_key=KEY, now=NOW
    )
    repository.claim_run(ALIAS, KEY, OWNER, now=NOW, lease_seconds=900)
    return repository


def completed_cloud_run(tmp_path):
    request = cloud_request()
    repository = claimed_repository()
    scenario = build_coordination_scenario(request, seed=7, scenario_id=ALIAS)
    publisher = CloudProgressPublisher(
        repository,
        run_alias=ALIAS,
        claim_owner=OWNER,
        clock=lambda: NOW,
    )
    store, observer = create_studio_progress(
        request,
        scenario,
        publisher=publisher,
    )
    result = generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        progress_observer=observer,
        presentation_observer=observer,
        mandate_request=request.objective,
        include_change_story=False,
        availability_date=date(2026, 8, 17),
        defer_authority_until_ready=True,
        include_conflict=request.include_conflict,
    )
    return repository, publisher, store, observer, result


def test_progress_store_publishes_a_validated_copy_and_none_is_unchanged() -> None:
    request = cloud_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id=ALIAS)
    plain, _ = create_studio_progress(request, scenario, publisher=None)
    capturing = CapturingPublisher()
    mirrored, _ = create_studio_progress(request, scenario, publisher=capturing)
    failed = mirrored.snapshot().model_copy(
        update={
            "run_state": "failed",
            "outcome": mirrored.snapshot().outcome.model_copy(
                update={
                    "state": "failed",
                    "headline": "Coordination stopped",
                    "summary": "The saved workspace remains available for review.",
                }
            ),
        }
    )

    plain.publish(failed)
    mirrored.publish(failed)

    assert plain.snapshot().model_dump_json() == mirrored.snapshot().model_dump_json()
    assert len(capturing.snapshots) == 1
    assert capturing.snapshots[0].model_dump_json() == failed.model_dump_json()


def test_real_projection_is_persisted_as_synchronized_immutable_records(tmp_path) -> None:
    repository, publisher, store, _observer, _result = completed_cloud_run(tmp_path)
    local = store.snapshot()
    durable_before_binding = repository.load_snapshot(ALIAS)

    assert local.run_state == "complete"
    assert durable_before_binding.run_state == "running"
    assert durable_before_binding.events == local.events
    assert durable_before_binding.conversations == local.conversations
    assert durable_before_binding.data_points == local.data_points
    assert durable_before_binding.lifecycle == local.lifecycle
    assert repository.load_metadata(ALIAS).timeline_count == len(local.events)

    artifacts = publisher.bind_completion(local)
    durable = repository.load_snapshot(ALIAS)
    cold_artifacts = bound_cloud_exports(repository, ALIAS)

    assert durable.model_dump(mode="json") == local.model_dump(mode="json")
    assert durable._final_trace_sha256 == local._final_trace_sha256
    assert durable._transcript_sha256 == local._transcript_sha256
    assert artifacts == cold_artifacts
    assert repository.load_metadata(ALIAS).state is CloudRunState.COMPLETE


def test_bound_json_and_csv_have_row_parity_unique_ordinals_and_digests(tmp_path) -> None:
    repository, publisher, store, _observer, _result = completed_cloud_run(tmp_path)
    artifacts = publisher.bind_completion(store.snapshot())

    payload = json.loads(artifacts.json_bytes)
    rows = list(csv.DictReader(io.StringIO(artifacts.csv_bytes.decode("utf-8"))))
    ordinals = [item["timeline_ordinal"] for item in payload["events"]]

    assert len(payload["events"]) == len(rows)
    assert {item["effect"] for item in payload["events"]} == {"persisted", "inert"}
    assert ordinals == list(range(1, len(ordinals) + 1))
    assert [int(row["timeline_ordinal"]) for row in rows] == ordinals
    assert [row["effect"] for row in rows] == [
        item["effect"] for item in payload["events"]
    ]
    metadata = repository.load_metadata(ALIAS)
    assert metadata.json_digest == artifacts.json_digest
    assert metadata.csv_digest == artifacts.csv_digest


def test_completion_binding_is_exact_idempotent_and_detects_late_rewrites(tmp_path) -> None:
    _repository, publisher, store, _observer, _result = completed_cloud_run(tmp_path)
    snapshot = store.snapshot()
    first = publisher.bind_completion(snapshot)

    assert publisher.bind_completion(snapshot) == first
    conversation = snapshot.conversations[-1].model_copy(update={"text": "Changed later."})
    divergent = snapshot.model_copy(
        update={"conversations": (*snapshot.conversations[:-1], conversation)}
    )
    divergent._final_trace_sha256 = snapshot._final_trace_sha256
    divergent._transcript_sha256 = snapshot._transcript_sha256

    with pytest.raises((CloudDivergenceError, ValueError)):
        publisher.publish(divergent)


def test_failure_binding_preserves_saved_prefix_without_exports(tmp_path) -> None:
    request = cloud_request()
    repository = claimed_repository()
    scenario = build_coordination_scenario(request, seed=7, scenario_id=ALIAS)
    publisher = CloudProgressPublisher(
        repository,
        run_alias=ALIAS,
        claim_owner=OWNER,
        clock=lambda: NOW,
    )
    store, _ = create_studio_progress(request, scenario, publisher=publisher)
    store.publish_failed()

    assert publisher.bind_failure(store.snapshot()) is True
    snapshot = repository.load_snapshot(ALIAS)
    assert snapshot.run_state == "failed"
    assert snapshot.downloads_ready is False
    with pytest.raises(CloudDivergenceError, match="exports_not_bound"):
        bound_cloud_exports(repository, ALIAS)
