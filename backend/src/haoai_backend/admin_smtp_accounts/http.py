"""FastAPI adapter for the six fixed SMTP-account administration routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_smtp_accounts_actor
from .crypto import LegacyFernet
from .daily_reset import SqlAlchemyDailyReset
from .domain import AdminSmtpAccountsActor
from .errors import AdminSmtpAccountsUnavailable
from .persistence import SqlAlchemyAdminSmtpAccountUnitOfWork
from .ports import Cipher, DailyReset, UnitOfWorkFactory

SessionFactory = Callable[[], Any]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_smtp_accounts_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
    cipher: Cipher | None = None,
    daily_reset: DailyReset | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin-smtp-accounts"])
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_uow() -> SqlAlchemyAdminSmtpAccountUnitOfWork:
            return SqlAlchemyAdminSmtpAccountUnitOfWork(session_factory())

        uow_factory = make_uow

    crypto = cipher or LegacyFernet(None)
    rollover = daily_reset or (
        SqlAlchemyDailyReset(session_factory) if session_factory is not None else None
    )

    def administrator(request: Request) -> AdminSmtpAccountsActor:
        if authentication_service is None or uow_factory is None:
            raise _http_error(AdminSmtpAccountsUnavailable())
        try:
            return authenticate_admin_smtp_accounts_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as error:
            raise _http_error(error) from error

    async def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if uow_factory is None:
            raise _http_error(AdminSmtpAccountsUnavailable())
        try:
            return await run_in_threadpool(
                call,
                uow_factory,
                *args,
                **kwargs,
            )
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/smtp-accounts", response_model=None)
    async def list_accounts(
        actor: AdminSmtpAccountsActor = Depends(administrator),
    ) -> dict[str, Any]:
        assert rollover is not None
        return await invoke(application.list_smtp_accounts, actor, crypto, rollover)

    @router.get("/smtp-accounts/{account_id}", response_model=None)
    async def get_account(
        account_id: str,
        actor: AdminSmtpAccountsActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(application.get_smtp_account, actor, account_id, crypto)

    @router.post("/smtp-accounts", response_model=None)
    async def create_account(
        data: dict[str, Any],
        actor: AdminSmtpAccountsActor = Depends(administrator),
    ) -> dict[str, str]:
        return await invoke(application.create_smtp_account, actor, data, crypto)

    @router.put("/smtp-accounts/{account_id}", response_model=None)
    async def update_account(
        account_id: str,
        data: dict[str, Any],
        actor: AdminSmtpAccountsActor = Depends(administrator),
    ) -> dict[str, str]:
        return await invoke(
            application.update_smtp_account,
            actor,
            account_id,
            data,
            crypto,
        )

    @router.delete("/smtp-accounts/{account_id}", response_model=None)
    async def delete_account(
        account_id: str,
        actor: AdminSmtpAccountsActor = Depends(administrator),
    ) -> dict[str, str]:
        return await invoke(application.delete_smtp_account, actor, account_id)

    @router.post("/smtp-accounts/{account_id}/reset-count", response_model=None)
    async def reset_count(
        account_id: str,
        actor: AdminSmtpAccountsActor = Depends(administrator),
    ) -> dict[str, str]:
        return await invoke(application.reset_smtp_count, actor, account_id)

    return router
