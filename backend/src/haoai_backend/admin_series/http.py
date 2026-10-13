"""FastAPI adapter for the four fixed legacy administrator series routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_series_actor
from .domain import AdminSeriesActor
from .errors import AdminSeriesUnavailable
from .persistence import SqlAlchemyAdminSeriesUnitOfWork
from .ports import UnitOfWorkFactory
from .schemas import AdminSeriesBody, AdminSeriesPayload

SessionFactory = Callable[[], Session]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_series_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
) -> APIRouter:
    """Build an inert router; sessions open only inside route worker calls."""

    router = APIRouter(prefix="/api/admin", tags=["admin-series"])
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:

        def make_uow() -> SqlAlchemyAdminSeriesUnitOfWork:
            return SqlAlchemyAdminSeriesUnitOfWork(session_factory())

        uow_factory = make_uow

    def administrator(request: Request) -> AdminSeriesActor:
        if authentication_service is None or uow_factory is None:
            raise _http_error(AdminSeriesUnavailable())
        try:
            return authenticate_admin_series_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except BusinessError as error:
            raise _http_error(error) from error

    async def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if uow_factory is None:
            raise _http_error(AdminSeriesUnavailable())
        try:
            return await run_in_threadpool(call, uow_factory, *args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/series", response_model=None)
    async def list_series(
        actor: AdminSeriesActor = Depends(administrator),
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
        user_id: str | None = Query(default=None),
        name: str | None = Query(default=None),
    ) -> AdminSeriesPayload:
        return await invoke(
            application.list_series,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
            user_id=user_id,
            name=name,
        )

    @router.get("/series/{series_id}", response_model=None)
    async def get_series(
        series_id: str,
        actor: AdminSeriesActor = Depends(administrator),
    ) -> AdminSeriesPayload:
        return await invoke(application.get_series, actor, series_id)

    @router.put("/series/{series_id}", response_model=None)
    async def update_series(
        series_id: str,
        actor: AdminSeriesActor = Depends(administrator),
        data: AdminSeriesBody = Body(...),
    ) -> AdminSeriesPayload:
        return await invoke(application.update_series, actor, series_id, data)

    @router.post("/series", response_model=None)
    async def create_series(
        actor: AdminSeriesActor = Depends(administrator),
        data: AdminSeriesBody = Body(...),
    ) -> AdminSeriesPayload:
        return await invoke(application.create_series, actor, data)

    return router
