"""FastAPI adapter for the five legacy administrator system-model routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_system_model_actor
from .crypto import LegacyFernet
from .domain import AdminSystemModelActor
from .errors import AdminSystemModelsUnavailable
from .persistence import SqlAlchemyAdminSystemModelUnitOfWork
from .ports import Cipher, UnitOfWorkFactory
from .schemas import SystemModelCreateRequest, SystemModelResponse, SystemModelUpdateRequest

SessionFactory = Callable[[], Session]
ADMIN_SYSTEM_MODELS_UNAVAILABLE = "系统模型服务尚未接线"


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_system_models_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
    cipher: Cipher | None = None,
) -> APIRouter:
    """Build an inert router; request sessions are created in worker threads."""

    router = APIRouter(prefix="/api/admin", tags=["admin-system-models"])
    effective_cipher = cipher if cipher is not None else LegacyFernet(None)
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_uow() -> SqlAlchemyAdminSystemModelUnitOfWork:
            return SqlAlchemyAdminSystemModelUnitOfWork(session_factory())

        uow_factory = make_uow

    def administrator(request: Request) -> AdminSystemModelActor:
        if authentication_service is None or uow_factory is None:
            raise HTTPException(
                status_code=503,
                detail=ADMIN_SYSTEM_MODELS_UNAVAILABLE,
            )
        try:
            return authenticate_admin_system_model_actor(
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

    @router.get("/system-models", response_model=None)
    async def list_system_models(
        category: str | None = Query(default=None),
        provider: str | None = Query(default=None),
        is_active: bool | None = Query(default=None),
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        actor: AdminSystemModelActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(
            application.list_system_models,
            actor,
            category=category,
            provider=provider,
            is_active=is_active,
            skip=skip,
            limit=limit,
        )

    @router.get("/system-models/{model_id}", response_model=None)
    async def get_system_model(
        model_id: str,
        actor: AdminSystemModelActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(
            application.get_system_model,
            actor,
            model_id,
            effective_cipher,
        )

    @router.post("/system-models", response_model=SystemModelResponse)
    async def create_system_model(
        data: SystemModelCreateRequest,
        actor: AdminSystemModelActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(
            application.create_system_model,
            actor,
            data.model_dump(),
            effective_cipher,
        )

    @router.put("/system-models/{model_id}", response_model=SystemModelResponse)
    async def update_system_model(
        model_id: str,
        data: SystemModelUpdateRequest,
        actor: AdminSystemModelActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(
            application.update_system_model,
            actor,
            model_id,
            data.model_dump(exclude_unset=True),
            effective_cipher,
        )

    @router.delete("/system-models/{model_id}", response_model=None)
    async def delete_system_model(
        model_id: str,
        actor: AdminSystemModelActor = Depends(administrator),
    ) -> dict[str, str]:
        return await invoke(
            application.delete_system_model,
            actor,
            model_id,
        )

    return router
