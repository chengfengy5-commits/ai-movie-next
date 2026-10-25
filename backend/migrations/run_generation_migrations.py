"""Explicit generation-schema migration runner with fail-closed preflight."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine, text

from haoai_backend.generation_core.schema import (
    CANVAS_RESULT_TARGET_INDEX_REQUIREMENTS,
    CANVAS_RESULT_TARGETS_SCHEMA_CHECKSUM,
    CANVAS_RESULT_TARGETS_SCHEMA_VERSION,
    CANVAS_RESULT_TARGETS_TABLE_NAME,
    GENERATION_MIGRATION_HISTORY_TABLE,
    GENERATION_RECORD_TABLE_NAMES,
    GENERATION_SCHEMA_CHECKSUM,
    GENERATION_SCHEMA_VERSION,
    check_canvas_result_target_schema,
    check_foundation_schema,
    check_generation_record_schema,
    check_generation_version_set,
    check_migration_history_schema,
    sqlalchemy_catalog,
)

MIGRATION_HISTORY_TABLE = GENERATION_MIGRATION_HISTORY_TABLE
_GENERATION_MIGRATION_TABLES = (
    "stream_fee_records",
    "generation_result_receipts",
    "application_records",
    "unit_fee_decisions",
    "execution_attempt_groups",
    "poll_reservations",
    "policy_stop_events",
    "local_continuation_work",
)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    status: Literal["applied", "already_current", "not_ready"]
    applied_versions: tuple[str, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Migration:
    version: str
    filename: str
    checksum: str
    tables: tuple[str, ...]
    sql: bytes


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


def _expected_migrations() -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    return (
        (
            GENERATION_SCHEMA_VERSION,
            "0001_generation_shared_records.sql",
            GENERATION_SCHEMA_CHECKSUM,
            _GENERATION_MIGRATION_TABLES,
        ),
        (
            CANVAS_RESULT_TARGETS_SCHEMA_VERSION,
            "0002_canvas_result_targets.sql",
            CANVAS_RESULT_TARGETS_SCHEMA_CHECKSUM,
            (CANVAS_RESULT_TARGETS_TABLE_NAME,),
        ),
    )


def _load_manifest(migration_directory: Path) -> tuple[_Migration, ...]:
    manifest_path = migration_directory / "manifest.json"
    try:
        document = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_manifest_object_pairs,
        )
    except (OSError, ValueError) as error:
        raise MigrationPreconditionError("migration manifest cannot be read") from error
    if (
        not isinstance(document, Mapping)
        or type(document.get("format_version")) is not int
        or document.get("format_version") != 1
        or set(document) != {"format_version", "migrations"}
    ):
        raise MigrationPreconditionError("unsupported migration manifest format")
    entries = document.get("migrations")
    expected = _expected_migrations()
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise MigrationPreconditionError("migration manifest is not the complete ordered set")

    migrations: list[_Migration] = []
    for raw, descriptor in zip(entries, expected, strict=True):
        version, filename, checksum, tables = descriptor
        if not isinstance(raw, Mapping) or set(raw) != {
            "version",
            "file",
            "sha256",
            "tables",
        }:
            raise MigrationPreconditionError("migration entry is malformed")
        raw_tables = raw.get("tables")
        if (
            raw.get("version") != version
            or raw.get("file") != filename
            or Path(str(raw.get("file", ""))).name != filename
            or raw.get("sha256") != checksum
            or not isinstance(raw_tables, list)
            or any(type(table) is not str for table in raw_tables)
            or tuple(raw_tables) != tables
        ):
            raise MigrationPreconditionError("migration descriptor differs from the frozen ordered contract")
        migration_path = migration_directory / filename
        try:
            sql = migration_path.read_bytes()
        except OSError as error:
            raise MigrationPreconditionError(f"migration SQL cannot be read: {filename}") from error
        if hashlib.sha256(sql).hexdigest() != checksum:
            raise MigrationPreconditionError(f"migration SQL checksum mismatch: {version}")
        migrations.append(_Migration(version, filename, checksum, tables, sql))
    return tuple(migrations)


def _has_v2_structure(catalog, table_names: set[str]) -> bool:
    if CANVAS_RESULT_TARGETS_TABLE_NAME in table_names:
        return True
    receipt_table = "generation_result_receipts"
    if receipt_table not in table_names:
        return False
    indexes = catalog.table(receipt_table).indexes
    expected_requirements = CANVAS_RESULT_TARGET_INDEX_REQUIREMENTS[receipt_table]
    return any(
        index.name == requirement.name or index.columns == requirement.columns
        for requirement in expected_requirements
        for index in indexes
    )


def _applied_rows(connection) -> list[tuple[str, str]]:
    return [
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            text(
                f"SELECT version, checksum FROM {MIGRATION_HISTORY_TABLE} "
                "ORDER BY version"
            )
        ).all()
    ]


def apply_generation_migrations(
    engine: Engine,
    migration_directory: Path,
) -> MigrationResult:
    """Apply the reviewed migrations after full, read-only preflight."""

    migrations = _load_manifest(migration_directory)
    known_checksums = {item.version: item.checksum for item in migrations}
    with engine.connect() as connection:
        catalog = sqlalchemy_catalog(connection)
        foundation = check_foundation_schema(catalog)
        if not foundation.ready:
            return MigrationResult("not_ready", (), _check_issues(foundation))

        table_names = catalog.table_names()
        history_exists = MIGRATION_HISTORY_TABLE in table_names
        existing_rows: list[tuple[str, str]] = []
        if history_exists:
            history = check_migration_history_schema(catalog)
            _require_ready(history)
            existing_rows = _applied_rows(connection)

        versions = tuple(version for version, _checksum in existing_rows)
        _require_ready(check_generation_version_set(versions, require_current=False))
        for version, checksum in existing_rows:
            if known_checksums.get(version) != checksum:
                raise MigrationPreconditionError(
                    f"applied migration checksum differs from the manifest: {version}"
                )

        applied = set(versions)
        for migration in migrations:
            present = set(migration.tables) & table_names
            if present and migration.version not in applied:
                raise MigrationPreconditionError(
                    f"migration structures exist without their history row: {migration.version}"
                )
        if CANVAS_RESULT_TARGETS_SCHEMA_VERSION not in applied and _has_v2_structure(
            catalog, table_names
        ):
            raise MigrationPreconditionError(
                "version 0002 index structures exist without their history row"
            )
        if GENERATION_SCHEMA_VERSION not in applied and (
            set(GENERATION_RECORD_TABLE_NAMES) & table_names
        ):
            raise MigrationPreconditionError(
                "generation record tables exist without migration history; refusing partial repair"
            )

        if GENERATION_SCHEMA_VERSION in applied:
            records = check_generation_record_schema(catalog)
            _require_ready(records)
        if CANVAS_RESULT_TARGETS_SCHEMA_VERSION in applied:
            targets = check_canvas_result_target_schema(catalog)
            _require_ready(targets)

        to_apply = tuple(item for item in migrations if item.version not in applied)
        if not to_apply:
            if connection.in_transaction():
                connection.rollback()
            return MigrationResult("already_current", ())

        if connection.in_transaction():
            connection.rollback()
        with connection.begin():
            if not history_exists:
                connection.exec_driver_sql(
                    f"CREATE TABLE {MIGRATION_HISTORY_TABLE} ("
                    "version VARCHAR(64) NOT NULL PRIMARY KEY, "
                    "checksum VARCHAR(64) NOT NULL, "
                    "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            for migration in to_apply:
                script = migration.sql.decode("utf-8")
                for statement in _sql_statements(script):
                    connection.exec_driver_sql(statement)
                connection.execute(
                    text(
                        f"INSERT INTO {MIGRATION_HISTORY_TABLE} (version, checksum) "
                        "VALUES (:version, :checksum)"
                    ),
                    {"version": migration.version, "checksum": migration.checksum},
                )

            catalog = sqlalchemy_catalog(connection)
            _require_ready(
                check_generation_record_schema(catalog),
                check_canvas_result_target_schema(catalog),
                check_migration_history_schema(catalog),
            )
            final_rows = _applied_rows(connection)
            _require_ready(
                check_generation_version_set(
                    tuple(version for version, _checksum in final_rows)
                )
            )
            if any(known_checksums.get(version) != checksum for version, checksum in final_rows):
                raise MigrationPreconditionError("applied migration checksum differs from the frozen manifest")

        return MigrationResult("applied", tuple(item.version for item in to_apply))


def _sql_statements(script: str) -> tuple[str, ...]:
    return tuple(statement.strip() for statement in script.split(";") if statement.strip())


def _unique_manifest_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("migration manifest contains a duplicate key")
        result[key] = value
    return result


__all__ = (
    "MIGRATION_HISTORY_TABLE",
    "MigrationResult",
    "MigrationPreconditionError",
    "apply_generation_migrations",
)
