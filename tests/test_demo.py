from fastapi.testclient import TestClient

from secondsignal.demo import create_demo_app


def test_demo_dashboard_contains_seeded_cases() -> None:
    client = TestClient(create_demo_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "Live demo" in response.text
    assert "sample verification data" in response.text
    assert "SS-7K4P2M" in response.text
    assert "SS-3B9L7Q" in response.text
    assert "SS-1H8R5T" in response.text
    assert "SS-9G2D1V" in response.text
    assert "SS-6M3F8J" in response.text


def test_demo_case_receipt_has_audit_events() -> None:
    client = TestClient(create_demo_app())

    response = client.get("/cases/SS-7K4P2M")

    assert response.status_code == 200
    assert "AI analyzed risk" in response.text
    assert "verdict denied" in response.text


def test_demo_health_endpoints_are_ready() -> None:
    client = TestClient(create_demo_app())

    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}
