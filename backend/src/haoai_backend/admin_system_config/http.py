"""FastAPI adapter for the three legacy administrator system-config routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_system_config_actor
from .domain import AdminSystemConfigActor
from .persistence import admin_system_config_uow_factory
from .ports import UnitOfWorkFactory

SessionFactory = Callable[[], Session]
ADMIN_SYSTEM_CONFIG_UNAVAILABLE = "系统配置服务尚未接线"


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_system_config_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
) -> APIRouter:
    """Build an inert router; request business sessions live in worker threads."""

    router = APIRouter(prefix="/api/admin", tags=["admin-system-config"])
    uow_factory: UnitOfWorkFactory | None = (
        admin_system_config_uow_factory(session_factory)
        if session_factory is not None
        else None
    )

    def administrator(request: Request) -> AdminSystemConfigActor:
        if authentication_service is None or uow_factory is None:
            raise HTTPException(
                status_code=503,
                detail=ADMIN_SYSTEM_CONFIG_UNAVAILABLE,
            )
        try:
            return authenticate_admin_system_config_actor(
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

    @router.get("/system-config", response_model=None)
    async def list_system_config(
        actor: AdminSystemConfigActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(application.list_system_config, actor)

    @router.get("/system-config/{config_id}", response_model=None)
    async def get_system_config(
        config_id: str,
        actor: AdminSystemConfigActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(application.get_system_config, actor, config_id)

    @router.put("/system-config/{config_id}", response_model=None)
    async def update_system_config(
        config_id: str,
        data: dict[str, Any],
        actor: AdminSystemConfigActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(
            application.update_system_config,
            actor,
            config_id,
            data,
        )

    return router
