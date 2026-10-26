from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_system_config.tables import system_configs
from haoai_backend.app import create_app
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import ShutdownReceipt
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_runtime.assembly import GenerationWorkerConfiguration
from haoai_backend.generation_runtime.frame_review_assembly import (
    FrameReviewWorkerConfiguration,
)
from haoai_backend.generation_runtime.worker_capacity import WorkerCapacityBootstrap
from haoai_backend.generation_runtime.worker_capacity import WorkerExecutionCapacity
from backend.server import __main__ as server_entrypoint
from backend.server.application import _read_worker_max_concurrency
from backend.server.application import _worker_db_call_timeout_seconds


def test_narrow_worker_capacity_reader_selects_one_column_and_closes_session() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    system_configs.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(system_configs).values(
                id="config-one",
                worker_max_concurrency=" 9 ",
                oss_access_key_secret="secret-must-not-be-read",
                zhangyuge_billing_pat="pat-must-not-be-read",
            )
        )

    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _parameters, _context, _many: statements.append(
            statement
        ),
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    sessions: list[Session] = []
    closed = False
    rolled_back = False

    def tracked_session() -> Session:
        nonlocal closed, rolled_back
        session = factory()
        sessions.append(session)
        original_rollback = session.rollback
        original_close = session.close

        def rollback() -> None:
            nonlocal rolled_back
            rolled_back = True
            original_rollback()

        def close() -> None:
            nonlocal closed
            closed = True
            original_close()

        session.rollback = rollback  # type: ignore[method-assign]
        session.close = close  # type: ignore[method-assign]
        return session

    try:
        assert _read_worker_max_concurrency(tracked_session) == " 9 "
        selects = [statement for statement in statements if "SELECT" in statement]
        assert len(selects) == 1
        normalized = " ".join(selects[0].split()).lower()
        assert "select system_configs.worker_max_concurrency" in normalized
        assert "oss_access_key_secret" not in normalized
        assert "zhangyuge_billing_pat" not in normalized
        assert len(sessions) == 1
        assert rolled_back is True
        assert closed is True
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("environment", "expected"),
    (
        ({}, 10.0),
        ({"WORKER_DB_CALL_TIMEOUT": "0"}, 0.1),
        ({"WORKER_DB_CALL_TIMEOUT": "-5"}, 0.1),
        ({"WORKER_DB_CALL_TIMEOUT": "0.25"}, 0.25),
    ),
)
def test_worker_db_call_timeout_preserves_legacy_bounds(
    environment: Mapping[str, str],
    expected: float,
) -> None:
    assert _worker_db_call_timeout_seconds(environment) == expected


@pytest.mark.parametrize("value", ("invalid", "nan", "inf", "-inf"))
def test_worker_db_call_timeout_rejects_invalid_or_non_finite_values(value: str) -> None:
    with pytest.raises(ValueError, match="finite number"):
        _worker_db_call_timeout_seconds({"WORKER_DB_CALL_TIMEOUT": value})


def _runtime_config() -> GenerationRuntimeConfig:
    now = datetime(2026, 10, 26, tzinfo=timezone.utc)
    return GenerationRuntimeConfig(
        session_factory=lambda: None,
        db_clock=lambda: now,
        id_factory=lambda: "worker-id",
        http_client_factory=lambda: None,
        credential_resolver=lambda _binding, _purpose: None,
        provider_factories=(),
        media_store_factory=lambda: None,
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: None,
        bound_provider_content_fetcher_factory=lambda: None,
        local_muse_capability_factory=lambda: None,
        r2_storage_factory=lambda: None,
    )


class _ObservedRuntime:
    def __init__(self, capacity: WorkerExecutionCapacity) -> None:
        self.capacity = capacity
        self.calls: list[str] = []

    def run_claimed_once(self, task_type: str) -> str:
        assert self.capacity.limit is not None
        self.calls.append(task_type)
        return "pending"

    def shutdown(self, reason: str) -> ShutdownReceipt:
        assert self.capacity.is_closed is True
        return ShutdownReceipt(
            reason=reason,
            released_resources=(),
            unreleased_resources=(),
            revoked_claim_refs=(),
            recovery_plan_refs=(),
            timed_out=False,
        )


def test_app_resolves_once_before_workers_and_shares_capacity_across_families(
    monkeypatch,
) -> None:
    import haoai_backend.app as app_module

    reads: list[str] = []
    capabilities = RegistryCapabilities.unconfigured()
    image_configuration = GenerationWorkerConfiguration(
        _runtime_config(), capabilities
    )
    review_configuration = FrameReviewWorkerConfiguration(
        _runtime_config(), capabilities
    )
    worker_capacity_bootstrap = WorkerCapacityBootstrap(
        read_database_value=lambda: (reads.append("read") or "3"),
        environment_value="8",
    )
    runtimes: list[_ObservedRuntime] = []

    def build_observed_runtime(configuration):
        assert configuration.runtime.worker_capacity is not None
        runtime = _ObservedRuntime(configuration.runtime.worker_capacity)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(
        app_module,
        "build_generation_worker_runtime",
        build_observed_runtime,
    )
    monkeypatch.setattr(
        app_module,
        "build_frame_review_worker_runtime",
        build_observed_runtime,
    )
    app = create_app(
        generation_worker_configuration=image_configuration,
        frame_review_worker_configuration=review_configuration,
        worker_capacity_bootstrap=worker_capacity_bootstrap,
    )

    assert reads == []
    capacity = app.state.worker_execution_capacity
    assert isinstance(capacity, WorkerExecutionCapacity)
    assert runtimes[0].capacity is capacity
    assert runtimes[1].capacity is capacity

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert reads == ["read"]
            assert capacity.limit == 3
            assert app.state.generation_worker_service is not None
            assert app.state.frame_review_worker_service is not None

    asyncio.run(run_lifespan())

    assert capacity.is_closed is True
    assert reads == ["read"]
    assert len(runtimes) == 2
    assert runtimes[0].calls and runtimes[1].calls


def test_app_without_enabled_workers_does_not_read_capacity_configuration() -> None:
    reads: list[str] = []
    app = create_app(
        worker_capacity_bootstrap=WorkerCapacityBootstrap(
            read_database_value=lambda: (reads.append("read") or "4")
        )
    )

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert app.state.worker_execution_capacity is None
            assert reads == []

    assert reads == []
    asyncio.run(run_lifespan())
    assert reads == []


def test_server_main_passes_the_named_worker_environment_to_host(monkeypatch) -> None:
    environment = {"WORKER_CONCURRENCY": "7"}
    captured: list[Mapping[str, str] | None] = []

    class Host:
        app = object()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        server_entrypoint,
        "load_server_configuration",
        lambda _environment: object(),
    )
    monkeypatch.setattr(
        server_entrypoint,
        "create_server_host",
        lambda _configuration, *, worker_environment=None: (
            captured.append(worker_environment) or Host()
        ),
    )

    server_entrypoint.main(environment, runner=lambda *_args, **_kwargs: None)

    assert captured == [environment]
