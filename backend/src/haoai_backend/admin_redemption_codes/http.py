"""FastAPI adapter for the four legacy administrator redemption-code routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_redemption_codes_actor
from .domain import AdminRedemptionCodesActor
from .errors import AdminRedemptionCodesUnavailable
from .persistence import SqlAlchemyAdminRedemptionCodesUnitOfWork
from .ports import CodeGenerator, UnitOfWorkFactory
from .schemas import AdminRedemptionCodesBody, AdminRedemptionCodePayload

SessionFactory = Callable[[], Any]
ADMIN_REDEMPTION_CODES_UNAVAILABLE = "兑换码管理服务尚未接线"


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_redemption_codes_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
    code_generator: CodeGenerator | None = None,
) -> APIRouter:
    """Build an inert router with request-scoped auth and business sessions."""

    router = APIRouter(prefix="/api/admin", tags=["admin-redemption-codes"])
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:

        def make_uow() -> SqlAlchemyAdminRedemptionCodesUnitOfWork:
            return SqlAlchemyAdminRedemptionCodesUnitOfWork(session_factory())

        uow_factory = make_uow

    def administrator(request: Request) -> AdminRedemptionCodesActor:
        if authentication_service is None or uow_factory is None:
            raise _http_error(AdminRedemptionCodesUnavailable())
        try:
            return authenticate_admin_redemption_codes_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except BusinessError as error:
            raise _http_error(error) from error

    async def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if uow_factory is None:
            raise _http_error(AdminRedemptionCodesUnavailable())
        try:
            return await run_in_threadpool(call, uow_factory, *args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/redemption-codes", response_model=None)
    async def list_redemption_codes(
        actor: AdminRedemptionCodesActor = Depends(administrator),
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
        code: str | None = Query(default=None),
        type: str | None = Query(default=None),
        is_used: str | None = Query(default=None),
        value: int | None = Query(default=None),
    ) -> AdminRedemptionCodePayload:
        return await invoke(
            application.list_redemption_codes,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
            code=code,
            code_type=type,
            is_used=is_used,
            value=value,
        )

    @router.get("/redemption-codes/{code_id}", response_model=None)
    async def get_redemption_code(
        code_id: str,
        actor: AdminRedemptionCodesActor = Depends(administrator),
    ) -> AdminRedemptionCodePayload:
        return await invoke(application.get_redemption_code, actor, code_id)

    @router.post("/redemption-codes", response_model=None)
    async def create_redemption_codes(
        actor: AdminRedemptionCodesActor = Depends(administrator),
        data: AdminRedemptionCodesBody = Body(...),
    ) -> AdminRedemptionCodePayload:
        return await invoke(
            application.create_redemption_codes,
            actor,
            data,
            code_generator=code_generator,
        )

    @router.delete("/redemption-codes/{code_id}", response_model=None)
    async def delete_redemption_code(
        code_id: str,
        actor: AdminRedemptionCodesActor = Depends(administrator),
    ) -> dict[str, str]:
        return await invoke(application.delete_redemption_code, actor, code_id)

    return router
