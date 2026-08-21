"""Lazy production construction for the separate HumanWire DecisionOS service."""

from __future__ import annotations

import json
import logging
import os
import secrets
from functools import lru_cache
from typing import Any, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from humanwire.decisionos_app import DecisionOSDependencies, create_decisionos_app
from humanwire.decisionos_auth import (
    FirebaseAppCheckVerifier,
    FirebaseSessionAuthenticator,
)
from humanwire.decisionos_store import FirestoreDecisionOSRepository

_FIREBASE_APP_NAME = "humanwire-decisionos"
_LOGGER = logging.getLogger("humanwire.decisionos.security")
_HEALTH_HEADERS = (
    (b"content-type", b"application/json"),
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
)


class DecisionOSSettings(BaseSettings):
    """Strict environment-only configuration for the DecisionOS service."""

    model_config = SettingsConfigDict(
        env_prefix="HUMANWIRE_DECISIONOS_",
        extra="ignore",
        frozen=True,
    )

    project_id: str = Field(pattern=r"^[a-z][a-z0-9-]{4,62}$")
    firestore_database: str = "(default)"
    allowed_hosts: str
    firebase_api_key: SecretStr
    firebase_app_id: str = Field(min_length=1, max_length=512)
    firebase_auth_domain: str = Field(min_length=1, max_length=253)
    firebase_storage_bucket: str | None = Field(default=None, max_length=253)
    firebase_messaging_sender_id: str | None = Field(default=None, max_length=64)
    app_check_site_key: SecretStr
    app_check_enforced: bool = False
    organization_features_enabled: bool = False
    council_features_enabled: bool = False
    mission_features_enabled: bool = False
    council_model_id: str = "gemini-3.5-flash"
    council_google_location: str = "global"
    council_timeout_seconds: float = Field(default=180.0, ge=10.0, le=300.0)

    @model_validator(mode="after")
    def mission_requires_council(self) -> Self:
        if self.mission_features_enabled and not self.council_features_enabled:
            raise ValueError("mission feature requires the council")
        return self

    @field_validator(
        "firestore_database",
        "allowed_hosts",
        "firebase_app_id",
        "firebase_auth_domain",
        "firebase_storage_bucket",
        "firebase_messaging_sender_id",
        "council_model_id",
        "council_google_location",
    )
    @classmethod
    def has_safe_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value.isascii()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or any(character in "<>&\"'" for character in value)
        ):
            raise ValueError("DecisionOS setting contains unsafe text")
        return value

    @field_validator("firebase_api_key", "app_check_site_key")
    @classmethod
    def has_safe_secret_text(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if (
            not 1 <= len(raw) <= 512
            or not raw.isascii()
            or any(character.isspace() or ord(character) < 33 for character in raw)
            or any(character in "<>&\"'" for character in raw)
        ):
            raise ValueError("DecisionOS setting contains unsafe secret text")
        return value

    @property
    def allowed_host_set(self) -> frozenset[str]:
        values = self.allowed_hosts.replace(";", ",")
        hosts = frozenset(item.strip() for item in values.split(",") if item.strip())
        if not hosts:
            raise ValueError("DecisionOS requires an allowed host")
        return hosts

    @property
    def firebase_public_config(self) -> dict[str, object]:
        firebase: dict[str, str] = {
            "apiKey": self.firebase_api_key.get_secret_value(),
            "appId": self.firebase_app_id,
            "authDomain": self.firebase_auth_domain,
            "projectId": self.project_id,
        }
        if self.firebase_storage_bucket:
            firebase["storageBucket"] = self.firebase_storage_bucket
        if self.firebase_messaging_sender_id:
            firebase["messagingSenderId"] = self.firebase_messaging_sender_id
        return {
            "firebase": firebase,
            "appCheckSiteKey": self.app_check_site_key.get_secret_value(),
        }


class _FirebaseAuthAdapter:
    def __init__(self, firebase_app: object) -> None:
        self._firebase_app = firebase_app

    def verify_id_token(self, token: str, *, check_revoked: bool) -> object:
        from firebase_admin import auth

        return auth.verify_id_token(
            token,
            app=self._firebase_app,
            check_revoked=check_revoked,
        )

    def create_session_cookie(self, token: str, *, expires_in) -> str:
        from firebase_admin import auth

        return auth.create_session_cookie(
            token,
            expires_in=expires_in,
            app=self._firebase_app,
        )

    def verify_session_cookie(self, cookie: str, *, check_revoked: bool) -> object:
        from firebase_admin import auth

        return auth.verify_session_cookie(
            cookie,
            app=self._firebase_app,
            check_revoked=check_revoked,
        )

    def revoke_refresh_tokens(self, uid: str) -> None:
        from firebase_admin import auth

        auth.revoke_refresh_tokens(uid, app=self._firebase_app)


class _FirebaseAppCheckAdapter:
    def __init__(self, firebase_app: object) -> None:
        self._firebase_app = firebase_app

    def verify_token(self, token: str) -> object:
        from firebase_admin import app_check

        return app_check.verify_token(token, app=self._firebase_app)


def _observe_app_check(valid: bool) -> None:
    event = "decisionos_app_check_valid" if valid else "decisionos_app_check_invalid"
    _LOGGER.info(event)


def initialize_firebase(settings: DecisionOSSettings) -> object:
    """Initialize one named Admin SDK app only after the first product request."""

    import firebase_admin

    try:
        return firebase_admin.get_app(_FIREBASE_APP_NAME)
    except ValueError:
        options: dict[str, str] = {"projectId": settings.project_id}
        if settings.firebase_storage_bucket:
            options["storageBucket"] = settings.firebase_storage_bucket
        return firebase_admin.initialize_app(
            options=options,
            name=_FIREBASE_APP_NAME,
        )


def build_dependencies(
    settings: DecisionOSSettings,
    firebase_app: object,
) -> DecisionOSDependencies:
    """Bind Admin SDK adapters without exposing provider objects to the app."""

    from firebase_admin import firestore

    firestore_client = firestore.client(
        app=firebase_app,
        database_id=settings.firestore_database,
    )
    decisionos_repository = FirestoreDecisionOSRepository(firestore_client)
    organization_dependencies: dict[str, object] = {}
    organization_repository = None
    if settings.organization_features_enabled:
        from humanwire.organization_import import OrganizationImportService
        from humanwire.organization_projection import build_organization_projection
        from humanwire.organization_sources import parse_organization_source
        from humanwire.organization_store import FirestoreOrganizationGraphRepository

        organization_repository = FirestoreOrganizationGraphRepository(firestore_client)
        organization_dependencies = {
            "organization_source_parser": parse_organization_source,
            "organization_import_service": OrganizationImportService(
                repository=organization_repository,
            ),
            "organization_graph_repository": organization_repository,
            "organization_projection_builder": build_organization_projection,
        }
    council_dependencies: dict[str, object] = {}
    council_runtime = None
    if settings.council_features_enabled:
        from humanwire.council_runtime import (
            DecisionOSCouncilRuntime,
            FirestoreCouncilEvidenceRegistry,
            FirestoreCouncilRunStore,
        )

        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        os.environ["GOOGLE_CLOUD_PROJECT"] = settings.project_id
        os.environ["GOOGLE_CLOUD_LOCATION"] = settings.council_google_location
        council_runtime = DecisionOSCouncilRuntime(
            store=FirestoreCouncilRunStore(firestore_client),
            evidence_registry=FirestoreCouncilEvidenceRegistry(firestore_client),
            model_identifier=settings.council_model_id,
            timeout_seconds=settings.council_timeout_seconds,
        )
        council_dependencies = {
            "council_features_enabled": True,
            "council_runtime": council_runtime,
        }
    mission_dependencies: dict[str, object] = {}
    if settings.mission_features_enabled:
        from humanwire.mission_participants import MissionParticipantResolver
        from humanwire.mission_service import MissionService
        from humanwire.mission_store import FirestoreMissionRepository
        from humanwire.mission_transport import (
            ConnectedMissionDispatcher,
            NoConfiguredMissionRoutes,
        )
        from humanwire.organization_store import FirestoreOrganizationGraphRepository

        if council_runtime is None:
            raise ValueError("DecisionOS mission council is unavailable")
        mission_graph_repository = organization_repository or (
            FirestoreOrganizationGraphRepository(firestore_client)
        )
        mission_dependencies = {
            "mission_features_enabled": True,
            "mission_service": MissionService(
                repository=FirestoreMissionRepository(firestore_client),
                resolver=MissionParticipantResolver(
                    graph_repository=mission_graph_repository,
                ),
                council=council_runtime,
                dispatcher=ConnectedMissionDispatcher(
                    routes=NoConfiguredMissionRoutes(),
                    transport=None,
                ),
            ),
        }
    return DecisionOSDependencies(
        authenticator=FirebaseSessionAuthenticator(_FirebaseAuthAdapter(firebase_app)),
        app_check=FirebaseAppCheckVerifier(_FirebaseAppCheckAdapter(firebase_app)),
        repository=decisionos_repository,
        allowed_hosts=settings.allowed_host_set,
        csrf_token_factory=lambda: secrets.token_urlsafe(32),
        firebase_public_config=settings.firebase_public_config,
        app_check_enforced=settings.app_check_enforced,
        app_check_observer=_observe_app_check,
        organization_features_enabled=settings.organization_features_enabled,
        **organization_dependencies,
        **council_dependencies,
        **mission_dependencies,
    )


@lru_cache(maxsize=1)
def build_decisionos_web_app():
    settings = DecisionOSSettings()
    firebase_app = initialize_firebase(settings)
    return create_decisionos_app(build_dependencies(settings, firebase_app))


class _LazyDecisionOSApplication:
    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if self._is_health_request(scope):
            payload = b"" if scope.get("method") == "HEAD" else json.dumps(
                {"status": "ok"},
                separators=(",", ":"),
            ).encode("ascii")
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": (*_HEALTH_HEADERS, (b"content-length", str(len(payload)).encode())),
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return
        await build_decisionos_web_app()(scope, receive, send)

    @staticmethod
    def _is_health_request(scope: dict[str, Any]) -> bool:
        return (
            scope.get("type") == "http"
            and scope.get("method") in {"GET", "HEAD"}
            and (
                (scope.get("path") == "/health" and scope.get("raw_path") == b"/health")
                or (
                    scope.get("path") == "/healthz"
                    and scope.get("raw_path") == b"/healthz"
                )
            )
            and not scope.get("query_string")
        )


app = _LazyDecisionOSApplication()

__all__ = [
    "DecisionOSSettings",
    "app",
    "build_decisionos_web_app",
    "build_dependencies",
    "initialize_firebase",
]
