from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from humanwire.google_config import GoogleRuntimeConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    caspian_api_key: SecretStr | None = None
    caspian_base_url: str = "https://api.trycaspianai.com"
    telegram_bot_token: SecretStr | None = None
    caspian_email_username: str = "humanwire"
    featherless_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    google_cloud_project: str | None = None
    google_cloud_location: str = "global"
    google_genai_use_vertexai: bool = True
    humanwire_model_id: str = "gemini-3.5-flash"
    analytics_read_token: SecretStr | None = None
    featherless_base_url: str = "https://api.featherless.ai/v1"
    featherless_model: str = "Qwen/Qwen2.5-7B-Instruct"
    database_url: str = "sqlite:///data/humanwire.db"
    organization_path: Path = Path("data/organization.json")
    acknowledgement_seconds: int = 300
    reminder_seconds: int = 300
    mandate_timeout_seconds: int = 86_400
    engagement_preview_seconds: int = Field(default=15, ge=0, le=3_600)
    engagement_require_go: bool = False
    due_action_poll_seconds: int = 5
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    public_demo: bool = False

    @field_validator("analytics_read_token", mode="before")
    @classmethod
    def disable_blank_analytics_read_token(cls, value):
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        return None if not raw.strip() else value

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def disable_blank_gemini_api_key(cls, value):
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        return None if not raw.strip() else value

    @field_validator("google_cloud_project", mode="before")
    @classmethod
    def disable_blank_google_cloud_project(cls, value):
        if value is None:
            return None
        raw = str(value).strip()
        return raw or None

    def require_google_runtime(self) -> GoogleRuntimeConfig:
        """Return only the safe runtime projection after credential readiness checks."""
        from humanwire.google_config import GoogleAuthMode, GoogleRuntimeConfig

        if self.google_genai_use_vertexai:
            if self.google_cloud_project is None:
                raise ValueError("google_project_missing")
            return GoogleRuntimeConfig(
                model_id=self.humanwire_model_id,
                auth_mode=GoogleAuthMode.VERTEX_AI_ADC,
                project_id=self.google_cloud_project,
                location=self.google_cloud_location,
            )
        if self.gemini_api_key is None:
            raise ValueError("google_credentials_missing")
        return GoogleRuntimeConfig(
            model_id=self.humanwire_model_id,
            auth_mode=GoogleAuthMode.AI_STUDIO_KEY,
            project_id=None,
            location=self.google_cloud_location,
        )

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
