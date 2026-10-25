import importlib.util
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_core.base_schema import FOUNDATION_TABLES, UserRow
from haoai_backend.generation_core.schema import (
    CANVAS_RESULT_TARGETS_TABLE_NAME,
    CatalogIndex,
    GENERATION_MIGRATION_HISTORY_TABLE,
    FoundationBase,
    check_canvas_result_target_schema,
    sqlalchemy_catalog,
)
from haoai_backend.generation_runtime.readiness import check_generation_readiness
from haoai_backend.generation_runtime.runtime import build_runtime


MIGRATION_DIRECTORY = Path(__file__).resolve().parents[2] / "migrations"
MIGRATION_RUNNER_PATH = MIGRATION_DIRECTORY / "run_generation_migrations.py"
_RUNNER_SPEC = importlib.util.spec_from_file_location(
    "_haoai_generation_migration_runner_readiness_test", MIGRATION_RUNNER_PATH
)
assert _RUNNER_SPEC is not None and _RUNNER_SPEC.loader is not None
_RUNNER_MODULE = importlib.util.module_from_spec(_RUNNER_SPEC)
sys.modules[_RUNNER_SPEC.name] = _RUNNER_MODULE
_RUNNER_SPEC.loader.exec_module(_RUNNER_MODULE)
apply_generation_migrations = _RUNNER_MODULE.apply_generation_migrations


def _engine_with_generation_schema():
    engine = create_engine("sqlite://")
    FoundationBase.metadata.create_all(engine, tables=list(FOUNDATION_TABLES))
    apply_generation_migrations(engine, MIGRATION_DIRECTORY)
    return engine


def test_readiness_is_read_only_and_checks_migration_checksum() -> None:
    engine = _engine_with_generation_schema()
    with Session(engine) as session:
        pending_user = UserRow(id="pending-user")
        session.add(pending_user)

        readiness = check_generation_readiness(session)
        stored = session.connection().execute(
            text("SELECT id FROM users WHERE id = :id"),
            {"id": pending_user.id},
        ).first()

        assert readiness.status == "ready"
        assert not readiness.issues
        assert pending_user in session.new
        assert stored is None
    engine.dispose()


def test_readiness_rejects_extra_version_and_missing_record_columns() -> None:
    engine = _engine_with_generation_schema()
    with engine.begin() as connection:
        connection.execute(
            text(
                f"INSERT INTO {GENERATION_MIGRATION_HISTORY_TABLE} "
                "(version, checksum) VALUES (:version, :checksum)"
            ),
            {"version": "0002_unreviewed", "checksum": "0" * 64},
        )

    with Session(engine) as session:
        readiness = check_generation_readiness(session)
    assert readiness.status == "not_ready"
    assert any(issue.code == "unsupported_generation_version" for issue in readiness.issues)
    engine.dispose()


def test_readiness_rejects_applied_checksum_with_same_name_incomplete_table() -> None:
    engine = _engine_with_generation_schema()
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE application_records")
        connection.exec_driver_sql(
            "CREATE TABLE application_records (application_ref VARCHAR(36) PRIMARY KEY)"
        )

    with Session(engine) as session:
        readiness = check_generation_readiness(session)
    assert readiness.status == "not_ready"
    assert any(issue.code == "missing_column" for issue in readiness.issues)
    engine.dispose()


def test_readiness_requires_the_complete_canvas_target_page_index() -> None:
    engine = _engine_with_generation_schema()
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_generation_result_targets_page")

    with Session(engine) as session:
        readiness = check_generation_readiness(session)

    assert readiness.status == "not_ready"
    assert any(
        issue.code == "missing_or_incompatible_index"
        and issue.detail
        == f"{CANVAS_RESULT_TARGETS_TABLE_NAME}: ix_generation_result_targets_page"
        for issue in readiness.issues
    )
    engine.dispose()


class _CatalogWithCanvasIndexOverride:
    def __init__(self, source, indexes: tuple[CatalogIndex, ...]) -> None:
        self._source = source
        self._indexes = indexes

    def table_names(self) -> set[str]:
        return self._source.table_names()

    def table(self, name: str):
        table = self._source.table(name)
        if name != CANVAS_RESULT_TARGETS_TABLE_NAME:
            return table
        return replace(table, indexes=self._indexes)


@pytest.mark.parametrize(
    ("defect", "expected_code"),
    (
        ("wrong_columns", "missing_or_incompatible_index"),
        ("partial_columns", "missing_or_incompatible_index"),
        ("invalid_catalog_state", "index_not_effective_ready_live_and_global"),
    ),
)
def test_canvas_v2_readiness_rejects_bad_indexes_from_fake_catalog(
    defect: str,
    expected_code: str,
) -> None:
    engine = _engine_with_generation_schema()
    with engine.connect() as connection:
        source = sqlalchemy_catalog(connection)
        table = source.table(CANVAS_RESULT_TARGETS_TABLE_NAME)
        page_index = next(
            index
            for index in table.indexes
            if index.name == "ix_generation_result_targets_page"
        )
        if defect == "wrong_columns":
            replacement = replace(
                page_index,
                columns=("owner_id", "chapter_id", "target_identity"),
            )
        elif defect == "partial_columns":
            replacement = replace(page_index, columns=page_index.columns[:-1])
        else:
            replacement = replace(page_index, effective=False)
        indexes = tuple(
            index
            for index in table.indexes
            if index.name != "ix_generation_result_targets_page"
        ) + (replacement,)

        check = check_canvas_result_target_schema(
            _CatalogWithCanvasIndexOverride(source, indexes)
        )

    assert not check.ready
    assert any(
        issue.code == expected_code
        and issue.table == CANVAS_RESULT_TARGETS_TABLE_NAME
        and issue.detail == "ix_generation_result_targets_page"
        for issue in check.issues
    )
    engine.dispose()


def test_runtime_build_retains_factories_without_invoking_them() -> None:
    called: list[str] = []

    def factory(name: str):
        def build():
            called.append(name)
            return None
        return build

    config = GenerationRuntimeConfig(
        session_factory=factory("session"),
        db_clock=lambda: datetime.now(timezone.utc),
        id_factory=lambda: "id",
        http_client_factory=factory("http"),
        credential_resolver=lambda binding, operation: None,
        provider_factories=(("haoai", factory("provider")),),
        media_store_factory=factory("media"),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=factory("public-fetcher"),
        bound_provider_content_fetcher_factory=factory("bound-fetcher"),
        local_muse_capability_factory=factory("muse"),
        r2_storage_factory=factory("r2"),
    )
    runtime = build_runtime(config, (factory("executor"),))

    assert runtime.run_claimed_once("video-single") == "pending"
    receipt = runtime.shutdown("test shutdown")
    assert receipt.released_resources == ()
    assert receipt.revoked_claim_refs == ()
    assert called == []
