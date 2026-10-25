import importlib.util
import sys
from pathlib import Path

import re

import pytest
from sqlalchemy import create_engine, event, inspect, text

from haoai_backend.generation_core.base_schema import FOUNDATION_TABLES
from haoai_backend.generation_core.schema import (
    CANVAS_RESULT_TARGETS_SCHEMA_CHECKSUM,
    CANVAS_RESULT_TARGETS_SCHEMA_VERSION,
    GENERATION_MIGRATION_HISTORY_TABLE,
    GENERATION_SCHEMA_CHECKSUM,
    GENERATION_SCHEMA_VERSION,
    GENERATION_RECORD_TABLE_NAMES,
    GENERATION_RECORD_TABLES,
    FoundationBase,
    GenerationResultTargetRow,
)


MIGRATION_RUNNER_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "run_generation_migrations.py"
)
_RUNNER_SPEC = importlib.util.spec_from_file_location(
    "_haoai_generation_migration_runner_test", MIGRATION_RUNNER_PATH
)
assert _RUNNER_SPEC is not None and _RUNNER_SPEC.loader is not None
_RUNNER_MODULE = importlib.util.module_from_spec(_RUNNER_SPEC)
sys.modules[_RUNNER_SPEC.name] = _RUNNER_MODULE
_RUNNER_SPEC.loader.exec_module(_RUNNER_MODULE)
apply_generation_migrations = _RUNNER_MODULE.apply_generation_migrations
MigrationPreconditionError = _RUNNER_MODULE.MigrationPreconditionError


MIGRATION_DIRECTORY = Path(__file__).resolve().parents[2] / "migrations"


def _foundation_engine():
    engine = create_engine("sqlite://")
    FoundationBase.metadata.create_all(engine, tables=list(FOUNDATION_TABLES))
    return engine


def _apply_only_v1_sql(engine) -> None:
    migration_sql = (
        MIGRATION_DIRECTORY / "0001_generation_shared_records.sql"
    ).read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TABLE {GENERATION_MIGRATION_HISTORY_TABLE} ("
            "version VARCHAR(64) NOT NULL PRIMARY KEY, "
            "checksum VARCHAR(64) NOT NULL, "
            "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for statement in migration_sql.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement.strip())
        connection.execute(
            text(
                f"INSERT INTO {GENERATION_MIGRATION_HISTORY_TABLE} "
                "(version, checksum) VALUES (:version, :checksum)"
            ),
            {"version": GENERATION_SCHEMA_VERSION, "checksum": GENERATION_SCHEMA_CHECKSUM},
        )


def test_explicit_migration_applies_once_and_records_checksum() -> None:
    engine = _foundation_engine()

    first = apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    repeated_statements: list[str] = []

    def record_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        repeated_statements.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        second = apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert first.status == "applied"
    assert first.applied_versions == (
        GENERATION_SCHEMA_VERSION,
        CANVAS_RESULT_TARGETS_SCHEMA_VERSION,
    )
    assert second.status == "already_current"
    assert second.applied_versions == ()
    assert not any(
        statement.startswith(("CREATE", "DROP", "INSERT", "UPDATE", "DELETE", "ALTER"))
        for statement in repeated_statements
    )
    assert set(GENERATION_RECORD_TABLE_NAMES).issubset(
        set(inspect(engine).get_table_names())
    )
    assert GenerationResultTargetRow.__tablename__ in inspect(engine).get_table_names()
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"SELECT version, checksum FROM {GENERATION_MIGRATION_HISTORY_TABLE} "
                "ORDER BY version"
            )
        ).all()
        indexes = inspect(connection).get_indexes("generation_result_receipts")
    assert rows == [
        (GENERATION_SCHEMA_VERSION, GENERATION_SCHEMA_CHECKSUM),
        (CANVAS_RESULT_TARGETS_SCHEMA_VERSION, CANVAS_RESULT_TARGETS_SCHEMA_CHECKSUM),
    ]
    assert any(
        tuple(item.get("column_names") or ()) == ("owner_id", "result_ref")
        for item in indexes
    )
    engine.dispose()


def test_valid_v1_database_upgrades_to_v2_without_losing_existing_rows() -> None:
    engine = _foundation_engine()
    migration_path = MIGRATION_DIRECTORY / "0001_generation_shared_records.sql"
    migration_sql = migration_path.read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TABLE {GENERATION_MIGRATION_HISTORY_TABLE} ("
            "version VARCHAR(64) NOT NULL PRIMARY KEY, "
            "checksum VARCHAR(64) NOT NULL, "
            "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for statement in migration_sql.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement.strip())
        connection.execute(
            text(
                f"INSERT INTO {GENERATION_MIGRATION_HISTORY_TABLE} "
                "(version, checksum) VALUES (:version, :checksum)"
            ),
            {"version": GENERATION_SCHEMA_VERSION, "checksum": GENERATION_SCHEMA_CHECKSUM},
        )
        connection.execute(
            text("INSERT INTO users (id) VALUES (:id)"),
            {"id": "preserved-v1-user"},
        )

    result = apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    with engine.connect() as connection:
        user_ids = connection.execute(text("SELECT id FROM users")).scalars().all()
        versions = connection.execute(
            text(
                f"SELECT version FROM {GENERATION_MIGRATION_HISTORY_TABLE} "
                "ORDER BY version"
            )
        ).scalars().all()

    assert result.status == "applied"
    assert result.applied_versions == (CANVAS_RESULT_TARGETS_SCHEMA_VERSION,)
    assert user_ids == ["preserved-v1-user"]
    assert versions == [GENERATION_SCHEMA_VERSION, CANVAS_RESULT_TARGETS_SCHEMA_VERSION]
    engine.dispose()


def test_migration_refuses_future_index_without_v2_history() -> None:
    engine = _foundation_engine()
    migration_sql = (MIGRATION_DIRECTORY / "0001_generation_shared_records.sql").read_text(
        encoding="utf-8"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TABLE {GENERATION_MIGRATION_HISTORY_TABLE} ("
            "version VARCHAR(64) NOT NULL PRIMARY KEY, "
            "checksum VARCHAR(64) NOT NULL, "
            "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for statement in migration_sql.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement.strip())
        connection.execute(
            text(
                f"INSERT INTO {GENERATION_MIGRATION_HISTORY_TABLE} "
                "(version, checksum) VALUES (:version, :checksum)"
            ),
            {"version": GENERATION_SCHEMA_VERSION, "checksum": GENERATION_SCHEMA_CHECKSUM},
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_generation_result_receipts_owner_result_ref "
            "ON generation_result_receipts (owner_id, result_ref)"
        )

    with pytest.raises(MigrationPreconditionError, match="version 0002 index"):
        apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    assert "generation_result_targets" not in inspect(engine).get_table_names()
    engine.dispose()


def test_migration_refuses_v2_history_gap_without_creating_v1_or_v2_tables() -> None:
    engine = _foundation_engine()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TABLE {GENERATION_MIGRATION_HISTORY_TABLE} ("
            "version VARCHAR(64) NOT NULL PRIMARY KEY, "
            "checksum VARCHAR(64) NOT NULL, "
            "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            text(
                f"INSERT INTO {GENERATION_MIGRATION_HISTORY_TABLE} "
                "(version, checksum) VALUES (:version, :checksum)"
            ),
            {
                "version": CANVAS_RESULT_TARGETS_SCHEMA_VERSION,
                "checksum": CANVAS_RESULT_TARGETS_SCHEMA_CHECKSUM,
            },
        )
    before_tables = set(inspect(engine).get_table_names())

    with pytest.raises(
        MigrationPreconditionError,
        match="generation_migration_history_gap",
    ):
        apply_generation_migrations(engine, MIGRATION_DIRECTORY)

    after_tables = set(inspect(engine).get_table_names())
    assert after_tables == before_tables
    assert not (set(GENERATION_RECORD_TABLE_NAMES) & after_tables)
    assert GenerationResultTargetRow.__tablename__ not in after_tables
    engine.dispose()


def test_migration_refuses_partial_v2_structure_without_its_history_row() -> None:
    engine = _foundation_engine()
    _apply_only_v1_sql(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE generation_result_targets ("
            "result_ref VARCHAR(36) NOT NULL PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "INSERT INTO generation_result_targets (result_ref) "
            "VALUES ('partial-v2-sentinel')"
        )

    def partial_table_snapshot():
        with engine.connect() as connection:
            columns = tuple(
                tuple(row)
                for row in connection.exec_driver_sql(
                    'PRAGMA table_info("generation_result_targets")'
                ).all()
            )
            objects = tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT type, name, tbl_name, sql FROM sqlite_master "
                        "WHERE tbl_name = :table_name ORDER BY type, name"
                    ),
                    {"table_name": "generation_result_targets"},
                ).all()
            )
            rows = tuple(
                tuple(row)
                for row in connection.exec_driver_sql(
                    "SELECT rowid, * FROM generation_result_targets ORDER BY rowid"
                ).all()
            )
        return columns, objects, rows

    partial_table_before = partial_table_snapshot()

    with pytest.raises(
        MigrationPreconditionError,
        match="migration structures exist without their history row: 0002_canvas_result_targets",
    ):
        apply_generation_migrations(engine, MIGRATION_DIRECTORY)

    assert partial_table_snapshot() == partial_table_before
    assert set(GENERATION_RECORD_TABLE_NAMES).issubset(
        set(inspect(engine).get_table_names())
    )
    with engine.connect() as connection:
        versions = connection.execute(
            text(
                f"SELECT version FROM {GENERATION_MIGRATION_HISTORY_TABLE} "
                "ORDER BY version"
            )
        ).scalars().all()
        target_ddl = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='generation_result_targets'"
        ).scalar_one()
    assert versions == [GENERATION_SCHEMA_VERSION]
    assert "target_identity" not in target_ddl
    engine.dispose()


def test_migration_refuses_v2_page_index_with_wrong_columns_without_repair() -> None:
    engine = _foundation_engine()
    apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_generation_result_targets_page")
        connection.exec_driver_sql(
            "CREATE INDEX ix_generation_result_targets_page "
            "ON generation_result_targets (owner_id, chapter_id, target_identity)"
        )

    with pytest.raises(
        MigrationPreconditionError,
        match="missing_or_incompatible_index:generation_result_targets:ix_generation_result_targets_page",
    ):
        apply_generation_migrations(engine, MIGRATION_DIRECTORY)

    indexes = inspect(engine).get_indexes("generation_result_targets")
    page_index = next(
        index
        for index in indexes
        if index.get("name") == "ix_generation_result_targets_page"
    )
    assert tuple(page_index["column_names"]) == (
        "owner_id",
        "chapter_id",
        "target_identity",
    )
    with engine.connect() as connection:
        versions = connection.execute(
            text(
                f"SELECT version FROM {GENERATION_MIGRATION_HISTORY_TABLE} "
                "ORDER BY version"
            )
        ).scalars().all()
    assert versions == [GENERATION_SCHEMA_VERSION, CANVAS_RESULT_TARGETS_SCHEMA_VERSION]
    engine.dispose()


def test_migration_refuses_corrupt_v2_history_checksum_without_writes() -> None:
    engine = _foundation_engine()
    apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    with engine.begin() as connection:
        connection.execute(
            text(
                f"UPDATE {GENERATION_MIGRATION_HISTORY_TABLE} SET checksum = :checksum "
                "WHERE version = :version"
            ),
            {
                "checksum": "0" * 64,
                "version": CANVAS_RESULT_TARGETS_SCHEMA_VERSION,
            },
        )
    before_indexes = inspect(engine).get_indexes("generation_result_targets")

    with pytest.raises(
        MigrationPreconditionError,
        match="applied migration checksum differs from the manifest: 0002_canvas_result_targets",
    ):
        apply_generation_migrations(engine, MIGRATION_DIRECTORY)

    assert inspect(engine).get_indexes("generation_result_targets") == before_indexes
    with engine.connect() as connection:
        checksum = connection.execute(
            text(
                f"SELECT checksum FROM {GENERATION_MIGRATION_HISTORY_TABLE} "
                "WHERE version = :version"
            ),
            {"version": CANVAS_RESULT_TARGETS_SCHEMA_VERSION},
        ).scalar_one()
    assert checksum == "0" * 64
    engine.dispose()


def test_migration_refuses_missing_base_table_without_ddl() -> None:
    engine = _foundation_engine()
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE task_quotes")

    result = apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    tables = set(inspect(engine).get_table_names())

    assert result.status == "not_ready"
    assert any("missing_table:task_quotes" in issue for issue in result.issues)
    assert "generation_schema_migrations" not in tables
    assert not (set(GENERATION_RECORD_TABLE_NAMES) & tables)
    engine.dispose()


def test_migration_refuses_malformed_history_primary_key() -> None:
    engine = _foundation_engine()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE generation_schema_migrations ("
            "version VARCHAR(64) NOT NULL, checksum VARCHAR(64) NOT NULL, "
            "applied_at TIMESTAMP NOT NULL)"
        )

    with pytest.raises(MigrationPreconditionError, match="incompatible_primary_key"):
        apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    engine.dispose()


def test_migration_refuses_extra_version_and_applied_schema_drift() -> None:
    engine = _foundation_engine()
    apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO generation_schema_migrations "
                "(version, checksum) VALUES (:version, :checksum)"
            ),
            {"version": "0002_unreviewed", "checksum": "0" * 64},
        )

    with pytest.raises(MigrationPreconditionError, match="unsupported_generation_version"):
        apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    engine.dispose()


def test_migration_refuses_same_name_record_table_with_missing_columns() -> None:
    engine = _foundation_engine()
    apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE application_records")
        connection.exec_driver_sql(
            "CREATE TABLE application_records (application_ref VARCHAR(36) PRIMARY KEY)"
        )

    with pytest.raises(MigrationPreconditionError, match="missing_column"):
        apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    engine.dispose()


def _normalize_check(expression: str) -> str:
    value = re.sub(r"\s+", "", expression.lower())
    value = value.replace('"', "").replace("`", "").replace("[", "").replace("]", "")
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        wraps_all = True
        for index, character in enumerate(value):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and index != len(value) - 1:
                    wraps_all = False
                    break
        if not wraps_all:
            break
        value = value[1:-1]
    return value


def _column_type_signature(value: object) -> str:
    rendered = str(value).lower()
    return "timestamp" if rendered in {"datetime", "timestamp"} else rendered


def _checks_from_ddl(ddl: str):
    checks = set()
    for match in re.finditer(r"CONSTRAINT\s+([A-Za-z0-9_]+)\s+CHECK\s*\(", ddl, re.IGNORECASE):
        start = match.end()
        depth = 1
        cursor = start
        while cursor < len(ddl) and depth:
            if ddl[cursor] == "(":
                depth += 1
            elif ddl[cursor] == ")":
                depth -= 1
            cursor += 1
        if depth:
            raise AssertionError(f"unclosed CHECK expression: {match.group(1)}")
        checks.add((match.group(1), _normalize_check(ddl[start:cursor - 1])))
    return checks


def _record_schema_signature(engine):
    with engine.connect() as connection:
        inspector = inspect(connection)
        signature = {}
        for table in GENERATION_RECORD_TABLES:
            name = table.name
            columns = {
                item["name"]: (_column_type_signature(item["type"]), bool(item["nullable"]))
                for item in inspector.get_columns(name)
            }
            primary_key = tuple(
                inspector.get_pk_constraint(name).get("constrained_columns") or ()
            )
            foreign_keys = {
                (
                    tuple(item.get("constrained_columns") or ()),
                    item.get("referred_table"),
                    tuple(item.get("referred_columns") or ()),
                    item.get("name"),
                )
                for item in inspector.get_foreign_keys(name)
            }
            unique_constraints = {
                (tuple(item.get("column_names") or ()), item.get("name"))
                for item in inspector.get_unique_constraints(name)
            }
            ddl = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
                {"name": name},
            ).scalar_one()
            checks = _checks_from_ddl(ddl)
            signature[name] = (columns, primary_key, foreign_keys, unique_constraints, checks)
        return signature


def test_migration_sql_matches_all_eight_orm_record_shapes() -> None:
    migrated_engine = _foundation_engine()
    apply_generation_migrations(migrated_engine, MIGRATION_DIRECTORY)
    mapped_engine = create_engine("sqlite://")
    FoundationBase.metadata.create_all(mapped_engine)

    assert _record_schema_signature(migrated_engine) == _record_schema_signature(mapped_engine)

    migrated_engine.dispose()
    mapped_engine.dispose()
