from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

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
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable, UniqueConstraint

from humanwire.database import Base

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "migrations" / "versions" / "0001_humanwire_schema.py"


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
        CreateTable(Base.metadata.tables["hw_assignments"]).compile(
            dialect=postgresql.dialect()
        )
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
