"""FastAPI adapter for the eight prompt configuration endpoints."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.shared.errors import BusinessError

from . import application
from .domain import PromptActor, PromptCreateCommand
from .errors import PromptsUnavailable
from .ports import PromptsUnitOfWorkFactory
from .schemas import PromptConfigCreate, PromptConfigResponse, PromptConfigUpdate, PromptReorderRequest

PromptActorResolver = Callable[[Request], PromptActor | Awaitable[PromptActor]]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def _invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return call(*args, **kwargs)
    except BusinessError as exc:
        raise _http_error(exc) from exc


async def _invoke_in_threadpool(
    call: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    return await run_in_threadpool(_invoke, call, *args, **kwargs)


def build_prompts_router(
    *,
    uow_factory: PromptsUnitOfWorkFactory | None,
    resolve_active_actor: PromptActorResolver | None,
    resolve_administrator: PromptActorResolver | None,
) -> APIRouter:
    """Build prompt routes without importing legacy state or opening a session."""

    router = APIRouter(prefix="/api/prompts", tags=["prompts"])

    async def resolve_actor(
        request: Request,
        resolver: PromptActorResolver | None,
    ) -> PromptActor:
        if uow_factory is None or resolver is None:
            raise _http_error(PromptsUnavailable())
        try:
            is_async_resolver = inspect.iscoroutinefunction(resolver) or inspect.iscoroutinefunction(
                getattr(resolver, "__call__", None)
            )
            candidate = (
                resolver(request)
                if is_async_resolver
                else await run_in_threadpool(resolver, request)
            )
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise _http_error(exc) from exc
        if not isinstance(actor, PromptActor) or not actor.user_id:
            raise _http_error(PromptsUnavailable())
        return actor

    async def resolve_active_actor_dependency(request: Request) -> PromptActor:
        return await resolve_actor(request, resolve_active_actor)

    async def resolve_administrator_dependency(request: Request) -> PromptActor:
        return await resolve_actor(request, resolve_administrator)

    @router.get("", response_model=list[dict[str, Any]])
    async def list_prompt_configs(
        category: str | None = Query(default=None),
        actor: PromptActor = Depends(resolve_active_actor_dependency),
    ) -> list[dict[str, Any]]:
        return await _invoke_in_threadpool(
            application.list_prompt_configs, uow_factory, actor, category
        )

    @router.get("/{prompt_id}", response_model=PromptConfigResponse)
    async def get_prompt_config(
        prompt_id: str,
        actor: PromptActor = Depends(resolve_active_actor_dependency),
    ) -> dict[str, object]:
        return await _invoke_in_threadpool(
            application.get_prompt_config, uow_factory, actor, prompt_id
        )

    @router.post("", response_model=PromptConfigResponse)
    async def create_prompt_config(
        prompt: PromptConfigCreate,
        actor: PromptActor = Depends(resolve_active_actor_dependency),
    ) -> dict[str, object]:
        command = PromptCreateCommand(
            category=prompt.category,
            name=prompt.name,
            system_prompt=prompt.system_prompt,
            user_prompt=prompt.user_prompt,
            is_system=prompt.is_system,
        )
        return await _invoke_in_threadpool(
            application.create_prompt_config, uow_factory, actor, command
        )

    @router.put("/{prompt_id}", response_model=PromptConfigResponse)
    async def update_prompt_config(
        prompt_id: str,
        prompt: PromptConfigUpdate,
        actor: PromptActor = Depends(resolve_active_actor_dependency),
    ) -> dict[str, object]:
        return await _invoke_in_threadpool(
            application.update_prompt_config,
            uow_factory,
            actor,
            prompt_id,
            prompt.model_dump(exclude_unset=True),
        )

    @router.delete("/{prompt_id}")
    async def delete_prompt_config(
        prompt_id: str,
        actor: PromptActor = Depends(resolve_active_actor_dependency),
    ) -> dict[str, str]:
        return await _invoke_in_threadpool(
            application.delete_prompt_config, uow_factory, actor, prompt_id
        )

    @router.post("/{prompt_id}/promote-to-system")
    async def promote_prompt_to_system(
        prompt_id: str,
        actor: PromptActor = Depends(resolve_administrator_dependency),
    ) -> dict[str, object]:
        return await _invoke_in_threadpool(
            application.promote_prompt_to_system, uow_factory, actor, prompt_id
        )

    @router.post("/{prompt_id}/demote-to-personal")
    async def demote_system_prompt_to_personal(
        prompt_id: str,
        actor: PromptActor = Depends(resolve_administrator_dependency),
    ) -> dict[str, object]:
        return await _invoke_in_threadpool(
            application.demote_system_prompt_to_personal,
            uow_factory,
            actor,
            prompt_id,
        )

    @router.post("/reorder")
    async def reorder_prompts(
        reorder: PromptReorderRequest,
        actor: PromptActor = Depends(resolve_active_actor_dependency),
    ) -> dict[str, str]:
        return await _invoke_in_threadpool(
            application.reorder_prompt_configs,
            uow_factory,
            actor,
            reorder.category,
            reorder.prompt_ids,
        )

    return router
