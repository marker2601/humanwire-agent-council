"""Protected FastAPI boundary for authenticated HumanWire DecisionOS workspaces."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from humanwire.decisionos_auth import (
    AppCheckUnavailable,
    AuthenticatedSession,
    AuthenticationUnavailable,
    SessionCookie,
    VerifiedAppCheck,
    csrf_matches,
)
from humanwire.decisionos_models import (
    DecisionOSPrincipal,
    DecisionOSRole,
    WorkspacePlaybook,
)
from humanwire.decisionos_store import (
    DecisionOSAuthorizationDenied,
    DecisionOSRepository,
    InvitationUnavailable,
    OrganizationUnavailable,
    WorkspaceUnavailable,
)

_MAX_BODY_BYTES = 8192
_PACKAGE_DIR = Path(__file__).resolve().parent
_SESSION_COOKIE = "__session"
_CSRF_COOKIE = "__Host-humanwire-csrf"
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::([0-9]{1,5}))?$")
_ORGANIZATION_ID = r"org_[0-9A-HJKMNP-TV-Z]{26}"
_WORKSPACE_ID = r"wrk_[0-9A-HJKMNP-TV-Z]{26}"
_PUBLIC_CONFIG_KEYS = frozenset({"firebase", "appCheckSiteKey"})
_FIREBASE_PUBLIC_KEYS = frozenset(
    {
        "apiKey",
        "appId",
        "authDomain",
        "messagingSenderId",
        "projectId",
        "storageBucket",
    }
)
_FIREBASE_REQUIRED_KEYS = frozenset({"apiKey", "appId", "authDomain", "projectId"})
_MUTATION_PATHS = (
    re.compile(r"^/api/session/login$"),
    re.compile(r"^/api/session/logout$"),
    re.compile(r"^/api/organizations$"),
    re.compile(r"^/api/invitations/accept$"),
    re.compile(rf"^/api/organizations/{_ORGANIZATION_ID}/invitations$"),
    re.compile(rf"^/api/organizations/{_ORGANIZATION_ID}/workspaces$"),
)
_SAFE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' https://apis.google.com https://www.google.com/recaptcha/ "
        "https://www.gstatic.com/recaptcha/; "
        "style-src 'self'; img-src 'self' data:; "
        "connect-src 'self' https://identitytoolkit.googleapis.com "
        "https://securetoken.googleapis.com https://content-firebaseappcheck.googleapis.com "
        "https://firebaseappcheck.googleapis.com "
        "https://www.google.com/recaptcha/; "
        "frame-src 'self' https://www.google.com/recaptcha/ "
        "https://recaptcha.google.com/recaptcha/; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _ignore_app_check_observation(_valid: bool) -> None:
    return None


class SessionAuthenticator(Protocol):
    def exchange_id_token(self, id_token: str) -> AuthenticatedSession:
        raise NotImplementedError

    def verify_session_cookie(
        self,
        cookie: str,
        *,
        check_revoked: bool,
    ) -> DecisionOSPrincipal:
        raise NotImplementedError

    def revoke_session(self, cookie: str) -> None:
        raise NotImplementedError


class AppCheckVerifier(Protocol):
    def verify(self, token: str) -> VerifiedAppCheck:
        raise NotImplementedError


@dataclass(frozen=True)
class DecisionOSDependencies:
    authenticator: SessionAuthenticator
    app_check: AppCheckVerifier
    repository: DecisionOSRepository
    allowed_hosts: frozenset[str]
    csrf_token_factory: Callable[[], str]
    firebase_public_config: Mapping[str, object] = field(default_factory=dict)
    app_check_enforced: bool = True
    app_check_observer: Callable[[bool], None] = _ignore_app_check_observation

    def __post_init__(self) -> None:
        if not self.allowed_hosts:
            raise ValueError("DecisionOS requires at least one allowed host")
        for host in self.allowed_hosts:
            matched = _HOST.fullmatch(host)
            if matched is None or host.casefold() != host:
                raise ValueError("DecisionOS allowed host is invalid")
            port = matched.group(1)
            if port is not None and not 1 <= int(port) <= 65535:
                raise ValueError("DecisionOS allowed host is invalid")
        config = _validated_public_config(self.firebase_public_config)
        object.__setattr__(self, "firebase_public_config", MappingProxyType(config))


def _public_value(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 512
        and value.isascii()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        and not any(character in "<>&\"'" for character in value)
    )


def _validated_public_config(value: Mapping[str, object]) -> dict[str, object]:
    config = json.loads(json.dumps(value))
    if not isinstance(config, dict):
        raise TypeError("DecisionOS public configuration is invalid")
    if not config:
        return {}
    if set(config) != _PUBLIC_CONFIG_KEYS:
        raise ValueError("DecisionOS public configuration contains an unknown field")
    firebase = config.get("firebase")
    if (
        not isinstance(firebase, dict)
        or not _FIREBASE_REQUIRED_KEYS.issubset(firebase)
        or not set(firebase).issubset(_FIREBASE_PUBLIC_KEYS)
        or not all(_public_value(item) for item in firebase.values())
        or not _public_value(config.get("appCheckSiteKey"))
    ):
        raise ValueError("DecisionOS public configuration is invalid")
    return config


class _BodyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _LoginBody(_BodyModel):
    id_token: SecretStr


class _LogoutBody(_BodyModel):
    confirm: bool


class _OrganizationBody(_BodyModel):
    name: str = Field(min_length=1, max_length=120)


class _InvitationBody(_BodyModel):
    role: DecisionOSRole = Field(strict=False)


class _InvitationAcceptanceBody(_BodyModel):
    invitation_token: SecretStr


class _WorkspaceBody(_BodyModel):
    name: str = Field(min_length=1, max_length=120)
    playbook: WorkspacePlaybook = Field(strict=False)


def _fixed_error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})


def _raw_headers(request: Request, name: bytes) -> tuple[bytes, ...]:
    lowered = name.lower()
    return tuple(
        value
        for key, value in request.scope.get("headers", ())
        if key.lower() == lowered
    )


def _ascii_header(values: tuple[bytes, ...]) -> str | None:
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _rejecting_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


async def _body(request: Request, model_type: type[_BodyModel]) -> _BodyModel | None:
    length_text = _ascii_header(_raw_headers(request, b"content-length"))
    if length_text is None:
        return None
    raw = await request.body()
    if len(raw) != int(length_text) or not 1 <= len(raw) <= _MAX_BODY_BYTES:
        return None
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_rejecting_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(decoded, dict):
            return None
        return model_type.model_validate(decoded)
    except (UnicodeError, ValueError, TypeError, ValidationError):
        return None


def _is_exact_mutation(request: Request) -> bool:
    path = request.scope.get("path")
    raw_path = request.scope.get("raw_path")
    if not isinstance(path, str) or not isinstance(raw_path, bytes):
        return False
    try:
        exact_raw = path.encode("ascii")
    except UnicodeEncodeError:
        return False
    return (
        raw_path == exact_raw
        and not request.scope.get("query_string")
        and any(pattern.fullmatch(path) for pattern in _MUTATION_PATHS)
    )


def _session_cookie(request: Request) -> str | None:
    if len(_raw_headers(request, b"cookie")) != 1:
        return None
    return request.cookies.get(_SESSION_COOKIE)


def _csrf_for_session(session_cookie: str) -> str:
    """Bind the browser-readable CSRF token to Firebase Hosting's session cookie."""
    return hashlib.sha256(session_cookie.encode("utf-8")).hexdigest()


def _principal(
    request: Request,
    authenticator: SessionAuthenticator,
) -> DecisionOSPrincipal | None:
    existing = getattr(request.state, "decisionos_principal", None)
    if isinstance(existing, DecisionOSPrincipal):
        return existing
    cookie = _session_cookie(request)
    if cookie is None:
        return None
    try:
        return authenticator.verify_session_cookie(cookie, check_revoked=True)
    except AuthenticationUnavailable:
        return None


def _apply_session_cookie(response: Response, cookie: SessionCookie) -> None:
    response.set_cookie(
        key=cookie.name,
        value=cookie.value.get_secret_value(),
        max_age=cookie.max_age_seconds,
        secure=cookie.secure,
        httponly=cookie.http_only,
        samesite=cookie.same_site,
        path=cookie.path,
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(_SESSION_COOKIE, path="/", secure=True, httponly=True)
    response.delete_cookie(_CSRF_COOKIE, path="/", secure=True, httponly=False)


def create_decisionos_app(dependencies: DecisionOSDependencies) -> FastAPI:
    """Build a same-origin, App-Check-protected DecisionOS application."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    public_config_json = json.dumps(
        dict(dependencies.firebase_public_config),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    allowed_origins = frozenset(f"https://{host}" for host in dependencies.allowed_hosts)
    app.mount(
        "/decisionos-static",
        StaticFiles(directory=str(_PACKAGE_DIR / "decisionos_static")),
        name="decisionos-static",
    )

    @app.exception_handler(StarletteHTTPException)
    async def fixed_http_error(
        _request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        if error.status_code == 404:
            return _fixed_error(404, "not_found")
        if error.status_code == 405:
            return _fixed_error(405, "method_not_allowed")
        return _fixed_error(error.status_code, "request_failed")

    @app.middleware("http")
    async def protected_boundary(request: Request, call_next):
        try:
            hosts = _raw_headers(request, b"host")
            host = _ascii_header(hosts)
            if host is None or host.casefold() not in dependencies.allowed_hosts:
                response = _fixed_error(400, "invalid_host")
            elif len(_raw_headers(request, b"cookie")) > 1:
                response = _fixed_error(400, "invalid_request")
            elif request.method not in {"GET", "HEAD", "POST"} or (
                request.method == "POST" and not _is_exact_mutation(request)
            ):
                response = _fixed_error(405, "method_not_allowed")
            elif request.method == "POST":
                lengths = _raw_headers(request, b"content-length")
                length_text = _ascii_header(lengths)
                if (
                    length_text is None
                    or not length_text.isdecimal()
                    or len(length_text) > 4
                    or int(length_text) < 1
                ):
                    response = _fixed_error(400, "invalid_request")
                elif int(length_text) > _MAX_BODY_BYTES:
                    response = _fixed_error(413, "request_too_large")
                elif _raw_headers(request, b"transfer-encoding") or _raw_headers(
                    request,
                    b"content-encoding",
                ):
                    response = _fixed_error(400, "invalid_request")
                else:
                    content_type = _ascii_header(_raw_headers(request, b"content-type"))
                    if content_type is None or content_type.split(";", 1)[0].strip().casefold() != (
                        "application/json"
                    ):
                        response = _fixed_error(415, "unsupported_media_type")
                    else:
                        origins = _raw_headers(request, b"origin")
                        origin = _ascii_header(origins)
                        if origin not in allowed_origins:
                            response = _fixed_error(403, "origin_forbidden")
                        else:
                            app_check_token = _ascii_header(
                                _raw_headers(request, b"x-firebase-appcheck")
                            )
                            app_check_failed = False
                            if app_check_token is None:
                                app_check_failed = True
                            else:
                                try:
                                    dependencies.app_check.verify(app_check_token)
                                except AppCheckUnavailable:
                                    app_check_failed = True
                            with suppress(Exception):
                                dependencies.app_check_observer(not app_check_failed)
                            if app_check_failed and dependencies.app_check_enforced:
                                response = _fixed_error(403, "app_check_failed")
                            else:
                                if request.url.path == "/api/session/login":
                                    response = await call_next(request)
                                else:
                                    principal = _principal(request, dependencies.authenticator)
                                    if principal is None:
                                        response = _fixed_error(
                                            401,
                                            "authentication_required",
                                        )
                                    else:
                                        csrf_header = _ascii_header(
                                            _raw_headers(request, b"x-humanwire-csrf")
                                        )
                                        session_cookie = _session_cookie(request)
                                        expected_csrf = (
                                            _csrf_for_session(session_cookie)
                                            if session_cookie is not None
                                            else None
                                        )
                                        if not csrf_matches(expected_csrf, csrf_header):
                                            response = _fixed_error(403, "csrf_failed")
                                        else:
                                            request.state.decisionos_principal = principal
                                            response = await call_next(request)
            else:
                response = await call_next(request)
        except Exception:  # noqa: BLE001 - boundary failures remain fixed and private
            response = _fixed_error(500, "request_failed")
        for name, value in _SAFE_HEADERS.items():
            response.headers[name] = value
        return response

    @app.api_route("/app", methods=["GET", "HEAD"])
    @app.api_route("/workspace", methods=["GET", "HEAD"])
    def protected_app(request: Request) -> Response:
        if _principal(request, dependencies.authenticator) is None:
            return _fixed_error(401, "authentication_required")
        return templates.TemplateResponse(
            request=request,
            name="decisionos_shell.html",
            context={"public_config_json": public_config_json},
        )

    @app.api_route("/signin", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def sign_in(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="decisionos_login.html",
            context={"public_config_json": public_config_json},
        )

    @app.post("/api/session/login")
    async def login(request: Request) -> Response:
        body = await _body(request, _LoginBody)
        if not isinstance(body, _LoginBody):
            return _fixed_error(400, "invalid_request")
        try:
            authenticated = dependencies.authenticator.exchange_id_token(
                body.id_token.get_secret_value()
            )
        except AuthenticationUnavailable:
            return _fixed_error(401, "authentication_failed")
        csrf_token = _csrf_for_session(
            authenticated.cookie.value.get_secret_value()
        )
        if not csrf_matches(csrf_token, csrf_token):
            return _fixed_error(500, "request_failed")
        response = Response(status_code=204)
        _apply_session_cookie(response, authenticated.cookie)
        response.set_cookie(
            key=_CSRF_COOKIE,
            value=csrf_token,
            max_age=authenticated.cookie.max_age_seconds,
            secure=True,
            httponly=False,
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/api/session/logout")
    async def logout(request: Request) -> Response:
        body = await _body(request, _LogoutBody)
        cookie = _session_cookie(request)
        if not isinstance(body, _LogoutBody) or body.confirm is not True or cookie is None:
            return _fixed_error(400, "invalid_request")
        try:
            dependencies.authenticator.revoke_session(cookie)
        except AuthenticationUnavailable:
            return _fixed_error(401, "authentication_required")
        response = Response(status_code=204)
        _clear_session_cookies(response)
        return response

    @app.api_route("/api/organizations", methods=["GET", "HEAD"])
    def list_organizations(request: Request) -> Response:
        principal = _principal(request, dependencies.authenticator)
        if principal is None:
            return _fixed_error(401, "authentication_required")
        organizations = dependencies.repository.list_organizations(principal)
        rows = []
        for organization in organizations:
            context = dependencies.repository.load_context(
                principal,
                organization.organization_id,
            )
            rows.append(
                {
                    "organization_id": organization.organization_id,
                    "name": organization.name,
                    "role": context.membership.role.value,
                }
            )
        return JSONResponse(content={"organizations": rows})

    @app.post("/api/organizations")
    async def create_organization(request: Request) -> Response:
        body = await _body(request, _OrganizationBody)
        principal = _principal(request, dependencies.authenticator)
        if not isinstance(body, _OrganizationBody) or principal is None:
            return _fixed_error(400, "invalid_request")
        try:
            organization = dependencies.repository.create_organization(
                principal,
                body.name,
            )
        except (TypeError, ValueError, OrganizationUnavailable):
            return _fixed_error(400, "invalid_request")
        return JSONResponse(
            status_code=201,
            content=organization.model_dump(mode="json"),
        )

    @app.post("/api/organizations/{organization_id}/invitations")
    async def create_invitation(organization_id: str, request: Request) -> Response:
        body = await _body(request, _InvitationBody)
        principal = _principal(request, dependencies.authenticator)
        if not isinstance(body, _InvitationBody) or principal is None:
            return _fixed_error(400, "invalid_request")
        try:
            context = dependencies.repository.load_context(principal, organization_id)
            invitation = dependencies.repository.create_invitation(
                context,
                role=body.role,
                expires_in=timedelta(days=7),
            )
        except OrganizationUnavailable:
            return _fixed_error(404, "organization_not_found")
        except DecisionOSAuthorizationDenied:
            return _fixed_error(403, "authorization_denied")
        return JSONResponse(
            status_code=201,
            content={
                "invitation_id": invitation.invitation_id,
                "invitation_token": invitation.token.get_secret_value(),
                "role": invitation.role.value,
                "expires_at": invitation.expires_at.isoformat(),
            },
        )

    @app.post("/api/invitations/accept")
    async def accept_invitation(request: Request) -> Response:
        body = await _body(request, _InvitationAcceptanceBody)
        principal = _principal(request, dependencies.authenticator)
        if not isinstance(body, _InvitationAcceptanceBody) or principal is None:
            return _fixed_error(400, "invalid_request")
        try:
            membership = dependencies.repository.accept_invitation(
                principal,
                body.invitation_token.get_secret_value(),
            )
        except InvitationUnavailable:
            return _fixed_error(400, "invitation_unavailable")
        return JSONResponse(
            content={
                "organization_id": membership.organization_id,
                "role": membership.role.value,
            }
        )

    @app.api_route(
        "/api/organizations/{organization_id}/workspaces",
        methods=["GET", "HEAD"],
    )
    def list_workspaces(organization_id: str, request: Request) -> Response:
        principal = _principal(request, dependencies.authenticator)
        if principal is None:
            return _fixed_error(401, "authentication_required")
        try:
            context = dependencies.repository.load_context(principal, organization_id)
            workspaces = dependencies.repository.list_workspaces(context)
        except OrganizationUnavailable:
            return _fixed_error(404, "organization_not_found")
        return JSONResponse(
            content={
                "workspaces": [item.model_dump(mode="json") for item in workspaces]
            }
        )

    @app.post("/api/organizations/{organization_id}/workspaces")
    async def create_workspace(organization_id: str, request: Request) -> Response:
        body = await _body(request, _WorkspaceBody)
        principal = _principal(request, dependencies.authenticator)
        if not isinstance(body, _WorkspaceBody) or principal is None:
            return _fixed_error(400, "invalid_request")
        try:
            context = dependencies.repository.load_context(principal, organization_id)
            workspace = dependencies.repository.create_workspace(
                context,
                name=body.name,
                playbook=body.playbook,
            )
        except OrganizationUnavailable:
            return _fixed_error(404, "organization_not_found")
        except DecisionOSAuthorizationDenied:
            return _fixed_error(403, "authorization_denied")
        except (TypeError, ValueError):
            return _fixed_error(400, "invalid_request")
        return JSONResponse(
            status_code=201,
            content=workspace.model_dump(mode="json"),
        )

    @app.api_route(
        "/api/organizations/{organization_id}/workspaces/{workspace_id}",
        methods=["GET", "HEAD"],
    )
    def load_workspace(
        organization_id: str,
        workspace_id: str,
        request: Request,
    ) -> Response:
        principal = _principal(request, dependencies.authenticator)
        if principal is None:
            return _fixed_error(401, "authentication_required")
        try:
            context = dependencies.repository.load_context(principal, organization_id)
            workspace = dependencies.repository.load_workspace(context, workspace_id)
        except OrganizationUnavailable:
            return _fixed_error(404, "organization_not_found")
        except WorkspaceUnavailable:
            return _fixed_error(404, "workspace_not_found")
        return JSONResponse(content=workspace.model_dump(mode="json"))

    return app
