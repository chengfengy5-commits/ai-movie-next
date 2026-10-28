"""Compose the opt-in frame.review host dependencies and HTTP router."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import FastAPI
from sqlalchemy.orm import Session

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.chat_text_generation.ports import FrameReviewAdmissionConfiguration
from haoai_backend.frame_review_http import build_frame_review_router
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    build_credential_registry,
)
from haoai_backend.generation_runtime.frame_review_assembly import (
    FrameReviewWorkerConfiguration,
)

from .configuration import ServerConfiguration
from .credentials import load_registry_capabilities
from .generation import (
    GenerationHostConfigurationError,
    _UnavailableBoundContentFetcher,
    _UnavailableGenerationMediaStore,
    _UnavailableLocalMuseCapability,
    _UnavailablePublicMediaFetcher,
    _UnavailableR2StorageCapability,
    _database_clock,
    _uuid_id,
)
from .http import AiohttpAsyncHttpClientFactory

SessionFactory = Callable[[], Session]
IdFactory = Callable[[], str]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class FrameReviewHostConfiguration:
    enabled: bool
    registry_capabilities: RegistryCapabilities
    worker_configuration: FrameReviewWorkerConfiguration | None
    admission_configuration: FrameReviewAdmissionConfiguration | None
    id_factory: IdFactory
    clock: Clock

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be explicit")
        if not isinstance(self.registry_capabilities, RegistryCapabilities):
            raise TypeError("registry capabilities must be explicit")
        if not callable(self.id_factory) or not callable(self.clock):
            raise TypeError("frame.review identity dependencies must be callable")
        if self.enabled:
            if self.worker_configuration is None or self.admission_configuration is None:
                raise ValueError("enabled frame.review requires admission and worker configuration")
            if (
                self.worker_configuration.registry_capabilities
                is not self.registry_capabilities
                or self.admission_configuration.registry_capabilities
                is not self.registry_capabilities
                or self.worker_configuration.runtime.id_factory is not self.id_factory
                or self.worker_configuration.runtime.db_clock is not self.clock
            ):
                raise ValueError("frame.review dependencies must share one host identity")
        elif self.worker_configuration is not None or self.admission_configuration is not None:
            raise ValueError("disabled frame.review cannot configure admission or worker")


def build_frame_review_host_configuration(
    configuration: ServerConfiguration,
    *,
    session_factory: SessionFactory,
) -> FrameReviewHostConfiguration:
    """Build lazy review dependencies using only this host's Session and credentials."""
    if not isinstance(configuration, ServerConfiguration):
        raise GenerationHostConfigurationError("server configuration is unavailable")
    if not callable(session_factory):
        raise GenerationHostConfigurationError("server Session factory is unavailable")

    id_factory = _uuid_id
    clock = _database_clock(session_factory)
    if not configuration.frame_review_enabled:
        return FrameReviewHostConfiguration(
            enabled=False,
            registry_capabilities=RegistryCapabilities.unconfigured(),
            worker_configuration=None,
            admission_configuration=None,
            id_factory=id_factory,
            clock=clock,
        )

    credentials_path = configuration.frame_review_credentials_file
    if credentials_path is None or not credentials_path.is_absolute():
        raise GenerationHostConfigurationError(
            "frame.review requires an absolute credentials file when enabled"
        )
    capabilities = load_registry_capabilities(
        credentials_path,
        legacy_secret_key=configuration.secret_key.value,
        session_factory=session_factory,
        db_clock=clock,
        id_factory=id_factory,
    )
    registry = build_credential_registry(capabilities)
    runtime = GenerationRuntimeConfig(
        session_factory=session_factory,
        db_clock=clock,
        id_factory=id_factory,
        http_client_factory=AiohttpAsyncHttpClientFactory(),
        credential_resolver=registry.resolve_original_credential,
        provider_factories=(),
        media_store_factory=_UnavailableGenerationMediaStore,
        local_muse_staging_dir=None,
        public_media_fetcher_factory=_UnavailablePublicMediaFetcher,
        bound_provider_content_fetcher_factory=_UnavailableBoundContentFetcher,
        local_muse_capability_factory=_UnavailableLocalMuseCapability,
        r2_storage_factory=_UnavailableR2StorageCapability,
    )
    worker_configuration = FrameReviewWorkerConfiguration(runtime, capabilities)
    admission_configuration = FrameReviewAdmissionConfiguration(capabilities)
    return FrameReviewHostConfiguration(
        enabled=True,
        registry_capabilities=capabilities,
        worker_configuration=worker_configuration,
        admission_configuration=admission_configuration,
        id_factory=id_factory,
        clock=clock,
    )


def include_frame_review_router(
    app: FastAPI,
    *,
    session_factory: SessionFactory,
    authentication_runtime: AuthenticationRuntime,
    configuration: FrameReviewHostConfiguration,
    max_user_concurrency: int,
) -> None:
    """Mount one registered review router with the server's active-auth Session."""
    if not isinstance(configuration, FrameReviewHostConfiguration):
        raise TypeError("an explicit frame.review host configuration is required")
    if authentication_runtime.session_factory is not session_factory:
        raise ValueError("frame.review authentication must share the server Session")
    if configuration.enabled and configuration.worker_configuration is None:
        raise ValueError("enabled frame.review requires a worker configuration")

    def authentication_uow():
        return SqlAlchemyAuthenticationUnitOfWork(session_factory())

    authentication_service = AuthenticationService(
        authentication_runtime,
        authentication_uow,
    )
    app.include_router(
        build_frame_review_router(
            authentication_service=authentication_service,
            session_factory=session_factory,
            registry_capabilities=configuration.registry_capabilities,
            enabled=configuration.enabled,
            max_user_concurrency=max_user_concurrency,
            id_factory=configuration.id_factory,
            clock=configuration.clock,
        )
    )


__all__ = (
    "FrameReviewHostConfiguration",
    "build_frame_review_host_configuration",
    "include_frame_review_router",
)
