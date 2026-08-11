from pathlib import Path

import pytest
from pydantic import SecretStr

from secondsignal.config import Settings


def test_listener_credentials_are_required_only_for_listener() -> None:
    settings = Settings(
        database_url="sqlite:///data/test.db",
        registry_path=Path("data/test-identities.json"),
    )

    with pytest.raises(ValueError, match="CASPIAN_API_KEY"):
        settings.require_listener_credentials()


def test_listener_credentials_return_plain_values() -> None:
    settings = Settings(
        caspian_api_key=SecretStr("caspian-key"),
        telegram_bot_token=SecretStr("telegram-token"),
    )

    assert settings.require_listener_credentials() == (
        "caspian-key",
        "telegram-token",
    )
