"""Compose the existing backend app with explicitly owned host resources."""

from __future__ import annotations

import asyncio
import math
import os
import threading
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import Column, MetaData, String, Table, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from haoai_backend.authentication import AuthenticationRuntime
from haoai_backend.chat_text_generation import ChatTextGenerationConfiguration
from haoai_backend.media_storage.owned_media_runtime import OwnedMediaRuntime
from haoai_backend.media_storage.r2_sdk_factory import Boto3S3ClientFactory
from haoai_backend.media_storage.transfer_types import SecretValue
from haoai_backend.media_uploads import MediaUploadApplication
from haoai_backend.media_uploads.adapters import R2MediaUploadStorage
from haoai_backend.runtime_status import (
    Boto3R2HeadBucketClientFactory,
    R2HeadBucketProbe,
    R2HeadBucketSettings,
    RuntimeStatusConfiguration,
    create_generation_schema_readiness_probe,
)
from haoai_backend.storyboard_asset_generation.openai_runtime import (
    OpenAICompatibleStoryboardAssetRuntime,
)
from haoai_backend.generation_runtime.worker_capacity import WorkerCapacityBootstrap

from .configuration import ServerConfiguration, ServerConfigurationError
from .generation import (
    _database_clock,
    _uuid_id,
    build_frame_optimize_configuration,
)


class ServerHostAssemblyError(RuntimeError):
    """A host setup failure with no configuration values in its message."""


_WORKER_CONFIGURATION_METADATA = MetaData()
_WORKER_CONFIGURATION = Table(
    "system_configs",
    _WORKER_CONFIGURATION_METADATA,
    Column("worker_max_concurrency", String(10)),
)


class _ServerAuthenticationRuntime(AuthenticationRuntime):
    def __repr__(self) -> str:
        return "_ServerAuthenticationRuntime(<redacted>)"


@dataclass(frozen=True, slots=True)
class ServerHost:
    app: Any
    session_factory: Callable[[], Any] = field(repr=False)
    _resources: _OwnedResources = field(repr=False, compare=False)

    def close(self) -> None:
        self._resources.close()


class _SecretHandle:
    __slots__ = ("_value",)

    def __init__(self, value: SecretValue) -> None:
        self._value = value

    def reveal(self) -> SecretValue:
        return self._value

    def __repr__(self) -> str:
        return "_SecretHandle(<redacted>)"


class _OwnedResources:
    def __init__(
        self,
        engine: Engine,
        storage: R2MediaUploadStorage | None,
        media_runtime: OwnedMediaRuntime | None = None,
    ) -> None:
        self._engine = engine
        self._storage = storage
        self._media_runtime = media_runtime
        self._lock = threading.Lock()
        self._storage_cleanup_complete = storage is None
        self._media_runtime_cleanup_complete = media_runtime is None
        self._engine_disposed = False

    def close(self) -> None:
        with self._lock:
            if (
                self._media_runtime_cleanup_complete
                and self._storage_cleanup_complete
                and self._engine_disposed
            ):
                return

            failures = False
            if (
                not self._media_runtime_cleanup_complete
                and self._media_runtime is not None
            ):
                try:
                    self._media_runtime.close()
                    self._media_runtime_cleanup_complete = True
                except BaseException:
                    failures = True

            if not self._storage_cleanup_complete and self._storage is not None:
                try:
                    outcomes = self._storage.retry_pending_cleanup()
                    self._storage_cleanup_complete = all(
                        outcome is True for outcome in outcomes
                    )
                    failures = failures or not self._storage_cleanup_complete
                except BaseException:
                    failures = True

            if not self._engine_disposed:
                try:
                    self._engine.dispose()
                except BaseException:
                    failures = True
                else:
                    self._engine_disposed = True

            if failures:
                raise RuntimeError("server resource cleanup did not complete") from None


def create_server_host(
    configuration: ServerConfiguration,
    *,
    sdk_factories: tuple[Callable[..., object], Callable[..., object]] | None = None,
    worker_environment: Mapping[str, str] | None = None,
) -> ServerHost:
    """Build the existing ASGI application without connecting to external systems."""
    if not isinstance(configuration, ServerConfiguration):
        raise ServerConfigurationError("an explicit server configuration is required")
    environment = os.environ if worker_environment is None else worker_environment

    engine: Engine | None = None
    resources: _OwnedResources | None = None
    try:
        engine = _create_engine(configuration.database_url)
        session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
            future=True,
        )
        frame_optimize_configuration = build_frame_optimize_configuration(
            configuration,
            session_factory=session_factory,
        )
        authentication_runtime = _ServerAuthenticationRuntime(
            session_factory=session_factory,
            secret_key=configuration.secret_key.value,
        )
        storyboard_asset_generation_runtime = OpenAICompatibleStoryboardAssetRuntime()
        storage: R2MediaUploadStorage | None = None
        upload_factory: Boto3S3ClientFactory | None = None
        storage_probe = None
        media_application: MediaUploadApplication

        if configuration.r2_configuration is None:
            media_application = MediaUploadApplication(
                storage=None,
                public_media_fetcher=None,
                managed_origin_map={},
            )
        else:
            if (
                configuration.r2_access_key is None
                or configuration.r2_secret_key is None
            ):
                raise ServerConfigurationError(
                    "R2 credentials are required with storage"
                )
            session_constructor, config_constructor = (
                sdk_factories if sdk_factories is not None else _load_sdk_factories()
            )
            access_key = _SecretHandle(configuration.r2_access_key)
            secret_key = _SecretHandle(configuration.r2_secret_key)
            upload_factory = Boto3S3ClientFactory(
                session_factory=session_constructor,
                config_factory=config_constructor,
            )
            storage = R2MediaUploadStorage(
                configuration=configuration.r2_configuration,
                access_key=access_key,
                secret_key=secret_key,
                client_factory=upload_factory,
            )
            media_application = MediaUploadApplication(
                storage=storage,
                public_media_fetcher=None,
                managed_origin_map={},
            )
            head_bucket_factory = Boto3R2HeadBucketClientFactory(
                session_factory=session_constructor,
                config_factory=config_constructor,
            )
            storage_probe = R2HeadBucketProbe(
                R2HeadBucketSettings(
                    endpoint_url=configuration.r2_configuration.endpoint_url,
                    bucket=configuration.r2_configuration.bucket,
                ),
                access_key=access_key,
                secret_key=secret_key,
                client_factory=head_bucket_factory,
            )

        image_generation_configuration = None
        if configuration.image_single_enabled or configuration.image_batch_enabled:
            from .image_generation import build_image_generation_host_configuration

            image_generation_configuration = build_image_generation_host_configuration(
                configuration,
                session_factory=session_factory,
                s3_client_factory=upload_factory,
            )

        chat_text_generation_configuration = None
        if configuration.text_chat_enabled:
            from haoai_backend.chat_text_generation.openai_runtime import (
                OpenAICompatibleChatTextRuntime,
            )

            chat_text_generation_configuration = ChatTextGenerationConfiguration(
                runtime=OpenAICompatibleChatTextRuntime(),
                max_user_concurrency=configuration.user_task_max_concurrency,
                server_id_factory=_uuid_id,
                clock=_database_clock(session_factory),
            )

        worker_capacity_bootstrap = None
        worker_capacity_enabled = bool(
            (
                image_generation_configuration is not None
                and (
                    image_generation_configuration.generation_worker_configuration
                    is not None
                    or image_generation_configuration.image_batch_worker_configuration
                    is not None
                )
            )
            or (
                frame_optimize_configuration is not None
                and frame_optimize_configuration.enabled
            )
        )
        if worker_capacity_enabled:
            worker_capacity_bootstrap = WorkerCapacityBootstrap(
                read_database_value=lambda: _read_worker_max_concurrency(
                    session_factory
                ),
                environment_value=environment.get("WORKER_CONCURRENCY"),
                timeout_seconds=_worker_db_call_timeout_seconds(environment),
            )

        resources = _OwnedResources(
            engine,
            storage,
            (
                image_generation_configuration.media_runtime
                if image_generation_configuration is not None
                else None
            ),
        )
        from haoai_backend.app import create_app

        app = create_app(
            session_factory=session_factory,
            authentication_runtime=authentication_runtime,
            runtime_status_configuration=RuntimeStatusConfiguration(
                service_name="Hao AI Backend",
                version="0.1.0",
                readiness_initialized=True,
                object_storage_configured=configuration.r2_configuration is not None,
                probe_timeout_seconds=10,
                ready_cache_seconds=1,
                initial_failure_backoff_seconds=1,
                max_failure_backoff_seconds=10,
            ),
            runtime_status_database_probe=(
                create_generation_schema_readiness_probe(session_factory)
            ),
            runtime_status_storage_probe=storage_probe,
            media_upload_application=media_application,
            frame_optimize_worker_configuration=frame_optimize_configuration,
            storyboard_asset_generation_runtime=storyboard_asset_generation_runtime,
            chat_media_admission_configuration=(
                image_generation_configuration.admission_configuration
                if image_generation_configuration is not None
                else None
            ),
            chat_text_generation_configuration=chat_text_generation_configuration,
            generation_worker_configuration=(
                image_generation_configuration.generation_worker_configuration
                if image_generation_configuration is not None
                else None
            ),
            image_batch_worker_configuration=(
                image_generation_configuration.image_batch_worker_configuration
                if image_generation_configuration is not None
                else None
            ),
            worker_capacity_bootstrap=worker_capacity_bootstrap,
        )
        app.state.server_session_factory = session_factory
        app.state.server_resources = resources
        app.state.server_engine = engine
        app.state.server_frame_optimize_worker_configuration = (
            frame_optimize_configuration
        )
        app.state.server_image_generation_configuration = (
            image_generation_configuration
        )
        app.state.server_image_batch_worker_configuration = (
            image_generation_configuration.image_batch_worker_configuration
            if image_generation_configuration is not None
            else None
        )
        app.state.server_chat_text_generation_configuration = (
            chat_text_generation_configuration
        )
        _compose_host_lifespan(app, resources)
        return ServerHost(
            app=app,
            session_factory=session_factory,
            _resources=resources,
        )
    except BaseException as error:
        cleanup_failed = False
        if resources is not None:
            try:
                resources.close()
            except BaseException:
                cleanup_failed = True
        elif engine is not None:
            try:
                engine.dispose()
            except BaseException:
                cleanup_failed = True
        if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        if cleanup_failed:
            raise ServerHostAssemblyError(
                "server host assembly and cleanup both failed"
            ) from None
        if isinstance(error, ServerHostAssemblyError):
            raise
        raise ServerHostAssemblyError("server host assembly failed") from None


def _read_worker_max_concurrency(session_factory: Callable[[], Any]) -> object:
    """Read only the worker capacity field using the host's existing Session factory."""

    session = session_factory()
    try:
        return session.execute(
            select(_WORKER_CONFIGURATION.c.worker_max_concurrency).limit(1)
        ).scalar_one_or_none()
    finally:
        try:
            session.rollback()
        finally:
            session.close()


def _worker_db_call_timeout_seconds(environment: Mapping[str, str]) -> float:
    """Preserve the bounded DB bridge's default and lower bound safely."""

    try:
        value = float(environment.get("WORKER_DB_CALL_TIMEOUT", "10"))
    except (TypeError, ValueError):
        raise ServerConfigurationError(
            "WORKER_DB_CALL_TIMEOUT must be a finite number"
        ) from None
    if not math.isfinite(value):
        raise ServerConfigurationError(
            "WORKER_DB_CALL_TIMEOUT must be a finite number"
        )
    return max(0.1, value)


def _create_engine(database_url: str) -> Engine:
    from sqlalchemy.engine import make_url

    parsed = make_url(database_url)
    options: dict[str, object] = {"future": True, "pool_pre_ping": True}
    if parsed.drivername in {"sqlite", "sqlite+pysqlite"}:
        options["connect_args"] = {"check_same_thread": False}
    elif parsed.get_backend_name() == "postgresql":
        options["isolation_level"] = "READ COMMITTED"
    return create_engine(database_url, **options)


def _load_sdk_factories() -> tuple[Callable[..., object], Callable[..., object]]:
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise ServerHostAssemblyError(
            "R2 is configured but its SDK is unavailable"
        ) from None
    return boto3.Session, Config


def _compose_host_lifespan(app: Any, resources: _OwnedResources) -> None:
    existing_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def owned_lifespan(application: Any):
        app_error: BaseException | None = None
        try:
            async with existing_lifespan(application):
                yield
        except BaseException as error:
            app_error = error

        cleanup_error: BaseException | None = None
        try:
            await _close_resources(resources)
        except BaseException as error:
            cleanup_error = error

        if app_error is not None and cleanup_error is not None:
            raise BaseExceptionGroup(
                "application shutdown and host cleanup both failed",
                [app_error, cleanup_error],
            )
        if cleanup_error is not None:
            raise cleanup_error
        if app_error is not None:
            raise app_error

    app.router.lifespan_context = owned_lifespan


async def _close_resources(resources: _OwnedResources) -> None:
    task = asyncio.create_task(asyncio.to_thread(resources.close))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if task.done():
            try:
                task.exception()
            except BaseException:
                pass
        raise


__all__ = ("ServerHost", "create_server_host")
