"""Thin explicit composition root for the registered frame.review application."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from fastapi import FastAPI
from sqlalchemy.orm import Session

from .app import create_app
from .authentication.application import AuthenticationService
from .authentication.configuration import AuthenticationRuntime
from .authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from .chat_text_generation import ChatTextGenerationConfiguration
from .chat_text_generation.ports import FrameReviewAdmissionConfiguration
from .frame_review_http import build_frame_review_router
from .generation_contracts.ports import GenerationRuntimeConfig
from .generation_credentials.application import (
    RegistryCapabilities,
    build_credential_registry,
)
from .generation_runtime.frame_review_assembly import FrameReviewWorkerConfiguration

SessionFactory = Callable[[], Session]


def create_frame_review_app(
    *,
    session_factory: SessionFactory,
    authentication_runtime: AuthenticationRuntime,
    registry_capabilities: RegistryCapabilities,
    generation_runtime: GenerationRuntimeConfig,
    id_factory: Callable[[], str] | None = None,
    clock: Callable[[], datetime] | None = None,
    max_user_concurrency: int = 0,
    enabled: bool = True,
) -> FastAPI:
    """Compose the existing app with an explicit registered review worker and routes."""

    if not callable(session_factory):
        raise TypeError("session_factory must be an explicit callable")
    if not isinstance(authentication_runtime, AuthenticationRuntime):
        raise TypeError("authentication_runtime must be explicit")
    if not isinstance(registry_capabilities, RegistryCapabilities):
        raise TypeError("registry_capabilities must be explicit")
    if not isinstance(generation_runtime, GenerationRuntimeConfig):
        raise TypeError("generation_runtime must be explicit")
    if not isinstance(enabled, bool):
        raise TypeError("enabled must be a boolean")
    if (
        authentication_runtime.session_factory is not session_factory
        or generation_runtime.session_factory is not session_factory
    ):
        raise ValueError("authentication, application, and worker must use the same session factory")

    requested_id_factory = id_factory or generation_runtime.id_factory
    requested_clock = clock or generation_runtime.db_clock
    for name, supplied, expected in (
        ("registry session factory", registry_capabilities.read_session_factory, session_factory),
        ("registry database clock", registry_capabilities.db_clock, generation_runtime.db_clock),
        ("registry id factory", registry_capabilities.id_factory, generation_runtime.id_factory),
        ("explicit id factory", requested_id_factory, generation_runtime.id_factory),
        ("explicit clock", requested_clock, generation_runtime.db_clock),
    ):
        if supplied is not None and supplied is not expected:
            raise ValueError(f"{name} must use the same configured dependency")

    capabilities = replace(
        registry_capabilities,
        read_session_factory=session_factory,
        db_clock=generation_runtime.db_clock,
        id_factory=generation_runtime.id_factory,
    )
    registry = build_credential_registry(capabilities)
    worker_runtime = replace(
        generation_runtime,
        credential_resolver=registry.resolve_original_credential,
    )

    chat_configuration = ChatTextGenerationConfiguration(
        runtime=None,
        max_user_concurrency=max_user_concurrency,
        server_id_factory=requested_id_factory,
        clock=requested_clock,
        frame_review_admission=(
            FrameReviewAdmissionConfiguration(capabilities) if enabled else None
        ),
    )
    worker_configuration = (
        FrameReviewWorkerConfiguration(worker_runtime, capabilities)
        if enabled
        else None
    )
    app = create_app(
        session_factory=session_factory,
        authentication_runtime=authentication_runtime,
        chat_text_generation_configuration=chat_configuration,
        frame_review_worker_configuration=worker_configuration,
    )

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
            registry_capabilities=capabilities,
            enabled=enabled,
            max_user_concurrency=max_user_concurrency,
            id_factory=requested_id_factory,
            clock=requested_clock,
        )
    )
    return app


__all__ = ("create_frame_review_app",)
