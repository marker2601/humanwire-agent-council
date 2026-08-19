from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
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
from humanwire.decisionos_models import DecisionOSPrincipal, DecisionOSRole
from humanwire.decisionos_store import InMemoryDecisionOSRepository, OrganizationUnavailable
from humanwire.organization_import import OrganizationImportService
from humanwire.organization_models import (
    AuthorityAssignment,
    AuthorityFunction,
    ImportReconciliation,
    OrganizationEdge,
    OrganizationEdgeKind,
    OrganizationGraph,
    OrganizationSubject,
    OrganizationSubjectKind,
    OrganizationUnit,
    SubjectLifecycle,
)
from humanwire.organization_projection import (
    OrganizationProjectionUnavailable,
    build_organization_projection,
)
from humanwire.organization_sources import parse_organization_source
from humanwire.organization_store import InMemoryOrganizationGraphRepository

BASE_URL = "https://decisionos.test"
ORIGIN = "https://decisionos.test"
NOW = datetime(2026, 8, 18, 18, 0, tzinfo=UTC)
ORG_A = "org_01ARZ3NDEKTSV4RRFFQ69G5FAV"
ORG_B = "org_01ARZ3NDEKTSV4RRFFQ69G5FAW"
SUBJECT = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAV"
UNIT = "unit_01ARZ3NDEKTSV4RRFFQ69G5FAV"
EDGE = "edge_01ARZ3NDEKTSV4RRFFQ69G5FAV"
ASSIGNMENT = "auth_01ARZ3NDEKTSV4RRFFQ69G5FAV"
IMPORT = "imp_01ARZ3NDEKTSV4RRFFQ69G5FAV"
COMPLETE_CSV = b"""source_identity,display_name,kind,title,unit_name,unit_leader
directory/ada,Ada Lovelace,human,Chief Executive,Executive,true
"""
PRIVATE_CSV = b"source_identity,display_name,email\nprivate/alice,Alice,alice@example.invalid\n"
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


class SequenceIdentifiers:
    def __init__(self) -> None:
        self.organizations = iter((ORG_A, ORG_B))
        self.invitation_sequence = 0

    def organization_id(self) -> str:
        return next(self.organizations)

    def workspace_id(self) -> str:
        return "wrk_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def invitation_id(self) -> str:
        self.invitation_sequence += 1
        return f"inv_{self.invitation_sequence:026d}"

    def invitation_token(self) -> str:
        return f"opaque-invitation-token-{self.invitation_sequence:04d}"


class FakeAuthenticator:
    def __init__(self) -> None:
        self.sessions = {
            "session-owner": _principal("firebase-owner-01"),
            "session-admin": _principal("firebase-admin-01"),
            "session-viewer": _principal("firebase-viewer-01"),
            "session-outsider": _principal("firebase-outsider-01"),
        }

    def exchange_id_token(self, id_token: str) -> AuthenticatedSession:
        session = f"session-{id_token}"
        principal = self.sessions.get(session)
        if principal is None:
            raise AuthenticationUnavailable()
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
        if principal is None:
            raise AuthenticationUnavailable()
        return principal

    def revoke_session(self, cookie: str) -> None:
        if cookie not in self.sessions:
            raise AuthenticationUnavailable()


class FakeAppCheck:
    def verify(self, token: str) -> VerifiedAppCheck:
        if token != "valid-app-check":
            raise AppCheckUnavailable()
        return VerifiedAppCheck(app_id="humanwire-web")


@dataclass
class OrganizationAppBundle:
    dependencies: DecisionOSDependencies
    decisionos: InMemoryDecisionOSRepository
    graph_repository: InMemoryOrganizationGraphRepository
    import_service: OrganizationImportService
    owner_context: Any


def _add_member(
    repository: InMemoryDecisionOSRepository,
    owner_context,
    *,
    uid: str,
    role: DecisionOSRole,
) -> None:
    invitation = repository.create_invitation(
        owner_context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    repository.accept_invitation(
        _principal(uid),
        invitation.token.get_secret_value(),
    )
    if role is not DecisionOSRole.VIEWER:
        repository.update_member_role(owner_context, uid, role)


@pytest.fixture
def bundle() -> OrganizationAppBundle:
    decisionos = InMemoryDecisionOSRepository(
        identifiers=SequenceIdentifiers(),
        clock=lambda: NOW,
    )
    owner = _principal("firebase-owner-01")
    organization = decisionos.create_organization(owner, "Northstar Labs")
    owner_context = decisionos.load_context(owner, organization.organization_id)
    _add_member(
        decisionos,
        owner_context,
        uid="firebase-admin-01",
        role=DecisionOSRole.ADMIN,
    )
    _add_member(
        decisionos,
        owner_context,
        uid="firebase-viewer-01",
        role=DecisionOSRole.VIEWER,
    )
    decisionos.create_organization(_principal("firebase-outsider-01"), "Other Tenant")
    graph_repository = InMemoryOrganizationGraphRepository(
        decisionos=decisionos,
        clock=lambda: NOW,
    )
    import_service = OrganizationImportService(
        repository=graph_repository,
        clock=lambda: NOW,
    )
    dependencies = DecisionOSDependencies(
        authenticator=FakeAuthenticator(),
        app_check=FakeAppCheck(),
        repository=decisionos,
        allowed_hosts=frozenset({"decisionos.test"}),
        csrf_token_factory=lambda: "unused-csrf-factory",
        organization_features_enabled=True,
        organization_source_parser=parse_organization_source,
        organization_import_service=import_service,
        organization_graph_repository=graph_repository,
        organization_projection_builder=build_organization_projection,
    )
    return OrganizationAppBundle(
        dependencies=dependencies,
        decisionos=decisionos,
        graph_repository=graph_repository,
        import_service=import_service,
        owner_context=owner_context,
    )


def _client(bundle: OrganizationAppBundle, identity: str | None = None) -> TestClient:
    client = TestClient(
        create_decisionos_app(bundle.dependencies),
        base_url=BASE_URL,
        raise_server_exceptions=False,
    )
    if identity is not None:
        response = client.post(
            "/api/session/login",
            headers=MUTATION_HEADERS,
            json={"id_token": identity},
        )
        assert response.status_code == 204
    return client


def _authorized_headers(client: TestClient) -> dict[str, str]:
    return {
        **MUTATION_HEADERS,
        "X-HumanWire-CSRF": client.cookies["__Host-humanwire-csrf"],
    }


def _replacement_dependencies(
    bundle: OrganizationAppBundle,
    **updates: object,
) -> DecisionOSDependencies:
    values = {
        "authenticator": bundle.dependencies.authenticator,
        "app_check": bundle.dependencies.app_check,
        "repository": bundle.dependencies.repository,
        "allowed_hosts": bundle.dependencies.allowed_hosts,
        "csrf_token_factory": bundle.dependencies.csrf_token_factory,
        "firebase_public_config": dict(bundle.dependencies.firebase_public_config),
        "app_check_enforced": bundle.dependencies.app_check_enforced,
        "app_check_observer": bundle.dependencies.app_check_observer,
        "organization_features_enabled": True,
        "organization_source_parser": bundle.dependencies.organization_source_parser,
        "organization_import_service": bundle.dependencies.organization_import_service,
        "organization_graph_repository": bundle.dependencies.organization_graph_repository,
        "organization_projection_builder": bundle.dependencies.organization_projection_builder,
    }
    values.update(updates)
    return DecisionOSDependencies(**values)


def _upload(
    client: TestClient,
    *,
    content: bytes = COMPLETE_CSV,
    filename: str = "team.csv",
    content_type: str = "text/csv",
):
    return client.post(
        f"/api/organizations/{ORG_A}/imports",
        headers=_authorized_headers(client),
        files={"source": (filename, content, content_type)},
    )


def _raw_request(
    dependencies: DecisionOSDependencies,
    *,
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes = b"",
    raw_path: bytes | None = None,
    query_string: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    app = create_decisionos_app(dependencies)
    messages: list[dict[str, object]] = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode() if raw_path is None else raw_path,
        "query_string": query_string,
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("decisionos.test", 443),
    }
    anyio.run(app, scope, receive, send)
    start = next(item for item in messages if item["type"] == "http.response.start")
    payload = b"".join(
        item.get("body", b"")
        for item in messages
        if item["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin-1").casefold(): value.decode("latin-1")
        for key, value in start["headers"]
    }
    return int(start["status"]), response_headers, payload


def _multipart_body(
    *,
    parts: tuple[tuple[tuple[bytes, ...], bytes], ...],
    boundary: bytes = b"humanwire-boundary",
) -> bytes:
    body = bytearray()
    for header_lines, content in parts:
        body.extend(b"--" + boundary + b"\r\n")
        for header in header_lines:
            body.extend(header + b"\r\n")
        body.extend(b"\r\n" + content + b"\r\n")
    body.extend(b"--" + boundary + b"--\r\n")
    return bytes(body)


def _raw_upload_headers(body: bytes) -> list[tuple[bytes, bytes]]:
    session = "session-owner"
    csrf = hashlib.sha256(session.encode()).hexdigest().encode()
    return [
        (b"host", b"decisionos.test"),
        (b"origin", ORIGIN.encode()),
        (b"x-firebase-appcheck", b"valid-app-check"),
        (b"x-humanwire-csrf", csrf),
        (
            b"cookie",
            b"__session=session-owner; __Host-humanwire-csrf=" + csrf,
        ),
        (b"content-type", b"multipart/form-data; boundary=humanwire-boundary"),
        (b"content-length", str(len(body)).encode()),
    ]


def _valid_source_part(content: bytes = COMPLETE_CSV) -> tuple[tuple[bytes, ...], bytes]:
    return (
        (
            b'Content-Disposition: form-data; name="source"; filename="team.csv"',
            b"Content-Type: text/csv",
        ),
        content,
    )


def _json(response) -> object:
    return json.loads(response.content or b"{}")


def test_upload_returns_reviewable_draft_without_inviting(bundle, monkeypatch) -> None:
    def forbidden_invitation(*_args, **_kwargs):
        raise AssertionError("import reached invitation transport")

    monkeypatch.setattr(bundle.decisionos, "create_invitation", forbidden_invitation)
    client = _client(bundle, "owner")

    response = _upload(client)

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "draft"
    assert payload["organization_id"] == ORG_A
    assert payload["source_kind"] == "csv"
    assert payload["source_count"] == 1
    assert len(payload["source_record_ids"]) == 1
    assert "reviewed_digest" in payload
    assert "invitation" not in response.text.casefold()
    assert "source_identity" not in response.text
    assert "raw" not in response.text.casefold()


def test_exact_import_detail_correction_and_commit_bind_review_state(
    bundle,
    monkeypatch,
) -> None:
    client = _client(bundle, "owner")
    uploaded = _upload(
        client,
        content=b"source_identity,title\nrow/one,Founder\n",
    )
    initial = uploaded.json()
    import_id = initial["import_id"]
    record_id = initial["source_record_ids"][0]

    stale = client.post(
        f"/api/organizations/{ORG_A}/imports/{import_id}/corrections",
        headers=_authorized_headers(client),
        json={
            "reviewed_digest": "0" * 64,
            "kind": "correct_record",
            "source_record_ids": [record_id],
            "replacement_fields": [
                ["display_name", "Ada Lovelace"],
                ["kind", "human"],
                ["unit_leader", "true"],
                ["unit_name", "Executive"],
            ],
        },
    )
    assert stale.status_code == 409
    assert stale.json() == {"error": "import_stale"}

    corrected_response = client.post(
        f"/api/organizations/{ORG_A}/imports/{import_id}/corrections",
        headers=_authorized_headers(client),
        json={
            "reviewed_digest": initial["reviewed_digest"],
            "kind": "correct_record",
            "source_record_ids": [record_id],
            "replacement_fields": [
                ["display_name", "Ada Lovelace"],
                ["kind", "human"],
                ["unit_leader", "true"],
                ["unit_name", "Executive"],
            ],
        },
    )
    assert corrected_response.status_code == 201
    corrected = corrected_response.json()
    assert corrected["status"] == "draft"
    assert corrected["import_id"] != import_id
    assert corrected["supersedes_import_id"] == import_id

    loaded = client.get(
        f"/api/organizations/{ORG_A}/imports/{corrected['import_id']}"
    )
    assert loaded.status_code == 200
    assert loaded.json() == corrected

    monkeypatch.setattr(
        bundle.decisionos,
        "create_invitation",
        lambda *_args, **_kwargs: pytest.fail("commit reached invitation transport"),
    )
    committed = client.post(
        f"/api/organizations/{ORG_A}/imports/{corrected['import_id']}/commit",
        headers=_authorized_headers(client),
        json={
            "reviewed_digest": corrected["reviewed_digest"],
            "acknowledged_codes": corrected["acknowledged_codes"],
        },
    )
    assert committed.status_code == 200
    assert committed.json()["status"] == "committed"
    assert "committed_by_uid" not in committed.text
    assert "invitation" not in committed.text.casefold()
    graph = bundle.graph_repository.load_graph(bundle.owner_context)
    assert all(item.lifecycle is SubjectLifecycle.DIRECTORY_ONLY for item in graph.subjects)
    with pytest.raises(OrganizationUnavailable):
        bundle.decisionos.load_context(_principal("firebase-imported-one"), ORG_A)


def test_commit_rejects_stale_digest_with_fixed_review_error(bundle) -> None:
    client = _client(bundle, "owner")
    draft = _upload(client).json()

    response = client.post(
        f"/api/organizations/{ORG_A}/imports/{draft['import_id']}/commit",
        headers=_authorized_headers(client),
        json={"reviewed_digest": "0" * 64, "acknowledged_codes": []},
    )

    assert response.status_code == 409
    assert response.json() == {"error": "import_review_required"}


def test_route_table_exposes_only_the_six_exact_organization_paths(bundle) -> None:
    app = create_decisionos_app(bundle.dependencies)
    routes = []
    for route in app.routes:
        included = getattr(route, "original_router", None)
        routes.extend(included.routes if included is not None else (route,))
    organization_paths = {
        route.path: frozenset(route.methods or ())
        for route in routes
        if getattr(route, "path", "").startswith("/api/organizations/{organization_id}")
        and (
            "/imports" in route.path
            or route.path.endswith("/organization-graph")
            or route.path.endswith("/authority-map")
        )
    }

    assert organization_paths == {
        "/api/organizations/{organization_id}/imports": frozenset({"POST"}),
        "/api/organizations/{organization_id}/imports/{import_id}": frozenset(
            {"GET", "HEAD"}
        ),
        "/api/organizations/{organization_id}/imports/{import_id}/corrections": frozenset(
            {"POST"}
        ),
        "/api/organizations/{organization_id}/imports/{import_id}/commit": frozenset(
            {"POST"}
        ),
        "/api/organizations/{organization_id}/organization-graph": frozenset(
            {"GET", "HEAD"}
        ),
        "/api/organizations/{organization_id}/authority-map": frozenset(
            {"GET", "HEAD"}
        ),
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/api/organizations/{ORG_A}/imports"),
        ("HEAD", f"/api/organizations/{ORG_A}/imports"),
        ("POST", f"/api/organizations/{ORG_A}/organization-graph"),
        ("GET", f"/api/organizations/{ORG_A}/imports/{IMPORT}/corrections"),
        ("GET", f"/api/organizations/{ORG_A}/imports/{IMPORT}/commit"),
    ],
)
def test_organization_routes_reject_wrong_methods(bundle, method, path) -> None:
    client = _client(bundle, "owner")
    response = client.request(
        method,
        path,
        headers=_authorized_headers(client),
        json={"reviewed_digest": "0" * 64},
    )

    assert response.status_code == 405
    if method != "HEAD":
        assert response.json() == {"error": "method_not_allowed"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", f"/api/organizations/{ORG_A}/imports?shadow=1"),
        ("POST", f"/api/organizations/{ORG_A}/imports/"),
        ("GET", f"/api/organizations/{ORG_A}/organization-graph?shadow=1"),
        ("GET", f"/api/organizations/{ORG_A}/authority-map/"),
    ],
)
def test_organization_routes_reject_query_and_path_aliases(bundle, method, path) -> None:
    client = _client(bundle, "owner")
    response = client.request(
        method,
        path,
        headers=_authorized_headers(client),
        files={"source": ("team.csv", COMPLETE_CSV, "text/csv")}
        if method == "POST"
        else None,
        follow_redirects=False,
    )

    assert response.status_code == 405
    assert response.json() == {"error": "method_not_allowed"}


def test_encoded_upload_raw_path_alias_is_rejected_before_parsing(bundle) -> None:
    body = _multipart_body(parts=(_valid_source_part(),))
    status, _headers, payload = _raw_request(
        bundle.dependencies,
        method="POST",
        path=f"/api/organizations/{ORG_A}/imports",
        raw_path=f"/api/organizations/{ORG_A}/%69mports".encode(),
        headers=_raw_upload_headers(body),
        body=body,
    )

    assert status == 405
    assert json.loads(payload) == {"error": "method_not_allowed"}


def test_upload_requires_exact_content_length_and_rejects_encoded_bodies(bundle) -> None:
    body = _multipart_body(parts=(_valid_source_part(),))
    headers = _raw_upload_headers(body)
    wrong_length = [
        (key, str(len(body) + 1).encode() if key == b"content-length" else value)
        for key, value in headers
    ]
    wrong_status, _, wrong_payload = _raw_request(
        bundle.dependencies,
        method="POST",
        path=f"/api/organizations/{ORG_A}/imports",
        headers=wrong_length,
        body=body,
    )
    encoded_status, _, encoded_payload = _raw_request(
        bundle.dependencies,
        method="POST",
        path=f"/api/organizations/{ORG_A}/imports",
        headers=[*headers, (b"content-encoding", b"gzip")],
        body=body,
    )
    oversized_headers = [
        (key, b"10490000" if key == b"content-length" else value)
        for key, value in headers
    ]
    oversized_status, _, oversized_payload = _raw_request(
        bundle.dependencies,
        method="POST",
        path=f"/api/organizations/{ORG_A}/imports",
        headers=oversized_headers,
        body=b"x",
    )

    assert (wrong_status, json.loads(wrong_payload)) == (
        400,
        {"error": "invalid_request"},
    )
    assert (encoded_status, json.loads(encoded_payload)) == (
        400,
        {"error": "invalid_request"},
    )
    assert (oversized_status, json.loads(oversized_payload)) == (
        413,
        {"error": "request_too_large"},
    )


@pytest.mark.parametrize(
    "parts",
    [
        (_valid_source_part(), _valid_source_part()),
        (
            _valid_source_part(),
            ((b'Content-Disposition: form-data; name="shadow"',), b"value"),
        ),
        (
            (
                (
                    b'Content-Disposition: form-data; name="source"; filename="team.csv"',
                    b'Content-Disposition: form-data; name="source"; filename="other.csv"',
                    b"Content-Type: text/csv",
                ),
                COMPLETE_CSV,
            ),
        ),
        (
            (
                (
                    b'Content-Disposition: form-data; name="source"; filename="team.csv"',
                    b"Content-Type: text/csv",
                    b"Content-Type: text/csv",
                ),
                COMPLETE_CSV,
            ),
        ),
    ],
)
def test_upload_rejects_multipart_ambiguity_and_duplicate_part_headers(
    bundle,
    parts,
) -> None:
    body = _multipart_body(parts=parts)
    status, _headers, payload = _raw_request(
        bundle.dependencies,
        method="POST",
        path=f"/api/organizations/{ORG_A}/imports",
        headers=_raw_upload_headers(body),
        body=body,
    )

    assert status == 400
    assert json.loads(payload) == {"error": "invalid_request"}


@pytest.mark.parametrize(
    ("duplicate", "expected_status", "expected_error"),
    [
        ((b"content-length", b"1"), 400, "invalid_request"),
        (
            (b"content-type", b"multipart/form-data; boundary=humanwire-boundary"),
            415,
            "unsupported_media_type",
        ),
        ((b"origin", ORIGIN.encode()), 403, "origin_forbidden"),
        ((b"x-firebase-appcheck", b"valid-app-check"), 403, "app_check_failed"),
        ((b"x-humanwire-csrf", b"duplicate"), 403, "csrf_failed"),
        ((b"cookie", b"__session=session-owner"), 400, "invalid_request"),
    ],
)
def test_duplicate_upload_security_headers_fail_closed(
    bundle,
    duplicate,
    expected_status,
    expected_error,
) -> None:
    body = _multipart_body(parts=(_valid_source_part(),))
    status, _headers, payload = _raw_request(
        bundle.dependencies,
        method="POST",
        path=f"/api/organizations/{ORG_A}/imports",
        headers=[*_raw_upload_headers(body), duplicate],
        body=body,
    )

    assert status == expected_status
    assert json.loads(payload) == {"error": expected_error}


def test_upload_requires_app_check_csrf_and_authentication(bundle) -> None:
    unsigned = _client(bundle)
    no_app_check = unsigned.post(
        f"/api/organizations/{ORG_A}/imports",
        headers={"Origin": ORIGIN},
        files={"source": ("team.csv", COMPLETE_CSV, "text/csv")},
    )
    no_auth = unsigned.post(
        f"/api/organizations/{ORG_A}/imports",
        headers=MUTATION_HEADERS,
        files={"source": ("team.csv", COMPLETE_CSV, "text/csv")},
    )
    owner = _client(bundle, "owner")
    no_csrf = owner.post(
        f"/api/organizations/{ORG_A}/imports",
        headers=MUTATION_HEADERS,
        files={"source": ("team.csv", COMPLETE_CSV, "text/csv")},
    )

    assert (no_app_check.status_code, no_app_check.json()) == (
        403,
        {"error": "app_check_failed"},
    )
    assert (no_auth.status_code, no_auth.json()) == (
        401,
        {"error": "authentication_required"},
    )
    assert (no_csrf.status_code, no_csrf.json()) == (
        403,
        {"error": "csrf_failed"},
    )


def test_missing_membership_and_viewer_writes_fail_before_source_parse(bundle) -> None:
    calls: list[str] = []

    def observed_parser(request):
        calls.append(request.filename)
        return parse_organization_source(request)

    dependencies = _replacement_dependencies(
        bundle,
        organization_source_parser=observed_parser,
    )
    guarded = OrganizationAppBundle(
        dependencies=dependencies,
        decisionos=bundle.decisionos,
        graph_repository=bundle.graph_repository,
        import_service=bundle.import_service,
        owner_context=bundle.owner_context,
    )
    outsider = _client(guarded, "outsider")
    viewer = _client(guarded, "viewer")

    missing = _upload(outsider)
    denied = _upload(viewer)

    assert (missing.status_code, missing.json()) == (
        404,
        {"error": "organization_not_found"},
    )
    assert (denied.status_code, denied.json()) == (
        403,
        {"error": "authorization_denied"},
    )
    assert calls == []


def test_other_tenant_cannot_read_graph_or_import(bundle) -> None:
    owner = _client(bundle, "owner")
    draft = _upload(owner).json()
    outsider = _client(bundle, "outsider")

    graph = outsider.get(f"/api/organizations/{ORG_A}/organization-graph")
    imported = outsider.get(
        f"/api/organizations/{ORG_A}/imports/{draft['import_id']}"
    )

    assert graph.status_code == 404
    assert graph.json() == {"error": "organization_not_found"}
    assert imported.status_code == 404
    assert imported.json() == {"error": "organization_not_found"}


def test_viewer_reads_graph_but_unsigned_user_is_rejected(bundle) -> None:
    viewer = _client(bundle, "viewer")
    unsigned = _client(bundle)

    allowed = viewer.get(f"/api/organizations/{ORG_A}/organization-graph")
    denied = unsigned.get(f"/api/organizations/{ORG_A}/organization-graph")

    assert allowed.status_code == 200
    assert allowed.json()["organization_id"] == ORG_A
    assert (denied.status_code, denied.json()) == (
        401,
        {"error": "authentication_required"},
    )


def test_projection_allowlists_safe_graph_fields_and_reconciliation_counts() -> None:
    graph = OrganizationGraph(
        organization_id=ORG_A,
        version=7,
        subjects=(
            OrganizationSubject(
                subject_id=SUBJECT,
                organization_id=ORG_A,
                kind=OrganizationSubjectKind.HUMAN,
                lifecycle=SubjectLifecycle.ACTIVE,
                display_name="Alice Example",
                source_identity="alice@example.invalid",
                member_uid="private-firebase-member-uid",
                unit_id=UNIT,
                title="Engineering Lead",
            ),
        ),
        units=(
            OrganizationUnit(
                unit_id=UNIT,
                organization_id=ORG_A,
                name="Engineering",
                leader_subject_id=SUBJECT,
            ),
        ),
        edges=(
            OrganizationEdge(
                edge_id=EDGE,
                organization_id=ORG_A,
                kind=OrganizationEdgeKind.MEMBER_OF,
                source_subject_id=SUBJECT,
                target_unit_id=UNIT,
            ),
        ),
        authority_assignments=(
            AuthorityAssignment(
                assignment_id=ASSIGNMENT,
                organization_id=ORG_A,
                subject_id=SUBJECT,
                decision_type="launch_decision",
                function=AuthorityFunction.DECISION_OWNER,
                effective_from=NOW,
            ),
        ),
        created_at=NOW,
    )
    reconciliation = ImportReconciliation(
        import_id=IMPORT,
        organization_id=ORG_A,
        source_count=1,
        normalized_count=1,
        rejected_count=0,
        lifecycle_counts=((SubjectLifecycle.ACTIVE, 1),),
    )

    projection = build_organization_projection(graph, reconciliation)
    serialized = projection.model_dump_json()

    assert projection.graph_version == 7
    assert projection.source_kind is None
    assert projection.synchronized_at == NOW
    assert projection.reconciliation == reconciliation
    assert projection.subjects[0].model_dump(mode="json") == {
        "subject_id": SUBJECT,
        "kind": "human",
        "lifecycle": "active",
        "display_name": "Alice Example",
        "unit_id": UNIT,
        "title": "Engineering Lead",
    }
    assert projection.units[0].name == "Engineering"
    assert projection.edges[0].kind is OrganizationEdgeKind.MEMBER_OF
    assert projection.authority_assignments[0].function is AuthorityFunction.DECISION_OWNER
    for private in (
        "alice@example.invalid",
        "private-firebase-member-uid",
        "source_identity",
        "member_uid",
        "connector",
        "token",
        "raw_rows",
        "evidence",
        "prompt",
        "provider_trace",
    ):
        assert private not in serialized


def test_projection_fails_closed_for_invalid_graph_without_private_exception_graph() -> None:
    invalid = OrganizationGraph(
        organization_id=ORG_A,
        version=1,
        subjects=(
            OrganizationSubject(
                subject_id=SUBJECT,
                organization_id=ORG_A,
                kind=OrganizationSubjectKind.HUMAN,
                lifecycle=SubjectLifecycle.DIRECTORY_ONLY,
                display_name="PRIVATE-PROJECTION-SENTINEL",
                source_identity="secret@example.invalid",
                unit_id=UNIT,
            ),
        ),
        created_at=NOW,
    )

    with pytest.raises(OrganizationProjectionUnavailable) as captured:
        build_organization_projection(invalid, None)

    graph_text = " ".join(
        (
            str(captured.value),
            repr(captured.value),
            repr(captured.value.__cause__),
            repr(captured.value.__context__),
        )
    )
    assert str(captured.value) == "organization_projection_unavailable"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "PRIVATE-PROJECTION-SENTINEL" not in graph_text
    assert "secret@example.invalid" not in graph_text


def test_graph_and_authority_routes_return_only_safe_projection(bundle) -> None:
    owner = _client(bundle, "owner")
    draft = _upload(owner, content=PRIVATE_CSV).json()
    assert "alice@example.invalid" not in json.dumps(draft)
    viewer = _client(bundle, "viewer")

    graph = viewer.get(f"/api/organizations/{ORG_A}/organization-graph")
    authority = viewer.get(f"/api/organizations/{ORG_A}/authority-map")

    assert graph.status_code == 200
    assert authority.status_code == 200
    for response in (graph, authority):
        assert "alice@example.invalid" not in response.text
        assert "private/alice" not in response.text
        assert "member_uid" not in response.text
        assert "source_identity" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"


def test_private_route_failure_is_fixed_and_keeps_security_headers(bundle) -> None:
    def exploding_projection(_graph, _reconciliation):
        raise RuntimeError("PRIVATE-PROVIDER-TRACE C:/private/source.csv")

    dependencies = _replacement_dependencies(
        bundle,
        organization_projection_builder=exploding_projection,
    )
    guarded = OrganizationAppBundle(
        dependencies=dependencies,
        decisionos=bundle.decisionos,
        graph_repository=bundle.graph_repository,
        import_service=bundle.import_service,
        owner_context=bundle.owner_context,
    )
    client = _client(guarded, "owner")

    response = client.get(f"/api/organizations/{ORG_A}/organization-graph")

    assert response.status_code == 500
    assert response.json() == {"error": "request_failed"}
    assert "PRIVATE-PROVIDER-TRACE" not in response.text
    assert "source.csv" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_enabled_feature_requires_every_organization_dependency(bundle) -> None:
    values = {
        "organization_source_parser": bundle.dependencies.organization_source_parser,
        "organization_import_service": bundle.dependencies.organization_import_service,
        "organization_graph_repository": bundle.dependencies.organization_graph_repository,
        "organization_projection_builder": bundle.dependencies.organization_projection_builder,
    }

    for field_name in values:
        with pytest.raises(ValueError, match="organization dependencies"):
            DecisionOSDependencies(
                authenticator=bundle.dependencies.authenticator,
                app_check=bundle.dependencies.app_check,
                repository=bundle.dependencies.repository,
                allowed_hosts=bundle.dependencies.allowed_hosts,
                csrf_token_factory=bundle.dependencies.csrf_token_factory,
                organization_features_enabled=True,
                **{**values, field_name: None},
            )


def test_disabled_mode_preserves_exact_existing_routes_errors_and_bytes() -> None:
    dependencies = DecisionOSDependencies(
        authenticator=FakeAuthenticator(),
        app_check=FakeAppCheck(),
        repository=InMemoryDecisionOSRepository(),
        allowed_hosts=frozenset({"decisionos.test"}),
        csrf_token_factory=lambda: "unused",
    )
    client = TestClient(create_decisionos_app(dependencies), base_url=BASE_URL)

    graph = client.get(f"/api/organizations/{ORG_A}/organization-graph")
    upload = client.post(
        f"/api/organizations/{ORG_A}/imports",
        headers=MUTATION_HEADERS,
        files={"source": ("team.csv", COMPLETE_CSV, "text/csv")},
    )
    unsigned_app = client.get("/app", follow_redirects=False)
    unsigned_api = client.get("/api/organizations")

    assert (graph.status_code, graph.content) == (404, b'{"error":"not_found"}')
    assert (upload.status_code, upload.content) == (
        405,
        b'{"error":"method_not_allowed"}',
    )
    assert (unsigned_app.status_code, unsigned_app.headers["location"]) == (303, "/signin")
    assert (unsigned_api.status_code, unsigned_api.content) == (
        401,
        b'{"error":"authentication_required"}',
    )
