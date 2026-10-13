"""FastAPI routes for legacy administrator credit queries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_credit_queries_actor
from .domain import AdminCreditQueriesActor
from .errors import AdminCreditQueriesUnavailable
from .persistence import SqlAlchemyAdminCreditQueriesUnitOfWork
from .ports import UnitOfWorkFactory
from .schemas import (
    AdminCreditUpdateBody,
    CreditLogListPayload,
    UserCreditListPayload,
    UserCreditPayload,
)

SessionFactory = Callable[[], Any]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_credit_queries_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
) -> APIRouter:
    """Build an inert router; create each business unit in its worker thread."""

    router = APIRouter(prefix="/api/admin", tags=["admin-credit-queries"])
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_uow() -> SqlAlchemyAdminCreditQueriesUnitOfWork:
            return SqlAlchemyAdminCreditQueriesUnitOfWork(session_factory())

        uow_factory = make_uow

    def administrator(request: Request) -> AdminCreditQueriesActor:
        if authentication_service is None or uow_factory is None:
            raise _http_error(AdminCreditQueriesUnavailable())
        try:
            return authenticate_admin_credit_queries_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except BusinessError as error:
            raise _http_error(error) from error

    async def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if uow_factory is None:
            raise _http_error(AdminCreditQueriesUnavailable())
        try:
            return await run_in_threadpool(call, uow_factory, *args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/user-credits", response_model=None)
    async def list_user_credits(
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: str = Query(default="updated_at"),
        sort_order: str = Query(default="desc"),
        actor: AdminCreditQueriesActor = Depends(administrator),
    ) -> UserCreditListPayload:
        return await invoke(
            application.list_user_credits,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
        )

    @router.get("/user-credits/{credit_id}", response_model=None)
    async def get_user_credit(
        credit_id: str,
        actor: AdminCreditQueriesActor = Depends(administrator),
    ) -> UserCreditPayload:
        return await invoke(application.get_user_credit, actor, credit_id)

    @router.put("/user-credits/{credit_id}", response_model=None)
    async def update_user_credit(
        credit_id: str,
        data: AdminCreditUpdateBody,
        actor: AdminCreditQueriesActor = Depends(administrator),
    ) -> UserCreditPayload:
        return await invoke(
            application.update_user_credit,
            actor,
            credit_id,
            data,
        )

    @router.get("/credit-logs", response_model=None)
    async def list_credit_logs(
        skip: int = Query(default=0),
        limit: int = Query(default=50),
        sort_field: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
        user_id: str | None = Query(default=None),
        actor: AdminCreditQueriesActor = Depends(administrator),
    ) -> CreditLogListPayload:
        return await invoke(
            application.list_credit_logs,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
            user_id=user_id,
        )

    return router
