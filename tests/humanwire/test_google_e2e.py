from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from humanwire.cloud_dispatch import RunDispatchMessage
from humanwire.cloud_e2e import verify_cloud_authority_story
from humanwire.cloud_store import InMemoryRunRepository
from humanwire.cloud_worker import CloudRunWorker, WorkerDisposition
from humanwire.google_config import GoogleAuthMode, GoogleRuntimeConfig
from humanwire.google_decision_engine import GoogleAdkPersonaDecisionEngineFactory
from humanwire.google_submission_app import create_google_submission_app
from humanwire.studio_exports import StudioProductEvidence, product_events_csv
from humanwire.synthetic import generate_scenario
from tests.humanwire.studio_fixtures import launch_request

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
HOST = "humanwire-cloud.example.test"
ACTION_TOKEN = "cloud-action-token"
OWNER = "worker-owner-000000000000801"


class RecordingDispatcher:
    def __init__(self) -> None:
        self.messages: list[RunDispatchMessage] = []

    def dispatch(self, run_alias: str, idempotency_key: str) -> None:
        self.messages.append(
            RunDispatchMessage(
                run_alias=run_alias,
                idempotency_key=idempotency_key,
            )
        )


class NoDispatch:
    def dispatch(self, _run_alias: str, _idempotency_key: str) -> None:
        raise AssertionError("cold reads must not dispatch")


def _request(*, include_conflict: bool = True):
    return launch_request(
        agent_mode="google_adk",
        include_conflict=include_conflict,
    )


def _headers() -> dict[str, str]:
    return {
        "Origin": f"https://{HOST}",
        "X-HumanWire-Action": ACTION_TOKEN,
    }


def _fake_adk_runner(
    observed_factories: list[object],
    observed_results: list[object],
):
    def run(scenario, output_path, run_root, **kwargs):
        observed_factories.append(kwargs["decision_engine"])
        result = generate_scenario(
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
        observed_results.append(result)
        return result

    return run


def _factory() -> GoogleAdkPersonaDecisionEngineFactory:
    return GoogleAdkPersonaDecisionEngineFactory(
        runtime=GoogleRuntimeConfig(
            model_id="gemini-3.5-flash",
            auth_mode=GoogleAuthMode.VERTEX_AI_ADC,
            project_id="humanwire-demo",
            location="us-central1",
        )
    )


def _create_and_execute(*, include_conflict: bool = True):
    repository = InMemoryRunRepository()
    dispatcher = RecordingDispatcher()
    web = create_google_submission_app(
        repository,
        dispatcher,
        action_token=ACTION_TOKEN,
        allowed_hosts={HOST},
        clock=lambda: NOW,
    )
    with TestClient(web, base_url=f"https://{HOST}") as client:
        created = client.post(
            "/api/runs",
            headers=_headers(),
            json=_request(include_conflict=include_conflict).model_dump(mode="json"),
        )
    assert created.status_code == 202
    assert len(dispatcher.messages) == 1

    observed_factories: list[object] = []
    observed_results: list[object] = []
    worker = CloudRunWorker(
        repository,
        decision_factory_builder=_factory,
        runner=_fake_adk_runner(observed_factories, observed_results),
        claim_owner_factory=lambda: OWNER,
        clock=lambda: NOW,
    )
    message = dispatcher.messages[0]
    assert worker.handle(message) is WorkerDisposition.ACCEPTED
    before_redelivery = repository.load_snapshot(message.run_alias).model_dump_json()
    assert worker.handle(message) is WorkerDisposition.ACCEPTED
    assert repository.load_snapshot(message.run_alias).model_dump_json() == before_redelivery
    assert len(observed_factories) == 1
    assert isinstance(observed_factories[0], GoogleAdkPersonaDecisionEngineFactory)
    assert len(observed_results) == 1
    assert observed_results[0].gateway_handler_count == 1
    return repository, created.json()["run_alias"]


def _cold_artifacts(repository: InMemoryRunRepository, run_alias: str):
    cold = create_google_submission_app(
        repository,
        NoDispatch(),
        action_token=ACTION_TOKEN,
        allowed_hosts={HOST},
        clock=lambda: NOW,
    )
    with TestClient(cold, base_url=f"https://{HOST}") as client:
        snapshot = client.get(f"/api/runs/{run_alias}")
        unchanged = client.get(
            f"/api/runs/{run_alias}",
            headers={"If-None-Match": snapshot.headers["etag"]},
        )
        evidence = client.get(f"/api/runs/{run_alias}/evidence.json")
        events = client.get(f"/api/runs/{run_alias}/evidence.csv")
    assert unchanged.status_code == 304
    return snapshot, evidence, events


def test_cloud_queue_worker_cold_poll_and_exports_preserve_authority_story() -> None:
    repository, run_alias = _create_and_execute()
    snapshot_response, evidence_response, csv_response = _cold_artifacts(
        repository,
        run_alias,
    )

    proof = verify_cloud_authority_story(
        snapshot_response.content,
        evidence_response.content,
        csv_response.content,
    )

    assert proof.run_alias == run_alias
    assert proof.event_count == 55
    assert proof.ordered_ordinals == (1, 4, 25, 31, 35, 36, 43, 49, 51, 55)
    assert proof.meeting_ordinal == proof.event_count
    assert proof.terminal_state == "meeting_ready"
    assert snapshot_response.headers["x-humanwire-saved-ordinal"] == "52"

    public_artifacts = b"\n".join(
        (snapshot_response.content, evidence_response.content, csv_response.content)
    )
    folded_artifacts = public_artifacts.lower()
    for forbidden in (
        b"private-",
        b"api_key",
        b"authorization",
        b"route_id",
        b"conversation_id",
        b"assignment_id",
        b"/confirm",
        b"/decide",
        b"/available",
    ):
        assert forbidden not in folded_artifacts
    assert re.search(rb"\b[^\s@]+@[^\s@]+\b", public_artifacts) is None
    assert re.search(
        rb"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        folded_artifacts,
    ) is None

    json_events = evidence_response.json()["events"]
    csv_events = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(json_events) == len(csv_events) == proof.event_count
    for expected, row in zip(json_events, csv_events, strict=True):
        assert row["timeline_ordinal"] == str(expected["timeline_ordinal"])
        assert row["persisted_ordinal"] == (
            ""
            if expected["persisted_ordinal"] is None
            else str(expected["persisted_ordinal"])
        )
        assert row["effect"] == expected["effect"]
        assert row["data_point"] == expected["data_point"]


def test_cloud_conflict_disabled_path_still_engages_risk_and_reaches_meeting() -> None:
    repository, run_alias = _create_and_execute(include_conflict=False)
    snapshot_response, evidence_response, csv_response = _cold_artifacts(
        repository,
        run_alias,
    )
    snapshot = snapshot_response.json()
    labels = [item["label"] for item in snapshot["data_points"]]

    proof = verify_cloud_authority_story(
        snapshot_response.content,
        evidence_response.content,
        csv_response.content,
        expect_conflict=False,
    )

    assert proof.terminal_state == "meeting_ready"
    assert "Conflict identified" not in labels
    assert "Targeted interview" not in {
        item["active_transition"]["destination_label"] for item in snapshot["events"]
    }
    assert any(
        item["role"] == "Risk & compliance lead"
        and item["direction"] == "to_humanwire"
        and item["text"] == "Acknowledged."
        for item in snapshot["conversations"]
    )
    assert not any(
        "rollback" in item["text"].casefold()
        for item in snapshot["conversations"]
    )


def test_cloud_authority_proof_rejects_reordered_authority_and_export_drift() -> None:
    repository, run_alias = _create_and_execute()
    snapshot_response, evidence_response, csv_response = _cold_artifacts(
        repository,
        run_alias,
    )

    with pytest.raises(ValueError, match="^cloud_authority_story_invalid$"):
        verify_cloud_authority_story(
            snapshot_response.content,
            evidence_response.content,
            csv_response.content.replace(b"Meeting ready", b"Meeting maybe"),
        )

    snapshot_payload = snapshot_response.json()
    evidence_payload = evidence_response.json()
    labels = [item["label"] for item in snapshot_payload["data_points"]]
    evidence_index = labels.index("Confirmed evidence assembled")
    approval_index = labels.index("Approval complete")
    for payload in (snapshot_payload, evidence_payload):
        payload["data_points"][evidence_index]["label"] = "Approval complete"
        payload["data_points"][approval_index]["label"] = (
            "Confirmed evidence assembled"
        )
    evidence_payload["events"][evidence_index]["data_point"] = "Approval complete"
    evidence_payload["events"][approval_index]["data_point"] = (
        "Confirmed evidence assembled"
    )
    reordered_evidence = StudioProductEvidence.model_validate_json(
        json.dumps(evidence_payload, separators=(",", ":"))
    )
    reordered_json = json.dumps(
        reordered_evidence.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    reordered_csv = product_events_csv(reordered_evidence).encode()

    with pytest.raises(ValueError, match="^cloud_authority_story_invalid$"):
        verify_cloud_authority_story(
            json.dumps(snapshot_payload, separators=(",", ":")).encode(),
            reordered_json,
            reordered_csv,
        )
