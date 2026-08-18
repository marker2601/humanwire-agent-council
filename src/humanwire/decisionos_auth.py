"""Safe Firebase identity, session, App Check, and CSRF boundaries."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from humanwire.decisionos_models import DecisionOSPrincipal

_OPAQUE_LIMIT = 8192
_CSRF_LIMIT = 512
_APP_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class AuthenticationUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("authentication_unavailable")


class AppCheckUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("app_check_unavailable")


class _AuthModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SessionCookie(_AuthModel):
    value: SecretStr
    name: Literal["__session"] = "__session"
    max_age_seconds: int = Field(ge=1, le=432000)
    secure: Literal[True] = True
    http_only: Literal[True] = True
    same_site: Literal["lax"] = "lax"
    path: Literal["/"] = "/"


class SessionCookieConfig(_AuthModel):
    max_age: timedelta = timedelta(days=5)

    @model_validator(mode="after")
    def has_bounded_session_lifetime(self) -> Self:
        if not timedelta(0) < self.max_age <= timedelta(days=5):
            raise ValueError("session lifetime must be between zero and five days")
        if self.max_age.total_seconds() != int(self.max_age.total_seconds()):
            raise ValueError("session lifetime must use whole seconds")
        return self

    def bind(self, value: str) -> SessionCookie:
        if not _valid_opaque(value):
            raise AuthenticationUnavailable()
        return SessionCookie(
            value=SecretStr(value),
            max_age_seconds=int(self.max_age.total_seconds()),
        )


class AuthenticatedSession(_AuthModel):
    principal: DecisionOSPrincipal
    cookie: SessionCookie


class VerifiedAppCheck(_AuthModel):
    app_id: str = Field(pattern=_APP_ID.pattern)


class FirebaseAuthClient(Protocol):
    def verify_id_token(self, token: str, *, check_revoked: bool) -> object:
        raise NotImplementedError

    def create_session_cookie(self, token: str, *, expires_in: timedelta) -> str:
        raise NotImplementedError

    def verify_session_cookie(self, cookie: str, *, check_revoked: bool) -> object:
        raise NotImplementedError

    def revoke_refresh_tokens(self, uid: str) -> None:
        raise NotImplementedError


class FirebaseAppCheckClient(Protocol):
    def verify_token(self, token: str) -> object:
        raise NotImplementedError


def _valid_opaque(value: object, *, limit: int = _OPAQUE_LIMIT) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= limit
        and value.isascii()
        and not any(
            character.isspace() or ord(character) < 33 or ord(character) == 127
            for character in value
        )
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("claim must be a mapping")
    return value


def _principal_from_claims(value: object) -> DecisionOSPrincipal:
    claims = _mapping(value)
    firebase = _mapping(claims.get("firebase"))
    identities = _mapping(firebase.get("identities"))
    provider = firebase.get("sign_in_provider")
    provider_subjects = identities.get(provider) if type(provider) is str else None
    if (
        type(provider) is not str
        or not isinstance(provider_subjects, (list, tuple))
        or not provider_subjects
        or any(not _valid_opaque(subject, limit=512) for subject in provider_subjects)
    ):
        raise ValueError("provider identity is invalid")
    if type(claims.get("email_verified")) is not bool or claims["email_verified"] is not True:
        raise ValueError("verified email is required")
    return DecisionOSPrincipal.model_validate(
        {
            "uid": claims.get("uid"),
            "email_verified": claims["email_verified"],
            "provider_ids": tuple(sorted(identities)),
        }
    )


def _require_recent_authentication(value: object, now: datetime) -> None:
    claims = _mapping(value)
    auth_time = claims.get("auth_time")
    if type(auth_time) is not int:
        raise ValueError("authentication time is invalid")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("authentication clock must be timezone-aware")
    age_seconds = now.astimezone(UTC).timestamp() - auth_time
    if not -30 <= age_seconds <= 300:
        raise ValueError("recent authentication is required")


class FirebaseSessionAuthenticator:
    def __init__(
        self,
        client: FirebaseAuthClient,
        *,
        cookie: SessionCookieConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._cookie = SessionCookieConfig() if cookie is None else cookie
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock

    def exchange_id_token(self, id_token: str) -> AuthenticatedSession:
        if not _valid_opaque(id_token):
            raise AuthenticationUnavailable() from None
        failed = False
        try:
            claims = self._client.verify_id_token(id_token, check_revoked=True)
            _require_recent_authentication(claims, self._clock())
            principal = _principal_from_claims(claims)
        except Exception:  # noqa: BLE001 - provider details must not cross the boundary
            failed = True
        if failed:
            raise AuthenticationUnavailable() from None

        failed = False
        try:
            value = self._client.create_session_cookie(
                id_token,
                expires_in=self._cookie.max_age,
            )
            cookie = self._cookie.bind(value)
        except Exception:  # noqa: BLE001 - provider details must not cross the boundary
            failed = True
        if failed:
            raise AuthenticationUnavailable() from None
        return AuthenticatedSession(principal=principal, cookie=cookie)

    def verify_session_cookie(
        self,
        cookie: str,
        *,
        check_revoked: bool,
    ) -> DecisionOSPrincipal:
        if not _valid_opaque(cookie):
            raise AuthenticationUnavailable() from None
        failed = False
        try:
            claims = self._client.verify_session_cookie(cookie, check_revoked=check_revoked)
            principal = _principal_from_claims(claims)
        except Exception:  # noqa: BLE001 - provider details must not cross the boundary
            failed = True
        if failed:
            raise AuthenticationUnavailable() from None
        return principal

    def revoke_session(self, cookie: str) -> None:
        failed = False
        try:
            principal = self.verify_session_cookie(cookie, check_revoked=True)
            self._client.revoke_refresh_tokens(principal.uid)
        except Exception:  # noqa: BLE001 - provider details must not cross the boundary
            failed = True
        if failed:
            raise AuthenticationUnavailable() from None


class FirebaseAppCheckVerifier:
    def __init__(self, client: FirebaseAppCheckClient) -> None:
        self._client = client

    def verify(self, token: str) -> VerifiedAppCheck:
        if not _valid_opaque(token):
            raise AppCheckUnavailable() from None
        failed = False
        try:
            claims = _mapping(self._client.verify_token(token))
            app_id = claims.get("app_id")
            if type(app_id) is not str or _APP_ID.fullmatch(app_id) is None:
                raise ValueError("app identity is invalid")
            verified = VerifiedAppCheck(app_id=app_id)
        except Exception:  # noqa: BLE001 - provider details must not cross the boundary
            failed = True
        if failed:
            raise AppCheckUnavailable() from None
        return verified


def csrf_matches(cookie: str | None, header: str | None) -> bool:
    if not _valid_opaque(cookie, limit=_CSRF_LIMIT) or not _valid_opaque(
        header,
        limit=_CSRF_LIMIT,
    ):
        return False
    return hmac.compare_digest(cookie, header)
