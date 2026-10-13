"""FastAPI adapter for the eight legacy AIStarsLab endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from haoai_backend.admin_system_config.ports import UnitOfWorkFactory as SystemConfigUnitOfWorkFactory
from haoai_backend.admin_system_models.ports import Cipher, UnitOfWorkFactory as SystemModelUnitOfWorkFactory
from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.model_configs.ports import ModelConfigsUnitOfWorkFactory
from haoai_backend.shared.errors import BusinessError

from .application import AistarslabApplication
from .authentication import AistarslabActor, authenticate_aistarslab_actor
from .ports import (
    AistarslabLegacyReaderFactory,
    AistarslabTransport,
    Clock,
    IdFactory,
)
from .provider import ConfigCache, system_clock
from .schemas import (
    AddDefaultRequest,
    BuildImageParamsRequest,
    BuildParamsRequest,
    EstimateCostRequest,
)


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_aistarslab_router(
    *,
    authentication_service: AuthenticationService | None,
    model_configs_uow_factory: ModelConfigsUnitOfWorkFactory | None,
    legacy_reader_factory: AistarslabLegacyReaderFactory,
    system_config_uow_factory: SystemConfigUnitOfWorkFactory | None,
    system_model_uow_factory: SystemModelUnitOfWorkFactory | None,
    cipher: Cipher,
    transport: AistarslabTransport,
    clock: Clock = system_clock,
    id_factory: IdFactory = lambda: str(uuid4()),
    cache: ConfigCache | None = None,
) -> APIRouter:
    """Build the route set from explicit authentication, storage, and HTTP ports."""
    router = APIRouter(prefix="/api/aistarslab", tags=["aistarslab"])
    application = AistarslabApplication(
        model_configs_uow_factory=model_configs_uow_factory,
        legacy_reader_factory=legacy_reader_factory,
        system_config_uow_factory=system_config_uow_factory,
        system_model_uow_factory=system_model_uow_factory,
        cipher=cipher,
        transport=transport,
        clock=clock,
        id_factory=id_factory,
        cache=cache,
    )

    def active_member(request: Request) -> AistarslabActor:
        if authentication_service is None:
            raise HTTPException(
                status_code=503,
                detail="认证服务尚未接线",
            )
        try:
            return authenticate_aistarslab_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers=error.headers,
            ) from error

    async def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return await call(*args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/config", response_model=None)
    async def config(
        actor: AistarslabActor = Depends(active_member),
    ) -> dict[str, Any]:
        return await invoke(application.config, actor.user_id)

    @router.get("/credits", response_model=None)
    async def credits(
        actor: AistarslabActor = Depends(active_member),
    ) -> dict[str, Any]:
        return await invoke(application.credits, actor.user_id)

    @router.post("/add-default", response_model=None)
    async def add_default(
        request_body: AddDefaultRequest,
        actor: AistarslabActor = Depends(active_member),
    ) -> dict[str, Any]:
        return await invoke(application.add_default, actor, request_body)

    @router.post("/build-image-params", response_model=None)
    async def build_image_params(
        request_body: BuildImageParamsRequest,
        actor: AistarslabActor = Depends(active_member),
    ) -> dict[str, Any]:
        return await invoke(
            application.build_image_params,
            actor.user_id,
            request_body.channel,
            request_body.model,
        )

    @router.post("/add-image-default", response_model=None)
    async def add_image_default(
        request_body: AddDefaultRequest,
        actor: AistarslabActor = Depends(active_member),
    ) -> dict[str, Any]:
        return await invoke(
            application.add_image_default,
            actor,
            request_body,
        )

    @router.post("/estimate-cost", response_model=None)
    async def estimate_cost(
        request_body: EstimateCostRequest,
        actor: AistarslabActor = Depends(active_member),
    ) -> dict[str, Any]:
        return await invoke(
            application.estimate_cost,
            actor.user_id,
            channel=request_body.channel,
            model_name=request_body.model,
            quality=request_body.quality,
            duration=request_body.duration,
            has_input_video=request_body.has_input_video,
        )

    @router.post("/build-params", response_model=None)
    async def build_video_params(
        request_body: BuildParamsRequest,
        actor: AistarslabActor = Depends(active_member),
    ) -> dict[str, Any]:
        return await invoke(
            application.build_video_params,
            actor.user_id,
            request_body.channel,
            request_body.model,
        )

    @router.post("/sync-all", response_model=None)
    async def sync_all(
        actor: AistarslabActor = Depends(active_member),
    ) -> dict[str, int]:
        return await invoke(application.sync_all, actor)

    return router
