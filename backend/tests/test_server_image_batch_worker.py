"""Explicit server-host integration for the opt-in image.batch worker."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from backend.server.application import create_server_host
from backend.server.configuration import load_server_configuration
from backend.server.image_generation import ImageGenerationHostConfiguration
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.generation_catalog.tables import ASSET_TABLES
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    CreditLogRow,
    ResultEvidenceRow,
    UserCreditRow,
)
from haoai_backend.generation_core.image_batch_results import (
    SqlAlchemyImageBatchResults,
)
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
)
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    check_credential_registry_readiness,
)
from haoai_backend.generation_runtime.assembly import GenerationWorkerConfiguration
from haoai_backend.generation_runtime.image_batch_assembly import (
    ImageBatchWorkerConfiguration,
)
import test_app_chat_media_admission_integration as media_integration
from test_server_generation_credentials import _provisioned_bundle


_IMAGE_BYTES = b"\x89PNG\r\n\x1a\nserver-batch-output"
_SERVER_SECRET = "synthetic-server-secret"


class _Response:
    status_code = 200

    def __init__(self) -> None:
        self._body = json.dumps(
            {
                "data": [
                    {"b64_json": base64.b64encode(_IMAGE_BYTES).decode("ascii")}
                ]
            }
        ).encode("utf-8")
        self.headers = {
            "content-length": str(len(self._body)),
            "content-type": "application/json",
        }

    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self._body


class _HttpClient:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self.calls = calls

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        **kwargs: object,
    ) -> _Response:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "content": content,
                "follow_redirects": kwargs.get("follow_redirects"),
            }
        )
        return _Response()


class _HttpContext:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self._client = _HttpClient(calls)

    async def __aenter__(self) -> _HttpClient:
        return self._client

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False


class _HttpFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self) -> _HttpContext:
        return _HttpContext(self.calls)


class _FakeS3Client:
    def __init__(self, uploaded: list[bytes]) -> None:
        self._uploaded = uploaded

    def put_object(self, **kwargs: Any) -> dict[str, object]:
        body = kwargs["Body"]
        self._uploaded.append(body.read() if hasattr(body, "read") else body)
        return {"ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "fake"}}

    def close(self) -> None:
        return None


class _FakeS3Session:
    def __init__(self, uploaded: list[bytes], **_credentials: object) -> None:
        self._uploaded = uploaded

    def client(self, service: str, **_options: object) -> _FakeS3Client:
        assert service == "s3"
        return _FakeS3Client(self._uploaded)


def _batch_host_environment(tmp_path: Path, credential_file: Path) -> dict[str, str]:
    transfer_dir = tmp_path / "transfer"
    transfer_dir.mkdir(exist_ok=True)
    return {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'g2-joint-fixture.sqlite'}",
        "SECRET_KEY": _SERVER_SECRET,
        "OBJECT_STORAGE_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com",
        "OBJECT_STORAGE_ACCESS_KEY_ID": "synthetic-access-key",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": "synthetic-r2-secret",
        "OBJECT_STORAGE_BUCKET": "haoai-media",
        "OBJECT_STORAGE_PUBLIC_BASE_URL": "https://cdn.example.test/media",
        "TRANSFER_TMP_DIR": str(transfer_dir),
        "IMAGE_SINGLE_ENABLED": "true",
        "IMAGE_SINGLE_CREDENTIALS_FILE": str(credential_file),
        "IMAGE_BATCH_ENABLED": "true",
        "IMAGE_BATCH_CREDENTIALS_FILE": str(credential_file),
    }


def _financial_state(
    session_factory: Callable[[], Session],
) -> tuple[int, tuple[tuple[object, ...], ...]]:
    with session_factory() as session:
        balance = session.execute(
            select(UserCreditRow.__table__.c.credits)
        ).scalar_one()
        ledger = tuple(
            tuple(row)
            for row in session.execute(
                select(
                    CreditLogRow.__table__.c.id,
                    CreditLogRow.__table__.c.amount,
                    CreditLogRow.__table__.c.business_key,
                ).order_by(CreditLogRow.__table__.c.id)
            ).all()
        )
    return balance, ledger


def test_server_lifespan_consumes_batch_post_and_replays_without_new_charge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        media_integration.admission_staging_module,
        "_G2_JOINT_PROVIDER",
        "openai",
    )
    fixture = media_integration._build_system(tmp_path)
    _, credential_file = _provisioned_bundle(tmp_path)
    with fixture.engine.begin() as connection:
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("system_configs")
        }
        if "worker_max_concurrency" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE system_configs ADD COLUMN worker_max_concurrency "
                "VARCHAR(10) NOT NULL DEFAULT '4'"
            )

    from backend.server import application as server_application
    from backend.server import image_generation as image_generation_module

    loaded_registry_paths: list[Path] = []

    def load_fixture_capabilities(
        _path: Path,
        *,
        session_factory: Any,
        db_clock: Any,
        id_factory: Any,
        **_kwargs: object,
    ) -> RegistryCapabilities:
        loaded_registry_paths.append(_path)
        return replace(
            fixture.capabilities,
            read_session_factory=session_factory,
            db_clock=db_clock,
            id_factory=id_factory,
        )

    http_factory = _HttpFactory()
    uploaded: list[bytes] = []
    application_outcomes: list[tuple[str, str | None]] = []
    original_apply_completed = SqlAlchemyImageBatchResults.apply_completed

    def observe_application(results, scope, result_ref, *, stop_signal=None):
        outcome = original_apply_completed(
            results, scope, result_ref, stop_signal=stop_signal
        )
        application_outcomes.append((outcome.kind, outcome.detail))
        return outcome

    monkeypatch.setattr(
        SqlAlchemyImageBatchResults, "apply_completed", observe_application
    )

    original_create_engine = server_application._create_engine

    def create_sqlite_engine_with_foreign_keys(database_url: str) -> Engine:
        engine = original_create_engine(database_url)
        if database_url.startswith("sqlite"):

            def enable_foreign_keys(connection, _record) -> None:
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(engine, "connect", enable_foreign_keys)
        return engine

    monkeypatch.setattr(
        image_generation_module,
        "load_registry_capabilities",
        load_fixture_capabilities,
    )
    monkeypatch.setattr(
        server_application,
        "_create_engine",
        create_sqlite_engine_with_foreign_keys,
    )
    monkeypatch.setattr(
        image_generation_module,
        "AiohttpAsyncHttpClientFactory",
        lambda: http_factory,
    )
    from haoai_backend import app as app_module

    single_effective_configurations: list[GenerationWorkerConfiguration] = []
    batch_effective_configurations: list[ImageBatchWorkerConfiguration] = []
    worker_service_class = app_module.GenerationWorkerService

    class FastRecoveryWorkerService(worker_service_class):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if kwargs.get("task_type") == "batch-image":
                kwargs["recovery_scan_interval_seconds"] = 0.05
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        app_module, "GenerationWorkerService", FastRecoveryWorkerService
    )
    original_build_single_runtime = app_module.build_generation_worker_runtime
    original_build_batch_runtime = app_module.build_image_batch_worker_runtime

    def observe_single_runtime(configuration: GenerationWorkerConfiguration):
        single_effective_configurations.append(configuration)
        return original_build_single_runtime(configuration)

    def observe_batch_runtime(configuration: ImageBatchWorkerConfiguration):
        batch_effective_configurations.append(configuration)
        return original_build_batch_runtime(configuration)

    monkeypatch.setattr(
        app_module,
        "build_generation_worker_runtime",
        observe_single_runtime,
    )
    monkeypatch.setattr(
        app_module,
        "build_image_batch_worker_runtime",
        observe_batch_runtime,
    )
    environment = _batch_host_environment(tmp_path, credential_file)
    host = create_server_host(
        load_server_configuration(environment),
        sdk_factories=(
            lambda **kwargs: _FakeS3Session(uploaded, **kwargs),
            lambda **kwargs: kwargs,
        ),
        worker_environment={"WORKER_CONCURRENCY": "4"},
    )

    try:
        image_configuration = host.app.state.server_image_generation_configuration
        assert isinstance(image_configuration, ImageGenerationHostConfiguration)
        assert image_configuration.generation_worker_configuration is not None
        assert image_configuration.image_batch_worker_configuration is not None
        assert loaded_registry_paths == [credential_file]
        assert (
            image_configuration.generation_worker_configuration.runtime
            is image_configuration.image_batch_worker_configuration.runtime
        )
        assert (
            image_configuration.generation_worker_configuration.registry_capabilities
            is image_configuration.image_batch_worker_configuration.registry_capabilities
        )
        admission_configuration = image_configuration.admission_configuration
        assert admission_configuration is not None
        with host.session_factory() as session:
            readiness = check_credential_registry_readiness(
                session,
                admission_configuration.registry_capabilities,
            )
        assert readiness.ready, readiness
        # The configured workers receive distinct runtime configs over one gate.
        assert len(single_effective_configurations) == 1
        assert len(batch_effective_configurations) == 1
        single_configuration = single_effective_configurations[0]
        batch_configuration = batch_effective_configurations[0]

        token = JoseTokenCodec(_SERVER_SECRET).encode(
            {
                "sub": media_integration._G2_JOINT_ACTOR_ID,
                "jti": media_integration.JTI,
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            }
        )
        key = "server-image-batch-host-947"

        async def run() -> None:
            assert (
                single_configuration.runtime.worker_capacity
                is batch_configuration.runtime.worker_capacity
                is host.app.state.worker_execution_capacity
            )
            async with host.app.router.lifespan_context(host.app):
                assert host.app.state.image_batch_worker_service is not None
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": key,
                }
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(
                        app=host.app,
                        raise_app_exceptions=False,
                    ),
                    base_url="http://image-batch-host.test",
                ) as client:
                    response = await client.post(
                        "/api/chat/batch-generate-images",
                        headers=headers,
                        json=media_integration._body("image.batch"),
                    )
                    assert response.status_code == 200, response.text
                    task_ids = [item["task_id"] for item in response.json()["tasks"]]
                    assert len(task_ids) == 2

                    for _ in range(120):
                        with host.session_factory() as session:
                            statuses = session.execute(
                                select(AITaskRow.__table__.c.status)
                                .where(AITaskRow.__table__.c.id.in_(task_ids))
                                .order_by(AITaskRow.__table__.c.created_at)
                            ).scalars().all()
                        if len(statuses) == 2 and all(
                            status == "completed" for status in statuses
                        ):
                            break
                        await asyncio.sleep(0.05)
                    assert statuses == ["completed", "completed"]
                    assert len(http_factory.calls) == 2
                    assert len(uploaded) == 2
                    assert all(content == _IMAGE_BYTES for content in uploaded)
                    task_receipts = []
                    for task_id in task_ids:
                        task_response = await client.get(
                            "/api/chat/tasks",
                            params={"task_id": task_id},
                            headers={"Authorization": headers["Authorization"]},
                        )
                        assert task_response.status_code == 200, task_response.text
                        task_receipts.append(task_response.json())
                    assert [item["status"] for item in task_receipts] == [
                        "completed",
                        "completed",
                    ]

                    receipt = await client.get(
                        "/api/chat/submissions/image.batch",
                        params={"idempotency_key": key},
                        headers={"Authorization": headers["Authorization"]},
                    )
                    assert receipt.status_code == 200, receipt.text
                    assert [
                        item["task_id"] for item in receipt.json()["tasks"]
                    ] == task_ids

                    def read_application_projection():
                        with host.session_factory() as session:
                            applications = session.execute(
                                select(
                                    GenerationResultReceiptRow.task_id,
                                    ResultEvidenceRow.application_status,
                                    ApplicationRecordRow.delivery_status,
                                )
                                .join(
                                    ResultEvidenceRow,
                                    ResultEvidenceRow.id
                                    == GenerationResultReceiptRow.evidence_id,
                                )
                                .outerjoin(
                                    ApplicationRecordRow,
                                    ApplicationRecordRow.result_ref
                                    == GenerationResultReceiptRow.result_ref,
                                )
                                .where(
                                    GenerationResultReceiptRow.task_id.in_(task_ids)
                                )
                                .order_by(GenerationResultReceiptRow.task_id)
                            ).all()
                            target_values = []
                            for asset_id, asset_type in (
                                ("joint-character-1", "character"),
                                ("joint-scene-1", "scene"),
                            ):
                                target_values.append(
                                    session.execute(
                                        select(
                                            ASSET_TABLES[asset_type].c.image_url
                                        ).where(
                                            ASSET_TABLES[asset_type].c.id == asset_id
                                        )
                                    ).scalar_one()
                                )
                        return applications, target_values

                    applications, target_values = read_application_projection()
                    for _ in range(100):
                        if (
                            len(applications) == 2
                            and all(
                                evidence == "applied" and delivery == "applied"
                                for _task_id, evidence, delivery in applications
                            )
                            and all(target_values)
                        ):
                            break
                        await asyncio.sleep(0.05)
                        applications, target_values = read_application_projection()
                    assert (
                        len(applications) == 2
                        and all(
                            evidence == "applied" and delivery == "applied"
                            for _task_id, evidence, delivery in applications
                        )
                        and all(target_values)
                    ), (applications, target_values, application_outcomes)

                    financials_before_replay = _financial_state(host.session_factory)
                    replay = await client.post(
                        "/api/chat/batch-generate-images",
                        headers=headers,
                        json=media_integration._body("image.batch"),
                    )
                    assert replay.status_code == 200, replay.text
                    assert [
                        item["task_id"] for item in replay.json()["tasks"]
                    ] == task_ids
                    assert len(http_factory.calls) == 2
                    assert (
                        _financial_state(host.session_factory)
                        == financials_before_replay
                    )

        asyncio.run(run())
        assert host.app.state.image_batch_worker_service is None
        assert host.app.state.image_batch_worker_shutdown is not None
    finally:
        host.close()
        fixture.engine.dispose()


def test_server_batch_only_builds_without_single_worker_or_startup_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        media_integration.admission_staging_module,
        "_G2_JOINT_PROVIDER",
        "openai",
    )
    fixture = media_integration._build_system(tmp_path)
    _, credential_file = _provisioned_bundle(tmp_path)
    from backend.server import image_generation as image_generation_module

    monkeypatch.setattr(
        image_generation_module,
        "load_registry_capabilities",
        lambda _path, *, session_factory, db_clock, id_factory, **_kwargs: replace(
            fixture.capabilities,
            read_session_factory=session_factory,
            db_clock=db_clock,
            id_factory=id_factory,
        ),
    )
    environment = _batch_host_environment(tmp_path, credential_file)
    environment["IMAGE_SINGLE_ENABLED"] = "false"
    environment.pop("IMAGE_SINGLE_CREDENTIALS_FILE")
    host = create_server_host(
        load_server_configuration(environment),
        sdk_factories=(lambda **_kwargs: object(), lambda **_kwargs: object()),
    )
    try:
        image_configuration = host.app.state.server_image_generation_configuration
        assert image_configuration is not None
        assert image_configuration.generation_worker_configuration is None
        assert image_configuration.image_batch_worker_configuration is not None
        assert image_configuration.media_runtime is not None
        assert image_configuration.media_runtime.state == "new"
        assert host.app.state.generation_worker_runtime is None
        assert host.app.state.image_batch_worker_runtime is not None
        assert host.app.state.image_batch_worker_service is None
    finally:
        host.close()
        fixture.engine.dispose()
