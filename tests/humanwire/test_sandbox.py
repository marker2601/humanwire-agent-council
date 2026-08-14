from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from humanwire.__main__ import main as installed_main
from humanwire.sandbox import check_lines, checklist_lines
from humanwire.sandbox import main as sandbox_main

ROOT = Path(__file__).resolve().parents[2]
HEAD_REVISION = "0001_humanwire_schema"
SECRET_VALUES = {
    "DATABASE_URL": "postgresql+psycopg://private-user:private-pass@db.private.invalid/humanwire",
    "ORGANIZATION_PATH": "private/directory.json",
    "CASPIAN_API_KEY": "private-caspian-key",
    "TELEGRAM_BOT_TOKEN": "private-telegram-token",
    "CASPIAN_EMAIL_USERNAME": "private-email-connection",
    "ENGAGEMENT_REQUIRE_GO": "true",
    "PUBLIC_DEMO": "false",
    "HUMANWIRE_ALEMBIC_REVISION": HEAD_REVISION,
    "FEATHERLESS_API_KEY": "private-optional-model-key",
}


def _write_directory(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "people": [
                    {
                        "person_id": "operator-manager",
                        "display_name": "Operator Manager",
                        "aliases": ["Manager"],
                        "role": "Manager",
                        "department": "Operations",
                        "timezone": "America/Chicago",
                        "routes": [
                            {
                                "route_id": "operator-email",
                                "channel": "email",
                                "sender_address": "operator@example.invalid",
                                "recipient": "operator@example.invalid",
                                "preferred": True,
                            },
                            {
                                "route_id": "operator-telegram",
                                "channel": "telegram",
                                "sender_address": "operator-telegram",
                                "conversation_id": "private-conversation",
                            },
                        ],
                    },
                    {
                        "person_id": "operator-reviewer",
                        "display_name": "Operator Reviewer",
                        "aliases": ["Reviewer"],
                        "role": "Reviewer",
                        "department": "Operations",
                        "timezone": "America/Chicago",
                        "manager_id": "operator-manager",
                        "routes": [
                            {
                                "route_id": "reviewer-email",
                                "channel": "email",
                                "sender_address": "reviewer@example.invalid",
                                "recipient": "reviewer@example.invalid",
                            }
                        ],
                    },
                ],
                "initiator_policies": [
                    {
                        "person_id": "operator-manager",
                        "allowed_directions": ["downward"],
                        "allowed_departments": ["Operations"],
                        "max_upward_levels": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def sandbox_environment(tmp_path: Path) -> dict[str, str]:
    directory_path = tmp_path / "private" / "directory.json"
    _write_directory(directory_path)
    return {**SECRET_VALUES, "ORGANIZATION_PATH": str(directory_path)}


def test_check_reports_exact_safe_passes_and_counts(
    sandbox_environment: dict[str, str],
) -> None:
    assert check_lines(sandbox_environment, ROOT) == (
        "PASS CASPIAN_API_KEY",
        "PASS TELEGRAM_BOT_TOKEN",
        "PASS CASPIAN_EMAIL_USERNAME",
        "PASS DATABASE_URL",
        "PASS ORGANIZATION_PATH",
        "PASS ENGAGEMENT_REQUIRE_GO",
        "PASS PUBLIC_DEMO",
        "PASS POSTGRESQL_SCHEME",
        "PASS PRIVATE_DATABASE_CONFIG",
        "PASS PRIVATE_DIRECTORY_CONFIG",
        "PASS ALEMBIC_CONFIG",
        "PASS ALEMBIC_MIGRATION_COUNT count=1",
        "PASS HUMANWIRE_ALEMBIC_REVISION",
        "PASS DIRECTORY_PERSON_COUNT count=2",
        "PASS DIRECTORY_EMAIL_ROUTE_COUNT count=2",
        "PASS DIRECTORY_TELEGRAM_ROUTE_COUNT count=1",
        "PASS DIRECTORY_ROUTE_TYPES count=2",
        "PASS OPTIONAL_FEATHERLESS_API_KEY",
    )


def test_check_never_emits_secret_or_private_directory_values(
    sandbox_environment: dict[str, str],
) -> None:
    output = "\n".join(check_lines(sandbox_environment, ROOT))

    for value in sandbox_environment.values():
        assert value not in output
    for private_value in (
        "private-user",
        "private-pass",
        "db.private.invalid",
        "humanwire",
        "operator-manager",
        "Operator Manager",
        "operator@example.invalid",
        "private-conversation",
    ):
        assert private_value not in output


def test_featherless_is_optional_and_does_not_block_readiness(
    sandbox_environment: dict[str, str],
) -> None:
    sandbox_environment.pop("FEATHERLESS_API_KEY")

    lines = check_lines(sandbox_environment, ROOT)

    assert "PASS OPTIONAL_FEATHERLESS_API_KEY" in lines
    assert all(not line.startswith("PENDING ") for line in lines)


@pytest.mark.parametrize(
    "variable_name",
    [
        "CASPIAN_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "CASPIAN_EMAIL_USERNAME",
        "DATABASE_URL",
        "ORGANIZATION_PATH",
        "ENGAGEMENT_REQUIRE_GO",
        "PUBLIC_DEMO",
    ],
)
def test_check_fails_when_a_required_variable_is_missing(
    sandbox_environment: dict[str, str], variable_name: str
) -> None:
    sandbox_environment[variable_name] = "   "

    lines = check_lines(sandbox_environment, ROOT)

    assert f"FAIL {variable_name}" in lines


def test_check_requires_postgresql_go_and_distinct_private_config(
    sandbox_environment: dict[str, str],
) -> None:
    sandbox_environment.update(
        DATABASE_URL="sqlite:///data/humanwire.db",
        ORGANIZATION_PATH="data/organization.json",
        ENGAGEMENT_REQUIRE_GO="false",
        PUBLIC_DEMO="true",
    )

    lines = check_lines(sandbox_environment, ROOT)

    assert "FAIL POSTGRESQL_SCHEME" in lines
    assert "FAIL PRIVATE_DATABASE_CONFIG" in lines
    assert "FAIL PRIVATE_DIRECTORY_CONFIG" in lines
    assert "FAIL ENGAGEMENT_REQUIRE_GO" in lines
    assert "FAIL PUBLIC_DEMO" in lines


def test_check_requires_operator_attestation_of_the_exact_local_alembic_head(
    sandbox_environment: dict[str, str],
) -> None:
    sandbox_environment.pop("HUMANWIRE_ALEMBIC_REVISION")
    assert "PENDING HUMANWIRE_ALEMBIC_REVISION" in check_lines(
        sandbox_environment, ROOT
    )

    sandbox_environment["HUMANWIRE_ALEMBIC_REVISION"] = "stale-revision"
    assert "FAIL HUMANWIRE_ALEMBIC_REVISION" in check_lines(
        sandbox_environment, ROOT
    )


def test_check_accepts_only_the_unique_head_of_a_migration_chain(
    sandbox_environment: dict[str, str], tmp_path: Path
) -> None:
    (tmp_path / "migrations" / "versions").mkdir(parents=True)
    (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (tmp_path / "migrations" / "versions" / "0001.py").write_text(
        'revision: str = "0001"\ndown_revision: str | None = None\n',
        encoding="utf-8",
    )
    (tmp_path / "migrations" / "versions" / "0002.py").write_text(
        'revision: str = "0002"\ndown_revision: str | None = "0001"\n',
        encoding="utf-8",
    )
    sandbox_environment["HUMANWIRE_ALEMBIC_REVISION"] = "0002"

    lines = check_lines(sandbox_environment, tmp_path)

    assert "PASS ALEMBIC_MIGRATION_COUNT count=2" in lines
    assert "PASS HUMANWIRE_ALEMBIC_REVISION" in lines


def test_check_fails_closed_for_unavailable_migrations_or_invalid_directory(
    sandbox_environment: dict[str, str], tmp_path: Path
) -> None:
    assert "FAIL ALEMBIC_CONFIG" in check_lines(sandbox_environment, tmp_path)

    sandbox_environment["ORGANIZATION_PATH"] = str(tmp_path / "missing.json")
    lines = check_lines(sandbox_environment, ROOT)
    assert "FAIL ORGANIZATION_PATH" in lines
    assert "FAIL DIRECTORY_PERSON_COUNT" in lines
    assert "FAIL DIRECTORY_EMAIL_ROUTE_COUNT" in lines
    assert "FAIL DIRECTORY_TELEGRAM_ROUTE_COUNT" in lines
    assert "FAIL DIRECTORY_ROUTE_TYPES" in lines


def test_check_uses_only_the_supplied_environment_mapping(
    sandbox_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in SECRET_VALUES.items():
        monkeypatch.setenv(name, f"ambient-{value}")

    isolated = dict(sandbox_environment)
    isolated.pop("CASPIAN_API_KEY")

    assert "FAIL CASPIAN_API_KEY" in check_lines(isolated, ROOT)


def test_check_is_read_only_and_does_not_call_external_or_persistence_boundaries(
    sandbox_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("sandbox readiness crossed a side-effect boundary")

    import humanwire.caspian_gateway as gateway
    from humanwire import database, model_client, repository

    monkeypatch.setattr(gateway.CaspianGateway, "connect", forbidden)
    monkeypatch.setattr(gateway.CaspianGateway, "listen", forbidden)
    monkeypatch.setattr(database, "create_session_factory", forbidden)
    monkeypatch.setattr(database.Base.metadata, "create_all", forbidden)
    monkeypatch.setattr(model_client.FeatherlessJsonClient, "complete_json", forbidden)
    monkeypatch.setattr(repository.SqlAlchemyHumanWireRepository, "transaction", forbidden)

    before = {
        path: path.read_bytes()
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.parts and ".venv" not in path.parts
    }
    assert all(line.startswith("PASS ") for line in check_lines(sandbox_environment, ROOT))
    after = {path: path.read_bytes() for path in before}
    assert after == before


def test_checklist_is_ordered_pending_operator_proof_with_safe_boundaries() -> None:
    assert checklist_lines() == (
        "PENDING OPERATOR_PRIVATE_DEPLOYMENT",
        "PENDING OPERATOR_MANAGED_POSTGRESQL",
        "PENDING OPERATOR_ALEMBIC_UPGRADE_HEAD",
        "PENDING OPERATOR_CASPIAN_PROJECT",
        "PENDING OPERATOR_EMAIL_CONNECTION",
        "PENDING OPERATOR_TELEGRAM_BOT",
        "PENDING OPERATOR_CONSENTING_IDENTITIES",
        "PENDING OPERATOR_SINGLE_LISTENER",
        "PENDING PROOF_FLOW_1",
        "PENDING PROOF_FLOW_2",
        "PENDING PROOF_FLOW_3",
        "PENDING PROOF_INFORM_ACK_QUICK_CONFIRM",
        "PENDING PROOF_EMAIL_TELEGRAM_CONFIRM",
        "PENDING PROOF_APPROVAL_ALTERNATE_PROPOSAL_SCHEDULING",
        "PENDING PROOF_READ_ONLY_PROJECTIONS",
        "PENDING RETENTION_BOUNDARY",
        "PENDING EVIDENCE_BOUNDARY",
        "PENDING LIVE_PROVIDER_VERIFIED_AFTER_THREE_FLOWS",
    )


def test_cli_delegates_without_loading_settings_or_starting_services(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "humanwire.sandbox.main", lambda argv: calls.append(argv[0]) or 2
    )

    assert installed_main(["sandbox", "check"]) == 2
    assert installed_main(["sandbox", "checklist"]) == 2
    assert calls == ["check", "checklist"]
    assert capsys.readouterr().out == ""


def test_sandbox_module_main_returns_pending_and_failure_statuses(
    sandbox_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(os, "environ", sandbox_environment)
    assert sandbox_main(["check"]) == 0
    assert capsys.readouterr().err == ""

    sandbox_environment.pop("HUMANWIRE_ALEMBIC_REVISION")
    assert sandbox_main(["check"]) == 2
    assert "PENDING HUMANWIRE_ALEMBIC_REVISION" in capsys.readouterr().out

    sandbox_environment.pop("CASPIAN_API_KEY")
    assert sandbox_main(["check"]) == 1
    assert "FAIL CASPIAN_API_KEY" in capsys.readouterr().out

    assert sandbox_main(["checklist"]) == 2
    assert capsys.readouterr().out == "\n".join(checklist_lines()) + "\n"


@pytest.mark.parametrize(
    "command",
    [
        [
            str(
                Path(sys.executable).with_name(
                    "humanwire.exe" if os.name == "nt" else "humanwire"
                )
            ),
            "sandbox",
            "checklist",
        ],
        [sys.executable, "-I", "-m", "humanwire", "sandbox", "checklist"],
        [sys.executable, "-I", "-m", "humanwire.sandbox", "checklist"],
    ],
)
def test_installed_cli_and_module_entrypoints_are_available_outside_repository(
    command: list[str], tmp_path: Path
) -> None:
    result = subprocess.run(
        command,
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout == "\n".join(checklist_lines()) + "\n"
    assert result.stderr == ""
