"""HTTP adapters for the five read-only team reporting routes."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.responses import Response

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams.errors import TeamsUnavailable
from haoai_backend.teams.ports import TeamsUnitOfWorkFactory

from . import application
from .persistence import adapt_reporting_uow_factory


ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


class _ReportingValidationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original_handler = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                errors = exc.errors()
                if not errors:
                    raise
                message = str(errors[0].get("msg", ""))
                prefix = "Value error, "
                if message.startswith(prefix):
                    message = message[len(prefix) :]
                return JSONResponse(status_code=422, content={"detail": message})

        return handler


def build_reporting_router(
    *,
    uow_factory: TeamsUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api",
        tags=["teams-reporting"],
        route_class=_ReportingValidationRoute,
    )
    reporting_uow_factory = (
        adapt_reporting_uow_factory(uow_factory) if uow_factory is not None else None
    )

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            error = TeamsUnavailable()
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            candidate = resolve_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            error = TeamsUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return call(reporting_uow_factory, *args, **kwargs)
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/teams/{team_id}/members/{user_id}/tasks")
    def member_ai_tasks(
        team_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 10,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(
            application.member_ai_tasks,
            actor,
            team_id,
            user_id,
            page,
            page_size,
        )

    @router.get("/teams/{team_id}/usage")
    def team_usage(
        team_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(
            application.team_usage,
            actor,
            team_id,
            start_time,
            end_time,
        )

    @router.get("/teams/{team_id}/series/{series_id}/usage")
    def team_series_usage(
        team_id: str,
        series_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(
            application.team_series_usage,
            actor,
            team_id,
            series_id,
            start_time,
            end_time,
        )

    @router.get("/teams/{team_id}/usage/model")
    def team_model_usage(
        team_id: str,
        model_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(
            application.team_model_usage,
            actor,
            team_id,
            model_name,
            start_time,
            end_time,
        )

    @router.get("/teams/{team_id}/usage/export")
    def team_usage_export(
        team_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(
            application.team_usage_export,
            actor,
            team_id,
            start_time,
            end_time,
        )

    return router
