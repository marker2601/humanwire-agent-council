from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

import humanwire.synthetic as synthetic_module
from humanwire.decisionos_app import DecisionOSDependencies, create_decisionos_app
from humanwire.decisionos_auth import (
    AuthenticatedSession,
    AuthenticationUnavailable,
    SessionCookieConfig,
    VerifiedAppCheck,
)
from humanwire.decisionos_models import DecisionOSPrincipal
from humanwire.decisionos_store import InMemoryDecisionOSRepository
from humanwire.synthetic import default_synthetic_scenario, generate_scenario

BASE_URL = "https://decisionos.test"
APP_CHECK = "decisionos-app-check"


def _principal(uid: str) -> DecisionOSPrincipal:
    return DecisionOSPrincipal(
        uid=uid,
        email_verified=True,
        provider_ids=("google.com",),
    )


class _Authenticator:
    def __init__(self) -> None:
        self._principals = {
            "id-a": _principal("firebase-founder-a"),
            "id-b": _principal("firebase-founder-b"),
        }
        self._sessions = {
            "session-a": self._principals["id-a"],
            "session-b": self._principals["id-b"],
        }

    def exchange_id_token(self, id_token: str) -> AuthenticatedSession:
        principal = self._principals.get(id_token)
        if principal is None:
            raise AuthenticationUnavailable()
        suffix = id_token.removeprefix("id-")
        return AuthenticatedSession(
            principal=principal,
            cookie=SessionCookieConfig(max_age=timedelta(hours=8)).bind(
                f"session-{suffix}"
            ),
        )

    def verify_session_cookie(
        self,
        cookie: str,
        *,
        check_revoked: bool,
    ) -> DecisionOSPrincipal:
        assert check_revoked is True
        principal = self._sessions.get(cookie)
        if principal is None:
            raise AuthenticationUnavailable()
        return principal

    def revoke_session(self, cookie: str) -> None:
        if cookie not in self._sessions:
            raise AuthenticationUnavailable()


class _AppCheck:
    def verify(self, token: str) -> VerifiedAppCheck:
        if token != APP_CHECK:
            raise AssertionError("unexpected app-check token")
        return VerifiedAppCheck(app_id="humanwire-decisionos")


def _headers(client: TestClient) -> dict[str, str]:
    return {
        "Origin": BASE_URL,
        "X-Firebase-AppCheck": APP_CHECK,
        "X-HumanWire-CSRF": client.cookies["__Host-humanwire-csrf"],
    }


def _login(client: TestClient, identity: str) -> None:
    response = client.post(
        "/api/session/login",
        headers={"Origin": BASE_URL, "X-Firebase-AppCheck": APP_CHECK},
        json={"id_token": identity},
    )
    assert response.status_code == 204


def _create_organization(client: TestClient, name: str) -> str:
    response = client.post(
        "/api/organizations",
        headers=_headers(client),
        json={"name": name},
    )
    assert response.status_code == 201
    return response.json()["organization_id"]


def _create_workspace(client: TestClient, organization_id: str, name: str) -> str:
    response = client.post(
        f"/api/organizations/{organization_id}/workspaces",
        headers=_headers(client),
        json={"name": name, "playbook": "launch_decision"},
    )
    assert response.status_code == 201
    return response.json()["workspace_id"]


def test_two_tenant_journey_isolated_and_public_proof_unchanged(tmp_path) -> None:
    baseline_path = tmp_path / "before" / "transcript.json"
    baseline = generate_scenario(
        default_synthetic_scenario(seed=0),
        baseline_path,
        tmp_path / "before",
    )

    repository = InMemoryDecisionOSRepository()
    app = create_decisionos_app(
        DecisionOSDependencies(
            authenticator=_Authenticator(),
            app_check=_AppCheck(),
            repository=repository,
            allowed_hosts=frozenset({"decisionos.test"}),
            csrf_token_factory=lambda: "csrf-token-1234567890",
        )
    )
    with (
        TestClient(app, base_url=BASE_URL) as client_a,
        TestClient(app, base_url=BASE_URL) as client_b,
    ):
        _login(client_a, "id-a")
        _login(client_b, "id-b")
        org_a = _create_organization(client_a, "Northstar Labs")
        org_b = _create_organization(client_b, "Harbor Ventures")
        workspace_a = _create_workspace(client_a, org_a, "Launch Decision")
        workspace_b = _create_workspace(client_b, org_b, "Fundraising Readiness")

        assert client_a.get(
            f"/api/organizations/{org_b}/workspaces/{workspace_b}"
        ).status_code == 404
        assert client_b.get(
            f"/api/organizations/{org_a}/workspaces/{workspace_a}"
        ).status_code == 404
        assert client_a.post(
            f"/api/organizations/{org_b}/workspaces",
            headers=_headers(client_a),
            json={"name": "Cross-tenant write", "playbook": "launch_decision"},
        ).status_code == 404
        assert client_b.post(
            f"/api/organizations/{org_a}/invitations",
            headers=_headers(client_b),
            json={"role": "owner"},
        ).status_code == 404

        invitation = client_a.post(
            f"/api/organizations/{org_a}/invitations",
            headers=_headers(client_a),
            json={"role": "approver"},
        )
        assert invitation.status_code == 201
        accepted = client_b.post(
            "/api/invitations/accept",
            headers=_headers(client_b),
            json={"invitation_token": invitation.json()["invitation_token"]},
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"organization_id": org_a, "role": "approver"}
        assert client_b.get(
            f"/api/organizations/{org_a}/workspaces/{workspace_a}"
        ).status_code == 200
        assert client_b.post(
            f"/api/organizations/{org_a}/workspaces",
            headers=_headers(client_b),
            json={"name": "Privilege escalation", "playbook": "launch_decision"},
        ).status_code == 403

    after_path = tmp_path / "after" / "transcript.json"
    after = generate_scenario(
        default_synthetic_scenario(seed=0),
        after_path,
        tmp_path / "after",
    )

    assert baseline_path.read_bytes() == after_path.read_bytes()
    assert synthetic_module.semantic_trace_hash(baseline) == (
        synthetic_module.semantic_trace_hash(after)
    )
