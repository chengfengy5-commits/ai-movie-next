"""FastAPI adapter for the six legacy provider-quota routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_provider_quota_actor
from .crypto import LegacyFernetCipher
from .domain import ProviderQuotaActor
from .errors import ProviderQuotaUnavailable
from .persistence import SqlAlchemyProviderQuotaUnitOfWork
from .ports import Cipher, ProviderQuotaTransport, UnitOfWorkFactory
from .query import ProviderQuotaQueryService

SessionFactory = Callable[[], Any]


class AccountRequest(BaseModel):
    name: str = ""
    provider_type: str = "new_api"
    base_url: str = ""
    username: str = ""
    password: str = ""
    is_active: bool = True


class ProbeRequest(BaseModel):
    base_url: str = ""
    username: str = ""
    password: str = ""
    provider_type: str = "new_api"


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def _request_payload(value: BaseModel) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


def build_provider_quota_router(
    *,
    authentication_service: Any | None,
    session_factory: SessionFactory | None,
    cipher: Cipher | None = None,
    transport: ProviderQuotaTransport | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/provider-quota", tags=["供应商额度监控"])
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_uow() -> SqlAlchemyProviderQuotaUnitOfWork:
            return SqlAlchemyProviderQuotaUnitOfWork(session_factory())

        uow_factory = make_uow

    crypto = cipher or LegacyFernetCipher(None)
    query_service = ProviderQuotaQueryService(transport)

    def administrator(request: Request) -> ProviderQuotaActor:
        if authentication_service is None or uow_factory is None:
            raise _http_error(ProviderQuotaUnavailable())
        try:
            return authenticate_provider_quota_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as error:
            raise _http_error(error) from error

    async def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if uow_factory is None:
            raise _http_error(ProviderQuotaUnavailable())
        try:
            return await run_in_threadpool(call, uow_factory, *args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/accounts", response_model=None)
    async def list_accounts(
        actor: ProviderQuotaActor = Depends(administrator),
    ) -> list[dict[str, Any]]:
        return await invoke(application.list_accounts, actor, crypto)

    @router.post("/accounts", response_model=None)
    async def create_account(
        req: AccountRequest,
        actor: ProviderQuotaActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(
            application.create_account,
            actor,
            _request_payload(req),
            crypto,
        )

    @router.put("/accounts/{account_id}", response_model=None)
    async def update_account(
        account_id: str,
        req: AccountRequest,
        actor: ProviderQuotaActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(
            application.update_account,
            actor,
            account_id,
            _request_payload(req),
            crypto,
        )

    @router.delete("/accounts/{account_id}", response_model=None)
    async def delete_account(
        account_id: str,
        actor: ProviderQuotaActor = Depends(administrator),
    ) -> dict[str, bool]:
        return await invoke(
            application.delete_account,
            actor,
            account_id,
            invalidate_sessions=query_service.invalidate,
        )

    @router.get("/accounts/{account_id}/balance", response_model=None)
    async def account_balance(
        account_id: str,
        actor: ProviderQuotaActor = Depends(administrator),
    ) -> dict[str, Any]:
        prepared = await invoke(
            application.load_balance_account,
            actor,
            account_id,
            crypto,
        )
        return await query_service.account_balance(prepared)

    @router.post("/probe", response_model=None)
    async def probe(
        req: ProbeRequest,
        actor: ProviderQuotaActor = Depends(administrator),
    ) -> dict[str, Any]:
        payload = _request_payload(req)
        try:
            base_url, _provider_type = application.validate_probe_payload(payload)
        except BusinessError as error:
            raise _http_error(error) from error
        return await query_service.probe(
            base_url,
            payload["username"],
            payload["password"],
            payload["provider_type"],
        )

    return router
