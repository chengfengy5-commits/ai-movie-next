"""Explicit PostgreSQL migration and read-only credential schema checks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from haoai_backend.generation_runtime.readiness import check_generation_readiness

from .persistence import REGISTRY_TABLES, credential_schema_migrations


CREDENTIAL_SCHEMA_VERSION = "0001_credential_producer"
CREDENTIAL_MIGRATION_FILENAME = "0001_credential_producer.sql"
CREDENTIAL_MANIFEST_FILENAME = "manifest.json"
OWN_TABLE_NAMES = tuple(table.name for table in REGISTRY_TABLES)
REQUIRED_TRIGGERS = {
    "trg_generation_credential_versions_immutable",
    "trg_generation_prepared_payloads_immutable",
    "trg_generation_credential_principals_immutable_identity",
    "trg_generation_credential_bindings_guard",
    "trg_generation_credential_migrations_immutable",
}
POSTGRES_ROW_BEFORE_UPDATE = 1 | 2 | 16
POSTGRES_ROW_BEFORE_UPDATE_OR_DELETE = POSTGRES_ROW_BEFORE_UPDATE | 8
POSTGRES_TRIGGER_EXPECTATIONS = {
    "trg_generation_credential_versions_immutable": (
        "generation_credential_versions",
        POSTGRES_ROW_BEFORE_UPDATE_OR_DELETE,
        "reject_generation_credential_immutable_change",
    ),
    "trg_generation_prepared_payloads_immutable": (
        "generation_prepared_payloads",
        POSTGRES_ROW_BEFORE_UPDATE_OR_DELETE,
        "reject_generation_credential_immutable_change",
    ),
    "trg_generation_credential_principals_immutable_identity": (
        "generation_credential_principals",
        POSTGRES_ROW_BEFORE_UPDATE_OR_DELETE,
        "guard_generation_credential_principal_update",
    ),
    "trg_generation_credential_bindings_guard": (
        "generation_credential_bindings",
        POSTGRES_ROW_BEFORE_UPDATE,
        "guard_generation_credential_binding_update",
    ),
    "trg_generation_credential_migrations_immutable": (
        "generation_credential_schema_migrations",
        POSTGRES_ROW_BEFORE_UPDATE_OR_DELETE,
        "reject_generation_credential_migration_history_change",
    ),
}
POSTGRES_GUARD_FUNCTIONS = {
    "reject_generation_credential_immutable_change": """
        BEGIN
            RAISE EXCEPTION 'immutable generation credential record';
        END;
    """,
    "guard_generation_credential_principal_update": """
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'immutable generation credential principal';
            END IF;
            IF NEW.principal_id <> OLD.principal_id
                OR NEW.provider <> OLD.provider
                OR NEW.issuer_namespace <> OLD.issuer_namespace
                OR NEW.stable_account_subject <> OLD.stable_account_subject
                OR NEW.owner_scope <> OLD.owner_scope
                OR NEW.record_id <> OLD.record_id
                OR NEW.evidence_ref <> OLD.evidence_ref
                OR NEW.evidence_version <> OLD.evidence_version
                OR NEW.created_by <> OLD.created_by
                OR NEW.created_at <> OLD.created_at
                OR NOT (OLD.source_state = 'valid' AND NEW.source_state = 'revoked') THEN
                RAISE EXCEPTION 'invalid generation credential principal change';
            END IF;
            RETURN NEW;
        END;
    """,
    "guard_generation_credential_binding_update": """
        BEGIN
            IF NEW.binding_id <> OLD.binding_id
                OR NEW.source <> OLD.source
                OR NEW.row_id <> OLD.row_id
                OR NEW.owner_or_system <> OLD.owner_or_system
                OR NEW.provider <> OLD.provider
                OR NEW.revision <> OLD.revision + 1
                OR NEW.current_version <> OLD.current_version + 1
                OR (OLD.enabled = FALSE AND NEW.enabled = TRUE) THEN
                RAISE EXCEPTION 'invalid generation credential binding change';
            END IF;
            RETURN NEW;
        END;
    """,
    "reject_generation_credential_migration_history_change": """
        BEGIN
            RAISE EXCEPTION 'immutable generation credential migration history';
        END;
    """,
}
SQLITE_GUARD_TABLES = {
    "trg_generation_credential_versions_immutable_update": "generation_credential_versions",
    "trg_generation_credential_versions_immutable_delete": "generation_credential_versions",
    "trg_generation_prepared_payloads_immutable_update": "generation_prepared_payloads",
    "trg_generation_prepared_payloads_immutable_delete": "generation_prepared_payloads",
    "trg_generation_credential_principals_immutable_identity_update": "generation_credential_principals",
    "trg_generation_credential_principals_immutable_identity_delete": "generation_credential_principals",
    "trg_generation_credential_bindings_guard": "generation_credential_bindings",
    "trg_generation_credential_migrations_immutable_update": "generation_credential_schema_migrations",
    "trg_generation_credential_migrations_immutable_delete": "generation_credential_schema_migrations",
}


@dataclass(frozen=True, slots=True)
class CredentialSchemaStatus:
    status: str
    issues: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True, slots=True)
class MigrationResult:
    status: str
    issues: tuple[str, ...] = ()


class _PostflightRejected(Exception):
    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = issues


def check_legacy_projection_schema(connection: Connection) -> CredentialSchemaStatus:
    """Check only actual columns/keys used by exact legacy projections."""

    inspector = inspect(connection)
    required = {
        "users": ({"id", "is_superuser"}, ("id",)),
        "system_models": (
            {"id", "category", "provider", "model", "base_url", "api_key", "is_active"},
            ("id",),
        ),
        "model_configs": (
            {"id", "user_id", "category", "provider", "model", "base_url", "api_key"},
            ("id",),
        ),
    }
    issues: list[str] = []
    table_names = set(inspector.get_table_names())
    for table_name, (columns, primary_key) in required.items():
        if table_name not in table_names:
            issues.append(f"legacy_table_missing:{table_name}")
            continue
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing = columns - actual_columns
        if missing:
            issues.append(f"legacy_columns_missing:{table_name}:{','.join(sorted(missing))}")
        actual_pk = tuple(inspector.get_pk_constraint(table_name).get("constrained_columns") or ())
        if actual_pk != primary_key:
            issues.append(f"legacy_primary_key_mismatch:{table_name}")
    return CredentialSchemaStatus("ready" if not issues else "not_ready", tuple(issues))


def check_credential_registry_schema(connection: Connection) -> CredentialSchemaStatus:
    """Validate current own tables, declared constraints, history, and guards."""

    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    present = table_names.intersection(OWN_TABLE_NAMES)
    if not present:
        return CredentialSchemaStatus("not_ready", ("registry_schema_missing",))
    missing_tables = set(OWN_TABLE_NAMES) - table_names
    if missing_tables:
        return CredentialSchemaStatus(
            "not_ready", (f"registry_schema_partial:{','.join(sorted(missing_tables))}",)
        )
    prefix_tables = {
        name for name in table_names
        if name.startswith("generation_credential_") or name == "generation_prepared_payloads"
    }
    if prefix_tables != set(OWN_TABLE_NAMES):
        return CredentialSchemaStatus("not_ready", ("registry_owned_table_set_mismatch",))

    issues: list[str] = []
    for table in REGISTRY_TABLES:
        issues.extend(_check_table_shape(connection, table))
    issues.extend(_check_guards(connection))
    if not issues:
        rows = connection.execute(
            text(
                f"SELECT version, checksum FROM {credential_schema_migrations.name} "
                "ORDER BY version"
            )
        ).all()
        if len(rows) != 1:
            issues.append("registry_migration_history_mismatch")
        elif rows[0][0] != CREDENTIAL_SCHEMA_VERSION:
            issues.append("registry_migration_version_mismatch")
        else:
            manifest = _manifest_for_default_sql()
            if manifest is None or str(rows[0][1]) != manifest["sha256"]:
                issues.append("registry_migration_checksum_mismatch")
    return CredentialSchemaStatus("ready" if not issues else "not_ready", tuple(issues))


def apply_credential_registry_migrations(
    explicit_engine: Engine,
    explicit_directory: str | Path,
) -> MigrationResult:
    """Apply the single owned migration in an explicit operator-owned transaction."""

    if explicit_engine.dialect.name != "postgresql":
        return MigrationResult("not_ready", ("postgresql_required_for_migration",))
    directory = Path(explicit_directory)
    manifest = _read_manifest(directory)
    if manifest is None:
        return MigrationResult("not_ready", ("registry_manifest_invalid",))
    if manifest["version"] != CREDENTIAL_SCHEMA_VERSION:
        return MigrationResult("not_ready", ("registry_migration_version_unsupported",))
    if tuple(manifest["tables"]) != OWN_TABLE_NAMES:
        return MigrationResult("not_ready", ("registry_manifest_table_set_mismatch",))

    with explicit_engine.connect() as connection:
        with Session(bind=connection) as prerequisite_session:
            prerequisite = check_generation_readiness(prerequisite_session)
        if prerequisite.status != "ready":
            details = tuple(f"foundation:{issue.code}" for issue in prerequisite.issues)
            return MigrationResult("not_ready", details or ("foundation_not_ready",))
        legacy = check_legacy_projection_schema(connection)
        if not legacy.ready:
            return MigrationResult("not_ready", legacy.issues)
        existing = set(inspect(connection).get_table_names()).intersection(OWN_TABLE_NAMES)
        if existing:
            current = check_credential_registry_schema(connection)
            return MigrationResult(
                "already_current" if current.ready else "not_ready",
                current.issues,
            )

    statements = _split_sql(str(manifest["_sql"]))
    if not statements:
        return MigrationResult("not_ready", ("registry_migration_empty",))
    try:
        with explicit_engine.begin() as connection:
            # Recheck immediately before DDL to reject a concurrently-created partial schema.
            existing = set(inspect(connection).get_table_names()).intersection(OWN_TABLE_NAMES)
            if existing:
                return MigrationResult("not_ready", ("registry_schema_changed_during_migration",))
            for statement in statements:
                connection.exec_driver_sql(statement)
            connection.execute(
                credential_schema_migrations.insert().values(
                    version=CREDENTIAL_SCHEMA_VERSION,
                    checksum=manifest["sha256"],
                    applied_at=datetime.now(timezone.utc),
                )
            )
            postflight = check_credential_registry_schema(connection)
            if not postflight.ready:
                raise _PostflightRejected(postflight.issues)
    except _PostflightRejected as exc:
        return MigrationResult(
            "not_ready", tuple(f"postflight:{issue}" for issue in exc.issues)
        )
    except Exception as exc:
        return MigrationResult("not_ready", (f"registry_migration_failed:{type(exc).__name__}",))
    return MigrationResult("applied")


def _check_table_shape(connection: Connection, table: Any) -> list[str]:
    inspector = inspect(connection)
    name = table.name
    issues: list[str] = []
    actual_columns = inspector.get_columns(name)
    expected_names = [column.name for column in table.columns]
    actual_names = [column["name"] for column in actual_columns]
    if actual_names != expected_names:
        issues.append(f"columns_mismatch:{name}")
        return issues
    for expected, actual in zip(table.columns, actual_columns):
        if bool(actual["nullable"]) != bool(expected.nullable):
            issues.append(f"column_nullability_mismatch:{name}:{expected.name}")
        if not _types_match(expected.type, actual["type"], connection.dialect.name):
            issues.append(f"column_type_mismatch:{name}:{expected.name}")
    expected_pk = tuple(column.name for column in table.primary_key.columns)
    actual_pk = tuple(inspector.get_pk_constraint(name).get("constrained_columns") or ())
    if actual_pk != expected_pk:
        issues.append(f"primary_key_mismatch:{name}")

    expected_unique = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    actual_unique = {
        item.get("name"): tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(name)
    }
    for constraint_name, columns in expected_unique.items():
        if actual_unique.get(constraint_name) != columns:
            issues.append(f"unique_constraint_mismatch:{name}:{constraint_name}")

    expected_fks = {
        constraint.name: (
            tuple(column.name for column in constraint.columns),
            tuple(element.column.table.name for element in constraint.elements),
            tuple(element.column.name for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    expected_fk_constraints = {
        constraint.name: constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    actual_fks = {}
    for item in inspector.get_foreign_keys(name):
        actual_fks[item.get("name")] = (
            tuple(item.get("constrained_columns") or ()),
            (item.get("referred_table"),) * len(item.get("constrained_columns") or ()),
            tuple(item.get("referred_columns") or ()),
        )
    for constraint_name, shape in expected_fks.items():
        if actual_fks.get(constraint_name) != shape:
            issues.append(f"foreign_key_mismatch:{name}:{constraint_name}")

    expected_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }
    actual_indexes = {
        item.get("name"): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(name)
    }
    for index_name, columns in expected_indexes.items():
        if actual_indexes.get(index_name) != columns:
            issues.append(f"index_mismatch:{name}:{index_name}")

    expected_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    actual_checks = {
        item.get("name"): str(item.get("sqltext", ""))
        for item in inspector.get_check_constraints(name)
    }
    column_types = {column.name: column.type for column in table.columns}
    for constraint_name, expression in expected_checks.items():
        actual = actual_checks.get(constraint_name)
        if connection.dialect.name == "postgresql":
            matches = actual is not None and _postgres_check_expressions_equivalent(
                expression, actual, column_types
            )
        else:
            matches = actual is not None and (
                _normalize_expression(actual) == _normalize_expression(expression)
            )
        if not matches:
            issues.append(f"check_constraint_mismatch:{name}:{constraint_name}")

    if connection.dialect.name == "postgresql":
        issues.extend(
            _check_postgres_constraints(
                connection,
                name,
                expected_unique,
                expected_fk_constraints,
                expected_checks,
            )
        )
    return issues


def _check_postgres_constraints(
    connection: Connection,
    table_name: str,
    unique: dict[str, tuple[str, ...]],
    foreign_keys: dict[str, ForeignKeyConstraint],
    checks: dict[str, str],
) -> list[str]:
    rows = connection.execute(
        text(
            "SELECT c.conname, c.contype, c.convalidated, c.condeferrable, c.condeferred "
            "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = current_schema() AND t.relname = :table_name"
        ),
        {"table_name": table_name},
    ).mappings().all()
    by_name = {row["conname"]: row for row in rows}
    expected_names = set(unique) | set(foreign_keys) | set(checks) | {f"{table_name}_pkey"}
    issues: list[str] = []
    if set(by_name) != expected_names:
        issues.append(f"postgres_constraint_set_mismatch:{table_name}")
    for name, row in by_name.items():
        if not row["convalidated"]:
            issues.append(f"postgres_constraint_unvalidated:{table_name}:{name}")
        if name in unique and row["contype"] != "u":
            issues.append(f"postgres_unique_not_constraint:{table_name}:{name}")
        if name in foreign_keys and row["contype"] != "f":
            issues.append(f"postgres_foreign_key_kind_mismatch:{table_name}:{name}")
        if name in checks and row["contype"] != "c":
            issues.append(f"postgres_check_kind_mismatch:{table_name}:{name}")
    action_names = {
        "a": "NO ACTION",
        "r": "RESTRICT",
        "c": "CASCADE",
        "n": "SET NULL",
        "d": "SET DEFAULT",
    }
    for name, constraint in foreign_keys.items():
        target = constraint.elements[0].column
        actual = connection.execute(
            text(
                "SELECT c.conrelid AS source_oid, source_ns.nspname AS source_schema, "
                "source_table.relname AS source_table, c.confrelid AS target_oid, "
                "target_ns.nspname AS target_schema, target_table.relname AS target_table, "
                "c.confupdtype, c.confdeltype, c.condeferrable, c.condeferred, "
                "ARRAY(SELECT attribute.attname::text FROM unnest(c.conkey) "
                "WITH ORDINALITY AS key(attnum, ordinal) JOIN pg_attribute attribute "
                "ON attribute.attrelid = c.conrelid AND attribute.attnum = key.attnum "
                "ORDER BY key.ordinal) AS constrained_columns, "
                "ARRAY(SELECT attribute.attname::text FROM unnest(c.confkey) "
                "WITH ORDINALITY AS key(attnum, ordinal) JOIN pg_attribute attribute "
                "ON attribute.attrelid = c.confrelid AND attribute.attnum = key.attnum "
                "ORDER BY key.ordinal) AS referred_columns, "
                "(SELECT expected_table.oid FROM pg_class expected_table "
                "JOIN pg_namespace expected_ns ON expected_ns.oid = expected_table.relnamespace "
                "WHERE expected_ns.nspname = COALESCE(CAST(:expected_schema AS text), current_schema()) "
                "AND expected_table.relname = :expected_table) AS expected_target_oid "
                "FROM pg_constraint c "
                "JOIN pg_class source_table ON source_table.oid = c.conrelid "
                "JOIN pg_namespace source_ns ON source_ns.oid = source_table.relnamespace "
                "JOIN pg_class target_table ON target_table.oid = c.confrelid "
                "JOIN pg_namespace target_ns ON target_ns.oid = target_table.relnamespace "
                "WHERE c.conname = :constraint_name "
                "AND source_ns.nspname = current_schema() AND source_table.relname = :source_table"
            ),
            {
                "constraint_name": name,
                "source_table": table_name,
                "expected_schema": target.table.schema,
                "expected_table": target.table.name,
            },
        ).mappings().first()
        expected_local = tuple(column.name for column in constraint.columns)
        expected_remote = tuple(element.column.name for element in constraint.elements)
        expected_schema = target.table.schema
        expected_delete = (constraint.ondelete or "NO ACTION").upper()
        expected_update = (constraint.onupdate or "NO ACTION").upper()
        expected_deferrable = bool(constraint.deferrable)
        expected_deferred = expected_deferrable and (
            str(constraint.initially or "IMMEDIATE").upper() == "DEFERRED"
        )
        if (
            actual is None
            or actual["source_table"] != table_name
            or actual["target_schema"] != (expected_schema or actual["source_schema"])
            or actual["target_table"] != target.table.name
            or actual["expected_target_oid"] is None
            or actual["target_oid"] != actual["expected_target_oid"]
            or tuple(actual["constrained_columns"] or ()) != expected_local
            or tuple(actual["referred_columns"] or ()) != expected_remote
            or action_names.get(str(actual["confdeltype"])) != expected_delete
            or action_names.get(str(actual["confupdtype"])) != expected_update
            or bool(actual["condeferrable"]) != expected_deferrable
            or bool(actual["condeferred"]) != expected_deferred
        ):
            issues.append(f"postgres_foreign_key_mismatch:{table_name}:{name}")
    return issues


def _check_guards(connection: Connection) -> list[str]:
    if connection.dialect.name == "postgresql":
        rows = connection.execute(
            text(
                "SELECT t.tgname, t.tgenabled, t.tgtype, c.relname AS table_name, "
                "p.proname AS function_name, p.prosrc AS function_body, l.lanname "
                "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "JOIN pg_language l ON l.oid = p.prolang "
                "WHERE NOT t.tgisinternal AND n.nspname = current_schema() "
                "AND c.relname IN ('generation_credential_principals', "
                "'generation_credential_bindings', 'generation_credential_versions', "
                "'generation_prepared_payloads', 'generation_credential_schema_migrations')"
            )
        ).all()
        actual = {str(row[0]): row for row in rows}
        issues: list[str] = []
        if set(actual) != REQUIRED_TRIGGERS:
            issues.append("registry_immutable_guard_set_mismatch")
        for name, (table_name, trigger_type, function_name) in POSTGRES_TRIGGER_EXPECTATIONS.items():
            row = actual.get(name)
            if row is None:
                continue
            body = POSTGRES_GUARD_FUNCTIONS[function_name]
            if (
                str(row[1]) != "O"
                or int(row[2]) != trigger_type
                or str(row[3]) != table_name
                or str(row[4]) != function_name
                or str(row[6]) != "plpgsql"
                or _normalize_expression(str(row[5])) != _normalize_expression(body)
            ):
                issues.append(f"registry_immutable_guard_definition_mismatch:{name}")
        return issues
    elif connection.dialect.name == "sqlite":
        rows = connection.execute(
            text(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger' AND "
                "(tbl_name LIKE 'generation_credential_%' OR tbl_name = 'generation_prepared_payloads')"
            )
        ).all()
        actual = {str(row[0]): row for row in rows}
        issues: list[str] = []
        foreign_keys_enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        if foreign_keys_enabled != 1:
            issues.append("registry_foreign_keys_disabled")
        if set(actual) != set(SQLITE_GUARD_TABLES):
            issues.append("registry_immutable_guard_set_mismatch")
        for name, table_name in SQLITE_GUARD_TABLES.items():
            row = actual.get(name)
            if row is None:
                continue
            action = "DELETE" if name.endswith("_delete") else "UPDATE"
            expected_sql = (
                f"CREATE TRIGGER {name} BEFORE {action} ON {table_name} "
                "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
            )
            if (
                str(row[1]) != table_name
                or _normalize_expression(str(row[2] or ""))
                != _normalize_expression(expected_sql)
            ):
                issues.append(f"registry_immutable_guard_definition_mismatch:{name}")
        return issues
    else:
        return ["registry_guard_catalog_unsupported"]


def _types_match(expected: Any, actual: Any, dialect: str) -> bool:
    from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, Text

    if isinstance(expected, String) and not isinstance(expected, Text):
        return isinstance(actual, String) and getattr(actual, "length", None) == expected.length
    if isinstance(expected, Text):
        return isinstance(actual, Text)
    if isinstance(expected, Integer):
        return isinstance(actual, Integer)
    if isinstance(expected, Boolean):
        return isinstance(actual, Boolean)
    if isinstance(expected, LargeBinary):
        return isinstance(actual, LargeBinary)
    if isinstance(expected, DateTime):
        return isinstance(actual, DateTime) and (
            dialect == "sqlite" or bool(getattr(actual, "timezone", False)) == bool(expected.timezone)
        )
    return type(expected) is type(actual)


class _CheckExpressionParser:
    """Small fail-closed parser for the fixed CHECK expressions owned here."""

    _token = re.compile(
        r"\s+|(?:'(?:''|[^'])*')|(?:::|>=|<=|<>|!=|[=><(),\[\]])|"
        r"(?:[A-Za-z_][A-Za-z_0-9$]*)|(?:[0-9]+)"
    )

    def __init__(self, expression: str) -> None:
        self._tokens: list[tuple[str, str]] = []
        position = 0
        while position < len(expression):
            match = self._token.match(expression, position)
            if match is None:
                raise ValueError("unsupported CHECK token")
            value = match.group(0)
            position = match.end()
            if value.isspace():
                continue
            if value.startswith("'"):
                self._tokens.append(("string", value[1:-1].replace("''", "'")))
            elif value[0].isalpha() or value[0] == "_":
                self._tokens.append(("word", value.lower()))
            elif value[0].isdigit():
                self._tokens.append(("number", value))
            else:
                self._tokens.append(("symbol", value))
        self._position = 0

    def parse(self) -> tuple[Any, ...]:
        value = self._parse_or()
        if self._position != len(self._tokens):
            raise ValueError("trailing CHECK tokens")
        return value

    def _parse_or(self) -> tuple[Any, ...]:
        value = self._parse_and()
        while self._take_word("or"):
            value = ("or", value, self._parse_and())
        return value

    def _parse_and(self) -> tuple[Any, ...]:
        value = self._parse_not()
        while self._take_word("and"):
            value = ("and", value, self._parse_not())
        return value

    def _parse_not(self) -> tuple[Any, ...]:
        if self._take_word("not"):
            return ("not", self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> tuple[Any, ...]:
        left = self._parse_primary()
        if self._take_word("is"):
            negate = self._take_word("not")
            self._expect_word("null")
            return ("is-not-null" if negate else "is-null", left)
        if self._take_word("in"):
            self._expect_symbol("(")
            values = [self._parse_primary()]
            while self._take_symbol(","):
                values.append(self._parse_primary())
            self._expect_symbol(")")
            return ("in", left, tuple(values))
        operator = self._take_operator()
        if operator is None:
            return left
        if operator == "=" and self._take_word("any"):
            self._expect_symbol("(")
            right = self._parse_or()
            self._expect_symbol(")")
            return ("compare", operator, left, ("any", right))
        return ("compare", operator, left, self._parse_primary())

    def _parse_primary(self) -> tuple[Any, ...]:
        if self._take_symbol("("):
            value = self._parse_or()
            self._expect_symbol(")")
            return self._parse_casts(value)
        token = self._peek()
        if token is None:
            raise ValueError("missing CHECK expression")
        if token == ("word", "array"):
            self._position += 1
            self._expect_symbol("[")
            values: list[tuple[Any, ...]] = []
            if not self._take_symbol("]"):
                values.append(self._parse_primary())
                while self._take_symbol(","):
                    values.append(self._parse_primary())
                self._expect_symbol("]")
            return self._parse_casts(("array", tuple(values)))
        if token[0] == "string":
            self._position += 1
            return self._parse_casts(("string", token[1]))
        if token[0] == "number":
            self._position += 1
            return self._parse_casts(("number", int(token[1])))
        if token[0] != "word":
            raise ValueError("unsupported CHECK expression")
        self._position += 1
        name = token[1]
        if name in {"true", "false"}:
            return self._parse_casts(("boolean", name == "true"))
        if name == "null":
            return self._parse_casts(("null",))
        if self._take_symbol("("):
            arguments: list[tuple[Any, ...]] = []
            if not self._take_symbol(")"):
                arguments.append(self._parse_or())
                while self._take_symbol(","):
                    arguments.append(self._parse_or())
                self._expect_symbol(")")
            return self._parse_casts(("function", name, tuple(arguments)))
        return self._parse_casts(("column", name))

    def _parse_casts(self, value: tuple[Any, ...]) -> tuple[Any, ...]:
        while self._take_symbol("::"):
            type_name = self._expect_type_name()
            length: int | None = None
            if self._take_symbol("("):
                token = self._take_kind("number")
                if token is None:
                    raise ValueError("unsupported CHECK type width")
                length = int(token)
                self._expect_symbol(")")
            dimensions = 0
            while self._take_symbol("["):
                self._expect_symbol("]")
                dimensions += 1
            value = ("cast", value, type_name, length, dimensions)
        return value

    def _expect_type_name(self) -> str:
        token = self._take_kind("word")
        if token is None:
            raise ValueError("unsupported CHECK cast")
        if token == "character" and self._take_word("varying"):
            return "character varying"
        return token

    def _take_operator(self) -> str | None:
        token = self._peek()
        if token is not None and token[0] == "symbol" and token[1] in {
            "=", "!=", "<>", ">", "<", ">=", "<=",
        }:
            self._position += 1
            return token[1]
        return None

    def _take_kind(self, kind: str) -> str | None:
        token = self._peek()
        if token is None or token[0] != kind:
            return None
        self._position += 1
        return token[1]

    def _take_word(self, word: str) -> bool:
        token = self._peek()
        if token == ("word", word):
            self._position += 1
            return True
        return False

    def _take_symbol(self, symbol: str) -> bool:
        token = self._peek()
        if token == ("symbol", symbol):
            self._position += 1
            return True
        return False

    def _expect_word(self, word: str) -> None:
        if not self._take_word(word):
            raise ValueError("unexpected CHECK keyword")

    def _expect_symbol(self, symbol: str) -> None:
        if not self._take_symbol(symbol):
            raise ValueError("unexpected CHECK punctuation")

    def _peek(self) -> tuple[str, str] | None:
        if self._position >= len(self._tokens):
            return None
        return self._tokens[self._position]


def _postgres_check_expressions_equivalent(
    expected: str,
    actual: str,
    column_types: dict[str, Any],
) -> bool:
    from sqlalchemy import String, Text

    try:
        expected_tree = _CheckExpressionParser(expected).parse()
        actual_tree = _CheckExpressionParser(actual).parse()
        return _normalize_check_tree(expected_tree, column_types) == _normalize_check_tree(
            actual_tree, column_types
        )
    except (RecursionError, TypeError, ValueError):
        return False


def _normalize_check_tree(
    expression: tuple[Any, ...],
    column_types: dict[str, Any],
) -> tuple[Any, ...]:
    from sqlalchemy import String, Text

    kind = expression[0]
    if kind == "cast":
        child = _normalize_check_tree(expression[1], column_types)
        type_name, length, dimensions = expression[2:]
        if child[0] == "column" and dimensions == 0 and length is None:
            column_type = column_types.get(child[1])
            if isinstance(column_type, (String, Text)) and type_name == "text":
                return child
        if child[0] == "string" and dimensions == 0 and length is None:
            if type_name in {"text", "varchar", "character varying"}:
                return child
        if child[0] == "array" and type_name == "text" and length is None and dimensions == 1:
            return child
        return ("cast", child, type_name, length, dimensions)
    if kind == "array":
        return ("array", tuple(_normalize_check_tree(value, column_types) for value in expression[1]))
    if kind == "compare":
        left = _normalize_check_tree(expression[2], column_types)
        right = _normalize_check_tree(expression[3], column_types)
        if expression[1] == "=" and right[0] == "any":
            values = right[1]
            if (
                values[0] == "array"
                and left[0] == "column"
                and isinstance(column_types.get(left[1]), (String, Text))
                and all(value[0] == "string" for value in values[1])
            ):
                return ("in", left, values[1])
        return ("compare", expression[1], left, right)
    if kind == "any":
        return ("any", _normalize_check_tree(expression[1], column_types))
    if kind in {"and", "or"}:
        return (
            kind,
            _normalize_check_tree(expression[1], column_types),
            _normalize_check_tree(expression[2], column_types),
        )
    if kind == "not":
        return ("not", _normalize_check_tree(expression[1], column_types))
    if kind in {"is-null", "is-not-null"}:
        return (kind, _normalize_check_tree(expression[1], column_types))
    if kind == "in":
        return (
            "in",
            _normalize_check_tree(expression[1], column_types),
            tuple(_normalize_check_tree(value, column_types) for value in expression[2]),
        )
    if kind == "function":
        return (
            "function",
            expression[1],
            tuple(_normalize_check_tree(value, column_types) for value in expression[2]),
        )
    return expression


def _normalize_expression(expression: str) -> str:
    # Collapse only SQL whitespace; casts, literals, operators, and grouping remain visible.
    pieces: list[str] = []
    in_quote = False
    pending_space = False
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            if pending_space and pieces:
                pieces.append(" ")
            pending_space = False
            pieces.append(char)
            if in_quote and index + 1 < len(expression) and expression[index + 1] == "'":
                pieces.append("'")
                index += 2
                continue
            in_quote = not in_quote
        elif not in_quote and char.isspace():
            pending_space = True
        else:
            if pending_space and pieces:
                pieces.append(" ")
            pending_space = False
            pieces.append(char if in_quote else char.lower())
        index += 1
    return "".join(pieces).strip()


def _read_manifest(directory: Path) -> dict[str, Any] | None:
    try:
        raw = (directory / CREDENTIAL_MANIFEST_FILENAME).read_text(encoding="utf-8")
        manifest = json.loads(raw)
        if set(manifest) != {"version", "sql_file", "sha256", "tables"}:
            return None
        if manifest["sql_file"] != CREDENTIAL_MIGRATION_FILENAME:
            return None
        sql = (directory / CREDENTIAL_MIGRATION_FILENAME).read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if digest != manifest["sha256"]:
            return None
        manifest["_sql"] = sql
        return manifest
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def _manifest_for_default_sql() -> dict[str, Any] | None:
    default_directory = Path(__file__).resolve().parents[3] / "migrations" / "credential_registry"
    return _read_manifest(default_directory)


def _split_sql(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    start = 0
    index = 0
    quote = False
    dollar_tag: str | None = None
    while index < len(sql):
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                index += len(dollar_tag)
                dollar_tag = None
                continue
        elif not quote and sql[index] == "$":
            match = re.match(r"\$[A-Za-z_0-9]*\$", sql[index:])
            if match:
                dollar_tag = match.group(0)
                index += len(dollar_tag)
                continue
        elif sql[index] == "'":
            if quote and index + 1 < len(sql) and sql[index + 1] == "'":
                index += 2
                continue
            quote = not quote
        elif not quote and sql[index] == ";":
            statement = sql[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1
    if quote or dollar_tag is not None:
        raise ValueError("migration SQL quoting is unbalanced")
    trailing = sql[start:].strip()
    if trailing:
        statements.append(trailing)
    return tuple(statements)
