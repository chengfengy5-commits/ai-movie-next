from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Text, create_engine, inspect

from haoai_backend.series_data.continuous_operation_receipts import operation_receipts
from haoai_backend.series_data.continuous_operation_schema import (
    ContinuousOperationMigrationError,
    apply_continuous_operation_migration,
    check_continuous_operation_schema,
    _normalize_check_expression,
)


@pytest.fixture
def engine(tmp_path: Path):
    created = create_engine(f"sqlite:///{tmp_path / 'continuous-operation-schema.sqlite'}", future=True)
    yield created
    created.dispose()


def test_explicit_migration_is_separate_from_generation_readiness(engine) -> None:
    with engine.connect() as connection:
        assert check_continuous_operation_schema(connection).status == "not_ready"

    first = apply_continuous_operation_migration(engine)
    second = apply_continuous_operation_migration(engine)

    assert first.status == "applied"
    assert second.status == "already_current"
    with engine.connect() as connection:
        readiness = check_continuous_operation_schema(connection)
        assert readiness.status == "ready"
        inspector = inspect(connection)
        assert inspector.get_pk_constraint("continuous_chapter_operation_receipts")["constrained_columns"] == [
            "owner_id",
            "operation_id",
        ]
        assert len(inspector.get_check_constraints("continuous_chapter_operation_receipts")) == 3


def test_target_draft_id_uses_unbounded_text_for_opaque_frontend_ids() -> None:
    column = operation_receipts.c.target_draft_id
    migration = Path(__file__).resolve().parents[1] / "migrations" / "0005_continuous_chapter_operations.sql"

    assert isinstance(column.type, Text)
    assert "target_draft_id TEXT," in migration.read_text(encoding="utf-8")


def test_partial_schema_fails_closed_without_creating_the_missing_receipt_table(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE continuous_chapter_operation_schema_migrations "
            "(version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, applied_at TIMESTAMP NOT NULL)"
        )

    with pytest.raises(ContinuousOperationMigrationError, match="partial or incompatible"):
        apply_continuous_operation_migration(engine)

    with engine.connect() as connection:
        assert check_continuous_operation_schema(connection).status == "not_ready"
        assert not inspect(connection).has_table("continuous_chapter_operation_receipts")


def test_manifest_checksum_mismatch_is_not_ready(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "migrations"
    copied = tmp_path / "migrations"
    copied.mkdir()
    (copied / "0005_continuous_chapter_operations.sql").write_text(
        (source / "0005_continuous_chapter_operations.sql").read_text(encoding="utf-8") + "\n-- changed\n",
        encoding="utf-8",
    )
    manifest = json.loads((source / "continuous_chapter_operations_manifest.json").read_text(encoding="utf-8"))
    (copied / "continuous_chapter_operations_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'bad-manifest.sqlite'}", future=True)
    try:
        with engine.connect() as connection:
            assert check_continuous_operation_schema(connection, migration_directory=copied).status == "not_ready"
    finally:
        engine.dispose()


def test_postgres_reflected_any_array_check_normalizes_to_the_declared_in_constraint() -> None:
    sqlite_expression = "outcome IN ('committed', 'rejected')"
    postgres_expressions = (
        "outcome = ANY (ARRAY['committed'::character varying, "
        "'rejected'::character varying])",
        "outcome = ANY ((ARRAY['committed'::character varying, "
        "'rejected'::character varying])::text[])",
        "(outcome)::text = ANY (((ARRAY['committed'::character varying, "
        "'rejected'::character varying])::text[]))",
        "(((outcome)::text = ANY ((ARRAY['committed'::character varying, "
        "'rejected'::character varying])::text[])))",
    )

    for postgres_expression in postgres_expressions:
        assert _normalize_check_expression(postgres_expression) == _normalize_check_expression(sqlite_expression)
