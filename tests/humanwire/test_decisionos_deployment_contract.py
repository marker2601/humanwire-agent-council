from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from humanwire import decisionos_web

ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_decisionos_module_import_is_provider_lazy() -> None:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("HUMANWIRE_DECISIONOS_") or name in {
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
        }:
            environment.pop(name)

    result = subprocess.run(
        [sys.executable, "-c", "import humanwire.decisionos_web; print('import-safe')"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "import-safe"
    assert result.stderr == ""


def test_disabled_decisionos_import_does_not_require_organization_parsers() -> None:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("HUMANWIRE_DECISIONOS_"):
            environment.pop(name)
    script = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.', 1)[0] in {'openpyxl', 'pypdf'}:
        raise ModuleNotFoundError(f'blocked optional dependency: {name}')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import humanwire.decisionos_web
print('organization-parsers-lazy')
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "organization-parsers-lazy"
    assert result.stderr == ""


def test_health_check_is_fixed_and_does_not_construct_firebase(monkeypatch) -> None:
    def forbidden_build():
        raise AssertionError("health must not initialize Firebase")

    monkeypatch.setattr(decisionos_web, "build_decisionos_web_app", forbidden_build)
    client = TestClient(decisionos_web.app, base_url="https://decisionos.test")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "project" not in response.text.casefold()
    assert "service" not in response.text.casefold()
    assert "account" not in response.text.casefold()
    assert response.headers["cache-control"] == "no-store"


def test_production_settings_bind_only_explicit_decisionos_environment(monkeypatch) -> None:
    values = {
        "PROJECT_ID": "humanwire-startup",
        "ALLOWED_HOSTS": "decisionos.example.com;humanwire-startup.firebaseapp.com",
        "FIREBASE_API_KEY": "public-web-key",
        "FIREBASE_APP_ID": "1:123:web:abc",
        "FIREBASE_AUTH_DOMAIN": "humanwire-startup.firebaseapp.com",
        "FIREBASE_STORAGE_BUCKET": "humanwire-startup.firebasestorage.app",
        "APP_CHECK_SITE_KEY": "recaptcha-site-key",
        "APP_CHECK_ENFORCED": "false",
    }
    for suffix, value in values.items():
        monkeypatch.setenv(f"HUMANWIRE_DECISIONOS_{suffix}", value)

    settings = decisionos_web.DecisionOSSettings()

    assert settings.allowed_host_set == frozenset(
        {"decisionos.example.com", "humanwire-startup.firebaseapp.com"}
    )
    assert settings.app_check_enforced is False
    assert settings.organization_features_enabled is False
    assert settings.council_features_enabled is False
    assert settings.mission_features_enabled is False
    assert settings.council_model_id == "gemini-3.5-flash"
    assert settings.firebase_public_config == {
        "firebase": {
            "apiKey": "public-web-key",
            "appId": "1:123:web:abc",
            "authDomain": "humanwire-startup.firebaseapp.com",
            "projectId": "humanwire-startup",
            "storageBucket": "humanwire-startup.firebasestorage.app",
        },
        "appCheckSiteKey": "recaptcha-site-key",
    }


def test_organization_feature_setting_is_explicit_and_disabled_by_default(monkeypatch) -> None:
    values = {
        "PROJECT_ID": "humanwire-startup",
        "ALLOWED_HOSTS": "decisionos.example.com",
        "FIREBASE_API_KEY": "public-web-key",
        "FIREBASE_APP_ID": "1:123:web:abc",
        "FIREBASE_AUTH_DOMAIN": "humanwire-startup.firebaseapp.com",
        "APP_CHECK_SITE_KEY": "recaptcha-site-key",
    }
    for suffix, value in values.items():
        monkeypatch.setenv(f"HUMANWIRE_DECISIONOS_{suffix}", value)

    disabled = decisionos_web.DecisionOSSettings()
    monkeypatch.setenv("HUMANWIRE_DECISIONOS_ORGANIZATION_FEATURES_ENABLED", "true")
    enabled = decisionos_web.DecisionOSSettings()

    assert disabled.organization_features_enabled is False
    assert enabled.organization_features_enabled is True


def test_council_feature_setting_is_explicit_and_disabled_by_default(monkeypatch) -> None:
    values = {
        "PROJECT_ID": "humanwire-startup",
        "ALLOWED_HOSTS": "decisionos.example.com",
        "FIREBASE_API_KEY": "public-web-key",
        "FIREBASE_APP_ID": "1:123:web:abc",
        "FIREBASE_AUTH_DOMAIN": "humanwire-startup.firebaseapp.com",
        "APP_CHECK_SITE_KEY": "recaptcha-site-key",
    }
    for suffix, value in values.items():
        monkeypatch.setenv(f"HUMANWIRE_DECISIONOS_{suffix}", value)

    disabled = decisionos_web.DecisionOSSettings()
    monkeypatch.setenv("HUMANWIRE_DECISIONOS_COUNCIL_FEATURES_ENABLED", "true")
    enabled = decisionos_web.DecisionOSSettings()

    assert disabled.council_features_enabled is False
    assert enabled.council_features_enabled is True
    assert enabled.council_model_id == "gemini-3.5-flash"
    assert enabled.council_google_location == "global"


def test_mission_feature_requires_the_council_and_is_disabled_by_default(
    monkeypatch,
) -> None:
    values = {
        "PROJECT_ID": "humanwire-startup",
        "ALLOWED_HOSTS": "decisionos.example.com",
        "FIREBASE_API_KEY": "public-web-key",
        "FIREBASE_APP_ID": "1:123:web:abc",
        "FIREBASE_AUTH_DOMAIN": "humanwire-startup.firebaseapp.com",
        "APP_CHECK_SITE_KEY": "recaptcha-site-key",
    }
    for suffix, value in values.items():
        monkeypatch.setenv(f"HUMANWIRE_DECISIONOS_{suffix}", value)

    assert decisionos_web.DecisionOSSettings().mission_features_enabled is False
    monkeypatch.setenv("HUMANWIRE_DECISIONOS_MISSION_FEATURES_ENABLED", "true")
    with pytest.raises(ValueError, match="mission feature requires the council"):
        decisionos_web.DecisionOSSettings()
    monkeypatch.setenv("HUMANWIRE_DECISIONOS_COUNCIL_FEATURES_ENABLED", "true")
    enabled = decisionos_web.DecisionOSSettings()
    assert enabled.mission_features_enabled is True


def test_container_keeps_existing_roles_and_installs_decisionos_runtime() -> None:
    dockerfile = _source("Dockerfile")
    selector = _source("src/google_service.py")

    assert '".[google,decisionos]"' in dockerfile
    assert 'role == "web"' in selector
    assert 'role == "worker"' in selector
    assert 'role == "decisionos"' in selector
    assert "humanwire.decisionos_web" in selector


def test_container_selector_import_is_decisionos_provider_lazy() -> None:
    environment = os.environ.copy()
    environment["HUMANWIRE_SERVICE_ROLE"] = "decisionos"
    for name in tuple(environment):
        if name.startswith("HUMANWIRE_DECISIONOS_"):
            environment.pop(name)

    result = subprocess.run(
        [sys.executable, "-c", "import google_service; print('selector-safe')"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "selector-safe"
    assert result.stderr == ""


def test_decisionos_deployments_are_separate_secret_bound_and_monitor_first() -> None:
    for relative in (
        "infra/google/deploy-decisionos.ps1",
        "infra/google/deploy-decisionos.sh",
    ):
        source = _source(relative)
        folded = source.casefold()

        assert "humanwire-decisionos" in source
        assert "humanwire-decisionos@" in source
        assert "humanwire-web" not in source
        assert "humanwire-worker" not in source
        assert "--set-secrets" in source
        assert "HUMANWIRE_DECISIONOS_FIREBASE_API_KEY" in source
        assert "HUMANWIRE_DECISIONOS_FIREBASE_APP_ID" in source
        assert "HUMANWIRE_DECISIONOS_APP_CHECK_SITE_KEY" in source
        assert "HUMANWIRE_DECISIONOS_APP_CHECK_ENFORCED=false" in source
        assert "firebase-tools@15.27.0" in source
        assert "firestore:rules" in source
        assert "firestore:indexes" in source
        assert "storage" in source
        assert "hosting" in source
        assert ".firebaseapp.com" in source
        assert ".web.app" in source
        assert "roles/datastore.user" in source
        assert "roles/firebaseauth.admin" in source
        assert "roles/logging.logWriter" in source
        assert "roles/aiplatform.user" in source
        assert "roles/secretmanager.secretAccessor" in source
        assert "firebasestorage.googleapis.com" in source
        assert "recaptchaenterprise.googleapis.com" in source
        assert "--allow-unauthenticated" in source
        assert "--min-instances=0" in source
        assert "gemini-3.5-flash" in folded
        assert "HUMANWIRE_DECISIONOS_COUNCIL_FEATURES_ENABLED=true" in source
        assert "HUMANWIRE_DECISIONOS_MISSION_FEATURES_ENABLED=true" in source
        assert "HUMANWIRE_DECISIONOS_COUNCIL_MODEL_ID" in source
        assert "HUMANWIRE_DECISIONOS_COUNCIL_GOOGLE_LOCATION" in source
        assert "aiplatform.googleapis.com" in source
        assert "featherless" not in folded
        assert "caspi" not in folded
        assert "AIza" not in source


def test_firebase_config_deploys_rules_and_indexes() -> None:
    firebase = _source("infra/firebase/firebase.json")
    config = json.loads(firebase)
    indexes = _source("infra/firebase/firestore.indexes.json")

    assert '"rules": "firestore.rules"' in firebase
    assert '"indexes": "firestore.indexes.json"' in firebase
    assert '"rules": "storage.rules"' in firebase
    assert '"indexes": []' in indexes
    assert '"collectionGroup": "members"' in indexes
    assert '"fieldPath": "uid"' in indexes
    assert '"queryScope": "COLLECTION_GROUP"' in indexes
    assert config["hosting"]["public"] == "hosting-public"
    assert config["hosting"]["rewrites"] == [
        {
            "source": "**",
            "run": {
                "serviceId": "humanwire-decisionos",
                "region": "us-central1",
            },
        }
    ]




def test_deployment_documentation_preserves_the_submitted_services() -> None:
    documentation = _source("infra/google/README.md")
    normalized = " ".join(documentation.split())

    assert "HumanWire DecisionOS" in documentation
    assert "humanwire-decisionos" in documentation
    assert "does not update or route traffic to `humanwire-web`" in normalized
    assert "App Check" in documentation
    assert "monitor" in documentation.casefold()
    assert "`/health`" in documentation
