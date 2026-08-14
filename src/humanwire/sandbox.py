"""Privacy-safe, read-only readiness checks for a private HumanWire sandbox."""

from __future__ import annotations

import argparse
import ast
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit

from humanwire.directory import OrganizationDocument

_REQUIRED_VARIABLES = (
    "CASPIAN_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "CASPIAN_EMAIL_USERNAME",
    "DATABASE_URL",
    "ORGANIZATION_PATH",
    "ENGAGEMENT_REQUIRE_GO",
    "PUBLIC_DEMO",
)
_REVISION_VARIABLE = "HUMANWIRE_ALEMBIC_REVISION"
_OPTIONAL_MODEL_VARIABLE = "FEATHERLESS_API_KEY"
_PUBLIC_DATABASE_PATH = "data/humanwire.db"
_PUBLIC_DIRECTORY_PATH = "data/organization.json"

_CHECKLIST = (
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


def _present(environment: Mapping[str, str], name: str) -> bool:
    return bool(environment.get(name, "").strip())


def _status(ok: bool, requirement: str) -> str:
    return f"{'PASS' if ok else 'FAIL'} {requirement}"


def _is_postgresql(database_url: str) -> bool:
    try:
        return urlsplit(database_url).scheme in {"postgresql", "postgresql+psycopg"}
    except ValueError:
        return False


def _resolved_path(raw_path: str, repository_root: Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else repository_root / path


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


def _migration_revisions(
    repository_root: Path,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    config_path = repository_root / "alembic.ini"
    versions_path = repository_root / "migrations" / "versions"
    if not config_path.is_file() or not versions_path.is_dir():
        return False, (), ()

    revisions: list[str] = []
    predecessors: set[str] = set()
    for migration_path in sorted(versions_path.glob("*.py")):
        try:
            tree = ast.parse(migration_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return False, (), ()
        values: dict[str, object] = {}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value_node = node.value
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in {
                        "revision",
                        "down_revision",
                    }:
                        try:
                            values[target.id] = ast.literal_eval(value_node)
                        except (ValueError, TypeError):
                            return False, (), ()
        revision = values.get("revision")
        down_revision = values.get("down_revision")
        if not isinstance(revision, str) or not revision:
            return False, (), ()
        revisions.append(revision)
        if isinstance(down_revision, str):
            predecessors.add(down_revision)
        elif isinstance(down_revision, tuple) and all(
            isinstance(item, str) for item in down_revision
        ):
            predecessors.update(down_revision)
        elif down_revision is not None:
            return False, (), ()

    revision_set = set(revisions)
    heads = tuple(sorted(revision_set - predecessors))
    valid = (
        bool(revisions)
        and len(revision_set) == len(revisions)
        and predecessors <= revision_set
        and len(heads) == 1
    )
    return valid, tuple(revisions), heads


def _directory_counts(
    environment: Mapping[str, str], repository_root: Path
) -> tuple[bool, tuple[int, int, int, int]]:
    raw_path = environment.get("ORGANIZATION_PATH", "").strip()
    if not raw_path:
        return False, (0, 0, 0, 0)
    try:
        document = OrganizationDocument.model_validate_json(
            _resolved_path(raw_path, repository_root).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return False, (0, 0, 0, 0)

    email_routes = 0
    telegram_routes = 0
    route_types: set[str] = set()
    for person in document.people:
        for route in person.routes:
            channel = route.channel.value
            route_types.add(channel)
            if channel == "email" and route.recipient:
                email_routes += 1
            elif channel == "telegram" and route.conversation_id:
                telegram_routes += 1
    return True, (len(document.people), email_routes, telegram_routes, len(route_types))


def check_lines(
    environment: Mapping[str, str], repository_root: Path | None = None
) -> tuple[str, ...]:
    """Return safe static readiness lines without opening external resources."""
    root = Path(__file__).resolve().parents[2] if repository_root is None else repository_root
    database_url = environment.get("DATABASE_URL", "").strip()
    organization_path = environment.get("ORGANIZATION_PATH", "").strip()
    directory_valid, counts = _directory_counts(environment, root)
    required_status = {
        name: _present(environment, name) for name in _REQUIRED_VARIABLES
    }
    required_status["ORGANIZATION_PATH"] = directory_valid
    required_status["ENGAGEMENT_REQUIRE_GO"] = (
        environment.get("ENGAGEMENT_REQUIRE_GO", "").strip().casefold() == "true"
    )
    required_status["PUBLIC_DEMO"] = (
        environment.get("PUBLIC_DEMO", "").strip().casefold() == "false"
    )
    lines = [_status(required_status[name], name) for name in _REQUIRED_VARIABLES]
    lines.extend(
        (
            _status(_is_postgresql(database_url), "POSTGRESQL_SCHEME"),
            _status(
                bool(database_url)
                and not database_url.replace("\\", "/").endswith(_PUBLIC_DATABASE_PATH),
                "PRIVATE_DATABASE_CONFIG",
            ),
            _status(
                bool(organization_path)
                and not _same_path(
                    _resolved_path(organization_path, root),
                    root / _PUBLIC_DIRECTORY_PATH,
                ),
                "PRIVATE_DIRECTORY_CONFIG",
            ),
        )
    )

    migration_available, revisions, migration_heads = _migration_revisions(root)
    lines.append(_status(migration_available, "ALEMBIC_CONFIG"))
    lines.append(
        f"{'PASS' if migration_available else 'FAIL'} ALEMBIC_MIGRATION_COUNT count={len(revisions)}"
    )
    supplied_revision = environment.get(_REVISION_VARIABLE, "").strip()
    if not supplied_revision:
        lines.append(f"PENDING {_REVISION_VARIABLE}")
    else:
        lines.append(
            _status(
                migration_available
                and supplied_revision == migration_heads[0],
                _REVISION_VARIABLE,
            )
        )

    person_count, email_count, telegram_count, type_count = counts
    if not directory_valid:
        lines.extend(
            f"FAIL {requirement}"
            for requirement in (
                "DIRECTORY_PERSON_COUNT",
                "DIRECTORY_EMAIL_ROUTE_COUNT",
                "DIRECTORY_TELEGRAM_ROUTE_COUNT",
                "DIRECTORY_ROUTE_TYPES",
            )
        )
    else:
        lines.extend(
            (
                f"PASS DIRECTORY_PERSON_COUNT count={person_count}",
                f"{'PASS' if email_count else 'FAIL'} DIRECTORY_EMAIL_ROUTE_COUNT count={email_count}",
                f"{'PASS' if telegram_count else 'FAIL'} DIRECTORY_TELEGRAM_ROUTE_COUNT count={telegram_count}",
                f"{'PASS' if type_count == 2 else 'FAIL'} DIRECTORY_ROUTE_TYPES count={type_count}",
            )
        )
    lines.append(f"PASS OPTIONAL_{_OPTIONAL_MODEL_VARIABLE}")
    return tuple(lines)


def checklist_lines() -> tuple[str, ...]:
    """Return the ordered, non-live operator proof prerequisites."""
    return _CHECKLIST


def _exit_code(lines: Sequence[str]) -> int:
    if any(line.startswith("FAIL ") for line in lines):
        return 1
    if any(line.startswith("PENDING ") for line in lines):
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="humanwire sandbox")
    parser.add_argument("command", choices=("check", "checklist"))
    arguments = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    lines = check_lines(os.environ) if arguments.command == "check" else checklist_lines()
    for line in lines:
        print(line)
    return _exit_code(lines)


if __name__ == "__main__":
    raise SystemExit(main())
