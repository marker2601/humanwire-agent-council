import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from humanwire.config import Settings
from humanwire.database import (
    DomainEventRecord,
    EvidenceItemRecord,
    MandateRecord,
    MeetingPackageRecord,
    RuntimeStatusRecord,
    StakeholderAssignmentRecord,
)
from humanwire.demo import create_demo_app
from humanwire.domain import AvailabilityWindow
from humanwire.web import create_app
from humanwire.workflow import json_windows

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
        mandate.model_copy(
            update={"objective": "Contact malicious-contact@example.invalid or @malicious_chat"}
        )
    )
    assignment = repository.list_assignments(mandate.mandate_id)[0]
    repository.save_assignment(
        assignment.model_copy(update={"reason": "Contact owner@example.test"})
    )
    repository.set_runtime_status(
        f"public.person:{assignment.person_id}",
        json.dumps({"name": "Owner @malicious_chat", "role": "owner@example.test"}),
        NOW,
    )

    client = TestClient(demo_app)
    responses = [
        client.get("/api/v1/mandates/HW-2411"),
        client.get("/api/v1/mandates/HW-2411/stakeholders"),
    ]

    assert all(response.status_code == 200 for response in responses)
    serialized = "".join(response.text for response in responses)
    assert "malicious-contact@example.invalid" not in serialized
    assert "owner@example.test" not in serialized
    assert "@malicious_chat" not in serialized
    assert "[REDACTED]" in serialized


def test_every_public_surface_applies_one_recursive_sanitization_boundary(demo_app) -> None:
    repository = demo_app.state.repository
    primary = repository.get_mandate_by_token("HW-2411")
    meeting = repository.get_mandate_by_token("HW-2413")
    assert primary is not None and meeting is not None
    sentinels = {
        "malicious-contact@example.invalid",
        "@malicious_chat",
        "Bearer persisted-secret",
    }
    with repository._session_factory() as session:
        session.execute(
            update(MandateRecord)
            .where(MandateRecord.mandate_id == str(primary.mandate_id))
            .values(
                initiator_id="malicious-contact@example.invalid",
                objective="Contact @malicious_chat using Bearer persisted-secret",
            )
        )
        assignment_id = session.scalar(
            StakeholderAssignmentRecord.__table__.select()
            .with_only_columns(StakeholderAssignmentRecord.assignment_id)
            .where(StakeholderAssignmentRecord.mandate_id == str(primary.mandate_id))
            .limit(1)
        )
        session.execute(
            update(StakeholderAssignmentRecord)
            .where(StakeholderAssignmentRecord.assignment_id == assignment_id)
            .values(
                person_id="malicious-contact@example.invalid",
                department="@malicious_chat",
                reason="Bearer persisted-secret",
            )
        )
        evidence_id = session.scalar(
            EvidenceItemRecord.__table__.select()
            .with_only_columns(EvidenceItemRecord.evidence_id)
            .where(EvidenceItemRecord.mandate_id == str(primary.mandate_id))
            .limit(1)
        )
        session.execute(
            update(EvidenceItemRecord)
            .where(EvidenceItemRecord.evidence_id == evidence_id)
            .values(
                stakeholder_id="malicious-contact@example.invalid",
                statement="@malicious_chat",
                related_decision="Bearer persisted-secret",
                resource="malicious-contact@example.invalid",
            )
        )
        event_id = session.scalar(
            DomainEventRecord.__table__.select()
            .with_only_columns(DomainEventRecord.event_id)
            .where(DomainEventRecord.mandate_id == str(primary.mandate_id))
            .limit(1)
        )
        session.execute(
            update(DomainEventRecord)
            .where(DomainEventRecord.event_id == event_id)
            .values(
                event_type="malicious-contact@example.invalid",
                actor_id="@malicious_chat",
                person_id="malicious-contact@example.invalid",
                department="Bearer persisted-secret",
                previous_state="@malicious_chat",
                new_state="Bearer persisted-secret",
                event_metadata={
                    "references": [{"person_id": "malicious-contact@example.invalid"}],
                    "malicious-contact@example.invalid": "Bearer persisted-secret",
                },
            )
        )
        session.execute(
            update(MeetingPackageRecord)
            .where(MeetingPackageRecord.mandate_id == str(meeting.mandate_id))
            .values(
                purpose="Bearer persisted-secret",
                optional_attendee_ids=["malicious-contact@example.invalid"],
                agreed_facts=["@malicious_chat"],
                open_decisions=["malicious-contact@example.invalid"],
                agenda=["Bearer persisted-secret"],
            )
        )
        session.execute(
            update(RuntimeStatusRecord)
            .where(RuntimeStatusRecord.key == "public.person:maya-chen")
            .values(
                value=json.dumps(
                    {
                        "name": "malicious-contact@example.invalid",
                        "role": "@malicious_chat",
                        "metadata": {"secret": "Bearer persisted-secret"},
                    }
                )
            )
        )
        session.commit()

    client = TestClient(demo_app)
    paths = [
        "/",
        "/mandates/HW-2411",
        "/mandates/HW-2411/reach",
        "/mandates/HW-2411/data",
        "/mandates/HW-2413",
        "/mandates/HW-2413/meeting.ics",
        "/api/v1/mandates",
        "/api/v1/mandates/HW-2411",
        "/api/v1/mandates/HW-2411/stakeholders",
        "/api/v1/mandates/HW-2411/outreach-events",
        "/api/v1/mandates/HW-2411/evidence-summary",
        "/health/live",
        "/health/ready",
        "/mandates/HW-UNKNOWN",
    ]
    responses = [client.get(path) for path in paths]
    serialized = "\n".join(
        response.text + json.dumps(dict(response.headers)) for response in responses
    )

    assert all(sentinel not in serialized for sentinel in sentinels)
    assert "interviewing" in serialized
    assert "downward" in serialized
    assert "mandate.interviewing" in serialized
    event_rows = responses[9].json()
    redacted_event = next(row for row in event_rows if row["event_type"] == "[REDACTED]")
    assert redacted_event["metadata"] == {
        "references": [{"person_id": "[REDACTED]"}]
    }


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


@pytest.mark.parametrize(
    ("configured", "authorization", "expected_status"),
    [
        ("", "Bearer ", 401),
        ("   ", "Bearer    ", 401),
        ("fictional-read-token", None, 401),
        ("fictional-read-token", "Bearer wrong-token", 401),
        ("fictional-read-token", "Bearer fictional-read-token", 200),
    ],
)
def test_production_analytics_fail_closed_for_blank_or_invalid_bearer_tokens(
    demo_app, configured, authorization, expected_status
) -> None:
    repository = demo_app.state.repository
    settings = Settings(
        _env_file=None,
        analytics_read_token=configured,
    )
    client = TestClient(create_app(repository, settings, clock=lambda: NOW))

    response = client.get(
        "/api/v1/mandates",
        headers={"Authorization": authorization} if authorization is not None else {},
    )

    assert response.status_code == expected_status
    assert configured not in response.text if configured else True


def test_demo_analytics_remain_anonymous(web_client) -> None:
    assert web_client.get("/api/v1/mandates").status_code == 200


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


def test_ics_rejects_package_owner_substitution_even_with_matching_attendees_and_availability(
    demo_app,
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    package = repository.get_meeting_package(mandate.mandate_id)
    assert package is not None
    intruder = "intruder-fictional"
    changed = package.model_copy(
        update={
            "decision_owner_id": intruder,
            "required_attendee_ids": sorted([*package.required_attendee_ids, intruder]),
        }
    )
    repository.save_meeting_package(changed)
    window = AvailabilityWindow(
        start=datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        end=datetime(2026, 8, 14, 21, 0, tzinfo=UTC),
    )
    repository.set_runtime_status(
        f"availability:{mandate.mandate_id}:{intruder}",
        json_windows(type("Command", (), {"windows": [window]})()),
        NOW,
    )

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404
    assert intruder not in response.text


def test_ics_rejects_missing_persisted_evidence(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    with repository._session_factory() as session:
        session.execute(
            delete(EvidenceItemRecord).where(
                EvidenceItemRecord.mandate_id == str(mandate.mandate_id)
            )
        )
        session.commit()

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404


def test_ics_rejects_availability_changed_after_package_creation(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    package = repository.get_meeting_package(mandate.mandate_id)
    assert package is not None
    attendee_id = package.required_attendee_ids[0]
    stored = repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:{attendee_id}"
    )
    assert stored is not None
    repository.set_runtime_status(
        f"availability:{mandate.mandate_id}:{attendee_id}",
        stored[0],
        package.created_at + timedelta(seconds=1),
    )

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "updates",
    [
        {"required_attendee_ids": ["maya-chen"]},
        {"purpose": "Altered package purpose"},
        {
            "proposed_start": datetime(2026, 8, 14, 20, 30, tzinfo=UTC),
            "proposed_end": datetime(2026, 8, 14, 21, 0, tzinfo=UTC),
        },
    ],
)
def test_ics_rejects_removed_attendee_or_altered_package_fields(demo_app, updates) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    package = repository.get_meeting_package(mandate.mandate_id)
    assert package is not None
    repository.save_meeting_package(package.model_copy(update=updates))

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404
