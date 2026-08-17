from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from humanwire.config import Settings
from humanwire.google_config import GoogleAuthMode, GoogleRuntimeConfig
from humanwire.studio_models import CoordinationRequest, StudioAgentMode
from tests.humanwire.studio_fixtures import launch_request


@pytest.mark.parametrize(
    ("model_id", "accepted"),
    [
        ("gemini-3.5-pro", True),
        ("gemini-3.6-flash", True),
        ("gemini-4.0-flash", True),
        ("gemini-3.4-pro", False),
        ("gemini-2.5-pro", False),
        ("other-3.6-flash", False),
        ("gemini-latest", False),
    ],
)
def test_google_runtime_accepts_only_qualifying_gemini_models(
    model_id: str,
    accepted: bool,
) -> None:
    values = {
        "model_id": model_id,
        "auth_mode": GoogleAuthMode.VERTEX_AI_ADC,
        "project_id": "judge-project",
        "location": "us-central1",
    }

    if accepted:
        assert GoogleRuntimeConfig.model_validate(values).model_id == model_id
    else:
        with pytest.raises(ValidationError):
            GoogleRuntimeConfig.model_validate(values)


def test_vertex_runtime_requires_project_and_returns_no_secret() -> None:
    settings = Settings(
        _env_file=None,
        google_cloud_project="judge-project",
        google_cloud_location="us-central1",
        google_genai_use_vertexai=True,
        humanwire_model_id="gemini-3.6-flash",
        gemini_api_key=SecretStr("PRIVATE-GEMINI-SENTINEL"),
    )

    runtime = settings.require_google_runtime()

    assert runtime == GoogleRuntimeConfig(
        model_id="gemini-3.6-flash",
        auth_mode=GoogleAuthMode.VERTEX_AI_ADC,
        project_id="judge-project",
        location="us-central1",
    )
    assert "PRIVATE-GEMINI-SENTINEL" not in repr(runtime)
    assert "PRIVATE-GEMINI-SENTINEL" not in runtime.model_dump_json()
    with pytest.raises(ValueError, match="google_project_missing"):
        Settings(_env_file=None, google_genai_use_vertexai=True).require_google_runtime()


@pytest.mark.parametrize("raw", [None, "", " \t\r\n"])
def test_ai_studio_runtime_requires_a_nonblank_key_without_returning_it(
    raw: str | None,
) -> None:
    settings = Settings(
        _env_file=None,
        google_genai_use_vertexai=False,
        gemini_api_key=raw,
    )

    with pytest.raises(ValueError, match="google_credentials_missing"):
        settings.require_google_runtime()

    configured = Settings(
        _env_file=None,
        google_genai_use_vertexai=False,
        gemini_api_key=SecretStr("PRIVATE-AI-STUDIO-SENTINEL"),
    )
    runtime = configured.require_google_runtime()
    assert runtime.auth_mode is GoogleAuthMode.AI_STUDIO_KEY
    assert runtime.project_id is None
    assert "PRIVATE-AI-STUDIO-SENTINEL" not in repr(runtime)
    assert "PRIVATE-AI-STUDIO-SENTINEL" not in runtime.model_dump_json()


def test_coordination_request_accepts_the_explicit_google_adk_mode() -> None:
    request = CoordinationRequest.model_validate(
        launch_request(agent_mode="google_adk").model_dump(mode="json")
    )

    assert request.agent_mode is StudioAgentMode.GOOGLE_ADK


def test_google_extra_declares_the_qualifying_runtime_dependencies() -> None:
    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    metadata = tomllib.loads(project.read_text(encoding="utf-8"))

    assert metadata["project"]["optional-dependencies"]["google"] == [
        "google-adk[gcp]>=2.0.0,<3.0.0",
        "google-cloud-firestore>=2.28,<3.0",
        "google-cloud-pubsub>=2.0,<3.0",
        "google-cloud-logging>=3.0,<4.0",
    ]
