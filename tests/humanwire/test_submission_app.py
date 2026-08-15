from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from humanwire.studio_models import CoordinationRequest
from humanwire.studio_run import ActiveRunError, StudioRunManager
from humanwire.submission_app import create_submission_app

_HOST = "humanwire.example.test"
_TOKEN = "submission-action-token"


def _request() -> dict[str, object]:
    return CoordinationRequest(
        template_id="launch-decision",
        objective="Set up a decision meeting tomorrow to approve the launch plan.",
        requester_name="Alex Morgan",
        requester_role="manager",
        participant_ids=(
            "inform",
            "ack",
            "quick-a",
            "quick-b",
            "structured",
            "approval",
            "availability",
        ),
        target_timing="tomorrow",
        include_conflict=True,
        agent_mode="standard",
    ).model_dump(mode="json")


def _app(tmp_path):
    manager = StudioRunManager(
        workspace_root=tmp_path / "submission-runs",
        seed=7,
        step_delay_ms=5,
    )
    return create_submission_app(
        manager,
        action_token=_TOKEN,
        allowed_hosts=(_HOST,),
        poll_interval_seconds=0.005,
    )


def test_submission_stream_runs_real_coordination_to_safe_final_exports(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app, base_url=f"https://{_HOST}") as client:
        page = client.get("/")
        assert page.status_code == 200
        assert '<meta name="humanwire-delivery-mode" content="stream">' in page.text
        assert 'aria-label="Interactive coordination studio"' in page.text
        assert "Interactive workspace" in page.text
        assert "Standard agents · no external messages" in page.text
        assert "Private workspace" not in page.text
        assert "Live workspace" not in page.text
        assert "Model-assisted" not in page.text
        assert "simulated" not in page.text.casefold()
        assert "fake" not in page.text.casefold()
        assert re.search(r'<button[^>]+data-studio-nav="decision"[^>]+disabled', page.text)

        with client.stream(
            "POST",
            "/api/runs",
            headers={
                "Origin": f"https://{_HOST}",
                "X-HumanWire-Action": _TOKEN,
            },
            json=_request(),
        ) as response:
            assert response.status_code == 201
            assert response.headers["content-type"].startswith("application/x-ndjson")
            assert response.headers["x-humanwire-run-alias"].startswith("coordination-")
            envelopes = [json.loads(line) for line in response.iter_lines() if line]

    snapshots = [item["snapshot"] for item in envelopes if item["type"] == "snapshot"]
    assert len(snapshots) >= 2
    assert snapshots[0]["run_state"] in {"starting", "running"}
    final = snapshots[-1]
    assert final["run_state"] == "complete"
    assert final["downloads_ready"] is True
    assert final["outcome"]["headline"] == "Meeting package ready"
    assert len(final["events"]) >= 40
    assert envelopes[-1]["type"] == "snapshot"
    assert envelopes[-1]["evidence"]["run_alias"] == final["run_alias"]
    assert envelopes[-1]["events_csv"].startswith(
        "timeline_ordinal,persisted_ordinal,effect,"
    )
    serialized = json.dumps(envelopes[-1], sort_keys=True)
    for forbidden in (
        "PRIVATE-PERSONA-SENTINEL",
        "route_id",
        "conversation_id",
        "connection_id",
        "message_id",
        "transcript_path",
    ):
        assert forbidden not in serialized


def test_submission_boundary_rejects_unapproved_host_origin_and_action(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app, base_url=f"https://{_HOST}") as client:
        wrong_host = client.get("/", headers={"Host": "elsewhere.example.test"})
        wrong_origin = client.post(
            "/api/runs",
            headers={
                "Origin": "https://elsewhere.example.test",
                "X-HumanWire-Action": _TOKEN,
            },
            json=_request(),
        )
        wrong_action = client.post(
            "/api/runs",
            headers={
                "Origin": f"https://{_HOST}",
                "X-HumanWire-Action": "wrong-token",
            },
            json=_request(),
        )

    assert (wrong_host.status_code, wrong_host.json()) == (400, {"error": "invalid_host"})
    assert (wrong_origin.status_code, wrong_origin.json()) == (
        403,
        {"error": "origin_forbidden"},
    )
    assert (wrong_action.status_code, wrong_action.json()) == (
        403,
        {"error": "action_forbidden"},
    )
    for response in (wrong_host, wrong_origin, wrong_action):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-frame-options"] == "DENY"


def test_submission_final_exports_are_inline_and_need_no_second_instance_route(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app, base_url=f"https://{_HOST}") as client, client.stream(
        "POST",
        "/api/runs",
        headers={
            "Origin": f"https://{_HOST}",
            "X-HumanWire-Action": _TOKEN,
        },
        json=_request(),
    ) as response:
        assert response.status_code == 201
        envelopes = [json.loads(line) for line in response.iter_lines() if line]

    final_evidence = envelopes[-1]["evidence"]
    run_alias = final_evidence["run_alias"]
    cold_app = _app(tmp_path / "cold-instance")
    with TestClient(cold_app, base_url=f"https://{_HOST}") as cold_client:
        stale_json = cold_client.get(f"/api/runs/{run_alias}/evidence.json")
        stale_csv = cold_client.get(f"/api/runs/{run_alias}/events.csv")
        extra_post = cold_client.post(
            "/api/export/evidence.json",
            headers={
                "Origin": f"https://{_HOST}",
                "X-HumanWire-Action": _TOKEN,
            },
            json={"evidence": final_evidence},
        )

    assert stale_json.status_code == 404
    assert stale_csv.status_code == 404
    assert extra_post.status_code == 405
    assert envelopes[-1]["evidence"]["run_alias"] == run_alias
    assert envelopes[-1]["events_csv"].startswith(
        "timeline_ordinal,persisted_ordinal,effect,"
    )


def test_submission_rejects_model_mode_before_manager_or_settings(
    tmp_path,
    monkeypatch,
) -> None:
    app = _app(tmp_path)

    def unexpected_create(_request):
        pytest.fail("public model mode reached the run manager")

    monkeypatch.setattr(app.state.manager, "create_run", unexpected_create)
    request = _request()
    request["agent_mode"] = "model_assisted"
    with TestClient(app, base_url=f"https://{_HOST}") as client:
        response = client.post(
            "/api/runs",
            headers={
                "Origin": f"https://{_HOST}",
                "X-HumanWire-Action": _TOKEN,
            },
            json=request,
        )

    assert (response.status_code, response.json()) == (
        409,
        {"error": "model_unavailable"},
    )


def test_submission_active_run_conflict_does_not_disclose_private_alias(
    tmp_path,
    monkeypatch,
) -> None:
    app = _app(tmp_path)

    def active_run(_request):
        raise ActiveRunError("PRIVATE-RUN-ALIAS")

    monkeypatch.setattr(app.state.manager, "create_run", active_run)
    with TestClient(app, base_url=f"https://{_HOST}") as client:
        response = client.post(
            "/api/runs",
            headers={
                "Origin": f"https://{_HOST}",
                "X-HumanWire-Action": _TOKEN,
            },
            json=_request(),
        )

    assert (response.status_code, response.json()) == (409, {"error": "active_run"})
    assert "PRIVATE-RUN-ALIAS" not in response.text


@pytest.mark.parametrize(
    "origin",
    (
        "https://second.example.test",
        f"https://{_HOST}:",
        f"https://{_HOST}?",
        f"https://{_HOST}#",
    ),
)
def test_submission_origin_must_exactly_match_request_host(tmp_path, origin) -> None:
    manager = StudioRunManager(
        workspace_root=tmp_path / "submission-runs",
        seed=7,
        step_delay_ms=5,
    )
    app = create_submission_app(
        manager,
        action_token=_TOKEN,
        allowed_hosts=(_HOST, "second.example.test"),
        poll_interval_seconds=0.005,
    )
    with TestClient(app, base_url=f"https://{_HOST}") as client:
        response = client.post(
            "/api/runs",
            headers={
                "Origin": origin,
                "X-HumanWire-Action": _TOKEN,
            },
            json=_request(),
        )

    assert (response.status_code, response.json()) == (
        403,
        {"error": "origin_forbidden"},
    )


def test_submission_stream_joins_worker_before_eof(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path)
    manager = app.state.manager
    original_join = manager.join
    joins: list[tuple[str, float | None]] = []

    def recorded_join(run_alias: str, timeout: float | None = None) -> None:
        joins.append((run_alias, timeout))
        original_join(run_alias, timeout)

    monkeypatch.setattr(manager, "join", recorded_join)
    with TestClient(app, base_url=f"https://{_HOST}") as client, client.stream(
        "POST",
        "/api/runs",
        headers={
            "Origin": f"https://{_HOST}",
            "X-HumanWire-Action": _TOKEN,
        },
        json=_request(),
    ) as response:
        run_alias = response.headers["x-humanwire-run-alias"]
        list(response.iter_lines())

    assert joins == [(run_alias, 10.0)]
    original_join(run_alias, 0)
