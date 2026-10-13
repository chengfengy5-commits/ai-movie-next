"""FastAPI adapter for the five legacy model-configuration routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_model_config_actor
from .domain import AdminModelConfigActor
from .persistence import SqlAlchemyAdminModelConfigUnitOfWork
from .ports import UnitOfWorkFactory
from .schemas import AdminModelConfigBody, AdminModelConfigPayload

SessionFactory = Callable[[], Any]
ADMIN_MODEL_CONFIGS_UNAVAILABLE = "模型配置服务尚未接线"


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_model_configs_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
) -> APIRouter:
    """Build an inert router whose auth and business units are request-scoped."""

    router = APIRouter(prefix="/api/admin", tags=["admin-model-configs"])
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_uow() -> SqlAlchemyAdminModelConfigUnitOfWork:
            return SqlAlchemyAdminModelConfigUnitOfWork(session_factory())

        uow_factory = make_uow

    def administrator(request: Request) -> AdminModelConfigActor:
        if authentication_service is None or uow_factory is None:
            raise HTTPException(
                status_code=503,
                detail=ADMIN_MODEL_CONFIGS_UNAVAILABLE,
            )
        try:
            return authenticate_admin_model_config_actor(
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
            return await run_in_threadpool(call, uow_factory, *args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/model-configs", response_model=None)
    async def list_model_configs(
        actor: AdminModelConfigActor = Depends(administrator),
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
        user_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return await invoke(
            application.list_model_configs,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
            user_id=user_id,
        )

    @router.get("/model-configs/{model_id}", response_model=None)
    async def get_model_config(
        model_id: str,
        actor: AdminModelConfigActor = Depends(administrator),
    ) -> AdminModelConfigPayload:
        return await invoke(application.get_model_config, actor, model_id)

    @router.post("/model-configs", response_model=None)
    async def create_model_config(
        actor: AdminModelConfigActor = Depends(administrator),
        data: AdminModelConfigBody = Body(...),
    ) -> AdminModelConfigPayload:
        return await invoke(application.create_model_config, actor, data)

    @router.put("/model-configs/{model_id}", response_model=None)
    async def update_model_config(
        model_id: str,
        actor: AdminModelConfigActor = Depends(administrator),
        data: AdminModelConfigBody = Body(...),
    ) -> AdminModelConfigPayload:
        return await invoke(application.update_model_config, actor, model_id, data)

    @router.delete("/model-configs/{model_id}", response_model=None)
    async def delete_model_config(
        model_id: str,
        actor: AdminModelConfigActor = Depends(administrator),
    ) -> dict[str, str]:
        return await invoke(application.delete_model_config, actor, model_id)

    return router
