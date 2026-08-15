import csv
import io
from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient

from humanwire.persona_runtime import SyntheticIntent
from humanwire.studio_app import create_coordination_studio_app
from humanwire.studio_models import coordination_target_date
from humanwire.studio_run import StudioRunManager
from humanwire.synthetic import build_coordination_scenario, generate_scenario
from tests.humanwire.studio_fixtures import launch_request

_ACTION_HEADERS = {
    "Content-Type": "application/json",
    "X-HumanWire-Action": "acceptance-token",
}


def _post_run(client: TestClient, **updates: object) -> str:
    response = client.post(
        "/api/runs",
        headers=_ACTION_HEADERS,
        content=launch_request(**updates).model_dump_json(),
    )
    assert response.status_code == 201
    return response.json()["run_alias"]


def _presentation_at(snapshot: dict[str, object], ordinal: int) -> dict[str, object]:
    events = snapshot["events"]
    conversations = snapshot["conversations"]
    data_points = snapshot["data_points"]
    assert isinstance(events, list)
    assert isinstance(conversations, list)
    assert isinstance(data_points, list)
    return {
        "event": events[ordinal - 1],
        "conversations": [
            item for item in conversations if item["event_ordinal"] <= ordinal
        ],
        "data_points": [
            item for item in data_points if item["event_ordinal"] <= ordinal
        ],
    }


def test_launch_request_visibly_resolves_conflict_and_creates_meeting(tmp_path) -> None:
    manager = StudioRunManager(
        workspace_root=tmp_path,
        seed=7,
        step_delay_ms=0,
        max_decision_workers=4,
        alias_factory=iter(["launch-001"]).__next__,
        reference_date_factory=lambda: date(2026, 8, 13),
    )
    app = create_coordination_studio_app(manager, action_token="acceptance-token")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/runs",
            headers={
                "Content-Type": "application/json",
                "X-HumanWire-Action": "acceptance-token",
            },
            content=launch_request().model_dump_json(),
        )
        assert response.status_code == 201
        manager.join("launch-001", timeout=20)
        snapshot = client.get("/api/runs/launch-001").json()

    assert snapshot["run_state"] == "complete"
    assert snapshot["outcome"]["state"] == "meeting_ready"
    assert snapshot["outcome"]["meeting_start"] == "2026-08-14T15:00:00Z"
    assert snapshot["outcome"]["meeting_end"] == "2026-08-14T15:30:00Z"
    assert {"Alex Morgan", "Anika Rao"} <= set(
        snapshot["outcome"]["required_attendees"]
    )
    event_labels = [item["label"] for item in snapshot["data_points"]]
    for required in (
        "Coordination request saved",
        "Outreach sent",
        "Conflict identified",
        "Interview answer recorded",
        "Evidence confirmed",
        "Proposal revised",
        "Approval complete",
        "Availability recorded",
        "Meeting package created",
    ):
        assert required in event_labels
    conflict_ordinal = event_labels.index("Conflict identified") + 1
    rollback_ordinal = next(
        item["event_ordinal"]
        for item in snapshot["conversations"]
        if item["speaker"] == "Anika Rao"
        and "rollback" in item["text"].casefold()
    )
    evidence_ordinal = event_labels.index("Confirmed evidence assembled") + 1
    proposal_ordinal = event_labels.index("Decision proposal prepared") + 1
    revision_ordinal = event_labels.index("Proposal revised") + 1
    approval_ordinal = event_labels.index("Approval complete") + 1
    scheduling_ordinal = event_labels.index("Scheduling started") + 1
    meeting_ordinal = event_labels.index("Meeting package created") + 1
    assert (
        conflict_ordinal
        < rollback_ordinal
        < evidence_ordinal
        < proposal_ordinal
        < revision_ordinal
        < approval_ordinal
        < scheduling_ordinal
        < meeting_ordinal
    )
    assert not any(
        item["label"] in {"Approval recorded", "Availability recorded"}
        and item["event_ordinal"] < proposal_ordinal
        for item in snapshot["data_points"]
    )
    assert not any(
        item["speaker"] == "Sofia Alvarez"
        and item["event_ordinal"] < proposal_ordinal
        and item["text"] in {"Approved.", "Accepted."}
        for item in snapshot["conversations"]
    )
    assert not any(
        item["speaker"] == "Daniel Brooks"
        and item["event_ordinal"] < approval_ordinal
        and "availability" in item["text"].casefold()
        for item in snapshot["conversations"]
    )
    assert any(
        item["speaker"] == "Anika Rao"
        and "rollback" in item["text"].casefold()
        for item in snapshot["conversations"]
    )


def test_conflict_control_false_skips_conflict_and_interview_branch(tmp_path) -> None:
    manager = StudioRunManager(
        workspace_root=tmp_path,
        seed=7,
        step_delay_ms=0,
        max_decision_workers=4,
        alias_factory=iter(["launch-no-conflict"]).__next__,
        reference_date_factory=lambda: date(2026, 8, 13),
    )
    app = create_coordination_studio_app(manager, action_token="acceptance-token")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/runs",
            headers=_ACTION_HEADERS,
            content=launch_request(include_conflict=False).model_dump_json(),
        )
        assert response.status_code == 201
        manager.join("launch-no-conflict", timeout=20)
        snapshot = client.get("/api/runs/launch-no-conflict").json()

    labels = [item["label"] for item in snapshot["data_points"]]
    assert snapshot["run_state"] == "complete"
    assert snapshot["outcome"]["headline"] == "Meeting package ready"
    assert snapshot["lifecycle"]["current"] == "schedule"
    assert snapshot["events"][-1]["stage"] == "schedule"
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
        "rollback" in item["text"].casefold() for item in snapshot["conversations"]
    )
    assert not any(
        item["role"] == "Risk & compliance lead"
        and (item["status"] == "rejected" or item["text"] == "Response could not be accepted.")
        for item in snapshot["conversations"]
    )
    approval_ordinal = next(
        item["event_ordinal"] for item in snapshot["data_points"]
        if item["label"] == "Approval complete"
    )
    meeting_ordinal = next(
        item["event_ordinal"] for item in snapshot["data_points"]
        if item["label"] == "Meeting package created"
    )
    assert approval_ordinal < meeting_ordinal


def test_refresh_manual_replay_downloads_and_second_run_preserve_saved_state(
    tmp_path, monkeypatch
) -> None:
    aliases = iter(["launch-001", "launch-002"])
    manager = StudioRunManager(
        workspace_root=tmp_path,
        seed=7,
        step_delay_ms=0,
        max_decision_workers=4,
        alias_factory=aliases.__next__,
    )
    app = create_coordination_studio_app(manager, action_token="acceptance-token")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first_alias = _post_run(client)
        manager.join(first_alias, timeout=20)
        first_snapshot_response = client.get(f"/api/runs/{first_alias}")
        first_snapshot_bytes = first_snapshot_response.content
        first_snapshot = first_snapshot_response.json()
        first_json = client.get(f"/api/runs/{first_alias}/evidence.json")
        first_csv = client.get(f"/api/runs/{first_alias}/events.csv")
        assert first_json.status_code == first_csv.status_code == 200

        binding = manager.final_binding(first_alias)
        assert binding is not None
        transcript_bytes = binding.transcript_path.read_bytes()
        database_path = binding.transcript_path.parent / "humanwire-synthetic.sqlite3"
        database_bytes = database_path.read_bytes()

        def forbid_replay_generation(*args, **kwargs):
            raise AssertionError("manual replay invoked persona or gateway mutation")

        monkeypatch.setattr("humanwire.synthetic._build_policy", forbid_replay_generation)
        monkeypatch.setattr(
            "humanwire.caspian_gateway.CaspianGateway.dispatch_all",
            forbid_replay_generation,
        )
        for selected_ordinal in range(1, len(first_snapshot["events"]) + 1):
            selected = _presentation_at(first_snapshot, selected_ordinal)
            refreshed_response = client.get(f"/api/runs/{first_alias}")
            assert refreshed_response.content == first_snapshot_bytes
            assert _presentation_at(
                refreshed_response.json(), selected_ordinal
            ) == selected
        for ordinal, event in enumerate(first_snapshot["events"], 1):
            assert event["timeline_ordinal"] == ordinal
            assert first_snapshot["data_points"][ordinal - 1]["event_ordinal"] == ordinal
        assert [
            item["event_ordinal"] for item in first_snapshot["conversations"]
        ] == sorted(item["event_ordinal"] for item in first_snapshot["conversations"])
        assert all(
            1 <= item["event_ordinal"] <= len(first_snapshot["events"])
            for item in first_snapshot["conversations"]
        )
        assert client.get(f"/api/runs/{first_alias}").content == first_snapshot_bytes
        assert client.get(f"/api/runs/{first_alias}/evidence.json").content == first_json.content
        assert client.get(f"/api/runs/{first_alias}/events.csv").content == first_csv.content
        assert binding.transcript_path.read_bytes() == transcript_bytes
        assert database_path.read_bytes() == database_bytes

        json_events = first_json.json()["events"]
        csv_events = list(csv.DictReader(io.StringIO(first_csv.text)))
        assert len(json_events) == len(csv_events) == len(first_snapshot["events"])
        for json_event, csv_event, snapshot_event in zip(
            json_events,
            csv_events,
            first_snapshot["events"],
            strict=True,
        ):
            assert json_event["timeline_ordinal"] == snapshot_event["timeline_ordinal"]
            assert csv_event["timeline_ordinal"] == str(
                snapshot_event["timeline_ordinal"]
            )
            assert json_event["effect"] == csv_event["effect"] == snapshot_event["effect"]
            expected_persisted = snapshot_event["persisted_ordinal"]
            assert csv_event["persisted_ordinal"] == (
                "" if expected_persisted is None else str(expected_persisted)
            )

        monkeypatch.undo()
        second_alias = _post_run(client)
        manager.join(second_alias, timeout=20)
        assert second_alias != first_alias
        second_binding = manager.final_binding(second_alias)
        assert second_binding is not None
        assert second_binding.transcript_path.parent != binding.transcript_path.parent
        assert binding.transcript_path.read_bytes() == transcript_bytes
        assert database_path.read_bytes() == database_bytes
        assert client.get(f"/api/runs/{first_alias}").content == first_snapshot_bytes
        assert client.get(f"/api/runs/{first_alias}/evidence.json").content == first_json.content
        assert client.get(f"/api/runs/{first_alias}/events.csv").content == first_csv.content


def test_one_gateway_handler_processes_email_and_telegram_interview_shapes(
    tmp_path,
) -> None:
    request = launch_request()
    scenario = build_coordination_scenario(
        request,
        seed=7,
        scenario_id="launch-001",
    )
    result = generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        availability_date=coordination_target_date(request),
        max_decision_workers=4,
    )

    assert result.gateway_handler_count == 1
    assert {item.channel.value for item in result.inbound_envelopes} == {
        "email",
        "telegram",
    }
    structured = [
        item for item in result.inbound_envelopes if item.persona_id == "structured"
    ]
    structured_email = next(
        person.email for person in scenario.personas if person.persona_id == "structured"
    )
    first_structured_delivery = next(
        item
        for item in result.captured_deliveries
        if item.destination == structured_email
    )
    assert first_structured_delivery.kind == "initiate"
    assert structured
    assert {item.channel.value for item in structured} == {"email", "telegram"}
    confirmation = next(
        item
        for item in result.transcript.actions
        if item.persona_id == "structured"
        and item.intent is SyntheticIntent.CONFIRM_EVIDENCE
    )
    assert confirmation.channel.value == "telegram"


def test_model_missing_is_pending_without_calls_and_standard_ignores_ambient_settings(
    tmp_path, monkeypatch
) -> None:
    model_calls: list[str] = []
    monkeypatch.setattr(
        "humanwire.studio_run.Settings",
        lambda: SimpleNamespace(featherless_api_key=None),
    )
    monkeypatch.setattr(
        "humanwire.studio_run.PydanticAIPersonaDecisionEngineFactory",
        lambda **kwargs: model_calls.append("factory"),
    )
    manager = StudioRunManager(
        workspace_root=tmp_path / "model",
        alias_factory=iter(["model-001"]).__next__,
        model_factory_builder=None,
    )
    app = create_coordination_studio_app(manager, action_token="acceptance-token")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/runs",
            headers=_ACTION_HEADERS,
            content=launch_request(agent_mode="model_assisted").model_dump_json(),
        )
    assert response.status_code == 409
    assert response.json() == {"error": "model_unavailable"}
    assert model_calls == []
    assert manager.list_runs() == ()

    def forbid_settings():
        raise AssertionError("standard mode read ambient model or provider settings")

    monkeypatch.setattr("humanwire.studio_run.Settings", forbid_settings)
    monkeypatch.setenv("FEATHERLESS_API_KEY", "PRIVATE-AMBIENT-MODEL")
    monkeypatch.setenv("CASPIAN_API_KEY", "PRIVATE-AMBIENT-PROVIDER")
    standard = StudioRunManager(
        workspace_root=tmp_path / "standard",
        seed=7,
        step_delay_ms=0,
        alias_factory=iter(["standard-001"]).__next__,
        model_factory_builder=lambda: model_calls.append("factory"),
    )
    standard.create_run(launch_request(agent_mode="standard"))
    standard.join("standard-001", timeout=20)
    assert standard.snapshot("standard-001").run_state == "complete"
    assert model_calls == []
