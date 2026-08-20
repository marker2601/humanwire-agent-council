from __future__ import annotations

import json
from datetime import timedelta

from fastapi.testclient import TestClient

from humanwire.council_gateway import CouncilGatewayResult
from humanwire.council_projection import build_council_projection
from humanwire.council_runtime import CouncilEvidenceSummary, CouncilRunOutput
from humanwire.decisionos_app import DecisionOSDependencies, create_decisionos_app
from humanwire.decisionos_auth import (
    AppCheckUnavailable,
    AuthenticatedSession,
    AuthenticationUnavailable,
    SessionCookieConfig,
    VerifiedAppCheck,
)
from humanwire.decisionos_models import DecisionOSPrincipal
from humanwire.decisionos_store import InMemoryDecisionOSRepository
from humanwire.google_council import (
    CouncilExecutionEvent,
    CouncilExecutionStatus,
)

BASE_URL = "https://decisionos.test"


class FakeAuthenticator:
    principal = DecisionOSPrincipal(
        uid="firebase-owner-01",
        email_verified=True,
        provider_ids=("google.com",),
    )

    def exchange_id_token(self, id_token: str) -> AuthenticatedSession:
        if id_token != "id-owner":
            raise AuthenticationUnavailable()
        return AuthenticatedSession(
            principal=self.principal,
            cookie=SessionCookieConfig(max_age=timedelta(hours=8)).bind(
                "session-owner"
            ),
        )

    def verify_session_cookie(self, cookie: str, *, check_revoked: bool):
        if cookie != "session-owner" or check_revoked is not True:
            raise AuthenticationUnavailable()
        return self.principal

    def revoke_session(self, cookie: str) -> None:
        if cookie != "session-owner":
            raise AuthenticationUnavailable()


class FakeAppCheck:
    def verify(self, token: str) -> VerifiedAppCheck:
        if token != "valid-app-check":
            raise AppCheckUnavailable()
        return VerifiedAppCheck(app_id="humanwire-web")


class FakeCouncilRuntime:
    def __init__(self) -> None:
        self.latest = None
        self.demo_seeded = None
        self.poison_summary = None

    def seed_demo_evidence(self, context, workspace):
        assert context.organization_id == workspace.organization_id
        self.demo_seeded = (context.organization_id, workspace.workspace_id)
        return (
            CouncilEvidenceSummary(
                evidence_id="evidence_demo_market_validation",
                title="Synthetic demo · Market validation",
                provenance="synthetic_demo",
            ),
            CouncilEvidenceSummary(
                evidence_id="evidence_demo_financial_runway",
                title="Synthetic demo · Financial runway",
                provenance="synthetic_demo",
            ),
        )

    def list_evidence(self, context, workspace):
        assert context.organization_id == workspace.organization_id
        if self.poison_summary is not None:
            return (self.poison_summary,)
        return self.seed_demo_evidence(context, workspace)

    def run(
        self,
        context,
        workspace,
        objective,
        *,
        cancellation,
        on_event,
    ):
        assert context.organization_id == workspace.organization_id
        assert cancellation.is_set() is False
        event = CouncilExecutionEvent(
            ordinal=1,
            specialist_id="market_intelligence",
            display_name="Market Intelligence",
            status=CouncilExecutionStatus.COMPLETED,
        )
        on_event(event)
        self.latest = build_council_projection(
            run_id="council_run_01",
            objective=objective,
            events=(event,),
        )
        return CouncilRunOutput(
            run_id="council_run_01",
            projection=self.latest,
            gateway=CouncilGatewayResult(
                accepted=True,
                reason="accepted",
                recommendation_digest="a" * 64,
                requires_human_approval=True,
            ),
        )

    def load_latest(self, context, workspace_id):
        assert context.organization_id
        assert workspace_id
        return self.latest


def _client() -> tuple[TestClient, FakeCouncilRuntime]:
    runtime = FakeCouncilRuntime()
    dependencies = DecisionOSDependencies(
        authenticator=FakeAuthenticator(),
        app_check=FakeAppCheck(),
        repository=InMemoryDecisionOSRepository(),
        allowed_hosts=frozenset({"decisionos.test"}),
        csrf_token_factory=lambda: "csrf-token-1234567890",
        app_check_enforced=False,
        council_features_enabled=True,
        council_runtime=runtime,
    )
    return TestClient(create_decisionos_app(dependencies), base_url=BASE_URL), runtime


def _setup(client: TestClient) -> tuple[str, str, dict[str, str]]:
    base = {"Origin": BASE_URL, "X-Firebase-AppCheck": "valid-app-check"}
    assert client.post(
        "/api/session/login", headers=base, json={"id_token": "id-owner"}
    ).status_code == 204
    headers = {
        **base,
        "X-HumanWire-CSRF": client.cookies["__Host-humanwire-csrf"],
    }
    organization = client.post(
        "/api/organizations", headers=headers, json={"name": "Northstar Labs"}
    ).json()["organization_id"]
    workspace = client.post(
        f"/api/organizations/{organization}/workspaces",
        headers=headers,
        json={"name": "Launch readiness", "playbook": "launch_decision"},
    ).json()["workspace_id"]
    return organization, workspace, headers


def test_council_route_streams_real_activity_and_terminal_projection() -> None:
    client, _runtime = _client()
    organization, workspace, headers = _setup(client)

    response = client.post(
        f"/api/organizations/{organization}/workspaces/{workspace}/council-runs",
        headers=headers,
        json={"objective": "Decide whether the product is ready for a limited launch."},
    )
    rows = [json.loads(line) for line in response.text.splitlines()]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert [row["type"] for row in rows] == ["started", "activity", "complete"]
    assert rows[1]["event"]["specialist_id"] == "market_intelligence"
    assert rows[2]["projection"]["objective"].startswith("Decide whether")
    assert "firebase-owner-01" not in response.text


def test_council_mutation_enforces_app_check_even_in_monitor_mode() -> None:
    client, _runtime = _client()
    organization, workspace, headers = _setup(client)
    headers.pop("X-Firebase-AppCheck")

    response = client.post(
        f"/api/organizations/{organization}/workspaces/{workspace}/council-runs",
        headers=headers,
        json={"objective": "Decide whether the product is ready for a limited launch."},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "app_check_failed"}


def test_latest_projection_is_tenant_bound_and_reloadable() -> None:
    client, _runtime = _client()
    organization, workspace, headers = _setup(client)
    client.post(
        f"/api/organizations/{organization}/workspaces/{workspace}/council-runs",
        headers=headers,
        json={"objective": "Decide whether the product is ready for a limited launch."},
    )

    response = client.get(
        f"/api/organizations/{organization}/workspaces/{workspace}/council-runs/latest"
    )

    assert response.status_code == 200
    assert response.json()["projection"]["run_id"] == "council_run_01"


def test_demo_evidence_route_persists_explicit_synthetic_workspace_records() -> None:
    client, runtime = _client()
    organization, workspace, headers = _setup(client)

    response = client.post(
        f"/api/organizations/{organization}/workspaces/{workspace}/demo-evidence",
        headers=headers,
        json={"confirm": "synthetic_demo"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "provenance": "synthetic_demo",
        "evidence": [
            {
                "evidence_id": "evidence_demo_market_validation",
                "title": "Synthetic demo · Market validation",
                "status": "ready",
                "provenance": "synthetic_demo",
            },
            {
                "evidence_id": "evidence_demo_financial_runway",
                "title": "Synthetic demo · Financial runway",
                "status": "ready",
                "provenance": "synthetic_demo",
            },
        ],
    }
    assert runtime.demo_seeded == (organization, workspace)


def test_evidence_route_lists_safe_summaries_without_model_visible_text() -> None:
    client, _runtime = _client()
    organization, workspace, _headers = _setup(client)

    response = client.get(
        f"/api/organizations/{organization}/workspaces/{workspace}/evidence"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence"][0]["provenance"] == "synthetic_demo"
    assert "sanitized_text" not in response.text


def test_evidence_route_rejects_attacker_dispatched_serialization_hooks() -> None:
    client, runtime = _client()
    organization, workspace, _headers = _setup(client)
    calls = []

    class PoisonSummary:
        def model_dump(self, *, mode: str):
            calls.append(mode)
            return {"private": "PRIVATE-RUNTIME-SENTINEL"}

    runtime.poison_summary = PoisonSummary()

    response = client.get(
        f"/api/organizations/{organization}/workspaces/{workspace}/evidence"
    )

    assert response.status_code == 500
    assert response.json() == {"error": "evidence_unavailable"}
    assert calls == []
    assert "PRIVATE-RUNTIME-SENTINEL" not in response.text
