"""Compose the existing backend app with explicitly owned host resources."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from haoai_backend.authentication import AuthenticationRuntime
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

from .configuration import ServerConfiguration, ServerConfigurationError


class ServerHostAssemblyError(RuntimeError):
    """A host setup failure with no configuration values in its message."""


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
    def __init__(self, engine: Engine, storage: R2MediaUploadStorage | None) -> None:
        self._engine = engine
        self._storage = storage
        self._lock = threading.Lock()
        self._storage_cleanup_complete = storage is None
        self._engine_disposed = False

    def close(self) -> None:
        with self._lock:
            if self._storage_cleanup_complete and self._engine_disposed:
                return

            failures = False
            if not self._storage_cleanup_complete and self._storage is not None:
                try:
                    outcomes = self._storage.retry_pending_cleanup()
                    self._storage_cleanup_complete = all(
                        outcome is True for outcome in outcomes
                    )
                    failures = not self._storage_cleanup_complete
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
) -> ServerHost:
    """Build the existing ASGI application without connecting to external systems."""
    if not isinstance(configuration, ServerConfiguration):
        raise ServerConfigurationError("an explicit server configuration is required")

    engine: Engine | None = None
    resources: _OwnedResources | None = None
    try:
        engine = _create_engine(configuration.database_url)
        session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
            future=True,
        )
        authentication_runtime = _ServerAuthenticationRuntime(
            session_factory=session_factory,
            secret_key=configuration.secret_key.value,
        )
        storage: R2MediaUploadStorage | None = None
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

        resources = _OwnedResources(engine, storage)
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
        )
        app.state.server_session_factory = session_factory
        app.state.server_resources = resources
        app.state.server_engine = engine
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


def _create_engine(database_url: str) -> Engine:
    from sqlalchemy.engine import make_url

    parsed = make_url(database_url)
    options: dict[str, object] = {"future": True, "pool_pre_ping": True}
    if parsed.drivername in {"sqlite", "sqlite+pysqlite"}:
        options["connect_args"] = {"check_same_thread": False}
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
