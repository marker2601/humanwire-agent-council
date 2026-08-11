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
