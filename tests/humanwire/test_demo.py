import json

from fastapi.testclient import TestClient

from humanwire.demo import create_demo_app


def test_demo_is_deterministic_isolated_and_ready_without_local_configuration(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CASPIAN_API_KEY", "ambient-real-looking-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ambient-telegram-secret")
    (tmp_path / ".env").write_text(
        "CASPIAN_API_KEY=real-looking-secret\nDATABASE_URL=sqlite:///local.db\n",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    (data / "organization.json").write_text(
        '{"recipient":"owner@real-company.com","conversation_id":"real-chat"}',
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
    assert not (tmp_path / "local.db").exists()


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
        "owner@real-company.com",
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
