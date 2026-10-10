from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .domain import AdminTaskFilters
from .ports import AdminTaskUnitOfWorkFactory

AdminActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_task_router(
    *,
    uow_factory: AdminTaskUnitOfWorkFactory | None,
    resolve_admin_actor: AdminActorResolver | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin-tasks"])

    async def trusted_admin(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_admin_actor is None:
            raise HTTPException(status_code=503, detail="管理员任务服务尚未接线")
        try:
            resolved = await run_in_threadpool(resolve_admin_actor, request)
            actor = await resolved if inspect.isawaitable(resolved) else resolved
        except BusinessError as exc:
            raise _http_error(exc) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise HTTPException(status_code=503, detail="管理员身份端口未正确接线")
        return actor

    def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return call(uow_factory, *args, **kwargs)
        except BusinessError as exc:
            raise _http_error(exc) from exc

    @router.get("/tasks")
    def admin_list_tasks(
        skip: int = 0,
        limit: int = 10,
        sort_field: str = "created_at",
        sort_order: str = "desc",
        user_id: str | None = None,
        task_type: str | None = Query(default=None, alias="type"),
        status: str | None = None,
        model_name: str | None = None,
        _actor: TrustedActor = Depends(trusted_admin),
    ) -> dict[str, Any]:
        page = invoke(
            application.list_tasks,
            filters=AdminTaskFilters(
                user_id=user_id,
                task_type=task_type,
                status=status,
                model_name=model_name,
            ),
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
        )
        return {
            "data": [
                {
                    "id": item.id,
                    "user_id": item.user_id,
                    "type": item.type,
                    "status": item.status,
                    "credit_cost": item.credit_cost,
                    "message_id": item.message_id,
                    "model_name": item.model_name,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in page.data
            ],
            "total": page.total,
        }

    @router.get("/tasks/{task_id}")
    def admin_get_task(
        task_id: str,
        _actor: TrustedActor = Depends(trusted_admin),
    ) -> dict[str, Any]:
        task = invoke(application.get_task, task_id)
        return {
            "id": task.id,
            "user_id": task.user_id,
            "type": task.type,
            "status": task.status,
            "message_id": task.message_id,
            "request_data": task.request_data,
            "result": task.result,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    return router
