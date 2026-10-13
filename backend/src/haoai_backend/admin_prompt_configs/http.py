"""FastAPI adapter for the five administrator prompt-config routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_prompt_configs_actor
from .domain import AdminPromptConfigsActor
from .errors import AdminPromptConfigsUnavailable
from .persistence import SqlAlchemyAdminPromptConfigsUnitOfWork
from .ports import AdminPromptConfigsUnitOfWork, AdminPromptConfigsUnitOfWorkFactory
from .schemas import AdminPromptConfigBody, PromptConfigListPayload, PromptConfigPayload

SessionFactory = Callable[[], Session]
ADMIN_PROMPT_CONFIGS_UNAVAILABLE = "提示词配置服务尚未接线"


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_prompt_configs_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
) -> APIRouter:
    """Build an inert router; auth and business units are request-scoped."""

    router = APIRouter(prefix="/api/admin", tags=["admin-prompt-configs"])
    unit_of_work_factory: AdminPromptConfigsUnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_unit_of_work() -> AdminPromptConfigsUnitOfWork:
            return SqlAlchemyAdminPromptConfigsUnitOfWork(session_factory())

        unit_of_work_factory = make_unit_of_work

    def administrator(request: Request) -> AdminPromptConfigsActor:
        if authentication_service is None or unit_of_work_factory is None:
            raise _http_error(AdminPromptConfigsUnavailable())
        try:
            return authenticate_admin_prompt_configs_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers=error.headers,
            ) from error

    def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        factory = unit_of_work_factory
        if factory is None:
            raise _http_error(AdminPromptConfigsUnavailable())
        try:
            return call(factory, *args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/prompt-configs", response_model=None)
    def list_prompt_configs(
        actor: AdminPromptConfigsActor = Depends(administrator),
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
        user_id: str | None = Query(default=None),
    ) -> PromptConfigListPayload:
        return invoke(
            application.list_prompt_configs,
            actor,
            user_id=user_id,
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )

    @router.get("/prompt-configs/{prompt_id}", response_model=None)
    def get_prompt_config(
        prompt_id: str,
        actor: AdminPromptConfigsActor = Depends(administrator),
    ) -> PromptConfigPayload:
        return invoke(application.get_prompt_config, actor, prompt_id)

    @router.post("/prompt-configs", response_model=None)
    def create_prompt_config(
        actor: AdminPromptConfigsActor = Depends(administrator),
        data: AdminPromptConfigBody = ...,
    ) -> PromptConfigPayload:
        return invoke(application.create_prompt_config, actor, data)

    @router.put("/prompt-configs/{prompt_id}", response_model=None)
    def update_prompt_config(
        prompt_id: str,
        actor: AdminPromptConfigsActor = Depends(administrator),
        data: AdminPromptConfigBody = ...,
    ) -> PromptConfigPayload:
        return invoke(application.update_prompt_config, actor, prompt_id, data)

    @router.delete("/prompt-configs/{prompt_id}", response_model=None)
    def delete_prompt_config(
        prompt_id: str,
        actor: AdminPromptConfigsActor = Depends(administrator),
    ) -> dict[str, str]:
        return invoke(application.delete_prompt_config, actor, prompt_id)

    return router
