from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    caspian_api_key: SecretStr | None = None
    caspian_base_url: str = "https://api.trycaspianai.com"
    telegram_bot_token: SecretStr | None = None
    caspian_email_username: str = "humanwire"
    featherless_api_key: SecretStr | None = None
    analytics_read_token: SecretStr | None = None
    featherless_base_url: str = "https://api.featherless.ai/v1"
    featherless_model: str = "Qwen/Qwen2.5-7B-Instruct"
    database_url: str = "sqlite:///data/humanwire.db"
    organization_path: Path = Path("data/organization.json")
    acknowledgement_seconds: int = 300
    reminder_seconds: int = 300
    mandate_timeout_seconds: int = 86_400
    due_action_poll_seconds: int = 5
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    public_demo: bool = False

    def require_listener_credentials(self) -> tuple[str, str]:
        missing = []
        if self.caspian_api_key is None:
            missing.append("CASPIAN_API_KEY")
        if self.telegram_bot_token is None:
            missing.append("TELEGRAM_BOT_TOKEN")
        if missing:
            raise ValueError("Missing listener credentials: " + ", ".join(missing))
        return (
            self.caspian_api_key.get_secret_value(),
            self.telegram_bot_token.get_secret_value(),
        )
