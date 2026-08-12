import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from humanwire.config import Settings
from humanwire.demo import create_demo_app
from humanwire.web import create_app

NOW = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)


@pytest.fixture
def demo_app():
    return create_demo_app()


@pytest.fixture
def web_client(demo_app) -> TestClient:
    return TestClient(demo_app)


def test_read_only_route_surface_and_html_placeholders(web_client, demo_app) -> None:
    expected_get_paths = {
        "/",
        "/mandates/{token}",
        "/mandates/{token}/reach",
        "/mandates/{token}/data",
        "/mandates/{token}/meeting.ics",
        "/health/live",
        "/health/ready",
        "/api/v1/mandates",
        "/api/v1/mandates/{token}",
        "/api/v1/mandates/{token}/stakeholders",
        "/api/v1/mandates/{token}/outreach-events",
        "/api/v1/mandates/{token}/evidence-summary",
    }
    actual_get_paths = {
        route.path
        for route in demo_app.routes
        if getattr(route, "methods", set()) == {"GET"}
    }
    mutating_methods = {
        method
        for route in demo_app.routes
        for method in getattr(route, "methods", set())
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    }

    assert expected_get_paths <= actual_get_paths
    assert mutating_methods == set()
    for path in ("/", "/mandates/HW-2411", "/mandates/HW-2411/reach", "/mandates/HW-2411/data"):
        response = web_client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


def test_public_demo_has_no_mutating_routes_or_hidden_mutation(web_client) -> None:
    before = web_client.get("/api/v1/mandates/HW-2411/outreach-events").json()

    response = web_client.post("/api/v1/mandates/HW-2411/cancel", json={"reason": "no"})

    assert response.status_code == 405
    assert web_client.get("/api/v1/mandates/HW-2411/outreach-events").json() == before


def test_mandate_api_contains_live_workflow_state_but_no_routes(web_client) -> None:
    payload = web_client.get("/api/v1/mandates/HW-2411").json()

    assert payload["token"] == "HW-2411"
    assert payload["state"] == "interviewing"
    assert payload["initiator"]["person_id"] == "arun-patel"
    assert payload["initiator"]["name"] == "Arun Patel"
    assert payload["next_action"]["event_type"] == "outreach.alternate_send"
    assert payload["next_action"]["person_id"] == "priya-raman"
    serialized = json.dumps(payload)
    for forbidden in (
        "@example.com",
        "@example.test",
        "tg-priya",
        "route_id",
        "connection_id",
        "sender_id",
        "origin_conversation_id",
        "origin_message_id",
        "idempotency_key",
        "PRIVATE",
        "medical leave",
    ):
        assert forbidden not in serialized


def test_public_projection_redacts_a_destination_even_if_it_reaches_persisted_text(
    demo_app,
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assert mandate is not None
    repository.save_mandate(
        mandate.model_copy(update={"objective": "Contact private@example.com or @private_chat"})
    )
    assignment = repository.list_assignments(mandate.mandate_id)[0]
    repository.save_assignment(
        assignment.model_copy(update={"reason": "Contact owner@example.com"})
    )
    repository.set_runtime_status(
        f"public.person:{assignment.person_id}",
        json.dumps({"name": "Owner @private_chat", "role": "owner@example.com"}),
        NOW,
    )

    client = TestClient(demo_app)
    responses = [
        client.get("/api/v1/mandates/HW-2411"),
        client.get("/api/v1/mandates/HW-2411/stakeholders"),
    ]

    assert all(response.status_code == 200 for response in responses)
    serialized = "".join(response.text for response in responses)
    assert "private@example.com" not in serialized
    assert "owner@example.com" not in serialized
    assert "@private_chat" not in serialized
    assert "[REDACTED]" in serialized


def test_list_filter_stakeholders_events_evidence_and_reach_are_persisted_projections(
    web_client,
) -> None:
    mandates = web_client.get("/api/v1/mandates").json()
    aligned = web_client.get("/api/v1/mandates", params={"state": "aligned"}).json()
    stakeholders = web_client.get("/api/v1/mandates/HW-2411/stakeholders").json()
    events = web_client.get("/api/v1/mandates/HW-2411/outreach-events").json()
    evidence = web_client.get("/api/v1/mandates/HW-2411/evidence-summary").json()
    reach_html = web_client.get("/mandates/HW-2411/reach").text

    assert [item["token"] for item in mandates] == ["HW-2413", "HW-2412", "HW-2411"]
    assert [item["token"] for item in aligned] == ["HW-2412"]
    assert {(item["direction"], item["state"]) for item in stakeholders} >= {
        ("downward", "complete"),
        ("lateral", "alternate_channel"),
        ("upward", "acknowledged"),
        ("upward", "interviewing"),
    }
    assert len([item for item in stakeholders if item["direction"] == "downward" and item["state"] == "complete"]) == 2
    assert [item["created_at"] for item in events] == sorted(item["created_at"] for item in events)
    assert len(events) >= 12
    assert evidence["counts"] == {"shareable": 2, "anonymous": 1, "private_blockers": 1}
    assert evidence["items"][1]["stakeholder_id"] is None
    assert "Private medical leave details" not in json.dumps(evidence)
    assert all(lane in reach_html for lane in ("downward", "lateral", "upward"))


@pytest.mark.parametrize(
    "path",
    [
        "/mandates/HW-UNKNOWN",
        "/mandates/HW-UNKNOWN/reach",
        "/mandates/HW-UNKNOWN/data",
        "/mandates/HW-UNKNOWN/meeting.ics",
        "/api/v1/mandates/HW-UNKNOWN",
        "/api/v1/mandates/HW-UNKNOWN/stakeholders",
        "/api/v1/mandates/HW-UNKNOWN/outreach-events",
        "/api/v1/mandates/HW-UNKNOWN/evidence-summary",
    ],
)
def test_unknown_tokens_return_safe_404(path, web_client) -> None:
    response = web_client.get(path)

    assert response.status_code == 404
    assert "HW-UNKNOWN" not in response.text
    assert "sql" not in response.text.lower()


def test_health_separates_liveness_from_production_readiness(demo_app) -> None:
    repository = demo_app.state.repository
    settings = Settings(
        _env_file=None,
        caspian_api_key="fictional-key",
        telegram_bot_token="fictional-token",
        due_action_poll_seconds=5,
    )
    repository.set_runtime_status("channel.email", "ready", NOW)
    repository.set_runtime_status("channel.telegram", "ready", NOW)
    repository.set_runtime_status("listener.heartbeat", "alive", NOW)
    client = TestClient(create_app(repository, settings, clock=lambda: NOW))

    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").json() == {"status": "ready"}

    repository.set_runtime_status("listener.heartbeat", "alive", NOW - timedelta(seconds=31))
    stale = client.get("/health/ready")
    assert stale.status_code == 503
    assert stale.json() == {"status": "not_ready", "reason": "listener_unavailable"}
    assert client.get("/health/live").status_code == 200


def test_production_readiness_requires_configuration_and_channel_state(demo_app) -> None:
    repository = demo_app.state.repository
    client = TestClient(
        create_app(repository, Settings(_env_file=None), clock=lambda: NOW)
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "reason": "configuration_unavailable"}


def test_production_analytics_require_configured_read_only_bearer_token(demo_app) -> None:
    repository = demo_app.state.repository
    settings = Settings(
        _env_file=None,
        analytics_read_token="fictional-read-token",
    )
    client = TestClient(create_app(repository, settings, clock=lambda: NOW))

    missing = client.get("/api/v1/mandates")
    wrong = client.get(
        "/api/v1/mandates/HW-2411",
        headers={"Authorization": "Bearer wrong-token"},
    )
    allowed = client.get(
        "/api/v1/mandates/HW-2411",
        headers={"Authorization": "Bearer fictional-read-token"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert allowed.status_code == 200
    assert "fictional-read-token" not in missing.text + wrong.text + allowed.text


class FailingRepository:
    def __getattr__(self, name):
        del name
        raise RuntimeError("sqlite:///private/path?token=secret")


def test_database_failure_is_safe_for_readiness_and_api() -> None:
    client = TestClient(
        create_app(
            FailingRepository(),
            Settings(
                _env_file=None,
                caspian_api_key="fictional-key",
                telegram_bot_token="fictional-token",
                analytics_read_token="fictional-read-token",
            ),
            clock=lambda: NOW,
        )
    )

    ready = client.get("/health/ready")
    api = client.get(
        "/api/v1/mandates",
        headers={"Authorization": "Bearer fictional-read-token"},
    )

    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready", "reason": "database_unavailable"}
    assert api.status_code == 503
    assert api.json() == {"detail": "Service unavailable"}
    assert "private/path" not in ready.text + api.text
    assert "secret" not in ready.text + api.text


def test_ics_requires_persisted_verified_meeting_ready_package(web_client) -> None:
    ready = web_client.get("/mandates/HW-2413/meeting.ics")
    not_ready = web_client.get("/mandates/HW-2411/meeting.ics")

    assert ready.status_code == 200
    assert ready.headers["content-type"].startswith("text/calendar")
    assert ready.headers["content-disposition"] == 'attachment; filename="HW-2413-meeting.ics"'
    assert "BEGIN:VCALENDAR" in ready.text
    assert "DTSTART:20260814T200000Z" in ready.text
    assert not_ready.status_code == 404
    assert "verified" not in not_ready.text.lower()


def test_ics_fails_closed_when_persisted_verification_is_missing(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    repository.set_runtime_status(
        f"availability:{mandate.mandate_id}:maya-chen", "malformed", NOW
    )
    client = TestClient(demo_app)

    response = client.get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404
    assert "malformed" not in response.text
