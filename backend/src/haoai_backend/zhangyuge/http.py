"""FastAPI adapter for the three legacy Zhangyuge administrator routes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_zhangyuge_admin_actor
from .crypto import LegacyFernet
from .domain import ZhangyugeAdminActor
from .errors import ZhangyugeUnavailable
from .persistence import SqlAlchemyZhangyugeUnitOfWork
from .ports import BalanceTransport, Cipher, UnitOfWorkFactory

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
ZHANGYUGE_UNAVAILABLE = "章鱼哥服务尚未接线"


class SavePatRequest(BaseModel):
    pat: str = ""


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_zhangyuge_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
    balance_transport: BalanceTransport | None = None,
    cipher: Cipher | None = None,
) -> APIRouter:
    """Build an inert router; request sessions and network calls are lazy."""

    router = APIRouter(prefix="/api/zhangyuge", tags=["章鱼哥AI"])
    effective_cipher = cipher if cipher is not None else LegacyFernet(None)
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_uow() -> SqlAlchemyZhangyugeUnitOfWork:
            return SqlAlchemyZhangyugeUnitOfWork(session_factory())

        uow_factory = make_uow

    def administrator(request: Request) -> ZhangyugeAdminActor:
        if authentication_service is None or uow_factory is None:
            raise HTTPException(status_code=503, detail=ZHANGYUGE_UNAVAILABLE)
        try:
            return authenticate_zhangyuge_admin_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers=error.headers,
            ) from error

    async def invoke(
        call: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        try:
            return await run_in_threadpool(call, uow_factory, *args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/balance", response_model=None)
    async def zhangyuge_balance(
        actor: ZhangyugeAdminActor = Depends(administrator),
    ) -> dict[str, Any]:
        credentials = await invoke(
            application.load_balance_credentials,
            actor,
            effective_cipher,
        )
        if balance_transport is None:
            raise _http_error(ZhangyugeUnavailable())
        try:
            return await application.get_balance(credentials, balance_transport)
        except Exception as error:
            logger.error("[章鱼哥] 查询余额失败: %s", error, exc_info=True)
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/pat", response_model=None)
    async def zhangyuge_get_pat(
        actor: ZhangyugeAdminActor = Depends(administrator),
    ) -> dict[str, bool]:
        return await invoke(
            application.get_pat_configured,
            actor,
            effective_cipher,
        )

    @router.post("/pat", response_model=None)
    async def zhangyuge_save_pat(
        req: SavePatRequest,
        actor: ZhangyugeAdminActor = Depends(administrator),
    ) -> dict[str, bool]:
        return await invoke(
            application.save_pat,
            actor,
            req.pat or "",
            effective_cipher,
        )

    return router
