from __future__ import annotations

import json
from pathlib import Path

import pytest

from humanwire.cloud_iam import cloud_iam_contract
from humanwire.cloud_worker_app import build_google_worker_app
from humanwire.google_config import GoogleAuthMode

ROOT = Path(__file__).resolve().parents[2]


def test_worker_entrypoint_uses_adc_and_only_worker_safe_environment() -> None:
    requested = []
    app = build_google_worker_app(
        {
            "GOOGLE_CLOUD_PROJECT": "humanwire-demo",
            "HUMANWIRE_FIRESTORE_DATABASE": "(default)",
            "HUMANWIRE_WORKER_HOST": "humanwire-worker.example.test",
            "HUMANWIRE_MODEL_ID": "gemini-3.6-flash",
            "HUMANWIRE_GOOGLE_LOCATION": "us-central1",
            "GEMINI_API_KEY": "PRIVATE-MUST-NOT-BE-READ",
            "HUMANWIRE_PUBLIC_ORIGINS": "PRIVATE-MUST-NOT-BE-READ",
        },
        firestore_client_factory=lambda **kwargs: requested.append(kwargs) or object(),
    )

    factory = app.state.worker._decision_factory_builder()
    assert requested == [{"project": "humanwire-demo", "database": "(default)"}]
    assert factory.runtime.auth_mode is GoogleAuthMode.VERTEX_AI_ADC
    assert factory.runtime.model_id == "gemini-3.6-flash"
    assert factory.runtime.project_id == "humanwire-demo"
    assert "PRIVATE" not in repr(app.state.__dict__)


@pytest.mark.parametrize(
    "environment",
    (
        {},
        {
            "GOOGLE_CLOUD_PROJECT": "humanwire-demo",
            "HUMANWIRE_WORKER_HOST": "humanwire-worker.example.test",
            "HUMANWIRE_MODEL_ID": "gemini-3.4-flash",
        },
        {
            "GOOGLE_CLOUD_PROJECT": "humanwire-demo",
            "HUMANWIRE_WORKER_HOST": "https://humanwire-worker.example.test",
            "HUMANWIRE_MODEL_ID": "gemini-3.6-flash",
        },
    ),
)
def test_worker_entrypoint_fails_before_client_creation(environment) -> None:
    calls = []
    with pytest.raises(ValueError, match="^cloud_worker_configuration_invalid$"):
        build_google_worker_app(
            environment,
            firestore_client_factory=lambda **kwargs: calls.append(kwargs),
        )
    assert calls == []


def test_one_image_launcher_has_only_exact_web_and_worker_roles() -> None:
    source = (ROOT / "src/google_service.py").read_text(encoding="utf-8")
    assert 'HUMANWIRE_SERVICE_ROLE' in source
    assert 'role == "web"' in source
    assert 'role == "worker"' in source
    assert "service_role_invalid" in source
    assert "GEMINI_API_KEY" not in source


def test_container_is_non_root_reproducible_and_installs_google_extra() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM python:3.12.13-slim-bookworm@sha256:")
    assert 'RUN python -m pip install --no-cache-dir ".[google]"' in dockerfile
    assert "USER humanwire" in dockerfile
    assert 'CMD ["python", "-m", "uvicorn", "google_service:app"' in dockerfile
    for private in (".env", ".git", ".venv", ".superpowers", "data", "work"):
        assert private in dockerignore.splitlines()


def test_deployment_files_lock_private_worker_iam_and_bounded_scale() -> None:
    powershell = (ROOT / "infra/google/deploy.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "infra/google/deploy.sh").read_text(encoding="utf-8")
    build = (ROOT / "infra/google/cloudbuild.yaml").read_text(encoding="utf-8")
    combined = powershell + "\n" + shell
    contract = cloud_iam_contract()

    for role in (*contract.web_roles, *contract.worker_roles, *contract.push_roles):
        assert role in combined
    assert "--no-allow-unauthenticated" in combined
    assert "--allow-unauthenticated" in combined
    assert "--min-instances=0" in combined
    assert "--max-instances=1" in combined
    assert "--concurrency=1" in combined
    assert "--timeout=600" in combined
    assert "--push-auth-service-account" in combined
    assert "image@" in combined
    assert "latest" not in combined.casefold()
    assert "_IMAGE" in build and "docker" in build


def test_firestore_contract_denies_clients_and_uses_no_destructive_ttl() -> None:
    indexes = json.loads(
        (ROOT / "infra/google/firestore.indexes.json").read_text(encoding="utf-8")
    )
    rules = (ROOT / "infra/google/firestore.rules").read_text(encoding="utf-8")
    readme = (ROOT / "infra/google/README.md").read_text(encoding="utf-8")

    assert indexes == {"indexes": [], "fieldOverrides": []}
    assert "allow read, write: if false" in rules
    assert "preserve Firestore history" in readme
    assert "gcloud run services update-traffic" in readme
    assert "gcloud pubsub subscriptions update" in readme


def test_cloudbuild_and_deploy_sources_contain_no_secret_or_ambient_key() -> None:
    paths = (
        ROOT / "Dockerfile",
        ROOT / ".dockerignore",
        *(ROOT / "infra/google").glob("*"),
        ROOT / "src/google_service.py",
        ROOT / "src/google_worker_index.py",
    )
    content = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "GEMINI_API_KEY" not in content
    assert "PRIVATE KEY" not in content
    assert "allUsers" not in content
    assert "roles/owner" not in content
    assert "roles/editor" not in content
