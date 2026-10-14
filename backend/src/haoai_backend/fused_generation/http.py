"""Authenticated HTTP adapter for legacy fused-image generation."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from .application import prepare_fused_generation
from .ports import FusedGenerationRuntime, FusedGenerationStorage, FusedGenerationUnitOfWorkFactory
from .execution import FusedGenerationExecution
from .schemas import FusedAssetGenerateRequest

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_fused_generation_router(
    *,
    uow_factory: FusedGenerationUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
    runtime: FusedGenerationRuntime | None,
    storage: FusedGenerationStorage | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["fused-generation"])
    execution = FusedGenerationExecution(
        runtime=runtime,
        uow_factory=uow_factory,
        storage=storage,
    )
    runtime_ready = runtime is not None and callable(getattr(runtime, "generate", None))

    async def trusted_actor(request: Request) -> TrustedActor:
        if resolve_actor is None:
            raise HTTPException(status_code=503, detail="认证服务尚未接线")
        try:
            if inspect.iscoroutinefunction(resolve_actor) or inspect.iscoroutinefunction(
                getattr(resolve_actor, "__call__", None)
            ):
                candidate = resolve_actor(request)
            else:
                candidate = await run_in_threadpool(resolve_actor, request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as error:
            raise _http_error(error) from error
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise HTTPException(status_code=503, detail="可信身份端口未正确接线")
        return actor

    @router.post("/fused-assets/generate", response_model=None)
    async def generate_fused_asset(
        request: Request,
        background_tasks: BackgroundTasks,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, str | None]:
        # Do not parse a potentially large/malformed body or open a business
        # Session until the authenticated-member resolver has closed its UOW.
        if uow_factory is None or not runtime_ready:
            raise HTTPException(status_code=503, detail="融合生成服务尚未接线")
        payload = await _validated_request(request)
        try:
            prepared = await run_in_threadpool(
                prepare_fused_generation,
                uow_factory,
                actor,
                payload,
            )
        except BusinessError as error:
            raise _http_error(error) from error
        background_tasks.add_task(execution.execute, prepared)
        return {"task_id": prepared.task_id, "message_id": prepared.message_id}

    return router


async def _validated_request(request: Request) -> FusedAssetGenerateRequest:
    try:
        body = await request.json()
    except Exception as error:
        raise HTTPException(status_code=422, detail="请求 JSON 无效") from error
    try:
        return FusedAssetGenerateRequest.model_validate(body)
    except ValidationError as error:
        raise RequestValidationError(error.errors(), body=body) from error
