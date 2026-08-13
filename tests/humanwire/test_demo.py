import json

from fastapi.testclient import TestClient

from humanwire.demo import create_demo_app
from humanwire.domain import EngagementDecisionKind, EngagementType, StakeholderState


def test_demo_is_deterministic_isolated_and_ready_without_local_configuration(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ambient = {
        "CASPIAN_API_KEY": "ambient-caspian-secret",
        "CASPIAN_BASE_URL": "https://ambient-caspian.example.test",
        "TELEGRAM_BOT_TOKEN": "ambient-telegram-secret",
        "CASPIAN_EMAIL_USERNAME": "ambient-email-user",
        "FEATHERLESS_API_KEY": "ambient-featherless-secret",
        "ANALYTICS_READ_TOKEN": "ambient-read-secret",
        "FEATHERLESS_BASE_URL": "https://ambient-model.example.test/v1",
        "FEATHERLESS_MODEL": "ambient/model",
        "DATABASE_URL": "sqlite:///ambient.db",
        "ORGANIZATION_PATH": "ambient/organization.json",
        "ACKNOWLEDGEMENT_SECONDS": "901",
        "REMINDER_SECONDS": "902",
        "MANDATE_TIMEOUT_SECONDS": "903",
        "ENGAGEMENT_PREVIEW_SECONDS": "905",
        "ENGAGEMENT_REQUIRE_GO": "true",
        "DUE_ACTION_POLL_SECONDS": "904",
        "DASHBOARD_HOST": "198.51.100.44",
        "DASHBOARD_PORT": "9999",
        "PUBLIC_DEMO": "false",
    }
    for key, value in ambient.items():
        monkeypatch.setenv(key, value)
    (tmp_path / ".env").write_text(
        "CASPIAN_API_KEY=real-looking-secret\nDATABASE_URL=sqlite:///local.db\n",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    (data / "organization.json").write_text(
        '{"recipient":"owner@example.test","conversation_id":"fictional-chat"}',
        encoding="utf-8",
    )

    first_app = create_demo_app()
    second_app = create_demo_app()
    first = TestClient(first_app)
    second = TestClient(second_app)

    assert first.get("/health/ready").json() == {"status": "ready", "mode": "demo"}
    assert first.get("/api/v1/mandates").json() == second.get("/api/v1/mandates").json()
    assert first.get("/api/v1/mandates/HW-2411").json() == second.get(
        "/api/v1/mandates/HW-2411"
    ).json()
    assert first_app.state.repository is not second_app.state.repository
    assert first_app.state.settings.caspian_api_key is None
    assert first_app.state.settings.telegram_bot_token is None
    assert first_app.state.settings.featherless_api_key is None
    assert first_app.state.settings.analytics_read_token is None
    assert first_app.state.settings.caspian_base_url == "https://api.trycaspianai.com"
    assert first_app.state.settings.caspian_email_username == "humanwire"
    assert first_app.state.settings.featherless_base_url == "https://api.featherless.ai/v1"
    assert first_app.state.settings.featherless_model == "Qwen/Qwen2.5-7B-Instruct"
    assert first_app.state.settings.database_url == "sqlite://"
    assert str(first_app.state.settings.organization_path) == "demo-organization-not-loaded.json"
    assert first_app.state.settings.acknowledgement_seconds == 300
    assert first_app.state.settings.reminder_seconds == 300
    assert first_app.state.settings.mandate_timeout_seconds == 86_400
    assert first_app.state.settings.engagement_preview_seconds == 15
    assert first_app.state.settings.engagement_require_go is False
    assert first_app.state.settings.due_action_poll_seconds == 5
    assert first_app.state.settings.dashboard_host == "127.0.0.1"
    assert first_app.state.settings.dashboard_port == 8000
    assert first_app.state.settings.public_demo is True
    assert not (tmp_path / "local.db").exists()
    assert not (tmp_path / "ambient.db").exists()


def test_exact_hw_2411_story_and_public_fixture_are_safe() -> None:
    client = TestClient(create_demo_app())
    detail = client.get("/api/v1/mandates/HW-2411").json()
    stakeholders = client.get("/api/v1/mandates/HW-2411/stakeholders").json()
    events = client.get("/api/v1/mandates/HW-2411/outreach-events").json()
    all_responses = [
        client.get("/").text,
        client.get("/mandates/HW-2411").text,
        client.get("/mandates/HW-2411/reach").text,
        client.get("/mandates/HW-2411/data").text,
        json.dumps(client.get("/api/v1/mandates").json()),
        json.dumps(detail),
        json.dumps(stakeholders),
        json.dumps(events),
        json.dumps(client.get("/api/v1/mandates/HW-2411/evidence-summary").json()),
        client.get("/mandates/HW-2413/meeting.ics").text,
    ]
    serialized = "\n".join(all_responses)

    assert detail["initiator"]["name"] == "Arun Patel"
    assert detail["initiator"]["role"] == "Support Manager"
    assert {(row["direction"], row["name"], row["state"]) for row in stakeholders} >= {
        ("downward", "Eli Torres", "complete"),
        ("downward", "Sora Kim", "complete"),
        ("lateral", "Priya Shah", "interviewing"),
        ("upward", "Nora Chen", "complete"),
        ("upward", "Maya Brooks", "awaiting_acknowledgement"),
    }
    assert any(row["interview_status"] == "complete" for row in stakeholders)
    assert any(row["interview_status"] == "in_progress" for row in stakeholders)
    assert len(events) >= 12
    assert [row["created_at"] for row in events] == sorted(row["created_at"] for row in events)
    assert "approval request" in serialized.lower()

    for forbidden in (
        "owner@example.test",
        "@example.com",
        "@example.test",
        "tg-priya",
        "route_id",
        "connection_id",
        "sender_address",
        "recipient",
        "conversation_id",
        "provider_body",
        "Private medical leave details",
        "real-looking-secret",
    ):
        assert forbidden not in serialized


def test_hw_2411_is_the_exact_mixed_engagement_story() -> None:
    app = create_demo_app()
    repository = app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assert mandate is not None
    assignments = {
        item.person_id: item
        for item in repository.list_assignments(mandate.mandate_id)
    }

    assert {
        person_id: (
            assignment.engagement_type,
            assignment.required,
            assignment.state,
            assignment.interview_id is not None,
        )
        for person_id, assignment in assignments.items()
    } == {
        "eli-torres": (
            EngagementType.QUICK_RESPONSE,
            True,
            StakeholderState.COMPLETE,
            True,
        ),
        "sora-kim": (
            EngagementType.QUICK_RESPONSE,
            True,
            StakeholderState.COMPLETE,
            True,
        ),
        "priya-shah": (
            EngagementType.STRUCTURED_INTERVIEW,
            True,
            StakeholderState.INTERVIEWING,
            True,
        ),
        "nora-chen": (
            EngagementType.ACKNOWLEDGE,
            True,
            StakeholderState.COMPLETE,
            False,
        ),
        "maya-brooks": (
            EngagementType.REVIEW_APPROVAL,
            True,
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            False,
        ),
        "inez-ward": (
            EngagementType.INFORM,
            False,
            StakeholderState.COMPLETE,
            False,
        ),
    }
    sessions = repository.list_interviews(mandate.mandate_id)
    assert len(sessions) == 3
    priya = next(
        session
        for session in sessions
        if session.assignment_id == assignments["priya-shah"].assignment_id
    )
    assert priya.questions == [
        "Which staffing rule applies?",
        "What constraint affects coverage?",
        "What safe option can you support?",
    ]
    assert priya.current_question_index == 1
    assert priya.current_channel.value == "telegram"
    assert priya.current_route_id == "demo-route-priya-shah-alternate"
    assert repository.get_engagement_decision(
        assignments["maya-brooks"].assignment_id
    ) is None

    events = repository.list_events(mandate.mandate_id)
    event_types = [item.event_type for item in events]
    assert {
        "engagement.plan_previewed",
        "engagement.plan_released",
        "engagement.inform_delivered",
        "engagement.acknowledged",
        "engagement.quick_response_completed",
        "engagement.structured_interview_progressed",
        "engagement.approval_pending",
    } <= set(event_types)
    assert not any(
        item.person_id == "inez-ward" and "reminder" in item.event_type
        for item in events
    )
    assert "mandate.aligned" not in event_types
    assert "engagement.approved" not in event_types

    mandate_events = [item for item in events if item.person_id is None]
    person_events = [item for item in events if item.person_id is not None]
    assert all(item.assignment_id is None for item in mandate_events)
    assert all(
        item.assignment_id == assignments[item.person_id].assignment_id
        for item in person_events
    )


def test_secondary_demo_person_events_use_the_exact_persisted_assignment() -> None:
    app = create_demo_app()
    repository = app.state.repository

    for token in ("HW-2412", "HW-2413"):
        mandate = repository.get_mandate_by_token(token)
        assert mandate is not None
        assignments = {
            item.person_id: item
            for item in repository.list_assignments(mandate.mandate_id)
        }
        for event in repository.list_events(mandate.mandate_id):
            if event.person_id is None:
                assert event.assignment_id is None
            else:
                assert event.assignment_id == assignments[event.person_id].assignment_id


def test_secondary_demo_cases_have_consistent_typed_authority_facts() -> None:
    app = create_demo_app()
    repository = app.state.repository
    aligned = repository.get_mandate_by_token("HW-2412")
    meeting = repository.get_mandate_by_token("HW-2413")
    assert aligned is not None and meeting is not None

    aligned_assignment = repository.list_assignments(aligned.mandate_id)[0]
    meeting_assignment = repository.list_assignments(meeting.mandate_id)[0]
    aligned_decision = repository.get_engagement_decision(
        aligned_assignment.assignment_id
    )
    meeting_decision = repository.get_engagement_decision(
        meeting_assignment.assignment_id
    )

    assert aligned_assignment.engagement_type is EngagementType.REVIEW_APPROVAL
    assert aligned_assignment.interview_id is None
    assert aligned_decision is not None
    assert aligned_decision.response is EngagementDecisionKind.APPROVE
    assert meeting_assignment.engagement_type is EngagementType.REVIEW_APPROVAL
    assert meeting_assignment.interview_id is None
    assert meeting_decision is not None
    assert meeting_decision.response in {
        EngagementDecisionKind.REJECT,
        EngagementDecisionKind.CHANGE,
    }
