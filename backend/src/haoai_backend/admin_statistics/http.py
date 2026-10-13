"""FastAPI adapter for the fixed administrator statistics route."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_statistics_actor
from .balance import RequestsBalanceReader
from .domain import AdminStatisticsActor
from .errors import AdminStatisticsUnavailable
from .persistence import SqlAlchemyAdminStatisticsUnitOfWork
from .ports import BalanceReader, Clock, UnitOfWorkFactory

SessionFactory = Callable[[], Any]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_statistics_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
    balance_reader: BalanceReader | None = None,
    clock: Clock = datetime.utcnow,
) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin-statistics"])
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_uow() -> SqlAlchemyAdminStatisticsUnitOfWork:
            return SqlAlchemyAdminStatisticsUnitOfWork(session_factory())

        uow_factory = make_uow
    reader = balance_reader or RequestsBalanceReader()

    def administrator(request: Request) -> AdminStatisticsActor:
        if authentication_service is None or uow_factory is None:
            raise _http_error(AdminStatisticsUnavailable())
        try:
            return authenticate_admin_statistics_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as error:
            raise _http_error(error) from error

    @router.get("/stats", response_model=None)
    async def get_stats(
        actor: AdminStatisticsActor = Depends(administrator),
    ) -> dict[str, Any]:
        if uow_factory is None:
            raise _http_error(AdminStatisticsUnavailable())
        try:
            return await run_in_threadpool(
                application.get_admin_statistics,
                uow_factory,
                actor,
                reader,
                clock,
            )
        except BusinessError as error:
            raise _http_error(error) from error

    return router
