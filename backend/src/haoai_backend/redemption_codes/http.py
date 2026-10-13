"""FastAPI routes for the two legacy redemption-code endpoints."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import (
    authenticate_redemption_actor,
    authenticate_redemption_administrator,
)
from .code_generator import generate_code_string
from .domain import (
    GenerateRedemptionCodesCommand,
    RedemptionCodesActor,
    RedeemRedemptionCodeCommand,
)
from .errors import RedemptionCodesUnavailable
from .persistence import redemption_codes_unit_of_work_factory
from .ports import Clock, CodeGenerator, UnitOfWorkFactory
from .schemas import (
    GenerateRequest,
    GenerateResponse,
    RedeemRequest,
    RedeemResponse,
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
    factory: UnitOfWorkFactory | None,
    *args: Any,
    **kwargs: Any,
) -> Any:
    if factory is None:
        raise _http_error(RedemptionCodesUnavailable())
    try:
        return await run_in_threadpool(call, factory, *args, **kwargs)
    except BusinessError as error:
        raise _http_error(error) from error


def build_redemption_codes_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
    code_generator: CodeGenerator | None = None,
    clock: Clock | None = None,
) -> APIRouter:
    """Build an inert router with auth and business work on worker threads."""

    router = APIRouter(prefix="/api/redemption-codes", tags=["redemption-codes"])
    unit_of_work_factory = (
        redemption_codes_unit_of_work_factory(session_factory)
        if session_factory is not None
        else None
    )
    generator = generate_code_string if code_generator is None else code_generator
    business_clock = datetime.utcnow if clock is None else clock

    async def resolve_actor(
        request: Request,
        *,
        administrator: bool,
    ) -> RedemptionCodesActor:
        if authentication_service is None or unit_of_work_factory is None:
            raise _http_error(RedemptionCodesUnavailable())
        resolver = (
            authenticate_redemption_administrator
            if administrator
            else authenticate_redemption_actor
        )
        try:
            return await run_in_threadpool(
                resolver,
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except BusinessError as error:
            raise _http_error(error) from error

    async def resolve_user(request: Request) -> RedemptionCodesActor:
        return await resolve_actor(request, administrator=False)

    async def resolve_administrator(request: Request) -> RedemptionCodesActor:
        return await resolve_actor(request, administrator=True)

    @router.post("/generate", response_model=GenerateResponse)
    async def generate_codes(
        actor: RedemptionCodesActor = Depends(resolve_administrator),
        data: GenerateRequest = Body(...),
    ) -> dict[str, object]:
        command = GenerateRedemptionCodesCommand(
            type=data.type,
            value=data.value,
            count=data.count,
            expires_at=data.expires_at,
        )
        return await _invoke(
            application.generate_redemption_codes,
            unit_of_work_factory,
            actor,
            command,
            code_generator=generator,
        )

    @router.post("/redeem", response_model=RedeemResponse)
    async def redeem_code(
        actor: RedemptionCodesActor = Depends(resolve_user),
        data: RedeemRequest = Body(...),
    ) -> dict[str, object]:
        command = RedeemRedemptionCodeCommand(code=data.code)
        return await _invoke(
            application.redeem_redemption_code,
            unit_of_work_factory,
            actor,
            command,
            clock=business_clock,
        )

    return router
