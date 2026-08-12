import json

from fastapi.testclient import TestClient

from humanwire.demo import create_demo_app


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
        ("lateral", "Priya Raman", "alternate_channel"),
        ("upward", "Nora Okafor", "acknowledged"),
        ("upward", "Maya Chen", "interviewing"),
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
