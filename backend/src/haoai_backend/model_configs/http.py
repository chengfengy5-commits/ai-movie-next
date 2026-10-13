"""FastAPI adapter for the nine legacy model-configuration endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import create_model_config_identity_resolvers
from .connection import ConnectionTester, UrllibConnectionTransport
from .domain import ConnectionConfig, ModelConfigActor, ModelConfigCreateCommand
from .errors import ModelConfigsUnavailable
from .persistence import model_configs_unit_of_work_factory
from .ports import ModelConfigsUnitOfWorkFactory
from .schemas import (
    ModelConfigCreate,
    ModelConfigResponse,
    ModelConfigUpdate,
    ModelReorderRequest,
    TestConnectionRequest,
)

SessionFactory = Callable[[], Any]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


async def _invoke(
    call: Callable[..., Any],
    factory: ModelConfigsUnitOfWorkFactory | None,
    *args: Any,
    **kwargs: Any,
) -> Any:
    try:
        return await run_in_threadpool(call, factory, *args, **kwargs)
    except BusinessError as error:
        raise _http_error(error) from error


def build_model_configs_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
    connection_tester: ConnectionTester | None = None,
) -> APIRouter:
    """Build an inert router without authenticating or opening a business session."""

    router = APIRouter(prefix="/api/models", tags=["models"])
    unit_of_work_factory: ModelConfigsUnitOfWorkFactory | None = None
    if session_factory is not None:
        unit_of_work_factory = model_configs_unit_of_work_factory(session_factory)

    identity_resolvers = (
        create_model_config_identity_resolvers(authentication_service)
        if authentication_service is not None
        else None
    )
    tester = (
        ConnectionTester(UrllibConnectionTransport())
        if connection_tester is None
        else connection_tester
    )

    async def resolve_actor(request: Request, *, administrator: bool) -> ModelConfigActor:
        if unit_of_work_factory is None or identity_resolvers is None:
            raise _http_error(ModelConfigsUnavailable())
        resolver = (
            identity_resolvers.administrator
            if administrator
            else identity_resolvers.active
        )
        try:
            return await run_in_threadpool(
                resolver,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as error:
            raise _http_error(error) from error

    async def resolve_active_actor(request: Request) -> ModelConfigActor:
        return await resolve_actor(request, administrator=False)

    async def resolve_administrator(request: Request) -> ModelConfigActor:
        return await resolve_actor(request, administrator=True)

    @router.get("", response_model=list[ModelConfigResponse])
    async def list_model_configs(
        category: str | None = Query(default=None),
        actor: ModelConfigActor = Depends(resolve_active_actor),
    ) -> list[dict[str, object]]:
        return await _invoke(
            application.list_model_configs,
            unit_of_work_factory,
            actor,
            category,
        )

    @router.get("/{model_id}", response_model=ModelConfigResponse)
    async def get_model_config(
        model_id: str,
        actor: ModelConfigActor = Depends(resolve_active_actor),
    ) -> dict[str, object]:
        return await _invoke(
            application.get_model_config,
            unit_of_work_factory,
            actor,
            model_id,
        )

    @router.post("", response_model=ModelConfigResponse)
    async def create_model_config(
        model: ModelConfigCreate,
        actor: ModelConfigActor = Depends(resolve_active_actor),
    ) -> dict[str, object]:
        command = ModelConfigCreateCommand(
            category=model.category,
            name=model.name,
            provider=model.provider,
            api_key=model.api_key,
            model=model.model,
            base_url=model.base_url,
            sort_order=model.sort_order,
            params=model.params,
            is_system=model.is_system,
        )
        return await _invoke(
            application.create_model_config,
            unit_of_work_factory,
            actor,
            command,
        )

    @router.put("/{model_id}", response_model=ModelConfigResponse)
    async def update_model_config(
        model_id: str,
        model: ModelConfigUpdate,
        actor: ModelConfigActor = Depends(resolve_active_actor),
    ) -> dict[str, object]:
        return await _invoke(
            application.update_model_config,
            unit_of_work_factory,
            actor,
            model_id,
            model.model_dump(exclude_unset=True),
        )

    @router.delete("/{model_id}")
    async def delete_model_config(
        model_id: str,
        actor: ModelConfigActor = Depends(resolve_active_actor),
    ) -> dict[str, str]:
        return await _invoke(
            application.delete_model_config,
            unit_of_work_factory,
            actor,
            model_id,
        )

    @router.post("/{model_id}/promote-to-system")
    async def promote_model_to_system(
        model_id: str,
        actor: ModelConfigActor = Depends(resolve_administrator),
    ) -> dict[str, object]:
        return await _invoke(
            application.promote_model_to_system,
            unit_of_work_factory,
            actor,
            model_id,
        )

    @router.post("/{model_id}/demote-to-personal")
    async def demote_system_model_to_personal(
        model_id: str,
        actor: ModelConfigActor = Depends(resolve_administrator),
    ) -> dict[str, object]:
        return await _invoke(
            application.demote_system_model_to_personal,
            unit_of_work_factory,
            actor,
            model_id,
        )

    @router.post("/reorder")
    async def reorder_model_configs(
        reorder: ModelReorderRequest,
        actor: ModelConfigActor = Depends(resolve_active_actor),
    ) -> dict[str, str]:
        return await _invoke(
            application.reorder_model_configs,
            unit_of_work_factory,
            actor,
            reorder.category,
            reorder.model_ids,
        )

    @router.post("/test-connection")
    async def test_model_connection(
        config: TestConnectionRequest,
    ) -> dict[str, object]:
        command = ConnectionConfig(
            provider=config.provider,
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            category=config.category,
        )
        return await run_in_threadpool(tester.test, command)

    return router
