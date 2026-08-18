from __future__ import annotations

from datetime import timedelta

import pytest

from humanwire.decisionos_auth import (
    AppCheckUnavailable,
    AuthenticationUnavailable,
    FirebaseAppCheckVerifier,
    FirebaseSessionAuthenticator,
    SessionCookieConfig,
    csrf_matches,
)


class FakeFirebaseAuth:
    def __init__(self) -> None:
        self.id_claims: object = {
            "uid": "firebase-user-01",
            "email_verified": True,
            "firebase": {
                "sign_in_provider": "google.com",
                "identities": {"google.com": ["google-subject"]},
            },
        }
        self.session_claims: object = self.id_claims
        self.created_tokens: list[str] = []
        self.verified_sessions: list[tuple[str, bool]] = []
        self.revoked_uids: list[str] = []
        self.failure: BaseException | None = None

    def verify_id_token(self, token: str, *, check_revoked: bool) -> object:
        assert check_revoked is True
        if self.failure is not None:
            raise self.failure
        return self.id_claims

    def create_session_cookie(self, token: str, *, expires_in: timedelta) -> str:
        assert expires_in == timedelta(days=5)
        self.created_tokens.append(token)
        return "opaque-session-cookie"

    def verify_session_cookie(self, cookie: str, *, check_revoked: bool) -> object:
        if self.failure is not None:
            raise self.failure
        self.verified_sessions.append((cookie, check_revoked))
        return self.session_claims

    def revoke_refresh_tokens(self, uid: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.revoked_uids.append(uid)


class FakeAppCheck:
    def __init__(self) -> None:
        self.result: object = {"app_id": "decisionos-web-app"}
        self.failure: BaseException | None = None
        self.tokens: list[str] = []

    def verify_token(self, token: str) -> object:
        if self.failure is not None:
            raise self.failure
        self.tokens.append(token)
        return self.result


def _auth(client: FakeFirebaseAuth | None = None) -> FirebaseSessionAuthenticator:
    return FirebaseSessionAuthenticator(client or FakeFirebaseAuth())


def _exception_graph(error: BaseException) -> tuple[str, ...]:
    values: list[str] = []
    pending: list[BaseException] = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        values.extend((str(current), repr(current)))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return tuple(values)


def test_id_token_exchange_creates_secure_bounded_session() -> None:
    client = FakeFirebaseAuth()
    result = _auth(client).exchange_id_token("opaque-id-token")

    assert result.principal.uid == "firebase-user-01"
    assert result.principal.email_verified is True
    assert result.principal.provider_ids == ("google.com",)
    assert result.cookie.value.get_secret_value() == "opaque-session-cookie"
    assert result.cookie.name == "__Host-humanwire-session"
    assert result.cookie.max_age_seconds == 432000
    assert result.cookie.secure is True
    assert result.cookie.http_only is True
    assert result.cookie.same_site == "lax"
    assert result.cookie.path == "/"
    assert client.created_tokens == ["opaque-id-token"]


def test_session_cookie_verification_returns_minimized_principal() -> None:
    client = FakeFirebaseAuth()
    principal = _auth(client).verify_session_cookie("opaque-session", check_revoked=True)

    assert principal.model_dump() == {
        "uid": "firebase-user-01",
        "email_verified": True,
        "provider_ids": ("google.com",),
    }
    assert client.verified_sessions == [("opaque-session", True)]


def test_logout_revokes_only_the_verified_principal() -> None:
    client = FakeFirebaseAuth()
    _auth(client).revoke_session("opaque-session")

    assert client.verified_sessions == [("opaque-session", True)]
    assert client.revoked_uids == ["firebase-user-01"]


@pytest.mark.parametrize(
    "claims",
    [
        None,
        {},
        {"uid": "user@example.com", "email_verified": True, "firebase": {}},
        {
            "uid": "firebase-user-01",
            "email_verified": False,
            "firebase": {"sign_in_provider": "google.com"},
        },
        {
            "uid": "firebase-user-01",
            "email_verified": 1,
            "firebase": {"sign_in_provider": "google.com"},
        },
        {
            "uid": "firebase-user-01",
            "email_verified": True,
            "firebase": {"sign_in_provider": "GOOGLE.COM"},
        },
        {
            "uid": "firebase-user-01",
            "email_verified": True,
            "firebase": {"sign_in_provider": "custom", "identities": []},
        },
        {
            "uid": "firebase-user-01",
            "email_verified": True,
            "firebase": {
                "sign_in_provider": "google.com",
                "identities": {"google.com": []},
            },
        },
    ],
)
def test_malformed_or_unverified_claims_fail_before_cookie_creation(claims: object) -> None:
    client = FakeFirebaseAuth()
    client.id_claims = claims

    with pytest.raises(AuthenticationUnavailable, match="authentication_unavailable"):
        _auth(client).exchange_id_token("opaque-id-token")

    assert client.created_tokens == []


@pytest.mark.parametrize(
    "token",
    ["", " ", "token\nvalue", "token\x7fvalue", "é", "x" * 8193],
)
def test_invalid_opaque_tokens_fail_without_calling_firebase(token: str) -> None:
    client = FakeFirebaseAuth()

    with pytest.raises(AuthenticationUnavailable, match="authentication_unavailable"):
        _auth(client).exchange_id_token(token)

    assert client.created_tokens == []


def test_provider_exception_graph_does_not_retain_private_details() -> None:
    client = FakeFirebaseAuth()
    client.failure = RuntimeError("PRIVATE-ID-TOKEN C:/private/account.json")

    with pytest.raises(AuthenticationUnavailable) as captured:
        _auth(client).exchange_id_token("opaque-id-token")

    serialized_graph = " ".join(_exception_graph(captured.value))
    assert "PRIVATE-ID-TOKEN" not in serialized_graph
    assert "account.json" not in serialized_graph
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "max_age",
    [timedelta(0), timedelta(minutes=-1), timedelta(days=5, seconds=1)],
)
def test_session_configuration_rejects_unbounded_lifetimes(max_age: timedelta) -> None:
    with pytest.raises(ValueError, match="session lifetime"):
        SessionCookieConfig(max_age=max_age)


def test_app_check_returns_only_canonical_app_identity() -> None:
    client = FakeAppCheck()
    verified = FirebaseAppCheckVerifier(client).verify("opaque-app-check")

    assert verified.app_id == "decisionos-web-app"
    assert client.tokens == ["opaque-app-check"]


@pytest.mark.parametrize(
    "result",
    [None, {}, {"app_id": ""}, {"app_id": "app id"}, {"app_id": 1}],
)
def test_malformed_app_check_result_fails_closed(result: object) -> None:
    client = FakeAppCheck()
    client.result = result

    with pytest.raises(AppCheckUnavailable, match="app_check_unavailable"):
        FirebaseAppCheckVerifier(client).verify("opaque-app-check")


def test_app_check_exception_graph_does_not_retain_private_details() -> None:
    client = FakeAppCheck()
    client.failure = RuntimeError("PRIVATE-APP-CHECK")

    with pytest.raises(AppCheckUnavailable) as captured:
        FirebaseAppCheckVerifier(client).verify("opaque-app-check")

    assert "PRIVATE-APP-CHECK" not in " ".join(_exception_graph(captured.value))
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    ("cookie", "header", "expected"),
    [
        ("csrf-token-01", "csrf-token-01", True),
        ("csrf-token-01", "csrf-token-02", False),
        (None, "csrf-token-01", False),
        ("csrf-token-01", None, False),
        ("", "", False),
        ("token\n", "token\n", False),
        ("é", "é", False),
    ],
)
def test_csrf_comparison_is_exact_ascii_and_nonempty(
    cookie: str | None,
    header: str | None,
    expected: bool,
) -> None:
    assert csrf_matches(cookie, header) is expected
