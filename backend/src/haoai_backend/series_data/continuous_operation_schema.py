"""Explicit migration and read-only readiness for operation receipts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

MIGRATION_VERSION = "0005_continuous_chapter_operations"
MIGRATION_FILENAME = "0005_continuous_chapter_operations.sql"
MANIFEST_FILENAME = "continuous_chapter_operations_manifest.json"
RECEIPT_TABLE = "continuous_chapter_operation_receipts"
MIGRATION_TABLE = "continuous_chapter_operation_schema_migrations"
EXPECTED_TABLES = (MIGRATION_TABLE, RECEIPT_TABLE)


@dataclass(frozen=True, slots=True)
class ContinuousOperationSchemaStatus:
    status: Literal["ready", "not_ready"]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContinuousOperationMigrationResult:
    status: Literal["applied", "already_current"]
    version: str
    checksum: str


class ContinuousOperationMigrationError(RuntimeError):
    """The feature migration manifest or schema state is inconsistent."""


def _normalize_check_expression(value: str) -> str:
    expression = value.lower()
    # Remove PostgreSQL's reflected element/array casts first. SQLAlchemy may
    # report varchar checks as `= ANY ((ARRAY[...] )::text[])`.
    expression = re.sub(
        r"::\s*[a-z_][a-z_0-9]*(?:\s+[a-z_][a-z_0-9]*)*(?:\s*\[\s*\])?",
        "",
        expression,
    )
    expression = _strip_outer_parentheses(expression)
    any_match = re.search(r"=\s*any\s*\(", expression)
    if any_match is not None:
        left = expression[:any_match.start()].strip()
        array_expression = expression[any_match.end():].strip()
        if array_expression.endswith(")"):
            array_expression = array_expression[:-1].strip()
        while array_expression.startswith("(") and array_expression.endswith(")"):
            array_expression = array_expression[1:-1].strip()
        array_match = re.fullmatch(r"array\s*\[([^\]]*)\]", array_expression)
        if array_match is not None:
            expression = f"{left} IN ({array_match.group(1)})"
    expression = expression.replace("]", ")")
    return re.sub(r"[\s\"()`]+", "", expression).replace("::", "").lower()


def _strip_outer_parentheses(expression: str) -> str:
    while expression.startswith("(") and expression.endswith(")"):
        depth = 0
        inside_string = False
        encloses_all = True
        index = 0
        while index < len(expression):
            character = expression[index]
            if character == "'":
                if inside_string and index + 1 < len(expression) and expression[index + 1] == "'":
                    index += 2
                    continue
                inside_string = not inside_string
            elif not inside_string:
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                    if depth == 0 and index != len(expression) - 1:
                        encloses_all = False
                        break
            index += 1
        if not encloses_all or depth != 0:
            return expression
        expression = expression[1:-1].strip()
    return expression


def _default_migration_directory() -> Path:
    return Path(__file__).resolve().parents[3] / "migrations"


def _load_descriptor(migration_directory: Path) -> tuple[str, bytes]:
    manifest_path = migration_directory / MANIFEST_FILENAME
    sql_path = migration_directory / MIGRATION_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sql = sql_path.read_bytes()
    except (OSError, ValueError) as error:
        raise ContinuousOperationMigrationError("continuous operation migration files cannot be read") from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"format_version", "migrations"}
        or manifest.get("format_version") != 1
        or not isinstance(manifest.get("migrations"), list)
        or len(manifest["migrations"]) != 1
    ):
        raise ContinuousOperationMigrationError("continuous operation migration manifest is malformed")
    row = manifest["migrations"][0]
    if (
        not isinstance(row, dict)
        or set(row) != {"version", "file", "sha256", "tables"}
        or row.get("version") != MIGRATION_VERSION
        or row.get("file") != MIGRATION_FILENAME
        or row.get("tables") != list(EXPECTED_TABLES)
        or not isinstance(row.get("sha256"), str)
        or len(row["sha256"]) != 64
    ):
        raise ContinuousOperationMigrationError("continuous operation migration descriptor differs from its contract")
    checksum = hashlib.sha256(sql).hexdigest()
    if checksum != row["sha256"]:
        raise ContinuousOperationMigrationError("continuous operation migration checksum mismatch")
    return checksum, sql


def check_continuous_operation_schema(
    connection: Connection,
    *,
    migration_directory: Path | None = None,
) -> ContinuousOperationSchemaStatus:
    """Inspect only the feature receipt schema; never writes or creates tables."""

    try:
        checksum, _sql = _load_descriptor(migration_directory or _default_migration_directory())
        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())
        missing = sorted(set(EXPECTED_TABLES) - table_names)
        if missing:
            return ContinuousOperationSchemaStatus("not_ready", tuple(f"missing_table:{name}" for name in missing))

        expected_columns = {
            MIGRATION_TABLE: {"version", "checksum", "applied_at"},
            RECEIPT_TABLE: {
                "owner_id", "operation_id", "receipt_version", "editor_session_id",
                "series_id", "chapter_id", "command_kind", "request_body_sha256",
                "source_snapshot_digest", "target_draft_id", "target_manifest_digest",
                "outcome", "http_status", "result_identity", "canonical_response",
                "result_digest", "committed_at", "created_at",
            },
        }
        for table_name, wanted in expected_columns.items():
            actual = {column["name"] for column in inspector.get_columns(table_name)}
            if actual != wanted:
                return ContinuousOperationSchemaStatus("not_ready", (f"column_mismatch:{table_name}",))
        primary_key = inspector.get_pk_constraint(RECEIPT_TABLE).get("constrained_columns") or []
        if set(primary_key) != {"owner_id", "operation_id"} or len(primary_key) != 2:
            return ContinuousOperationSchemaStatus("not_ready", ("receipt_primary_key_mismatch",))
        indexes = {
            item.get("name"): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(RECEIPT_TABLE)
        }
        if indexes.get("ix_continuous_chapter_operation_target") != (
            "owner_id", "series_id", "chapter_id", "created_at",
        ):
            return ContinuousOperationSchemaStatus("not_ready", ("receipt_target_index_mismatch",))
        actual_checks = {
            _normalize_check_expression(str(item.get("sqltext") or ""))
            for item in inspector.get_check_constraints(RECEIPT_TABLE)
        }
        required_checks = {
            _normalize_check_expression("receipt_version = 1"),
            _normalize_check_expression("outcome IN ('committed', 'rejected')"),
            _normalize_check_expression("http_status >= 200 AND http_status < 500"),
        }
        if not required_checks.issubset(actual_checks):
            return ContinuousOperationSchemaStatus("not_ready", ("receipt_constraints_missing",))

        rows = connection.execute(
            text(f"SELECT version, checksum FROM {MIGRATION_TABLE}")
        ).all()
        if len(rows) != 1 or str(rows[0][0]) != MIGRATION_VERSION or str(rows[0][1]) != checksum:
            return ContinuousOperationSchemaStatus("not_ready", ("migration_history_mismatch",))
    except (ContinuousOperationMigrationError, SQLAlchemyError, OSError, ValueError, TypeError) as error:
        return ContinuousOperationSchemaStatus("not_ready", (f"schema_unavailable:{type(error).__name__}",))

    return ContinuousOperationSchemaStatus("ready")


def apply_continuous_operation_migration(
    engine: Engine,
    *,
    migration_directory: Path | None = None,
) -> ContinuousOperationMigrationResult:
    """Apply the feature migration explicitly to a caller-owned test or admin engine."""

    directory = migration_directory or _default_migration_directory()
    checksum, sql = _load_descriptor(directory)
    with engine.begin() as connection:
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())
        feature_tables = existing.intersection(EXPECTED_TABLES)
        if feature_tables:
            status = check_continuous_operation_schema(connection, migration_directory=directory)
            if status.status == "ready":
                return ContinuousOperationMigrationResult("already_current", MIGRATION_VERSION, checksum)
            raise ContinuousOperationMigrationError("partial or incompatible continuous operation schema")

        # This owned migration contains only straightforward CREATE statements.
        statements = [item.strip() for item in sql.decode("utf-8").split(";") if item.strip()]
        if len(statements) != 3:
            raise ContinuousOperationMigrationError("continuous operation migration statement count changed")
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.execute(
            text(
                f"INSERT INTO {MIGRATION_TABLE} (version, checksum, applied_at) "
                "VALUES (:version, :checksum, :applied_at)"
            ),
            {
                "version": MIGRATION_VERSION,
                "checksum": checksum,
                "applied_at": datetime.now(timezone.utc).replace(tzinfo=None),
            },
        )
        status = check_continuous_operation_schema(connection, migration_directory=directory)
        if status.status != "ready":
            raise ContinuousOperationMigrationError("new continuous operation schema failed readiness: " + ",".join(status.issues))
    return ContinuousOperationMigrationResult("applied", MIGRATION_VERSION, checksum)


__all__ = (
    "ContinuousOperationMigrationError",
    "ContinuousOperationMigrationResult",
    "ContinuousOperationSchemaStatus",
    "apply_continuous_operation_migration",
    "check_continuous_operation_schema",
)
