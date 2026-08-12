import pytest
from pydantic import SecretStr

from humanwire.config import Settings


def test_listener_credentials_are_required_only_when_listening() -> None:
    with pytest.raises(ValueError, match="CASPIAN_API_KEY"):
        Settings().require_listener_credentials()


def test_listener_credentials_return_plain_values() -> None:
    settings = Settings(
        caspian_api_key=SecretStr("caspian"),
        telegram_bot_token=SecretStr("telegram"),
    )
    assert settings.require_listener_credentials() == ("caspian", "telegram")


def test_analytics_read_token_is_secret_and_optional() -> None:
    assert Settings(_env_file=None).analytics_read_token is None
    configured = Settings(_env_file=None, analytics_read_token="fictional-read-token")

    assert configured.analytics_read_token is not None
    assert configured.analytics_read_token.get_secret_value() == "fictional-read-token"
    assert "fictional-read-token" not in repr(configured)


@pytest.mark.parametrize("raw", ["", " ", "\t\r\n"])
def test_blank_analytics_read_token_is_disabled(raw: str) -> None:
    assert Settings(_env_file=None, analytics_read_token=raw).analytics_read_token is None
