"""HTTP routes for the four generic-task methods."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from haoai_backend.provider_status import (
    ProviderStatusPort,
    ProviderStatusRuntime,
    create_provider_status_service,
)
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .ports import GenericTaskRuntime, GenericTaskUnitOfWorkFactory, TaskClock, TaskIdFactory

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


class CreateTaskRequest(BaseModel):
    type: str
    credit_cost: int = 0
    request_data: str | None = None


class UpdateTaskRequest(BaseModel):
    status: str
    result: str | None = None


class UpdateTaskProgressRequest(BaseModel):
    progress: int
    progress_message: str | None = None


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_generic_tasks_router(
    *,
    uow_factory: GenericTaskUnitOfWorkFactory | None,
    resolve_active_actor: ActorResolver | None,
    provider_status_runtime: ProviderStatusRuntime | None = None,
    provider_status_port: ProviderStatusPort | None = None,
    clock: TaskClock | None = None,
    id_factory: TaskIdFactory | None = None,
) -> APIRouter:
    """Build all four routes from explicit business, identity, and provider ports."""

    status_port = provider_status_port
    if status_port is None and provider_status_runtime is not None:
        status_port = create_provider_status_service(provider_status_runtime)

    runtime = GenericTaskRuntime(
        clock=datetime.utcnow if clock is None else clock,
        id_factory=(lambda: str(uuid.uuid4())) if id_factory is None else id_factory,
        provider_status=status_port,
    )
    router = APIRouter(prefix="/api/tasks", tags=["generic-tasks"])

    async def active_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_active_actor is None:
            raise HTTPException(status_code=503, detail="通用任务服务暂不可用")
        try:
            candidate = resolve_active_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise _http_error(exc) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise HTTPException(status_code=503, detail="可信身份端口未正确接线")
        return actor

    def invoke(call: Callable[..., Any], *args: Any) -> Any:
        try:
            return call(uow_factory, *args, runtime)
        except BusinessError as exc:
            raise _http_error(exc) from exc

    @router.post("")
    def create(
        body: CreateTaskRequest,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        return invoke(
            application.create_task,
            actor,
            body.type,
            body.credit_cost,
            body.request_data,
        )

    @router.put("/{task_id}")
    def update(
        task_id: str,
        body: UpdateTaskRequest,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        return invoke(application.update_task, actor, task_id, body.status, body.result)

    @router.put("/{task_id}/progress")
    def update_progress(
        task_id: str,
        body: UpdateTaskProgressRequest,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        return invoke(
            application.update_task_progress,
            actor,
            task_id,
            body.progress,
            body.progress_message,
        )

    @router.get("/{task_id}/external-status")
    async def external_status(
        task_id: str,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        try:
            return await application.get_external_task_status(
                uow_factory,
                actor,
                task_id,
                runtime,
            )
        except BusinessError as exc:
            raise _http_error(exc) from exc

    return router
