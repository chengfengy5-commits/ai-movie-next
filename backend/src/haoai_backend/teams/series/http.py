"""FastAPI adapter for the team-series routes."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams import ShareSeriesRequest, TeamSeriesCreate, TransferRequest
from haoai_backend.teams.errors import TeamsUnavailable
from .application import (
    claim_series,
    create_team_series,
    list_team_series,
    share_series_to_team,
    transfer_series_claim,
    unclaim_series,
    unshare_series,
)
from .ports import SeriesUnitOfWorkFactory


ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


class _FirstMessageValidationRoute(APIRoute):
    """Keep the legacy first-message response local to team-series routes."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError as exc:
                errors = exc.errors()
                message = (
                    errors[0].get("msg", "Invalid request")
                    if errors
                    else "Invalid request"
                )
                if message.startswith("Value error, "):
                    message = message.removeprefix("Value error, ")
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"detail": message},
                )

        return handle


def build_series_router(
    *,
    uow_factory: SeriesUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api",
        tags=["teams"],
        route_class=_FirstMessageValidationRoute,
    )

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            error = TeamsUnavailable()
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            result = resolve_actor(request)
            actor = await result if inspect.isawaitable(result) else result
        except BusinessError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            error = TeamsUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    def translate(call: Callable[[], Any]) -> Any:
        try:
            return call()
        except BusinessError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc

    @router.get("/teams/{team_id}/series")
    def team_series_list(
        team_id: str,
        page: int = 1,
        page_size: int = 10,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return translate(
            lambda: list_team_series(
                uow_factory,
                actor,
                team_id,
                page=page,
                page_size=page_size,
            )
        )

    @router.post(
        "/teams/{team_id}/series",
        status_code=status.HTTP_201_CREATED,
    )
    def create_team_series_route(
        team_id: str,
        body: TeamSeriesCreate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return translate(
            lambda: create_team_series(uow_factory, actor, team_id, body)
        )

    @router.post("/series/{series_id}/share")
    def share_series_route(
        series_id: str,
        body: ShareSeriesRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return translate(
            lambda: share_series_to_team(uow_factory, actor, series_id, body)
        )

    @router.delete("/series/{series_id}/share")
    def unshare_series_route(
        series_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, str]:
        return translate(lambda: unshare_series(uow_factory, actor, series_id))

    @router.post("/teams/{team_id}/series/{series_id}/claim")
    def claim_series_route(
        team_id: str,
        series_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return translate(
            lambda: claim_series(uow_factory, actor, team_id, series_id)
        )

    @router.delete("/teams/{team_id}/series/{series_id}/claim")
    def unclaim_series_route(
        team_id: str,
        series_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, str]:
        return translate(
            lambda: unclaim_series(uow_factory, actor, team_id, series_id)
        )

    @router.post("/teams/{team_id}/series/{series_id}/transfer")
    def transfer_series_route(
        team_id: str,
        series_id: str,
        body: TransferRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, str]:
        return translate(
            lambda: transfer_series_claim(
                uow_factory,
                actor,
                team_id,
                series_id,
                body,
            )
        )

    return router
