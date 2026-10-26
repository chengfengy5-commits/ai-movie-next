"""Explicit server-host integration for the opt-in video.single worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import event, inspect, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from backend.server.application import create_server_host
from backend.server.configuration import (
    ServerConfigurationError,
    load_server_configuration,
)
from backend.server.image_generation import _image_media_policy
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_catalog.tables import model_configs, system_models
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    CreditLogRow,
    ResultEvidenceRow,
    UserCreditRow,
)
from haoai_backend.generation_contracts.types import (
    ReferenceInput,
    TransferFailure,
)
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.media_storage.pinned_http import (
    EncodedRequestTarget,
    PinnedPublicMediaFetcher,
)
from haoai_backend.media_storage.public_fetch_policy import PublicFetchPolicy
from haoai_backend.media_uploads.domain import MAX_VIDEO_BYTES
import test_app_chat_media_admission_integration as media_integration
import test_server_generation_credentials as server_credentials


_SERVER_SECRET = "synthetic-server-secret"
_VIDEO_BYTES = b"controlled MP4 content for host integration"
_VIDEO_URL = "https://media.example.test/generated/clip.mp4"
_PROVIDER_TASK_ID = "xai-host-video-task-966"


class _ProviderResponse:
    status_code = 200

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.headers = {
            "content-length": str(len(body)),
            "content-type": "application/json",
        }

    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self._body


class _ProviderClient:
    def __init__(self, calls: list[tuple[str, str]]) -> None:
        self.calls = calls

    async def request(self, method: str, url: str, **_kwargs: object) -> _ProviderResponse:
        self.calls.append((method, url))
        if method == "POST":
            body = {"request_id": _PROVIDER_TASK_ID}
        else:
            assert method == "GET"
            body = {"status": "done", "video": {"url": _VIDEO_URL}}
        return _ProviderResponse(json.dumps(body).encode("utf-8"))


class _ProviderContext:
    def __init__(self, client: _ProviderClient) -> None:
        self.client = client

    async def __aenter__(self) -> _ProviderClient:
        return self.client

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _ProviderFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.client = _ProviderClient(self.calls)

    def __call__(self) -> _ProviderContext:
        return _ProviderContext(self.client)


class _MediaResponse:
    status = 200

    def __init__(
        self,
        *,
        media_type: str = "video/mp4",
        content: bytes = _VIDEO_BYTES,
        declared_length: int | None = None,
    ) -> None:
        self.content = content
        self.headers = {
            "Content-Type": media_type,
            "Content-Length": str(
                len(content) if declared_length is None else declared_length
            ),
        }
        self.iterated = False
        self.closed = False

    def iter_chunks(self, chunk_size: int):
        assert chunk_size > 0

        async def chunks():
            self.iterated = True
            if self.content:
                yield self.content

        return chunks()

    async def close(self) -> None:
        self.closed = True


class _PinnedConnector:
    def __init__(self, resolver: object) -> None:
        self.resolver = resolver
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _PinnedSession:
    def __init__(self, connector: _PinnedConnector, response: _MediaResponse) -> None:
        self.connector = connector
        self.response = response
        self.closed = False
        self.request: tuple[EncodedRequestTarget, dict[str, str], float, bool] | None = None

    async def get(
        self,
        target: EncodedRequestTarget,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
        allow_redirects: bool,
    ) -> _MediaResponse:
        self.request = (target, dict(headers), timeout_seconds, allow_redirects)
        parsed = urlsplit(target.raw_url)
        await self.connector.resolver.resolve(
            target.tls_hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
        return self.response

    async def close(self) -> None:
        self.closed = True


class _PinnedMediaLibrary:
    def __init__(self, response: _MediaResponse) -> None:
        self.response = response
        self.connector_value: _PinnedConnector | None = None
        self.session_value: _PinnedSession | None = None

    def encoded_url(
        self, raw_url: str, *, preserve_encoded: bool
    ) -> EncodedRequestTarget:
        assert preserve_encoded
        parsed = urlsplit(raw_url)
        return EncodedRequestTarget(
            raw_url,
            parsed.netloc,
            parsed.hostname or "",
            encoded=True,
        )

    def connector(
        self,
        resolver: object,
        *,
        tls_hostname: str,
        verify_tls: bool,
        use_dns_cache: bool,
    ) -> _PinnedConnector:
        assert tls_hostname == "media.example.test"
        assert verify_tls is True and use_dns_cache is False
        self.connector_value = _PinnedConnector(resolver)
        return self.connector_value

    def session(
        self,
        connector: _PinnedConnector,
        *,
        trust_env: bool,
        cookies_enabled: bool,
        auto_decompress: bool,
        connector_owned_by_session: bool,
    ) -> _PinnedSession:
        assert trust_env is False
        assert cookies_enabled is False
        assert auto_decompress is True
        assert connector_owned_by_session is False
        self.session_value = _PinnedSession(connector, self.response)
        return self.session_value


class _FakeS3Client:
    def __init__(self, uploaded: list[dict[str, object]]) -> None:
        self.uploaded = uploaded

    def put_object(self, **kwargs: Any) -> dict[str, object]:
        self.uploaded.append(
            {
                "bucket": kwargs["Bucket"],
                "key": kwargs["Key"],
                "content_type": kwargs["ContentType"],
                "body": kwargs["Body"].read(),
            }
        )
        return {
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "synthetic-video-r2-write",
            }
        }

    def close(self) -> None:
        return None


class _FakeS3Session:
    def __init__(self, uploaded: list[dict[str, object]], **credentials: object) -> None:
        assert credentials == {
            "aws_access_key_id": "synthetic-access-key",
            "aws_secret_access_key": "synthetic-r2-secret",
            "region_name": "auto",
        }
        self.uploaded = uploaded

    def client(self, service: str, **_options: object) -> _FakeS3Client:
        assert service == "s3"
        return _FakeS3Client(self.uploaded)


def _build_video_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    admission_module = media_integration.admission_staging_module
    monkeypatch.setattr(admission_module, "_G2_JOINT_PROVIDER", "xai")
    original_insert_model = admission_module._joint_insert_catalog_model

    def insert_model(connection, source: ModelSource) -> None:
        original_insert_model(connection, source)
        table = system_models if source is ModelSource.SYSTEM else model_configs
        connection.execute(
            update(table)
            .where(table.c.id == media_integration._G2_JOINT_MODEL_ID)
            .values(
                category="video",
                model="grok-imagine-video",
                params=json.dumps(
                    {
                        "billing_mode": "per_second",
                        "per_second_cost": 1,
                        "duration": {
                            "enabled": True,
                            "default": 5,
                            "options": [5],
                        },
                        "resolution": {
                            "enabled": True,
                            "default": "720p",
                            "options": ["720p"],
                        },
                        "image_size": {
                            "enabled": True,
                            "default": "16:9",
                            "options": ["16:9"],
                        },
                    }
                ),
            )
        )

    monkeypatch.setattr(admission_module, "_joint_insert_catalog_model", insert_model)
    return media_integration._build_system(tmp_path, operation_category="video")


def _base_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'g2-joint-fixture.sqlite'}",
        "SECRET_KEY": _SERVER_SECRET,
    }


def _host_environment(
    tmp_path: Path,
    credentials_file: Path,
    *,
    image_single_enabled: bool = False,
) -> dict[str, str]:
    transfer_dir = tmp_path / "transfer"
    transfer_dir.mkdir(parents=True, exist_ok=True)
    environment = {
        **_base_environment(tmp_path),
        "OBJECT_STORAGE_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com",
        "OBJECT_STORAGE_ACCESS_KEY_ID": "synthetic-access-key",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": "synthetic-r2-secret",
        "OBJECT_STORAGE_BUCKET": "haoai-media",
        "OBJECT_STORAGE_PUBLIC_BASE_URL": "https://cdn.example.test/media",
        "TRANSFER_TMP_DIR": str(transfer_dir),
        "IMAGE_SINGLE_ENABLED": "true" if image_single_enabled else "false",
        "VIDEO_SINGLE_ENABLED": "true",
        "VIDEO_SINGLE_CREDENTIALS_FILE": str(credentials_file),
    }
    if image_single_enabled:
        environment["IMAGE_SINGLE_CREDENTIALS_FILE"] = str(credentials_file)
    return environment


def _add_worker_capacity_column(engine: Engine) -> None:
    with engine.begin() as connection:
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("system_configs")
        }
        if "worker_max_concurrency" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE system_configs ADD COLUMN worker_max_concurrency "
                "VARCHAR(10) NOT NULL DEFAULT '4'"
            )


async def _public_getaddrinfo(
    _host: str,
    port: int,
    *,
    family: int,
    type: int,
) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    assert family == socket.AF_UNSPEC
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port))]


def test_video_single_configuration_is_opt_in_and_builds_cold_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _base_environment(tmp_path)
    disabled = load_server_configuration(environment)
    assert disabled.video_single_enabled is False
    assert disabled.video_single_credentials_file is None

    environment["VIDEO_SINGLE_ENABLED"] = "true"
    with pytest.raises(ServerConfigurationError, match="VIDEO_SINGLE_CREDENTIALS_FILE"):
        load_server_configuration(environment)

    credentials_file = tmp_path / "not-loaded-video-credentials.json"
    environment["VIDEO_SINGLE_CREDENTIALS_FILE"] = str(credentials_file)
    with pytest.raises(ServerConfigurationError, match="complete object storage"):
        load_server_configuration(environment)

    fixture = _build_video_fixture(tmp_path, monkeypatch)
    token = JoseTokenCodec(_SERVER_SECRET).encode(
        {
            "sub": media_integration._G2_JOINT_ACTOR_ID,
            "jti": media_integration.JTI,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )
    disabled_host = create_server_host(disabled)
    try:
        assert disabled_host.app.state.server_image_generation_configuration is None
        assert disabled_host.app.state.video_single_worker_runtime is None

        async def request_disabled_video() -> int:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=disabled_host.app),
                base_url="http://video-single-disabled.test",
            ) as client:
                response = await client.post(
                    "/api/chat/video",
                    headers={"Authorization": f"Bearer {token}"},
                    json=media_integration._body("video.single"),
                )
                return response.status_code

        assert asyncio.run(request_disabled_video()) == 503
    finally:
        disabled_host.close()

    _, provisioned_bundle = server_credentials._provisioned_bundle(tmp_path)
    host_environment = _host_environment(tmp_path, provisioned_bundle)
    loaded_paths: list[Path] = []
    from backend.server import image_generation as image_generation_module

    def load_fixture_capabilities(
        path: Path,
        *,
        session_factory: Callable[[], Session],
        db_clock: Callable[[], datetime],
        id_factory: Callable[[], str],
        **_kwargs: object,
    ) -> RegistryCapabilities:
        loaded_paths.append(path)
        return replace(
            fixture.capabilities,
            read_session_factory=session_factory,
            db_clock=db_clock,
            id_factory=id_factory,
        )

    monkeypatch.setattr(
        image_generation_module,
        "load_registry_capabilities",
        load_fixture_capabilities,
    )
    _add_worker_capacity_column(fixture.engine)

    host = create_server_host(
        load_server_configuration(host_environment),
        sdk_factories=(
            lambda **kwargs: _FakeS3Session([], **kwargs),
            lambda **kwargs: kwargs,
        ),
    )
    try:
        generation = host.app.state.server_image_generation_configuration
        assert generation is not None
        assert generation.generation_worker_configuration is None
        assert generation.image_batch_worker_configuration is None
        assert generation.video_single_worker_configuration is not None
        assert generation.media_runtime is not None
        assert generation.media_runtime.state == "new"
        admission = generation.admission_configuration
        assert admission is not None
        assert admission.enabled_operations == frozenset({"video.single"})
        assert admission.image_references_enabled is True
        assert loaded_paths == [provisioned_bundle]
        assert not credentials_file.exists()
        assert host.app.state.video_single_worker_runtime is not None
        assert host.app.state.video_single_worker_service is None
    finally:
        host.close()

    full_environment = _host_environment(
        tmp_path / "second",
        provisioned_bundle,
        image_single_enabled=True,
    )
    full_environment["IMAGE_SINGLE_CREDENTIALS_FILE"] = str(
        tmp_path / "different-image-bundle.json"
    )
    with pytest.raises(ServerConfigurationError, match="same credentials file"):
        load_server_configuration(full_environment)


def test_server_lifespan_runs_video_single_through_shared_image_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_video_fixture(tmp_path, monkeypatch)
    _, credentials_file = server_credentials._provisioned_bundle(tmp_path)
    _add_worker_capacity_column(fixture.engine)

    from backend.server import application as server_application
    from backend.server import image_generation as image_generation_module
    from haoai_backend import app as app_module

    loaded_registry_paths: list[Path] = []

    def load_fixture_capabilities(
        path: Path,
        *,
        session_factory: Callable[[], Session],
        db_clock: Callable[[], datetime],
        id_factory: Callable[[], str],
        **_kwargs: object,
    ) -> RegistryCapabilities:
        loaded_registry_paths.append(path)
        return replace(
            fixture.capabilities,
            read_session_factory=session_factory,
            db_clock=db_clock,
            id_factory=id_factory,
        )

    monkeypatch.setattr(
        image_generation_module,
        "load_registry_capabilities",
        load_fixture_capabilities,
    )
    provider_factory = _ProviderFactory()
    media_library = _PinnedMediaLibrary(_MediaResponse())
    uploaded: list[dict[str, object]] = []
    monkeypatch.setattr(
        image_generation_module,
        "AiohttpAsyncHttpClientFactory",
        lambda: provider_factory,
    )
    monkeypatch.setattr(image_generation_module, "_build_http_library", lambda: media_library)
    monkeypatch.setattr(image_generation_module, "_system_getaddrinfo", _public_getaddrinfo)

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
        server_application,
        "_create_engine",
        create_sqlite_engine_with_foreign_keys,
    )
    single_configurations = []
    video_configurations = []
    original_build_single = app_module.build_generation_worker_runtime
    original_build_video = app_module.build_video_single_worker_runtime

    def observe_single(configuration):
        single_configurations.append(configuration)
        return original_build_single(configuration)

    def observe_video(configuration):
        video_configurations.append(configuration)
        return original_build_video(configuration)

    monkeypatch.setattr(
        app_module, "build_generation_worker_runtime", observe_single
    )
    monkeypatch.setattr(
        app_module, "build_video_single_worker_runtime", observe_video
    )
    worker_service_class = app_module.GenerationWorkerService

    class FastRecoveryWorkerService(worker_service_class):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["recovery_scan_interval_seconds"] = 0.05
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        app_module, "GenerationWorkerService", FastRecoveryWorkerService
    )
    host = create_server_host(
        load_server_configuration(
            _host_environment(
                tmp_path,
                credentials_file,
                image_single_enabled=True,
            )
        ),
        sdk_factories=(
            lambda **kwargs: _FakeS3Session(uploaded, **kwargs),
            lambda **kwargs: kwargs,
        ),
        worker_environment={"WORKER_CONCURRENCY": "4"},
    )

    try:
        generation = host.app.state.server_image_generation_configuration
        assert generation is not None
        single = generation.generation_worker_configuration
        video = generation.video_single_worker_configuration
        assert single is not None and video is not None
        assert generation.media_runtime is not None
        assert loaded_registry_paths == [credentials_file]
        assert single.runtime is video.runtime
        assert single.registry_capabilities is video.registry_capabilities
        admission = generation.admission_configuration
        assert admission is not None
        assert admission.enabled_operations == frozenset(
            {"image.single", "video.single"}
        )
        assert admission.image_references_enabled is True
        assert len(single_configurations) == len(video_configurations) == 1
        assert (
            single_configurations[0].runtime.worker_capacity
            is video_configurations[0].runtime.worker_capacity
            is host.app.state.worker_execution_capacity
        )

        token = JoseTokenCodec(_SERVER_SECRET).encode(
            {
                "sub": media_integration._G2_JOINT_ACTOR_ID,
                "jti": media_integration.JTI,
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            }
        )
        key = "server-video-single-host-966"
        headers = {
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": key,
        }

        async def run() -> int:
            async with host.app.router.lifespan_context(host.app):
                assert host.app.state.video_single_worker_service is not None
                assert host.app.state.generation_worker_service is not None
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(
                        app=host.app,
                        raise_app_exceptions=False,
                    ),
                    base_url="http://video-single-host.test",
                ) as client:
                    response = await client.post(
                        "/api/chat/video",
                        headers=headers,
                        json=media_integration._body("video.single"),
                    )
                    assert response.status_code == 200, response.text
                    accepted = response.json()
                    assert accepted["operation"] == "video.single"
                    assert accepted["status"] == "queued"
                    assert accepted["billing_status"] == "charged"
                    task_id = accepted["task_id"]

                    task_receipt = None
                    for _ in range(120):
                        task_response = await client.get(
                            "/api/chat/tasks",
                            params={"task_id": task_id},
                            headers={"Authorization": headers["Authorization"]},
                        )
                        assert task_response.status_code == 200, task_response.text
                        task_receipt = task_response.json()
                        if task_receipt["status"] == "completed":
                            break
                        await asyncio.sleep(0.05)
                    assert task_receipt is not None
                    assert task_receipt["status"] == "completed"

                    submission = await client.get(
                        "/api/chat/submissions/video.single",
                        params={"idempotency_key": key},
                        headers={"Authorization": headers["Authorization"]},
                    )
                    assert submission.status_code == 200, submission.text
                    assert submission.json()["task_id"] == task_id
                    assert submission.json()["status"] == "completed"
                    return accepted["quoted_amount"]

        accepted_quoted_amount = asyncio.run(run())

        assert [method for method, _url in provider_factory.calls] == ["POST", "GET"]
        assert provider_factory.calls[1][1].endswith(
            f"/v1/videos/{_PROVIDER_TASK_ID}"
        )
        assert len(uploaded) == 1
        stored = uploaded[0]
        assert stored["body"] == _VIDEO_BYTES
        assert stored["content_type"] == "video/mp4"
        assert isinstance(stored["key"], str)
        assert stored["key"].endswith(".mp4")
        assert "/generation/" in stored["key"]
        digest = hashlib.sha256(_VIDEO_BYTES).hexdigest()
        with host.session_factory() as session:
            evidence = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_kind == "provider_video"
                )
            ).mappings().one()
            balance = session.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            credit_logs = session.execute(select(CreditLogRow.__table__)).all()
        assert evidence["payload_digest"] == digest
        assert evidence["result_digest"] == digest
        assert evidence["result_location"].endswith(str(stored["key"]))
        assert balance == 500 - accepted_quoted_amount
        assert len(credit_logs) == 1
        assert host.app.state.video_single_worker_service is None
        assert host.app.state.video_single_worker_shutdown is not None
        assert host.app.state.generation_worker_service is None
    finally:
        host.close()
        fixture.engine.dispose()


def test_video_media_policy_rejects_generic_mime_and_oversized_content() -> None:
    async def run(response: _MediaResponse) -> tuple[object, _PinnedMediaLibrary]:
        library = _PinnedMediaLibrary(response)
        fetcher = PinnedPublicMediaFetcher(
            PublicFetchPolicy(_public_getaddrinfo),
            _image_media_policy(include_video=True),
            http_library=library,
        )
        outcome = await fetcher.fetch_public(
            ReferenceInput(kind="public_http", locator=_VIDEO_URL),
            max_bytes=MAX_VIDEO_BYTES,
            timeout_seconds=600,
        )
        return outcome, library

    generic, generic_library = asyncio.run(
        run(_MediaResponse(media_type="application/octet-stream"))
    )
    assert isinstance(generic, TransferFailure)
    assert generic.code == "unsupported_media_type"
    assert generic_library.response.iterated is False

    oversized, oversized_library = asyncio.run(
        run(
            _MediaResponse(
                media_type="video/mp4",
                content=b"",
                declared_length=MAX_VIDEO_BYTES + 1,
            )
        )
    )
    assert isinstance(oversized, TransferFailure)
    assert oversized.code == "reference_too_large"
    assert oversized_library.response.iterated is False
