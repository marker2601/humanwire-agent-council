import pytest
from pydantic import SecretStr, ValidationError

from humanwire.config import Settings


def test_listener_credentials_are_required_only_when_listening() -> None:
    with pytest.raises(ValueError, match="CASPIAN_API_KEY"):
        Settings(
            _env_file=None,
            caspian_api_key=None,
            telegram_bot_token=None,
        ).require_listener_credentials()


def test_listener_credentials_return_plain_values() -> None:
    settings = Settings(
        caspian_api_key=SecretStr("caspian"),
        telegram_bot_token=SecretStr("telegram"),
    )
    assert settings.require_listener_credentials() == ("caspian", "telegram")


def test_analytics_read_token_is_secret_and_optional() -> None:
    assert Settings(_env_file=None, analytics_read_token=None).analytics_read_token is None
    configured = Settings(_env_file=None, analytics_read_token="fictional-read-token")

    assert configured.analytics_read_token is not None
    assert configured.analytics_read_token.get_secret_value() == "fictional-read-token"
    assert "fictional-read-token" not in repr(configured)


@pytest.mark.parametrize("raw", ["", " ", "\t\r\n"])
def test_blank_analytics_read_token_is_disabled(raw: str) -> None:
    assert Settings(_env_file=None, analytics_read_token=raw).analytics_read_token is None


def test_engagement_preview_defaults_and_environment_parsing(monkeypatch) -> None:
    defaults = Settings(
        _env_file=None,
        engagement_preview_seconds=15,
        engagement_require_go=False,
    )

    assert defaults.engagement_preview_seconds == 15
    assert defaults.engagement_require_go is False

    monkeypatch.setenv("ENGAGEMENT_PREVIEW_SECONDS", "0")
    monkeypatch.setenv("ENGAGEMENT_REQUIRE_GO", "true")
    configured = Settings(_env_file=None)

    assert configured.engagement_preview_seconds == 0
    assert configured.engagement_require_go is True


@pytest.mark.parametrize("value", [-1, 3601])
def test_engagement_preview_seconds_rejects_unsafe_bounds(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, engagement_preview_seconds=value)
