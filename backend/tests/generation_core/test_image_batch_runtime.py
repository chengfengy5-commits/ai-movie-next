from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import OwnerTaskScope
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
)
from haoai_backend.generation_core.image_batch_recovery import SqlAlchemyImageBatchRecovery
from haoai_backend.generation_runtime.image_batch_assembly import (
    ImageBatchWorkerConfiguration,
    build_image_batch_worker_runtime,
)
from haoai_backend.generation_catalog.domain import ModelSource
from test_batch_execution_snapshot_reader import _authenticated_batch_environment


def _runtime_configuration(calls: list[str]) -> GenerationRuntimeConfig:
    def unused(name: str):
        def call(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must remain cold")

        return call

    return GenerationRuntimeConfig(
        session_factory=unused("session_factory"),
        db_clock=unused("db_clock"),
        id_factory=unused("id_factory"),
        http_client_factory=unused("http_client_factory"),
        credential_resolver=unused("credential_resolver"),
        provider_factories=(),
        media_store_factory=unused("media_store_factory"),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=unused("public_media_fetcher_factory"),
        bound_provider_content_fetcher_factory=unused("bound_provider_content_fetcher_factory"),
        local_muse_capability_factory=unused("local_muse_capability_factory"),
        r2_storage_factory=unused("r2_storage_factory"),
    )


def test_batch_runtime_assembly_is_cold_and_registers_only_its_family(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, _staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
        item_count=2,
    )
    calls: list[str] = []
    runtime = build_image_batch_worker_runtime(
        ImageBatchWorkerConfiguration(
            _runtime_configuration(calls),
            environment.capabilities,
        )
    )

    assert runtime.supports_recovery_scan("batch-image")
    assert not runtime.supports_recovery_scan("image-single")
    assert calls == []
    receipt = runtime.shutdown("test_complete")
    assert receipt.unreleased_resources == ()
    assert calls == []
    environment.engine.dispose()


def test_batch_recovery_scans_due_items_and_delegates_to_results_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
        item_count=2,
    )
    first_task_id = staged.ordered_tasks[0].task_id
    with Session(environment.engine) as session:
        owner_id = session.execute(
            select(AITaskRow.__table__.c.user_id).where(
                AITaskRow.__table__.c.id == first_task_id
            )
        ).scalar_one()
        unit = session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == first_task_id
            )
        ).mappings().one()
        session.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == first_task_id)
            .values(status="reconciling")
        )
        session.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.id == unit["id"])
            .values(
                result_status="unknown",
                first_intent_at=datetime(2026, 10, 16, 0, 0),
                deadline_at=datetime(2026, 10, 16, 1, 0),
            )
        )
        session.commit()

    class ResultsSpy:
        scopes: list[OwnerTaskScope] = []

        def settle_due(self, scope, *, stop_signal=None):
            self.scopes.append(scope)
            return "pending"

    results = ResultsSpy()
    recovery = SqlAlchemyImageBatchRecovery(
        lambda: Session(environment.engine),
        lambda: datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc),
        results,
    )

    outcomes = recovery.scan_due()

    assert outcomes == ("pending",)
    assert results.scopes == [
        OwnerTaskScope(
                user_id=owner_id,
            task_id=first_task_id,
            operation="image.batch",
        )
    ]
    environment.engine.dispose()
