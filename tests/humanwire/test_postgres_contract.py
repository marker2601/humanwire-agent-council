from __future__ import annotations

import importlib.util
import os
import re
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable, UniqueConstraint

from humanwire.database import Base
from humanwire.domain import MandateState
from humanwire.repository import SqlAlchemyHumanWireRepository

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "migrations" / "versions" / "0001_humanwire_schema.py"
POSTGRES_TEST_URL_ENV = "HUMANWIRE_TEST_POSTGRES_URL"
POSTGRES_TEST_SCHEMA = re.compile(r"humanwire_test_[0-9a-f]{32}\Z")


EXPECTED_COLUMNS = {
    "hw_mandates": (
        ("mandate_id", "string:36", False, True),
        ("token", "string:32", False, False),
        ("initiator_id", "string:128", False, False),
        ("origin_channel", "string:32", False, False),
        ("origin_conversation_id", "string:255", False, False),
        ("origin_message_id", "string:255", False, False),
        ("redacted_request", "text", False, False),
        ("objective", "text", False, False),
        ("plan", "json", False, False),
        ("state", "string:40", False, False),
        ("reason", "text", True, False),
        ("next_action_at", "datetime:tz", True, False),
        ("created_at", "datetime:tz", False, False),
        ("updated_at", "datetime:tz", False, False),
        ("expires_at", "datetime:tz", False, False),
        ("completed_at", "datetime:tz", True, False),
        ("idempotency_key", "string:128", False, False),
    ),
    "hw_assignments": (
        ("assignment_id", "string:36", False, True),
        ("mandate_id", "string:36", False, False),
        ("person_id", "string:128", False, False),
        ("department", "string:128", False, False),
        ("direction", "string:32", False, False),
        ("reason", "text", False, False),
        ("required", "boolean", False, False),
        ("engagement_type", "string:40", False, False),
        ("response_required", "boolean", False, False),
        ("state", "string:40", False, False),
        ("route_ids", "json", False, False),
        ("active_route_index", "integer", False, False),
        ("attempt_count", "integer", False, False),
        ("interview_id", "string:36", True, False),
        ("first_contact_at", "datetime:tz", True, False),
        ("last_delivery_at", "datetime:tz", True, False),
        ("next_action_at", "datetime:tz", True, False),
        ("acknowledged_at", "datetime:tz", True, False),
        ("completed_at", "datetime:tz", True, False),
        ("failure_reason", "text", True, False),
    ),
    "hw_release_outbox": (
        ("outbox_id", "string:64", False, True),
        ("mandate_id", "string:36", False, False),
        ("assignment_id", "string:36", False, False),
        ("delivery_id", "string:48", False, False),
        ("attempt_count", "integer", False, False),
        ("route_index", "integer", False, False),
        ("state", "string:16", False, False),
        ("claim_owner", "string:36", True, False),
        ("claimed_at", "datetime:tz", True, False),
        ("created_at", "datetime:tz", False, False),
        ("completed_at", "datetime:tz", True, False),
    ),
    "hw_interviews": (
        ("session_id", "string:36", False, True),
        ("mandate_id", "string:36", False, False),
        ("assignment_id", "string:36", False, False),
        ("stakeholder_person_id", "string:128", False, False),
        ("questions", "json", False, False),
        ("current_question_index", "integer", False, False),
        ("current_channel", "string:32", True, False),
        ("current_route_id", "string:128", True, False),
        ("current_conversation_id", "string:255", True, False),
        ("channel_history", "json", False, False),
        ("default_visibility", "string:32", False, False),
        ("acknowledged_at", "datetime:tz", True, False),
        ("started_at", "datetime:tz", False, False),
        ("updated_at", "datetime:tz", False, False),
        ("completed_at", "datetime:tz", True, False),
    ),
    "hw_evidence": (
        ("evidence_id", "string:36", False, True),
        ("mandate_id", "string:36", False, False),
        ("assignment_id", "string:36", False, False),
        ("stakeholder_id", "string:128", False, False),
        ("evidence_type", "string:40", False, False),
        ("statement", "text", False, False),
        ("visibility", "string:32", False, False),
        ("status", "string:32", False, False),
        ("source_message_id", "string:255", False, False),
        ("channel", "string:32", False, False),
        ("created_at", "datetime:tz", False, False),
        ("related_decision", "text", True, False),
        ("deadline", "datetime:tz", True, False),
        ("resource", "text", True, False),
    ),
    "hw_issues": (
        ("issue_id", "string:36", False, True),
        ("mandate_id", "string:36", False, False),
        ("issue_type", "string:40", False, False),
        ("evidence_ids", "json", False, False),
        ("stakeholder_ids", "json", False, False),
        ("related_decision", "text", True, False),
        ("summary", "text", False, False),
        ("blocking", "boolean", False, False),
        ("resolution", "text", True, False),
    ),
    "hw_proposals": (
        ("proposal_id", "string:36", False, True),
        ("mandate_id", "string:36", False, False),
        ("round_number", "integer", False, False),
        ("text", "text", False, False),
        ("issue_ids", "json", False, False),
        ("required_respondent_ids", "json", False, False),
        ("state", "string:32", False, False),
        ("created_at", "datetime:tz", False, False),
        ("expires_at", "datetime:tz", False, False),
    ),
    "hw_proposal_responses": (
        ("receipt_order", "integer", False, True),
        ("response_id", "string:36", False, False),
        ("proposal_id", "string:36", False, False),
        ("stakeholder_id", "string:128", False, False),
        ("response", "string:32", False, False),
        ("change_text", "text", True, False),
        ("source_message_id", "string:255", False, False),
        ("created_at", "datetime:tz", False, False),
        ("idempotency_key", "string:128", False, False),
    ),
    "hw_engagement_decisions": (
        ("decision_id", "string:36", False, True),
        ("mandate_id", "string:36", False, False),
        ("assignment_id", "string:36", False, False),
        ("stakeholder_id", "string:128", False, False),
        ("response", "string:32", False, False),
        ("change_text", "text", True, False),
        ("source_message_id", "string:255", False, False),
        ("created_at", "datetime:tz", False, False),
        ("idempotency_key", "string:128", False, False),
    ),
    "hw_meeting_packages": (
        ("meeting_id", "string:36", False, True),
        ("mandate_id", "string:36", False, False),
        ("purpose", "text", False, False),
        ("decision_owner_id", "string:128", False, False),
        ("required_attendee_ids", "json", False, False),
        ("optional_attendee_ids", "json", False, False),
        ("proposed_start", "datetime:tz", True, False),
        ("proposed_end", "datetime:tz", True, False),
        ("timezone", "string:64", False, False),
        ("agreed_facts", "json", False, False),
        ("open_decisions", "json", False, False),
        ("agenda", "json", False, False),
        ("pre_read_evidence_ids", "json", False, False),
        ("calendar_written", "boolean", False, False),
        ("created_at", "datetime:tz", False, False),
    ),
    "hw_events": (
        ("event_id", "integer", False, True),
        ("mandate_id", "string:36", False, False),
        ("event_type", "string:100", False, False),
        ("created_at", "datetime:tz", False, False),
        ("idempotency_key", "string:128", False, False),
        ("actor_id", "string:128", True, False),
        ("assignment_id", "string:36", True, False),
        ("person_id", "string:128", True, False),
        ("department", "string:128", True, False),
        ("direction", "string:32", True, False),
        ("channel", "string:32", True, False),
        ("previous_state", "string:40", True, False),
        ("new_state", "string:40", True, False),
        ("metadata", "json", False, False),
    ),
    "hw_runtime_status": (
        ("key", "string:100", False, True),
        ("value", "text", False, False),
        ("updated_at", "datetime:tz", False, False),
    ),
}

EXPECTED_FOREIGN_KEYS = {
    "hw_assignments": {("mandate_id", "hw_mandates.mandate_id")},
    "hw_release_outbox": {
        ("mandate_id", "hw_mandates.mandate_id"),
        ("assignment_id", "hw_assignments.assignment_id"),
    },
    "hw_interviews": {
        ("mandate_id", "hw_mandates.mandate_id"),
        ("assignment_id", "hw_assignments.assignment_id"),
    },
    "hw_evidence": {
        ("mandate_id", "hw_mandates.mandate_id"),
        ("assignment_id", "hw_assignments.assignment_id"),
    },
    "hw_issues": {("mandate_id", "hw_mandates.mandate_id")},
    "hw_proposals": {("mandate_id", "hw_mandates.mandate_id")},
    "hw_proposal_responses": {("proposal_id", "hw_proposals.proposal_id")},
    "hw_engagement_decisions": {
        ("mandate_id", "hw_mandates.mandate_id"),
        ("assignment_id", "hw_assignments.assignment_id"),
    },
    "hw_meeting_packages": {("mandate_id", "hw_mandates.mandate_id")},
    "hw_events": {("mandate_id", "hw_mandates.mandate_id")},
}

EXPECTED_INDEXES = {
    "hw_mandates": {
        "ix_hw_mandates_created_at": (("created_at",), False),
        "ix_hw_mandates_expires_at": (("expires_at",), False),
        "ix_hw_mandates_idempotency_key": (("idempotency_key",), True),
        "ix_hw_mandates_initiator_id": (("initiator_id",), False),
        "ix_hw_mandates_state": (("state",), False),
        "ix_hw_mandates_token": (("token",), True),
    },
    "hw_assignments": {
        "ix_hw_assignments_interview_id": (("interview_id",), False),
        "ix_hw_assignments_mandate_id": (("mandate_id",), False),
        "ix_hw_assignments_next_action_at": (("next_action_at",), False),
        "ix_hw_assignments_person_id": (("person_id",), False),
        "ix_hw_assignments_state": (("state",), False),
    },
    "hw_release_outbox": {
        "ix_hw_release_outbox_assignment_id": (("assignment_id",), False),
        "ix_hw_release_outbox_claim": (("state", "claimed_at", "created_at"), False),
        "ix_hw_release_outbox_created_at": (("created_at",), False),
        "ix_hw_release_outbox_delivery_id": (("delivery_id",), True),
        "ix_hw_release_outbox_mandate_id": (("mandate_id",), False),
        "ix_hw_release_outbox_state": (("state",), False),
    },
    "hw_interviews": {
        "ix_hw_interviews_assignment_id": (("assignment_id",), True),
        "ix_hw_interviews_mandate_id": (("mandate_id",), False),
        "ix_hw_interviews_stakeholder_person_id": (("stakeholder_person_id",), False),
        "ix_hw_interviews_started_at": (("started_at",), False),
        "uq_hw_active_interview_stakeholder": (
            ("mandate_id", "stakeholder_person_id"),
            True,
        ),
    },
    "hw_evidence": {
        "ix_hw_evidence_assignment_id": (("assignment_id",), False),
        "ix_hw_evidence_created_at": (("created_at",), False),
        "ix_hw_evidence_mandate_id": (("mandate_id",), False),
        "ix_hw_evidence_stakeholder_id": (("stakeholder_id",), False),
    },
    "hw_issues": {"ix_hw_issues_mandate_id": (("mandate_id",), False)},
    "hw_proposals": {
        "ix_hw_proposals_created_at": (("created_at",), False),
        "ix_hw_proposals_expires_at": (("expires_at",), False),
        "ix_hw_proposals_mandate_id": (("mandate_id",), False),
        "ix_hw_proposals_state": (("state",), False),
    },
    "hw_proposal_responses": {
        "ix_hw_proposal_responses_created_at": (("created_at",), False),
        "ix_hw_proposal_responses_idempotency_key": (("idempotency_key",), True),
        "ix_hw_proposal_responses_proposal_id": (("proposal_id",), False),
        "ix_hw_proposal_responses_response_id": (("response_id",), True),
        "ix_hw_proposal_responses_stakeholder_id": (("stakeholder_id",), False),
        "uq_hw_proposal_response_source": (("proposal_id", "source_message_id"), True),
    },
    "hw_engagement_decisions": {
        "ix_hw_engagement_decisions_assignment_id": (("assignment_id",), True),
        "ix_hw_engagement_decisions_created_at": (("created_at",), False),
        "ix_hw_engagement_decisions_idempotency_key": (("idempotency_key",), True),
        "ix_hw_engagement_decisions_mandate_id": (("mandate_id",), False),
        "ix_hw_engagement_decisions_stakeholder_id": (("stakeholder_id",), False),
    },
    "hw_meeting_packages": {
        "ix_hw_meeting_packages_created_at": (("created_at",), False),
        "ix_hw_meeting_packages_mandate_id": (("mandate_id",), True),
    },
    "hw_events": {
        "ix_hw_events_assignment_id": (("assignment_id",), False),
        "ix_hw_events_created_at": (("created_at",), False),
        "ix_hw_events_idempotency_key": (("idempotency_key",), True),
        "ix_hw_events_mandate_id": (("mandate_id",), False),
    },
}


def _validate_postgres_test_url(value: str) -> URL:
    try:
        url = make_url(value)
    except Exception as error:
        raise ValueError("explicit PostgreSQL test URL required") from error
    if url.drivername not in {"postgresql", "postgresql+psycopg"} or not url.database:
        raise ValueError("explicit PostgreSQL test URL required")
    return url.set(drivername="postgresql+psycopg")


def _explicit_postgres_test_url(environ: Mapping[str, str]) -> URL | None:
    value = environ.get(POSTGRES_TEST_URL_ENV)
    return None if value is None else _validate_postgres_test_url(value)


def _new_postgres_test_schema() -> str:
    return f"humanwire_test_{uuid4().hex}"


def _validated_postgres_test_schema(schema: str) -> str:
    if POSTGRES_TEST_SCHEMA.fullmatch(schema) is None:
        raise ValueError("refusing PostgreSQL test schema cleanup")
    return schema


def _drop_postgres_test_schema(connection: object, schema: str) -> None:
    exact_schema = _validated_postgres_test_schema(schema)
    connection.exec_driver_sql(f'DROP SCHEMA "{exact_schema}" CASCADE')


@contextmanager
def _postgres_test_session_factory(environ: Mapping[str, str]):
    url = _explicit_postgres_test_url(environ)
    if url is None:
        pytest.skip(f"{POSTGRES_TEST_URL_ENV} is not explicitly supplied")

    schema = _new_postgres_test_schema()
    schema_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    admin_engine = None
    schema_engine = None
    created = False
    try:
        try:
            from alembic import command
            from alembic.config import Config

            admin_engine = create_engine(url)
            with admin_engine.begin() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                created = True

            config = Config(str(ROOT / "alembic.ini"))
            configured_url = schema_url.render_as_string(hide_password=False).replace("%", "%%")
            config.set_main_option("sqlalchemy.url", configured_url)
            ambient_database_url = os.environ.pop("DATABASE_URL", None)
            try:
                command.upgrade(config, "head")
            finally:
                if ambient_database_url is not None:
                    os.environ["DATABASE_URL"] = ambient_database_url

            schema_engine = create_engine(schema_url)
            with schema_engine.connect() as connection:
                assert connection.execute(text("SELECT current_schema() ")).scalar_one() == schema
                assert set(inspect(connection).get_table_names()) == set(EXPECTED_COLUMNS) | {
                    "alembic_version"
                }
        except Exception:  # noqa: BLE001 -- redact connection coordinates from setup errors.
            raise RuntimeError("PostgreSQL integration gate setup failed") from None
        yield sessionmaker(bind=schema_engine, expire_on_commit=False)
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        if created and admin_engine is not None:
            try:
                with admin_engine.begin() as connection:
                    _drop_postgres_test_schema(connection, schema)
            except Exception:  # noqa: BLE001 -- redact connection coordinates from cleanup.
                raise RuntimeError("PostgreSQL integration gate cleanup failed") from None
        if admin_engine is not None:
            admin_engine.dispose()


@pytest.fixture
def postgres_session_factory():
    with _postgres_test_session_factory(os.environ) as factory:
        yield factory


def _type_token(column_type: object) -> str:
    if isinstance(column_type, Text):
        return "text"
    if isinstance(column_type, String):
        return f"string:{column_type.length}"
    if isinstance(column_type, JSON):
        return "json"
    if isinstance(column_type, Boolean):
        return "boolean"
    if isinstance(column_type, Integer):
        return "integer"
    if isinstance(column_type, DateTime):
        return "datetime:tz" if column_type.timezone else "datetime"
    raise AssertionError(f"uncontracted column type: {column_type!r}")


def _metadata_indexes() -> dict[str, dict[str, tuple[tuple[str, ...], bool]]]:
    return {
        table.name: {
            index.name: (tuple(column.name for column in index.columns), bool(index.unique))
            for index in table.indexes
        }
        for table in Base.metadata.tables.values()
        if table.indexes
    }


def _metadata_foreign_keys() -> dict[str, set[tuple[str, str]]]:
    return {
        table.name: {
            (column.name, foreign_key.target_fullname)
            for column in table.columns
            for foreign_key in column.foreign_keys
        }
        for table in Base.metadata.tables.values()
        if any(column.foreign_keys for column in table.columns)
    }


def _load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing initial migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("humanwire_migration_0001", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    if importlib.util.find_spec("alembic") is None:
        shim = ModuleType("alembic")
        shim.op = None
        previous = sys.modules.setdefault("alembic", shim)
        try:
            spec.loader.exec_module(module)
        finally:
            if previous is shim:
                del sys.modules["alembic"]
    else:
        spec.loader.exec_module(module)
    return module


class _SchemaOperations:
    def __init__(self, *, connection=None, dialect=None) -> None:
        self.connection = connection
        self.dialect = dialect or connection.dialect
        self.metadata = MetaData()
        self.statements: list[str] = []

    def create_table(self, name: str, *elements: object) -> None:
        table = Table(name, self.metadata, *elements)
        if self.connection is None:
            self.statements.append(str(CreateTable(table).compile(dialect=self.dialect)))
        else:
            table.create(self.connection)

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        *,
        unique: bool = False,
        **dialect_options: object,
    ) -> None:
        table = self.metadata.tables[table_name]
        index = Index(
            name,
            *(table.c[column] for column in columns),
            unique=unique,
            **dialect_options,
        )
        if self.connection is None:
            self.statements.append(str(CreateIndex(index).compile(dialect=self.dialect)))
        else:
            index.create(self.connection)

    def drop_table(self, name: str) -> None:
        assert self.connection is not None
        self.metadata.tables[name].drop(self.connection)


def _run_offline_upgrade(module: ModuleType, dialect_name: str) -> _SchemaOperations:
    dialect = {"postgresql": postgresql.dialect(), "sqlite": sqlite.dialect()}[dialect_name]
    operations = _SchemaOperations(dialect=dialect)
    module.op = operations
    module.upgrade()
    return operations


def _normalize_ddl(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().rstrip(";")).lower()


def _where_token(value: object | None) -> str:
    return "" if value is None else str(value)


def _metadata_schema_signature(metadata: MetaData) -> dict[str, object]:
    return {
        "columns": {
            table.name: tuple(
                (
                    column.name,
                    _type_token(column.type),
                    column.nullable,
                    column.primary_key,
                    str(column.server_default.arg) if column.server_default is not None else None,
                )
                for column in table.columns
            )
            for table in metadata.tables.values()
        },
        "foreign_keys": {
            table.name: {
                (column.name, foreign_key.target_fullname)
                for column in table.columns
                for foreign_key in column.foreign_keys
            }
            for table in metadata.tables.values()
        },
        "indexes": {
            table.name: {
                (
                    index.name,
                    tuple(column.name for column in index.columns),
                    bool(index.unique),
                    _where_token(index.dialect_options["sqlite"].get("where")),
                    _where_token(index.dialect_options["postgresql"].get("where")),
                )
                for index in table.indexes
            }
            for table in metadata.tables.values()
        },
        "unique_constraints": {
            table.name: {
                tuple(column.name for column in constraint.columns)
                for constraint in table.constraints
                if isinstance(constraint, UniqueConstraint)
            }
            for table in metadata.tables.values()
        },
    }


def _inspector_signature(
    inspector: object, table_names: set[str] | None = None
) -> dict[str, object]:
    if table_names is None:
        table_names = set(inspector.get_table_names())
    columns = {
        table: tuple(
            (column["name"], str(column["type"]), column["nullable"], column["default"])
            for column in inspector.get_columns(table)
        )
        for table in table_names
    }
    primary_keys = {
        table: tuple(inspector.get_pk_constraint(table)["constrained_columns"])
        for table in table_names
    }
    foreign_keys = {
        table: {
            (
                tuple(key["constrained_columns"]),
                key["referred_table"],
                tuple(key["referred_columns"]),
            )
            for key in inspector.get_foreign_keys(table)
        }
        for table in table_names
    }
    indexes = {
        table: {
            (
                index["name"],
                tuple(index["column_names"]),
                bool(index["unique"]),
                (
                    str(index.get("dialect_options", {}).get("sqlite_where"))
                    if index.get("dialect_options", {}).get("sqlite_where") is not None
                    else ""
                ),
            )
            for index in inspector.get_indexes(table)
        }
        for table in table_names
    }
    unique_constraints = {
        table: {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table)
        }
        for table in table_names
    }
    return {
        "tables": table_names,
        "columns": columns,
        "primary_keys": primary_keys,
        "foreign_keys": foreign_keys,
        "indexes": indexes,
        "unique_constraints": unique_constraints,
    }


def test_ddl_metadata_has_the_complete_reviewed_schema_contract() -> None:
    actual_columns = {
        table.name: tuple(
            (column.name, _type_token(column.type), column.nullable, column.primary_key)
            for column in table.columns
        )
        for table in Base.metadata.tables.values()
    }

    assert actual_columns == EXPECTED_COLUMNS
    assert _metadata_foreign_keys() == EXPECTED_FOREIGN_KEYS
    assert _metadata_indexes() == EXPECTED_INDEXES
    assert all(
        not any(isinstance(item, UniqueConstraint) for item in table.constraints)
        for table in Base.metadata.tables.values()
    )


def test_ddl_defaults_are_portable_and_preserve_runtime_behavior() -> None:
    assignments = Base.metadata.tables["hw_assignments"].c
    metadata = Base.metadata.tables["hw_events"].c.metadata

    assert assignments.engagement_type.default.arg == "structured_interview"
    assert str(assignments.engagement_type.server_default.arg) == "structured_interview"
    assert assignments.response_required.default.arg is True
    assert str(assignments.response_required.server_default.arg) == "true"
    assert metadata.default.is_callable
    assert metadata.default.arg(None) == {}
    assert metadata.server_default is None

    sqlite_ddl = str(
        CreateTable(Base.metadata.tables["hw_assignments"]).compile(dialect=sqlite.dialect())
    )
    postgres_ddl = str(
        CreateTable(Base.metadata.tables["hw_assignments"]).compile(dialect=postgresql.dialect())
    )
    assert "response_required BOOLEAN DEFAULT 1 NOT NULL" in sqlite_ddl
    assert "response_required BOOLEAN DEFAULT true NOT NULL" in postgres_ddl


def test_ddl_active_interview_uniqueness_is_equivalent_for_both_dialects() -> None:
    index = next(
        item
        for item in Base.metadata.tables["hw_interviews"].indexes
        if item.name == "uq_hw_active_interview_stakeholder"
    )

    sqlite_ddl = str(CreateIndex(index).compile(dialect=sqlite.dialect()))
    postgres_ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))

    assert sqlite_ddl.endswith("WHERE completed_at IS NULL")
    assert postgres_ddl.endswith("WHERE completed_at IS NULL")


def test_migration_upgrade_matches_metadata_on_sqlite_and_downgrade_is_complete() -> None:
    module = _load_migration()

    migrated_engine = create_engine("sqlite://")
    metadata_engine = create_engine("sqlite://")
    Base.metadata.create_all(metadata_engine)

    with migrated_engine.begin() as connection:
        module.op = _SchemaOperations(connection=connection)
        module.upgrade()
        assert _inspector_signature(inspect(connection)) == _inspector_signature(
            inspect(metadata_engine)
        )
        module.downgrade()
        assert inspect(connection).get_table_names() == []


def test_real_alembic_upgrades_and_downgrades_only_the_configured_temp_database(
    tmp_path: Path, monkeypatch
) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "humanwire-alembic.sqlite3"
    default_database_path = ROOT / "data" / "humanwire.db"
    default_database_state = (
        default_database_path.exists(),
        default_database_path.stat().st_mtime_ns if default_database_path.exists() else None,
    )
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    migrated_engine = create_engine(database_url)
    metadata_engine = create_engine("sqlite://")
    Base.metadata.create_all(metadata_engine)
    with migrated_engine.connect() as connection:
        inspector = inspect(connection)
        assert set(inspector.get_table_names()) == set(EXPECTED_COLUMNS) | {"alembic_version"}
        assert _inspector_signature(inspector, set(EXPECTED_COLUMNS)) == _inspector_signature(
            inspect(metadata_engine)
        )
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0001_humanwire_schema"
        )
    migrated_engine.dispose()

    command.downgrade(config, "base")

    downgraded_engine = create_engine(database_url)
    with downgraded_engine.connect() as connection:
        inspector = inspect(connection)
        assert not any(table.startswith("hw_") for table in inspector.get_table_names())
        assert connection.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar_one() == 0
    downgraded_engine.dispose()
    assert database_path.is_file()
    assert (
        default_database_path.exists(),
        default_database_path.stat().st_mtime_ns if default_database_path.exists() else None,
    ) == default_database_state


def test_migration_postgresql_ddl_matches_every_metadata_table_and_index() -> None:
    module = _load_migration()
    operations = _run_offline_upgrade(module, "postgresql")
    migration_ddl = _normalize_ddl("\n".join(operations.statements))

    assert _metadata_schema_signature(operations.metadata) == _metadata_schema_signature(
        Base.metadata
    )
    for dialect in (sqlite.dialect(), postgresql.dialect()):
        for table in operations.metadata.sorted_tables:
            assert str(CreateTable(table).compile(dialect=dialect))
            for index in table.indexes:
                assert str(CreateIndex(index).compile(dialect=dialect))

    assert (
        "create unique index uq_hw_active_interview_stakeholder "
        "on hw_interviews (mandate_id, stakeholder_person_id) where completed_at is null"
        in migration_ddl
    )


def test_postgres_gate_ignores_ambient_database_url(monkeypatch) -> None:
    """Break caught: the destructive gate discovers an ambient application database."""
    monkeypatch.delenv("HUMANWIRE_TEST_POSTGRES_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://ambient.invalid/private")

    assert _explicit_postgres_test_url(os.environ) is None


def test_postgres_gate_skips_before_engine_or_driver_loading(monkeypatch) -> None:
    """Break caught: an absent opt-in variable still reaches connection setup or DNS."""
    monkeypatch.delenv(POSTGRES_TEST_URL_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://ambient.invalid/private")

    def unexpected_engine(*_args, **_kwargs):
        raise AssertionError("connection setup was reached")

    monkeypatch.setattr(sys.modules[__name__], "create_engine", unexpected_engine)

    with (
        pytest.raises(pytest.skip.Exception, match=POSTGRES_TEST_URL_ENV),
        _postgres_test_session_factory(os.environ),
    ):
        raise AssertionError("skipped gate entered its body")


@pytest.mark.parametrize(
    "value",
    [
        "sqlite:///not-postgres.db",
        "mysql://localhost/not-postgres",
        "postgres://localhost/ambiguous-alias",
        "postgresql://localhost",
        "not a url",
    ],
)
def test_postgres_gate_rejects_non_postgresql_urls_without_disclosing_them(value) -> None:
    """Break caught: malformed or non-PostgreSQL targets reach schema creation."""
    with pytest.raises(ValueError, match="explicit PostgreSQL test URL required") as error:
        _validate_postgres_test_url(value)

    assert value not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://operator:secret@db.example.test/humanwire",
        "postgresql+psycopg://operator:secret@db.example.test/humanwire",
    ],
)
def test_postgres_gate_accepts_only_explicit_postgresql_schemes(value) -> None:
    """Break caught: a valid psycopg/PostgreSQL URL is rejected before the opt-in gate."""
    assert _validate_postgres_test_url(value).get_backend_name() == "postgresql"


def test_postgres_gate_generates_unique_safe_schema_identifiers() -> None:
    """Break caught: an unsafe or reused identifier broadens destructive SQL scope."""
    first = _new_postgres_test_schema()
    second = _new_postgres_test_schema()

    assert first != second
    assert re.fullmatch(r"humanwire_test_[0-9a-f]{32}", first)
    assert re.fullmatch(r"humanwire_test_[0-9a-f]{32}", second)


@pytest.mark.parametrize(
    "schema",
    [
        "public",
        "humanwire_test_123",
        "humanwire_test_" + "g" * 32,
        "humanwire_test_" + "a" * 32 + ";drop schema public",
    ],
)
def test_postgres_teardown_guard_rejects_every_non_generated_schema(schema) -> None:
    """Break caught: teardown can target a shared, malformed, or injected schema."""
    with pytest.raises(ValueError, match="refusing PostgreSQL test schema cleanup"):
        _validated_postgres_test_schema(schema)


def test_postgres_teardown_targets_only_the_validated_exact_schema() -> None:
    """Break caught: teardown emits broad or unquoted destructive SQL."""
    schema = "humanwire_test_" + "a" * 32

    class RecordingConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def exec_driver_sql(self, statement: str) -> None:
            self.statements.append(statement)

    connection = RecordingConnection()
    _drop_postgres_test_schema(connection, schema)

    assert connection.statements == [f'DROP SCHEMA "{schema}" CASCADE']


POSTGRES_TRANSACTION_INVARIANTS = (
    "release_cas",
    "outbox_lease",
    "outbox_fence",
    "callback_ownership",
    "evidence_confirmation",
    "synthesis",
    "proposal",
    "availability",
    "meeting_cancel",
    "meeting_expiry",
)


@pytest.mark.parametrize("invariant", POSTGRES_TRANSACTION_INVARIANTS)
def test_postgresql_transaction_invariants_in_disposable_schema(
    invariant,
    postgres_session_factory,
    tmp_path,
    incoming_message_factory,
    now,
    monkeypatch,
) -> None:
    """Run the established transaction/race contracts against real PostgreSQL."""
    from tests.humanwire import test_workflow as workflow_contract

    monkeypatch.setattr(
        workflow_contract,
        "create_session_factory",
        lambda _database_url: postgres_session_factory,
    )

    calls = {
        "release_cas": lambda: (
            workflow_contract.test_file_go_and_due_release_race_has_one_complete_winner(
                tmp_path, incoming_message_factory, now, monkeypatch, 0, "due"
            )
        ),
        "outbox_lease": lambda: (
            workflow_contract.test_concurrent_restart_drains_claim_each_initial_release_entry_once(
                tmp_path, incoming_message_factory, now
            )
        ),
        "outbox_fence": lambda: (
            workflow_contract.test_reclaimed_later_batch_entry_fences_the_original_dispatch_owner(
                tmp_path, incoming_message_factory, now
            )
        ),
        "callback_ownership": lambda: (
            workflow_contract.test_stale_release_owner_callback_is_inert_after_reclaim(
                tmp_path, incoming_message_factory, now, "quick-person", True
            )
        ),
        "evidence_confirmation": lambda: (
            workflow_contract.test_file_concurrent_evidence_confirmation_promotes_once(
                tmp_path, incoming_message_factory
            )
        ),
        "synthesis": lambda: (
            workflow_contract.test_file_backed_final_approval_racing_synthesis_fails_closed_then_retries_fresh(
                tmp_path, incoming_message_factory, now, monkeypatch
            )
        ),
        "proposal": lambda: (
            workflow_contract.test_proposals_require_all_authenticated_respondents_and_cap_after_round_two(
                SqlAlchemyHumanWireRepository(postgres_session_factory),
                incoming_message_factory,
            )
        ),
        "availability": lambda: (
            workflow_contract.test_file_scheduling_concurrent_conflicting_replay_preserves_first_windows(
                tmp_path, incoming_message_factory, monkeypatch
            )
        ),
        "meeting_cancel": lambda: (
            workflow_contract.test_file_final_scheduling_availability_cannot_resurrect_terminal_mandate(
                tmp_path,
                incoming_message_factory,
                monkeypatch,
                MandateState.CANCELLED,
                "before_package",
            )
        ),
        "meeting_expiry": lambda: (
            workflow_contract.test_file_final_scheduling_availability_cannot_resurrect_terminal_mandate(
                tmp_path,
                incoming_message_factory,
                monkeypatch,
                MandateState.EXPIRED,
                "before_package",
            )
        ),
    }

    try:
        calls[invariant]()
    except Exception:  # noqa: BLE001 -- never disclose connection coordinates.
        raise AssertionError(f"PostgreSQL invariant failed: {invariant}") from None
