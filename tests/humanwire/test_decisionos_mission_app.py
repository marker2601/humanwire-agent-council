from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

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
from humanwire.mission_models import (
    MissionActorType,
    MissionEvent,
    MissionParticipant,
    MissionRequest,
    MissionSnapshot,
    MissionState,
)

BASE_URL = "https://decisionos.test"
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
MISSION = "mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"


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


class FakeMissionService:
    def __init__(self) -> None:
        self.snapshot: MissionSnapshot | None = None

    def create(self, context, workspace, request: MissionRequest) -> MissionSnapshot:
        participant = MissionParticipant(
            participant_id="ai-market-intelligence",
            actor_type=MissionActorType.AI_SPECIALIST,
            display_name="Market Intelligence",
            role="Market Intelligence AI",
            response_required=False,
        )
        self.snapshot = MissionSnapshot(
            schema_version="humanwire.mission/v1",
            mission_id=MISSION,
            version=2,
            organization_id=context.organization_id,
            workspace_id=workspace.workspace_id,
            mode=request.mode,
            state=MissionState.READY,
            objective=request.objective,
            urgency=request.urgency,
            include_conflict=request.include_conflict,
            participants=(participant,),
            events=(
                MissionEvent(
                    ordinal=1,
                    kind="mission.created",
                    stage="request",
                    summary="Mission created.",
                    created_at=NOW,
                ),
            ),
            blocked_reason=None,
            created_at=NOW,
            updated_at=NOW,
        )
        return self.snapshot

    def load(self, context, workspace, mission_id: str) -> MissionSnapshot:
        assert context.organization_id == workspace.organization_id
        if self.snapshot is None or mission_id != self.snapshot.mission_id:
            raise RuntimeError("private load failure")
        return self.snapshot

    def run(
        self,
        context,
        workspace,
        mission_id: str,
        *,
        cancellation,
        on_event,
    ) -> MissionSnapshot:
        current = self.load(context, workspace, mission_id)
        event = MissionEvent(
            ordinal=2,
            kind="decision_brief.ready",
            stage="decision",
            summary="Decision brief ready.",
            created_at=NOW,
        )
        self.snapshot = current.model_copy(
            update={
                "version": current.version + 1,
                "state": MissionState.COMPLETE,
                "events": (*current.events, event),
            }
        )
        on_event(event)
        return self.snapshot


def client_fixture(
    *,
    enabled: bool = True,
) -> tuple[TestClient, FakeMissionService]:
    service = FakeMissionService()
    dependencies = DecisionOSDependencies(
        authenticator=FakeAuthenticator(),
        app_check=FakeAppCheck(),
        repository=InMemoryDecisionOSRepository(),
        allowed_hosts=frozenset({"decisionos.test"}),
        csrf_token_factory=lambda: "csrf-token-1234567890",
        app_check_enforced=False,
        mission_features_enabled=enabled,
        mission_service=service if enabled else None,
    )
    return TestClient(create_decisionos_app(dependencies), base_url=BASE_URL), service


def setup(client: TestClient) -> tuple[str, str, dict[str, str]]:
    base = {"Origin": BASE_URL, "X-Firebase-AppCheck": "valid-app-check"}
    assert client.post(
        "/api/session/login",
        headers=base,
        json={"id_token": "id-owner"},
    ).status_code == 204
    headers = {
        **base,
        "X-HumanWire-CSRF": client.cookies["__Host-humanwire-csrf"],
    }
    organization_id = client.post(
        "/api/organizations",
        headers=headers,
        json={"name": "Northstar Labs"},
    ).json()["organization_id"]
    workspace_id = client.post(
        f"/api/organizations/{organization_id}/workspaces",
        headers=headers,
        json={"name": "Launch decisions", "playbook": "launch_decision"},
    ).json()["workspace_id"]
    return organization_id, workspace_id, headers


def mission_url(organization_id: str, workspace_id: str) -> str:
    return f"/api/organizations/{organization_id}/workspaces/{workspace_id}/missions"


def test_create_demo_mission_returns_canonical_projection() -> None:
    client, _ = client_fixture()
    organization_id, workspace_id, headers = setup(client)

    response = client.post(
        mission_url(organization_id, workspace_id),
        headers=headers,
        json={
            "mode": "demo_run",
            "objective": "Approve the launch decision with current evidence.",
            "urgency": "standard",
            "include_conflict": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["mission"]["mode_label"] == "Demo run"
    assert response.json()["mission"]["state"] == "ready"


def test_connected_route_never_accepts_browser_contact_destination() -> None:
    client, _ = client_fixture()
    organization_id, workspace_id, headers = setup(client)

    response = client.post(
        mission_url(organization_id, workspace_id),
        headers=headers,
        json={
            "mode": "connected_organization",
            "objective": "Approve the launch decision with current evidence.",
            "recipient": "alice@example.invalid",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}


def test_run_streams_started_activity_and_terminal_projection() -> None:
    client, _ = client_fixture()
    organization_id, workspace_id, headers = setup(client)
    created = client.post(
        mission_url(organization_id, workspace_id),
        headers=headers,
        json={
            "mode": "demo_run",
            "objective": "Approve the launch decision with current evidence.",
            "urgency": "standard",
            "include_conflict": True,
        },
    ).json()["mission"]

    response = client.post(
        f"{mission_url(organization_id, workspace_id)}/{created['mission_id']}/run",
        headers=headers,
        json={},
    )
    rows = [json.loads(line) for line in response.text.splitlines()]

    assert response.status_code == 200
    assert [row["type"] for row in rows] == ["started", "activity", "complete"]
    assert rows[1]["event"]["kind"] == "decision_brief.ready"
    assert rows[2]["mission"]["state"] == "complete"


def test_load_is_exact_and_query_strings_are_rejected() -> None:
    client, _ = client_fixture()
    organization_id, workspace_id, headers = setup(client)
    mission = client.post(
        mission_url(organization_id, workspace_id),
        headers=headers,
        json={
            "mode": "demo_run",
            "objective": "Approve the launch decision with current evidence.",
            "urgency": "standard",
            "include_conflict": True,
        },
    ).json()["mission"]
    url = f"{mission_url(organization_id, workspace_id)}/{mission['mission_id']}"

    assert client.get(url).status_code == 200
    rejected = client.get(f"{url}?private=1")
    assert rejected.status_code == 405
    assert rejected.json() == {"error": "method_not_allowed"}


def test_mission_mutation_requires_app_check_and_csrf() -> None:
    client, _ = client_fixture()
    organization_id, workspace_id, headers = setup(client)
    payload = {
        "mode": "demo_run",
        "objective": "Approve the launch decision with current evidence.",
        "urgency": "standard",
        "include_conflict": True,
    }

    no_app_check = {key: value for key, value in headers.items() if key != "X-Firebase-AppCheck"}
    assert client.post(
        mission_url(organization_id, workspace_id),
        headers=no_app_check,
        json=payload,
    ).status_code == 403
    no_csrf = {key: value for key, value in headers.items() if key != "X-HumanWire-CSRF"}
    assert client.post(
        mission_url(organization_id, workspace_id),
        headers=no_csrf,
        json=payload,
    ).status_code == 403


def test_disabled_mode_keeps_mission_routes_unregistered() -> None:
    client, _ = client_fixture(enabled=False)
    organization_id, workspace_id, headers = setup(client)

    response = client.post(
        mission_url(organization_id, workspace_id),
        headers=headers,
        json={
            "mode": "demo_run",
            "objective": "Approve the launch decision with current evidence.",
            "urgency": "standard",
            "include_conflict": True,
        },
    )

    assert response.status_code == 405
    assert response.json() == {"error": "method_not_allowed"}


def test_mission_dependency_pair_must_be_exact() -> None:
    with pytest.raises(ValueError, match="mission dependencies are incomplete"):
        DecisionOSDependencies(
            authenticator=FakeAuthenticator(),
            app_check=FakeAppCheck(),
            repository=InMemoryDecisionOSRepository(),
            allowed_hosts=frozenset({"decisionos.test"}),
            csrf_token_factory=lambda: "csrf-token-1234567890",
            mission_features_enabled=True,
            mission_service=None,
        )
