"""FastAPI adapter for the five administrator system-prompt routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_system_prompt_actor
from .domain import AdminSystemPromptActor
from .persistence import SqlAlchemyAdminSystemPromptUnitOfWork
from .ports import UnitOfWorkFactory
from .schemas import AdminSystemPromptBody, AdminSystemPromptPayload

SessionFactory = Callable[[], Any]
ADMIN_SYSTEM_PROMPTS_UNAVAILABLE = "系统提示词服务尚未接线"


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_system_prompts_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
) -> APIRouter:
    """Build an inert router; authentication and business sessions are request-scoped."""

    router = APIRouter(prefix="/api/admin", tags=["admin-system-prompts"])
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_uow() -> SqlAlchemyAdminSystemPromptUnitOfWork:
            return SqlAlchemyAdminSystemPromptUnitOfWork(session_factory())

        uow_factory = make_uow

    def administrator(request: Request) -> AdminSystemPromptActor:
        if authentication_service is None or uow_factory is None:
            raise HTTPException(
                status_code=503,
                detail=ADMIN_SYSTEM_PROMPTS_UNAVAILABLE,
            )
        try:
            return authenticate_admin_system_prompt_actor(
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

    @router.get("/system-prompts", response_model=None)
    async def list_system_prompts(
        category: str | None = Query(default=None),
        is_active: bool | None = Query(default=None),
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        actor: AdminSystemPromptActor = Depends(administrator),
    ) -> AdminSystemPromptPayload:
        return await invoke(
            application.list_system_prompts,
            actor,
            category=category,
            is_active=is_active,
            skip=skip,
            limit=limit,
        )

    @router.get("/system-prompts/{prompt_id}", response_model=None)
    async def get_system_prompt(
        prompt_id: str,
        actor: AdminSystemPromptActor = Depends(administrator),
    ) -> AdminSystemPromptPayload:
        return await invoke(application.get_system_prompt, actor, prompt_id)

    @router.post("/system-prompts", response_model=None)
    async def create_system_prompt(
        data: AdminSystemPromptBody,
        actor: AdminSystemPromptActor = Depends(administrator),
    ) -> AdminSystemPromptPayload:
        return await invoke(application.create_system_prompt, actor, data)

    @router.put("/system-prompts/{prompt_id}", response_model=None)
    async def update_system_prompt(
        prompt_id: str,
        data: AdminSystemPromptBody,
        actor: AdminSystemPromptActor = Depends(administrator),
    ) -> AdminSystemPromptPayload:
        return await invoke(application.update_system_prompt, actor, prompt_id, data)

    @router.delete("/system-prompts/{prompt_id}", response_model=None)
    async def delete_system_prompt(
        prompt_id: str,
        actor: AdminSystemPromptActor = Depends(administrator),
    ) -> dict[str, str]:
        return await invoke(application.delete_system_prompt, actor, prompt_id)

    return router
