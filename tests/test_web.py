from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from secondsignal.config import Settings
from secondsignal.domain import CaseEvent
from secondsignal.web import create_app


@pytest.fixture
def persisted_case(repository, sample_case):
    repository.add_case(sample_case)
    repository.append_event(
        sample_case.case_id,
        CaseEvent(event_type="case.created", created_at=sample_case.created_at),
    )
    repository.append_event(
        sample_case.case_id,
        CaseEvent(
            event_type="verification.requested",
            created_at=sample_case.created_at + timedelta(seconds=2),
            metadata={"channel": "email"},
        ),
    )
    return sample_case


@pytest.fixture
def web_now(persisted_case):
    return persisted_case.created_at + timedelta(minutes=2)


@pytest.fixture
def web_settings() -> Settings:
    return Settings(database_url="sqlite://", expiry_poll_seconds=5)


@pytest.fixture
def web_client(repository, persisted_case, web_now, web_settings):
    repository.set_runtime_status("channel.email", "ready", web_now)
    repository.set_runtime_status("channel.telegram", "ready", web_now)
    repository.set_runtime_status("listener.heartbeat", "alive", web_now)
    app = create_app(repository, web_settings, clock=lambda: web_now)
    return TestClient(app)


def test_dashboard_lists_cases(web_client, persisted_case):
    response = web_client.get("/")

    assert response.status_code == 200
    assert "SecondSignal" in response.text
    assert persisted_case.token in response.text
    assert "Telegram → Email" in response.text
    assert "The channel carrying a request should not verify itself." in response.text


def test_case_page_shows_human_decision_boundary(web_client, persisted_case):
    response = web_client.get(f"/cases/{persisted_case.token}")

    assert response.status_code == 200
    assert "SECOND SIGNAL RECEIPT" in response.text
    assert "AI analyzed risk" in response.text
    assert "a human response determined the verdict" in response.text


def test_case_page_does_not_expose_private_addresses(web_client, persisted_case):
    response = web_client.get(f"/cases/{persisted_case.token}")

    assert persisted_case.reporter_address not in response.text
    assert persisted_case.verification_route.sender_address not in response.text
    assert persisted_case.verification_route.recipient not in response.text


def test_dashboard_has_no_mutating_routes(web_client, persisted_case):
    assert web_client.post(f"/cases/{persisted_case.token}").status_code == 405
    assert web_client.put(f"/cases/{persisted_case.token}").status_code == 405
    assert web_client.patch(f"/cases/{persisted_case.token}").status_code == 405
    assert web_client.delete(f"/cases/{persisted_case.token}").status_code == 405


def test_unknown_case_returns_404(web_client):
    response = web_client.get("/cases/SS-UNKNOWN")

    assert response.status_code == 404


def test_liveness_is_independent_of_channel_state(web_client):
    response = web_client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_succeeds_with_ready_channels_and_fresh_heartbeat(web_client):
    response = web_client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_fails_when_channel_is_not_ready(
    repository,
    persisted_case,
    web_now,
    web_settings,
):
    repository.set_runtime_status("channel.email", "stopped", web_now)
    repository.set_runtime_status("channel.telegram", "ready", web_now)
    repository.set_runtime_status("listener.heartbeat", "alive", web_now)
    client = TestClient(create_app(repository, web_settings, clock=lambda: web_now))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "reason": "channels_not_ready"}


def test_readiness_fails_when_heartbeat_is_stale(
    repository,
    persisted_case,
    web_now,
    web_settings,
):
    repository.set_runtime_status("channel.email", "ready", web_now)
    repository.set_runtime_status("channel.telegram", "ready", web_now)
    repository.set_runtime_status(
        "listener.heartbeat",
        "alive",
        web_now - timedelta(seconds=21),
    )
    client = TestClient(create_app(repository, web_settings, clock=lambda: web_now))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "reason": "stale_heartbeat"}


def test_readiness_fails_safely_when_database_query_fails(web_settings):
    class BrokenRepository:
        def get_runtime_status(self, key):
            raise RuntimeError("database path and credentials must not leak")

    client = TestClient(create_app(BrokenRepository(), web_settings))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "reason": "database_unavailable"}
    assert "credentials" not in response.text
