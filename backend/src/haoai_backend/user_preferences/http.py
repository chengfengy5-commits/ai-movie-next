"""FastAPI routes for account-scoped user preferences."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .errors import UserPreferencesUnavailable
from .ports import UnitOfWorkFactory
from .schemas import PreferencesUpdateRequest

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_user_preferences_router(
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_account_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/user", tags=["user-preferences"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_account_actor is None:
            error = UserPreferencesUnavailable()
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            candidate = resolve_account_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=getattr(exc, "headers", None),
            ) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            error = UserPreferencesUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return call(uow_factory, *args, **kwargs)
        except BusinessError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=getattr(exc, "headers", None),
            ) from exc

    @router.get("/preferences")
    def get_preferences(
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(application.get_user_preferences, actor)

    @router.put("/preferences")
    def update_preferences(
        body: PreferencesUpdateRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(
            application.update_user_preferences,
            actor,
            body.preferences,
        )

    return router


def mount_user_preferences_routes(
    app: FastAPI,
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_account_actor: ActorResolver | None,
) -> None:
    app.include_router(
        build_user_preferences_router(
            uow_factory=uow_factory,
            resolve_account_actor=resolve_account_actor,
        )
    )
