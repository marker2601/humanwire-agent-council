import json

from secondsignal.config import Settings
from secondsignal.container import ApplicationContainer
from secondsignal.risk import FeatherlessRiskAnalyzer, RuleBasedRiskAnalyzer


def write_registry(path) -> None:
    path.write_text(
        json.dumps(
            {
                "authorized_reporters": {"telegram": [], "email": []},
                "identities": [],
            }
        ),
        encoding="utf-8",
    )


def test_container_builds_offline_rule_analyzer_without_opening_channels(tmp_path):
    registry_path = tmp_path / "identities.json"
    write_registry(registry_path)
    settings = Settings(database_url="sqlite://", registry_path=registry_path)

    container = ApplicationContainer.build(settings)

    assert isinstance(container.analyzer, RuleBasedRiskAnalyzer)
    assert container.repository.list_recent() == []


def test_container_selects_featherless_when_key_is_configured(tmp_path):
    registry_path = tmp_path / "identities.json"
    write_registry(registry_path)
    settings = Settings(
        database_url="sqlite://",
        registry_path=registry_path,
        featherless_api_key="featherless-test-key",
    )

    container = ApplicationContainer.build(settings)

    assert isinstance(container.analyzer, FeatherlessRiskAnalyzer)
