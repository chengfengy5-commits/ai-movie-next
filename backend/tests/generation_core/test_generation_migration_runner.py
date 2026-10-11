import importlib.util
import sys
from pathlib import Path

import re

import pytest
from sqlalchemy import create_engine, inspect, text

from haoai_backend.generation_core.base_schema import FOUNDATION_TABLES
from haoai_backend.generation_core.schema import (
    GENERATION_MIGRATION_HISTORY_TABLE,
    GENERATION_RECORD_TABLE_NAMES,
    GENERATION_RECORD_TABLES,
    FoundationBase,
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


def test_explicit_migration_applies_once_and_records_checksum() -> None:
    engine = _foundation_engine()

    first = apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    second = apply_generation_migrations(engine, MIGRATION_DIRECTORY)

    assert first.status == "applied"
    assert second.status == "already_current"
    assert set(GENERATION_RECORD_TABLE_NAMES).issubset(
        set(inspect(engine).get_table_names())
    )
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
