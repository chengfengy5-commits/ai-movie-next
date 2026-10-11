"""Explicit generation-schema migration runner with fail-closed preflight."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from sqlalchemy import Engine, text

from haoai_backend.generation_core.schema import (
    GENERATION_MIGRATION_HISTORY_TABLE,
    GENERATION_RECORD_TABLE_NAMES,
    GENERATION_SCHEMA_CHECKSUM,
    GENERATION_SCHEMA_VERSION,
    check_foundation_schema,
    check_generation_record_schema,
    check_generation_version_set,
    check_migration_history_schema,
    sqlalchemy_catalog,
)


MIGRATION_HISTORY_TABLE = GENERATION_MIGRATION_HISTORY_TABLE


@dataclass(frozen=True, slots=True)
class MigrationResult:
    status: Literal["applied", "already_current", "not_ready"]
    applied_versions: tuple[str, ...]
    issues: tuple[str, ...] = ()


class MigrationPreconditionError(RuntimeError):
    """The migration manifest or existing schema state is inconsistent."""


def _check_issues(*checks) -> tuple[str, ...]:
    return tuple(
        f"{issue.code}:{issue.table}:{issue.detail}"
        for check in checks
        for issue in check.issues
    )


def _require_ready(*checks) -> None:
    issues = _check_issues(*checks)
    if issues:
        raise MigrationPreconditionError("; ".join(issues))


def apply_generation_migrations(
    engine: Engine,
    migration_directory: Path,
) -> MigrationResult:
    """Apply the reviewed migration after structural prerequisite preflight.

    The caller explicitly supplies both the database engine and migration
    directory. This function never discovers application configuration or
    creates legacy prerequisite tables.
    """

    version, filename, expected_checksum, expected_tables = _load_manifest(
        migration_directory
    )
    migration_path = migration_directory / filename
    migration_bytes = migration_path.read_bytes()
    actual_checksum = hashlib.sha256(migration_bytes).hexdigest()
    if actual_checksum != expected_checksum or actual_checksum != GENERATION_SCHEMA_CHECKSUM:
        raise MigrationPreconditionError("migration SQL checksum does not match the frozen manifest")
    if version != GENERATION_SCHEMA_VERSION:
        raise MigrationPreconditionError("manifest version differs from the supported schema version")
    if set(expected_tables) != set(GENERATION_RECORD_TABLE_NAMES):
        raise MigrationPreconditionError("manifest table vector differs from the eight canonical records")

    with engine.connect() as connection:
        catalog = sqlalchemy_catalog(connection)
        foundation = check_foundation_schema(catalog)
        if not foundation.ready:
            return MigrationResult(
                "not_ready",
                (),
                _check_issues(foundation),
            )

        table_names = catalog.table_names()
        history_exists = MIGRATION_HISTORY_TABLE in table_names
        present_generation_tables = set(expected_tables) & table_names
        if not history_exists and present_generation_tables:
            raise MigrationPreconditionError(
                "generation record tables exist without migration history; refusing partial repair"
            )

        existing_rows: list[tuple[str, str]] = []
        if history_exists:
            history = check_migration_history_schema(catalog)
            _require_ready(history)
            existing_rows = [
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    text(f"SELECT version, checksum FROM {MIGRATION_HISTORY_TABLE}")
                ).all()
            ]
            versions = tuple(row[0] for row in existing_rows)
            version_check = check_generation_version_set(versions, require_current=False)
            _require_ready(version_check)
            current_rows = [row for row in existing_rows if row[0] == version]
            if len(current_rows) > 1:
                raise MigrationPreconditionError("migration history contains duplicate version rows")
            if current_rows:
                if current_rows[0][1] != actual_checksum:
                    raise MigrationPreconditionError("applied migration checksum differs from the manifest")
                records = check_generation_record_schema(catalog)
                _require_ready(records)
                if connection.in_transaction():
                    connection.rollback()
                return MigrationResult("already_current", (version,))
            if present_generation_tables:
                raise MigrationPreconditionError(
                    "generation record tables exist without the matching migration history row"
                )

        if connection.in_transaction():
            connection.commit()
        with connection.begin():
            if not history_exists:
                connection.exec_driver_sql(
                    f"CREATE TABLE {MIGRATION_HISTORY_TABLE} ("
                    "version VARCHAR(64) NOT NULL PRIMARY KEY, "
                    "checksum VARCHAR(64) NOT NULL, "
                    "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            script = migration_bytes.decode("utf-8")
            for statement in _sql_statements(script):
                connection.exec_driver_sql(statement)
            connection.execute(
                text(
                    f"INSERT INTO {MIGRATION_HISTORY_TABLE} (version, checksum) "
                    "VALUES (:version, :checksum)"
                ),
                {"version": version, "checksum": actual_checksum},
            )
            catalog = sqlalchemy_catalog(connection)
            _require_ready(
                check_generation_record_schema(catalog),
                check_migration_history_schema(catalog),
            )
            applied_rows = [
                str(row[0])
                for row in connection.execute(
                    text(f"SELECT version FROM {MIGRATION_HISTORY_TABLE}")
                ).all()
            ]
            _require_ready(check_generation_version_set(applied_rows))

        return MigrationResult("applied", (version,))


def _load_manifest(
    migration_directory: Path,
) -> tuple[str, str, str, tuple[str, ...]]:
    manifest_path = migration_directory / "manifest.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MigrationPreconditionError("migration manifest cannot be read") from error
    if not isinstance(document, Mapping) or document.get("format_version") != 1:
        raise MigrationPreconditionError("unsupported migration manifest format")
    migrations = document.get("migrations")
    if not isinstance(migrations, list) or len(migrations) != 1:
        raise MigrationPreconditionError("expected one explicit generation migration")
    entry = migrations[0]
    if not isinstance(entry, Mapping):
        raise MigrationPreconditionError("migration entry must be an object")
    version = entry.get("version")
    filename = entry.get("file")
    checksum = entry.get("sha256")
    tables = entry.get("tables")
    if (
        not isinstance(version, str)
        or not isinstance(filename, str)
        or Path(filename).name != filename
        or not isinstance(checksum, str)
        or len(checksum) != 64
        or not isinstance(tables, list)
        or any(not isinstance(name, str) for name in tables)
    ):
        raise MigrationPreconditionError("migration entry is malformed")
    return version, filename, checksum, cast(tuple[str, ...], tuple(tables))


def _sql_statements(script: str) -> tuple[str, ...]:
    return tuple(statement.strip() for statement in script.split(";") if statement.strip())


__all__ = (
    "MIGRATION_HISTORY_TABLE",
    "MigrationResult",
    "MigrationPreconditionError",
    "apply_generation_migrations",
)
