from __future__ import annotations

import json
from datetime import timedelta

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

BASE_URL = "https://decisionos.test"
ORIGIN = "https://decisionos.test"
MUTATION_HEADERS = {
    "Origin": ORIGIN,
    "X-Firebase-AppCheck": "valid-app-check",
}


def _principal(uid: str) -> DecisionOSPrincipal:
    return DecisionOSPrincipal(
        uid=uid,
        email_verified=True,
        provider_ids=("google.com",),
    )


class FakeAuthenticator:
    def __init__(self) -> None:
        self.owner = _principal("firebase-owner-01")
        self.invitee = _principal("firebase-invitee-01")
        self.sessions = {
            "session-owner": self.owner,
            "session-invitee": self.invitee,
        }
        self.revoked: list[str] = []

    def exchange_id_token(self, id_token: str) -> AuthenticatedSession:
        mapping = {
            "id-owner": (self.owner, "session-owner"),
            "id-invitee": (self.invitee, "session-invitee"),
        }
        bound = mapping.get(id_token)
        if bound is None:
            raise AuthenticationUnavailable()
        principal, session = bound
        return AuthenticatedSession(
            principal=principal,
            cookie=SessionCookieConfig(max_age=timedelta(hours=8)).bind(session),
        )

    def verify_session_cookie(
        self,
        cookie: str,
        *,
        check_revoked: bool,
    ) -> DecisionOSPrincipal:
        assert check_revoked is True
        principal = self.sessions.get(cookie)
        if principal is None or cookie in self.revoked:
            raise AuthenticationUnavailable()
        return principal

    def revoke_session(self, cookie: str) -> None:
        if cookie not in self.sessions:
            raise AuthenticationUnavailable()
        self.revoked.append(cookie)


class FakeAppCheck:
    def verify(self, token: str) -> VerifiedAppCheck:
        if token != "valid-app-check":
            raise AppCheckUnavailable()
        return VerifiedAppCheck(app_id="humanwire-web")


@pytest.fixture
def dependencies() -> DecisionOSDependencies:
    return DecisionOSDependencies(
        authenticator=FakeAuthenticator(),
        app_check=FakeAppCheck(),
        repository=InMemoryDecisionOSRepository(),
        allowed_hosts=frozenset({"decisionos.test"}),
        csrf_token_factory=lambda: "csrf-token-1234567890",
    )


@pytest.fixture
def client(dependencies) -> TestClient:
    return TestClient(create_decisionos_app(dependencies), base_url=BASE_URL)


def _login(client: TestClient, *, identity: str = "id-owner") -> str:
    response = client.post(
        "/api/session/login",
        headers=MUTATION_HEADERS,
        json={"id_token": identity},
    )
    assert response.status_code == 204
    return client.cookies["__Host-humanwire-csrf"]


def _authorized_headers(client: TestClient) -> dict[str, str]:
    return {
        **MUTATION_HEADERS,
        "X-HumanWire-CSRF": client.cookies["__Host-humanwire-csrf"],
    }


def _create_organization(client: TestClient, name: str = "Northstar Labs") -> str:
    response = client.post(
        "/api/organizations",
        headers=_authorized_headers(client),
        json={"name": name},
    )
    assert response.status_code == 201
    return response.json()["organization_id"]


def _raw_asgi_post(
    dependencies: DecisionOSDependencies,
    *,
    path: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    raw_path: bytes | None = None,
) -> tuple[int, dict[str, str]]:
    app = create_decisionos_app(dependencies)
    messages = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode() if raw_path is None else raw_path,
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("decisionos.test", 443),
    }

    import anyio

    anyio.run(app, scope, receive, send)
    start = next(item for item in messages if item["type"] == "http.response.start")
    payload = b"".join(
        item.get("body", b"")
        for item in messages
        if item["type"] == "http.response.body"
    )
    return start["status"], json.loads(payload or b"{}")


def test_protected_app_requires_verified_session(client) -> None:
    response = client.get("/workspace")

    assert response.status_code == 401
    assert response.json() == {"error": "authentication_required"}


def test_signin_and_authenticated_shell_use_local_product_assets(client) -> None:
    sign_in = client.get("/signin")
    _login(client)
    workspace = client.get("/workspace")
    stylesheet = client.get("/decisionos-static/decisionos.css")

    assert sign_in.status_code == 200
    assert "Make the decision." in sign_in.text
    assert "Keep the evidence." in sign_in.text
    assert workspace.status_code == 200
    assert 'data-panel-target="home"' in workspace.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")


def test_public_configuration_rejects_private_or_unknown_fields(dependencies) -> None:
    with pytest.raises(ValueError, match="public configuration"):
        DecisionOSDependencies(
            authenticator=dependencies.authenticator,
            app_check=dependencies.app_check,
            repository=dependencies.repository,
            allowed_hosts=dependencies.allowed_hosts,
            csrf_token_factory=dependencies.csrf_token_factory,
            firebase_public_config={
                "firebase": {
                    "apiKey": "public-browser-key",
                    "projectId": "humanwire",
                    "privateKey": "server-only-value",
                },
                "appCheckSiteKey": "public-site-key",
            },
        )


def test_login_sets_bounded_secure_session_and_csrf_cookies(client) -> None:
    response = client.post(
        "/api/session/login",
        headers=MUTATION_HEADERS,
        json={"id_token": "id-owner"},
    )

    assert response.status_code == 204
    cookies = response.headers.get_list("set-cookie")
    session = next(item for item in cookies if item.startswith("__session="))
    csrf = next(item for item in cookies if item.startswith("__Host-humanwire-csrf="))
    assert "HttpOnly" in session
    assert "Secure" in session
    assert "SameSite=lax" in session
    assert "Max-Age=28800" in session
    assert "HttpOnly" not in csrf
    assert "Secure" in csrf
    assert "id-owner" not in " ".join(cookies)


def test_login_rejects_invalid_token_with_fixed_error(client) -> None:
    response = client.post(
        "/api/session/login",
        headers=MUTATION_HEADERS,
        json={"id_token": "private-provider-detail"},
    )

    assert response.status_code == 401
    assert response.json() == {"error": "authentication_failed"}
    assert "private-provider-detail" not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://attacker.example", "X-Firebase-AppCheck": "valid-app-check"},
        {"Origin": ORIGIN, "X-Firebase-AppCheck": "invalid"},
        {"Origin": ORIGIN},
    ],
)
def test_mutations_require_same_origin_and_app_check(client, headers) -> None:
    response = client.post(
        "/api/session/login",
        headers=headers,
        json={"id_token": "id-owner"},
    )

    assert response.status_code == 403
    assert response.json()["error"] in {"origin_forbidden", "app_check_failed"}


def test_hosting_rewrite_accepts_an_exact_allowlisted_public_origin(dependencies) -> None:
    hosted = DecisionOSDependencies(
        authenticator=dependencies.authenticator,
        app_check=dependencies.app_check,
        repository=dependencies.repository,
        allowed_hosts=frozenset(
            {
                "humanwire-decisionos-wjjhjrgnyq-uc.a.run.app",
                "humanwire-agentic-2026.firebaseapp.com",
            }
        ),
        csrf_token_factory=dependencies.csrf_token_factory,
    )
    body = b'{"id_token":"id-owner"}'

    status, payload = _raw_asgi_post(
        hosted,
        path="/api/session/login",
        headers=[
            (b"host", b"humanwire-decisionos-wjjhjrgnyq-uc.a.run.app"),
            (b"origin", b"https://humanwire-agentic-2026.firebaseapp.com"),
            (b"x-firebase-appcheck", b"valid-app-check"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        body=body,
    )

    assert status == 204
    assert payload == {}


@pytest.mark.parametrize(
    "origin",
    [
        b"https://attacker.example",
        b"http://humanwire-agentic-2026.firebaseapp.com",
        b"https://humanwire-agentic-2026.firebaseapp.com/",
    ],
)
def test_hosting_rewrite_rejects_noncanonical_or_untrusted_public_origin(
    dependencies,
    origin,
) -> None:
    hosted = DecisionOSDependencies(
        authenticator=dependencies.authenticator,
        app_check=dependencies.app_check,
        repository=dependencies.repository,
        allowed_hosts=frozenset(
            {
                "humanwire-decisionos-wjjhjrgnyq-uc.a.run.app",
                "humanwire-agentic-2026.firebaseapp.com",
                "humanwire-agentic-2026.web.app",
            }
        ),
        csrf_token_factory=dependencies.csrf_token_factory,
    )
    body = b'{"id_token":"id-owner"}'

    status, payload = _raw_asgi_post(
        hosted,
        path="/api/session/login",
        headers=[
            (b"host", b"humanwire-decisionos-wjjhjrgnyq-uc.a.run.app"),
            (b"origin", origin),
            (b"x-firebase-appcheck", b"valid-app-check"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        body=body,
    )

    assert status == 403
    assert payload == {"error": "origin_forbidden"}


def test_app_check_monitor_mode_observes_without_blocking_login(dependencies) -> None:
    observations: list[bool] = []
    monitored = DecisionOSDependencies(
        authenticator=dependencies.authenticator,
        app_check=dependencies.app_check,
        repository=dependencies.repository,
        allowed_hosts=dependencies.allowed_hosts,
        csrf_token_factory=dependencies.csrf_token_factory,
        app_check_enforced=False,
        app_check_observer=observations.append,
    )
    client = TestClient(create_decisionos_app(monitored), base_url=BASE_URL)

    response = client.post(
        "/api/session/login",
        headers={"Origin": ORIGIN},
        json={"id_token": "id-owner"},
    )

    assert response.status_code == 204
    assert observations == [False]


@pytest.mark.parametrize("raw_path", [b"/api%2Fsession/login", b"/%61pi/session/login"])
def test_mutations_reject_encoded_raw_path_aliases(dependencies, raw_path) -> None:
    body = b'{"id_token":"id-owner"}'
    status, _payload = _raw_asgi_post(
        dependencies,
        path="/api/session/login",
        raw_path=raw_path,
        headers=[
            (b"host", b"decisionos.test"),
            (b"origin", ORIGIN.encode()),
            (b"x-firebase-appcheck", b"valid-app-check"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        body=body,
    )

    assert status == 405


def test_login_rejects_duplicate_security_headers(client) -> None:
    response = client.post(
        "/api/session/login",
        headers=[
            ("Host", "decisionos.test"),
            ("Origin", ORIGIN),
            ("Origin", ORIGIN),
            ("X-Firebase-AppCheck", "valid-app-check"),
            ("Content-Type", "application/json"),
        ],
        content=b'{"id_token":"id-owner"}',
    )

    assert response.status_code == 403
    assert response.json() == {"error": "origin_forbidden"}


@pytest.mark.parametrize(
    ("extra_header", "status", "error"),
    [
        ((b"x-firebase-appcheck", b"valid-app-check"), 403, "app_check_failed"),
        ((b"x-humanwire-csrf", b"csrf-token-1234567890"), 403, "csrf_failed"),
        (
            (
                b"cookie",
                (
                    b"__session=session-owner; "
                    b"__Host-humanwire-csrf=csrf-token-1234567890"
                ),
            ),
            400,
            "invalid_request",
        ),
    ],
)
def test_duplicate_app_check_csrf_and_cookie_headers_fail_closed(
    dependencies,
    extra_header,
    status,
    error,
) -> None:
    body = b'{"name":"Northstar Labs"}'
    headers = [
        (b"host", b"decisionos.test"),
        (b"origin", ORIGIN.encode()),
        (b"x-firebase-appcheck", b"valid-app-check"),
        (b"x-humanwire-csrf", b"csrf-token-1234567890"),
        (
            b"cookie",
            (
                b"__session=session-owner; "
                b"__Host-humanwire-csrf=csrf-token-1234567890"
            ),
        ),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        extra_header,
    ]

    actual_status, payload = _raw_asgi_post(
        dependencies,
        path="/api/organizations",
        headers=headers,
        body=body,
    )

    assert actual_status == status
    assert payload == {"error": error}


def test_actual_body_length_and_content_encoding_fail_closed(dependencies) -> None:
    body = b'{"id_token":"id-owner"}'
    base_headers = [
        (b"host", b"decisionos.test"),
        (b"origin", ORIGIN.encode()),
        (b"x-firebase-appcheck", b"valid-app-check"),
        (b"content-type", b"application/json"),
    ]
    wrong_length, wrong_payload = _raw_asgi_post(
        dependencies,
        path="/api/session/login",
        headers=[*base_headers, (b"content-length", str(len(body) + 1).encode())],
        body=body,
    )
    encoded, encoded_payload = _raw_asgi_post(
        dependencies,
        path="/api/session/login",
        headers=[
            *base_headers,
            (b"content-length", str(len(body)).encode()),
            (b"content-encoding", b"gzip"),
        ],
        body=body,
    )

    assert (wrong_length, wrong_payload) == (400, {"error": "invalid_request"})
    assert (encoded, encoded_payload) == (400, {"error": "invalid_request"})


def test_create_and_list_organizations_requires_csrf(client) -> None:
    _login(client)

    denied = client.post(
        "/api/organizations",
        headers=MUTATION_HEADERS,
        json={"name": "Northstar Labs"},
    )
    assert denied.status_code == 403
    assert denied.json() == {"error": "csrf_failed"}

    organization_id = _create_organization(client)
    listed = client.get("/api/organizations")
    assert listed.status_code == 200
    assert listed.json() == {
        "organizations": [
            {
                "organization_id": organization_id,
                "name": "Northstar Labs",
                "role": "owner",
            }
        ]
    }


def test_invitation_acceptance_and_viewer_role_are_enforced(dependencies) -> None:
    owner_client = TestClient(create_decisionos_app(dependencies), base_url=BASE_URL)
    _login(owner_client)
    organization_id = _create_organization(owner_client)
    created = owner_client.post(
        f"/api/organizations/{organization_id}/invitations",
        headers=_authorized_headers(owner_client),
        json={"role": "viewer"},
    )
    assert created.status_code == 201
    token = created.json()["invitation_token"]

    invitee_client = TestClient(create_decisionos_app(dependencies), base_url=BASE_URL)
    _login(invitee_client, identity="id-invitee")
    accepted = invitee_client.post(
        "/api/invitations/accept",
        headers=_authorized_headers(invitee_client),
        json={"invitation_token": token},
    )
    assert accepted.status_code == 200
    assert accepted.json() == {
        "organization_id": organization_id,
        "role": "viewer",
    }

    denied = invitee_client.post(
        f"/api/organizations/{organization_id}/workspaces",
        headers=_authorized_headers(invitee_client),
        json={"name": "Forbidden", "playbook": "launch_decision"},
    )
    assert denied.status_code == 403
    assert denied.json() == {"error": "authorization_denied"}


def test_cross_tenant_workspace_identifier_is_not_authority(dependencies) -> None:
    owner_client = TestClient(create_decisionos_app(dependencies), base_url=BASE_URL)
    _login(owner_client)
    organization_a = _create_organization(owner_client, "Organization A")
    created_workspace = owner_client.post(
        f"/api/organizations/{organization_a}/workspaces",
        headers=_authorized_headers(owner_client),
        json={"name": "Launch", "playbook": "launch_decision"},
    )
    workspace_id = created_workspace.json()["workspace_id"]

    other_client = TestClient(create_decisionos_app(dependencies), base_url=BASE_URL)
    _login(other_client, identity="id-invitee")
    organization_b = _create_organization(other_client, "Organization B")
    response = other_client.get(
        f"/api/organizations/{organization_b}/workspaces/{workspace_id}"
    )

    assert response.status_code == 404
    assert response.json() == {"error": "workspace_not_found"}


def test_stale_or_revoked_session_is_rejected(client, dependencies) -> None:
    _login(client)
    dependencies.authenticator.revoked.append("session-owner")

    response = client.get("/api/organizations")

    assert response.status_code == 401
    assert response.json() == {"error": "authentication_required"}


def test_logout_revokes_and_clears_both_cookies(client, dependencies) -> None:
    _login(client)

    response = client.post(
        "/api/session/logout",
        headers=_authorized_headers(client),
        json={"confirm": True},
    )

    assert response.status_code == 204
    assert dependencies.authenticator.revoked == ["session-owner"]
    cookies = response.headers.get_list("set-cookie")
    assert any(item.startswith("__session=") and "Max-Age=0" in item for item in cookies)
    assert any(item.startswith("__Host-humanwire-csrf=") and "Max-Age=0" in item for item in cookies)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/api/organizations"),
        ("POST", "/api/organizations?shadow=1"),
        ("POST", "/api/organizations/"),
    ],
)
def test_exact_mutation_routes_fail_closed(client, method, path) -> None:
    response = client.request(
        method,
        path,
        headers=MUTATION_HEADERS,
        json={"name": "Northstar Labs"},
    )

    assert response.status_code == 405
    assert response.json() == {"error": "method_not_allowed"}


def test_oversized_and_duplicate_json_fail_before_authentication(client) -> None:
    oversized = b'{"id_token":"' + b"a" * 9000 + b'"}'
    too_large = client.post(
        "/api/session/login",
        headers={**MUTATION_HEADERS, "Content-Type": "application/json"},
        content=oversized,
    )
    duplicate = client.post(
        "/api/session/login",
        headers={**MUTATION_HEADERS, "Content-Type": "application/json"},
        content=b'{"id_token":"id-owner","id_token":"id-invitee"}',
    )

    assert too_large.status_code == 413
    assert too_large.json() == {"error": "request_too_large"}
    assert duplicate.status_code == 400
    assert duplicate.json() == {"error": "invalid_request"}


def test_route_exception_is_redacted_and_security_headers_remain(dependencies) -> None:
    class ExplodingRepository(InMemoryDecisionOSRepository):
        def list_organizations(self, principal):
            raise RuntimeError("PRIVATE_DATABASE_PATH")

    guarded = DecisionOSDependencies(
        authenticator=dependencies.authenticator,
        app_check=dependencies.app_check,
        repository=ExplodingRepository(),
        allowed_hosts=dependencies.allowed_hosts,
        csrf_token_factory=dependencies.csrf_token_factory,
    )
    client = TestClient(create_decisionos_app(guarded), base_url=BASE_URL, raise_server_exceptions=False)
    _login(client)

    response = client.get("/api/organizations")

    assert response.status_code == 500
    assert response.json() == {"error": "request_failed"}
    assert "PRIVATE_DATABASE_PATH" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_sign_in_csp_allows_only_required_firebase_and_recaptcha_origins(client) -> None:
    response = client.get("/signin")

    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert (
        "script-src 'self' https://apis.google.com https://www.google.com/recaptcha/ "
        "https://www.gstatic.com/recaptcha/"
    ) in csp
    assert (
        "connect-src 'self' https://identitytoolkit.googleapis.com "
        "https://securetoken.googleapis.com https://content-firebaseappcheck.googleapis.com "
        "https://firebaseappcheck.googleapis.com "
        "https://www.google.com/recaptcha/"
    ) in csp
    assert (
        "frame-src 'self' https://www.google.com/recaptcha/ "
        "https://recaptcha.google.com/recaptcha/"
    ) in csp
    assert "https:" not in csp.replace("https://", "")
    assert "*" not in csp
