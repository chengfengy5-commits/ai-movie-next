"""Schema tests distinguish SQLite structure fixtures from PostgreSQL migration proof."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import CheckConstraint, Integer, String, create_engine, event, inspect, insert, text, update
from sqlalchemy.exc import DBAPIError

from haoai_backend.generation_credentials import schema as credential_schema
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    check_credential_registry_readiness,
)
from haoai_backend.generation_credentials.persistence import (
    REGISTRY_TABLES,
    credential_bindings,
    credential_principals,
    credential_versions,
    prepared_payloads,
    metadata,
)
from haoai_backend.generation_credentials.schema import (
    CREDENTIAL_MIGRATION_FILENAME,
    CREDENTIAL_SCHEMA_VERSION,
    OWN_TABLE_NAMES,
    MigrationResult,
    apply_credential_registry_migrations,
    check_credential_registry_schema,
)


TARGET_MIGRATION_DIR = Path("/Users/yanghaibo/data/projects/ai/haoai-next/backend/migrations/credential_registry")


def test_partial_registry_schema_fails_closed() -> None:
    engine = create_engine("sqlite:///:memory:")
    REGISTRY_TABLES[0].create(engine)
    with engine.connect() as connection:
        result = check_credential_registry_schema(connection)
    assert not result.ready
    assert result.issues[0].startswith("registry_schema_partial:")
    engine.dispose()


def test_complete_sqlite_schema_fixture_checks_constraints_history_and_immutability() -> None:
    engine = create_engine("sqlite:///:memory:")
    _enable_sqlite_foreign_keys(engine)
    metadata.create_all(engine)
    _install_sqlite_fixture_guards(engine)
    manifest = json.loads((TARGET_MIGRATION_DIR / "manifest.json").read_text(encoding="utf-8"))
    migration_bytes = (TARGET_MIGRATION_DIR / CREDENTIAL_MIGRATION_FILENAME).read_bytes()
    assert hashlib.sha256(migration_bytes).hexdigest() == manifest["sha256"]
    assert manifest["version"] == CREDENTIAL_SCHEMA_VERSION
    assert tuple(manifest["tables"]) == OWN_TABLE_NAMES

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO generation_credential_schema_migrations(version, checksum, applied_at) "
                "VALUES (:version, :checksum, CURRENT_TIMESTAMP)"
            ),
            {"version": CREDENTIAL_SCHEMA_VERSION, "checksum": manifest["sha256"]},
        )
    with engine.connect() as connection:
        result = check_credential_registry_schema(connection)
        assert result.ready, result.issues
        connection.execute(
            insert(credential_principals).values(
                principal_id="30000000-0000-4000-8000-000000000001",
                provider="openai",
                issuer_namespace="fixture-issuer",
                stable_account_subject="fixture-subject",
                owner_scope="owner-1",
                record_id="fixture-record",
                evidence_ref="fixture-evidence",
                evidence_version="evidence-v1",
                source_state="valid",
                created_by="10000000-0000-4000-8000-000000000001",
                created_at=datetime(2026, 10, 12, 4, 0),
            )
        )
        connection.commit()
        with pytest.raises(DBAPIError):
            connection.execute(
                update(credential_principals)
                .where(credential_principals.c.principal_id == "30000000-0000-4000-8000-000000000001")
                .values(stable_account_subject="mutated")
            )
    engine.dispose()


def test_extra_owned_column_is_schema_drift() -> None:
    engine = create_engine("sqlite:///:memory:")
    _enable_sqlite_foreign_keys(engine)
    metadata.create_all(engine)
    _install_sqlite_fixture_guards(engine)
    manifest = json.loads((TARGET_MIGRATION_DIR / "manifest.json").read_text(encoding="utf-8"))
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE generation_prepared_payloads ADD COLUMN unexpected TEXT"
        )
        connection.execute(
            text(
                "INSERT INTO generation_credential_schema_migrations(version, checksum, applied_at) "
                "VALUES (:version, :checksum, CURRENT_TIMESTAMP)"
            ),
            {"version": CREDENTIAL_SCHEMA_VERSION, "checksum": manifest["sha256"]},
        )
    with engine.connect() as connection:
        result = check_credential_registry_schema(connection)
    assert not result.ready
    assert "columns_mismatch:generation_prepared_payloads" in result.issues
    engine.dispose()


def test_missing_owned_column_fails_readiness() -> None:
    engine = create_engine("sqlite:///:memory:")
    _enable_sqlite_foreign_keys(engine)
    metadata.create_all(engine)
    _install_sqlite_fixture_guards(engine)
    manifest = json.loads((TARGET_MIGRATION_DIR / "manifest.json").read_text(encoding="utf-8"))
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE generation_credential_versions DROP COLUMN expected_revision"
        )
        connection.execute(
            text(
                "INSERT INTO generation_credential_schema_migrations(version, checksum, applied_at) "
                "VALUES (:version, :checksum, CURRENT_TIMESTAMP)"
            ),
            {"version": CREDENTIAL_SCHEMA_VERSION, "checksum": manifest["sha256"]},
        )
    with engine.connect() as connection:
        result = check_credential_registry_schema(connection)
    assert not result.ready
    assert "columns_mismatch:generation_credential_versions" in result.issues
    engine.dispose()


def test_bad_history_checksum_fails_readiness() -> None:
    engine = create_engine("sqlite:///:memory:")
    _enable_sqlite_foreign_keys(engine)
    metadata.create_all(engine)
    _install_sqlite_fixture_guards(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO generation_credential_schema_migrations(version, checksum, applied_at) "
                "VALUES (:version, :checksum, CURRENT_TIMESTAMP)"
            ),
            {"version": CREDENTIAL_SCHEMA_VERSION, "checksum": "0" * 64},
        )
    with engine.connect() as connection:
        result = check_credential_registry_schema(connection)
    assert not result.ready
    assert "registry_migration_checksum_mismatch" in result.issues
    engine.dispose()


def test_sqlite_schema_readiness_requires_enabled_foreign_keys() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    _install_sqlite_fixture_guards(engine)
    manifest = json.loads((TARGET_MIGRATION_DIR / "manifest.json").read_text(encoding="utf-8"))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO generation_credential_schema_migrations(version, checksum, applied_at) "
                "VALUES (:version, :checksum, CURRENT_TIMESTAMP)"
            ),
            {"version": CREDENTIAL_SCHEMA_VERSION, "checksum": manifest["sha256"]},
        )
    with engine.connect() as connection:
        result = check_credential_registry_schema(connection)
    assert not result.ready
    assert "registry_foreign_keys_disabled" in result.issues
    engine.dispose()


def test_sqlite_schema_readiness_rejects_named_noop_immutability_trigger() -> None:
    engine = create_engine("sqlite:///:memory:")
    _enable_sqlite_foreign_keys(engine)
    metadata.create_all(engine)
    _install_sqlite_fixture_guards(engine)
    manifest = json.loads((TARGET_MIGRATION_DIR / "manifest.json").read_text(encoding="utf-8"))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO generation_credential_schema_migrations(version, checksum, applied_at) "
                "VALUES (:version, :checksum, CURRENT_TIMESTAMP)"
            ),
            {"version": CREDENTIAL_SCHEMA_VERSION, "checksum": manifest["sha256"]},
        )
        connection.exec_driver_sql(
            "DROP TRIGGER trg_generation_credential_versions_immutable_update"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER trg_generation_credential_versions_immutable_update "
            "BEFORE UPDATE ON generation_credential_versions BEGIN SELECT 1; END"
        )
    with engine.connect() as connection:
        result = check_credential_registry_schema(connection)
    assert not result.ready
    assert (
        "registry_immutable_guard_definition_mismatch:"
        "trg_generation_credential_versions_immutable_update"
    ) in result.issues
    engine.dispose()


def test_sqlite_enforces_registry_foreign_key_for_invalid_payload() -> None:
    engine = create_engine("sqlite:///:memory:")
    _enable_sqlite_foreign_keys(engine)
    metadata.create_all(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        connection.rollback()
        with pytest.raises(DBAPIError):
            with connection.begin():
                connection.execute(
                    insert(prepared_payloads).values(
                        prepared_payload_id="50000000-0000-4000-8000-000000000001",
                        actor_id="10000000-0000-4000-8000-000000000001",
                        operation="create",
                        server_attempt_id="attempt-1",
                        command_proof="a" * 64,
                        context_fingerprint="b" * 64,
                        schema_version="schema-v1",
                        projector_version="projector-v1",
                        reference_policy_version="reference-v1",
                        target_ref="target-1",
                        model_ref="model-1",
                        grant_ref="grant-1",
                        source="user",
                        row_id="20000000-0000-4000-8000-000000000001",
                        owner_or_system="10000000-0000-4000-8000-000000000001",
                        provider="openai",
                        binding_id="40000000-0000-4000-8000-000000000001",
                        binding_version=1,
                        principal_id="30000000-0000-4000-8000-000000000001",
                        ciphertext=b"ciphertext",
                        nonce=b"nonce",
                        encryption_key_id="test-key",
                        created_at=datetime(2026, 10, 12, 4, 0),
                    )
                )
    engine.dispose()


def test_readiness_reports_foundation_before_unconfigured_cp_capabilities() -> None:
    engine = create_engine("sqlite:///:memory:")
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        result = check_credential_registry_readiness(
            session,
            RegistryCapabilities.unconfigured(),
        )
    assert not result.ready
    assert result.schema_status == "not_ready"
    assert result.enrollment_status == "not_ready"
    assert result.crypto_status == "not_ready"
    assert result.clock_status == "not_ready"
    assert any(issue.startswith("foundation:") for issue in result.issues)
    engine.dispose()


def test_explicit_migration_runner_refuses_sqlite_without_ddl() -> None:
    engine = create_engine("sqlite:///:memory:")
    result = apply_credential_registry_migrations(engine, TARGET_MIGRATION_DIR)
    assert isinstance(result, MigrationResult)
    assert result.status == "not_ready"
    assert result.issues == ("postgresql_required_for_migration",)
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == set()
    engine.dispose()


def test_postgres_check_comparison_accepts_catalog_any_array_shape() -> None:
    assert credential_schema._postgres_check_expressions_equivalent(
        "source_state IN ('valid', 'revoked')",
        "source_state::text = ANY (ARRAY['valid'::character varying, "
        "'revoked'::character varying]::text[])",
        {"source_state": String(16)},
    )


@pytest.mark.parametrize(
    "actual",
    [
        "source_state::text = ANY (ARRAY['VALID'::character varying, "
        "'revoked'::character varying]::text[]) AND enabled",
        "source_state::text = ANY (ARRAY['valid'::character varying, "
        "'revoked'::character varying]::character(16)[]) AND enabled",
        "source_state IN ('valid', 'revoked') OR revision > 0 AND enabled",
    ],
    ids=["literal-case", "cast-dimension", "precedence"],
)
def test_postgres_check_comparison_keeps_literal_cast_and_precedence(
    actual: str,
) -> None:
    assert not credential_schema._postgres_check_expressions_equivalent(
        "source_state IN ('valid', 'revoked') AND enabled",
        actual,
        {"source_state": String(16), "revision": Integer()},
    )


@pytest.mark.parametrize("wrong_field", ["target", "delete_action", "deferral"])
def test_postgres_fk_catalog_rejects_wrong_target_action_or_deferral(
    wrong_field: str,
) -> None:
    if wrong_field == "deferral":
        table = credential_versions
        constraint_name = "fk_generation_credential_version_binding_scope"
    else:
        table = credential_bindings
        constraint_name = "fk_generation_credential_binding_head"
    constraint = next(item for item in table.constraints if item.name == constraint_name)
    target_table = constraint.elements[0].column.table.name
    actual = {
        "conname": constraint.name,
        "contype": "f",
        "convalidated": True,
        "condeferrable": True,
        "condeferred": True,
        "source_schema": "tenant",
        "source_table": table.name,
        "source_oid": 101,
        "target_schema": "tenant",
        "target_table": target_table,
        "target_oid": 202,
        "expected_target_oid": 202,
        "constrained_columns": tuple(column.name for column in constraint.columns),
        "referred_columns": tuple(element.column.name for element in constraint.elements),
        "confdeltype": "r",
        "confupdtype": "a",
    }
    if wrong_field == "target":
        actual.update(target_schema="other", target_oid=999)
    elif wrong_field == "delete_action":
        actual["confdeltype"] = "a"

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def all(self):
            return self._rows

        def first(self):
            return self._rows[0] if self._rows else None

    primary_key_name = f"{table.name}_pkey"

    class Connection:
        def execute(self, statement, params=None):
            if "AS source_oid" in str(statement):
                return Result([actual])
            return Result([
                {
                    "conname": constraint.name,
                    "contype": "f",
                    "convalidated": True,
                    "condeferrable": actual["condeferrable"],
                    "condeferred": actual["condeferred"],
                },
                {
                    "conname": primary_key_name,
                    "contype": "p",
                    "convalidated": True,
                    "condeferrable": False,
                    "condeferred": False,
                },
            ])

    issues = credential_schema._check_postgres_constraints(
        Connection(),
        table.name,
        {},
        {constraint.name: constraint},
        {},
    )
    assert any(
        issue.startswith(("postgres_foreign_key_mismatch:", "postgres_head_fk_not_deferred"))
        for issue in issues
    )


def test_migration_head_fk_action_matches_public_metadata_and_manifest() -> None:
    constraint = next(
        item for item in credential_bindings.constraints
        if item.name == "fk_generation_credential_binding_head"
    )
    migration = (TARGET_MIGRATION_DIR / CREDENTIAL_MIGRATION_FILENAME).read_text()
    start = migration.index("ADD CONSTRAINT fk_generation_credential_binding_head")
    statement = migration[start:migration.index(";", start)]
    manifest = json.loads((TARGET_MIGRATION_DIR / "manifest.json").read_text())

    assert constraint.ondelete == "RESTRICT"
    assert "ON DELETE RESTRICT" in statement
    assert hashlib.sha256(migration.encode()).hexdigest() == manifest["sha256"]


def test_migration_check_expressions_match_own_metadata() -> None:
    migration = credential_schema._normalize_expression(
        (TARGET_MIGRATION_DIR / CREDENTIAL_MIGRATION_FILENAME).read_text()
    )

    for table in REGISTRY_TABLES:
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                expected = credential_schema._normalize_expression(str(constraint.sqltext))
                assert expected in migration


@pytest.mark.parametrize("postflight_ready", [False, True], ids=["rollback", "commit"])
def test_owned_migration_postflight_stays_inside_atomic_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    postflight_ready: bool,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'atomic-migration.sqlite'}")
    event.listen(engine, "begin", lambda connection: connection.exec_driver_sql("BEGIN"))
    migration_engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        connect=engine.connect,
        begin=engine.begin,
    )
    monkeypatch.setattr(
        credential_schema,
        "check_generation_readiness",
        lambda _session: SimpleNamespace(status="ready", issues=()),
    )
    monkeypatch.setattr(
        credential_schema,
        "check_legacy_projection_schema",
        lambda _connection: credential_schema.CredentialSchemaStatus("ready"),
    )
    observed_transaction: list[bool] = []

    def postflight(connection):
        observed_transaction.append(connection.in_transaction())
        status = "ready" if postflight_ready else "not_ready"
        issues = () if postflight_ready else ("synthetic_postflight_failure",)
        return credential_schema.CredentialSchemaStatus(status, issues)

    monkeypatch.setattr(credential_schema, "check_credential_registry_schema", postflight)
    migration_dir = tmp_path / "migration"
    migration_dir.mkdir()
    sql = (
        "CREATE TABLE generation_credential_schema_migrations "
        "(version VARCHAR(128) PRIMARY KEY, checksum VARCHAR(64), applied_at DATETIME);\n"
        "CREATE TABLE credential_registry_probe (id INTEGER PRIMARY KEY);\n"
    )
    (migration_dir / CREDENTIAL_MIGRATION_FILENAME).write_text(sql)
    (migration_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": CREDENTIAL_SCHEMA_VERSION,
                "sql_file": CREDENTIAL_MIGRATION_FILENAME,
                "sha256": hashlib.sha256(sql.encode()).hexdigest(),
                "tables": list(OWN_TABLE_NAMES),
            }
        )
    )

    result = apply_credential_registry_migrations(migration_engine, migration_dir)

    assert observed_transaction == [True]
    with engine.connect() as connection:
        names = set(inspect(connection).get_table_names())
        if postflight_ready:
            assert result.status == "applied"
            assert "credential_registry_probe" in names
            assert connection.execute(
                text("SELECT COUNT(*) FROM generation_credential_schema_migrations")
            ).scalar_one() == 1
        else:
            assert result.status == "not_ready"
            assert "credential_registry_probe" not in names
            assert "generation_credential_schema_migrations" not in names
    engine.dispose()


def _install_sqlite_fixture_guards(engine) -> None:
    statements = (
        "CREATE TRIGGER trg_generation_credential_versions_immutable_update BEFORE UPDATE ON generation_credential_versions BEGIN SELECT RAISE(ABORT, 'immutable'); END",
        "CREATE TRIGGER trg_generation_credential_versions_immutable_delete BEFORE DELETE ON generation_credential_versions BEGIN SELECT RAISE(ABORT, 'immutable'); END",
        "CREATE TRIGGER trg_generation_prepared_payloads_immutable_update BEFORE UPDATE ON generation_prepared_payloads BEGIN SELECT RAISE(ABORT, 'immutable'); END",
        "CREATE TRIGGER trg_generation_prepared_payloads_immutable_delete BEFORE DELETE ON generation_prepared_payloads BEGIN SELECT RAISE(ABORT, 'immutable'); END",
        "CREATE TRIGGER trg_generation_credential_principals_immutable_identity_update BEFORE UPDATE ON generation_credential_principals BEGIN SELECT RAISE(ABORT, 'immutable'); END",
        "CREATE TRIGGER trg_generation_credential_principals_immutable_identity_delete BEFORE DELETE ON generation_credential_principals BEGIN SELECT RAISE(ABORT, 'immutable'); END",
        "CREATE TRIGGER trg_generation_credential_bindings_guard BEFORE UPDATE ON generation_credential_bindings BEGIN SELECT RAISE(ABORT, 'immutable'); END",
        "CREATE TRIGGER trg_generation_credential_migrations_immutable_update BEFORE UPDATE ON generation_credential_schema_migrations BEGIN SELECT RAISE(ABORT, 'immutable'); END",
        "CREATE TRIGGER trg_generation_credential_migrations_immutable_delete BEFORE DELETE ON generation_credential_schema_migrations BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def _enable_sqlite_foreign_keys(engine) -> None:
    def enable(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", enable)
