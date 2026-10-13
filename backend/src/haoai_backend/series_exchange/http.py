"""FastAPI adapter for the five legacy series-exchange methods."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from starlette.concurrency import run_in_threadpool

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .errors import SeriesExchangeUnavailable
from .ports import SeriesExchangeUnitOfWorkFactory
from .schemas import (
    FusedAssetResponse,
    FusedAssetUpdate,
    ImportSeriesRequest,
    ImportSeriesResponse,
)


ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_series_exchange_router(
    *,
    uow_factory: SeriesExchangeUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    """Build an inert router; auth and business sessions open only per request."""
    router = APIRouter(prefix="/api", tags=["series-exchange"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            raise _http_error(SeriesExchangeUnavailable())
        try:
            if inspect.iscoroutinefunction(resolve_actor):
                actor = await resolve_actor(request)
            else:
                candidate = await run_in_threadpool(resolve_actor, request)
                actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as error:
            raise _http_error(error) from error
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise _http_error(
                SeriesExchangeUnavailable("可信身份端口未正确接线")
            )
        return actor

    async def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if uow_factory is None:
            raise _http_error(SeriesExchangeUnavailable())
        try:
            return await run_in_threadpool(
                call,
                uow_factory,
                *args,
                **kwargs,
            )
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get(
        "/series/{series_id}/fused-assets",
        response_model=list[FusedAssetResponse],
    )
    async def get_fused_assets(
        series_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> list[dict[str, Any]]:
        return await invoke(application.list_fused_assets, actor, series_id)

    @router.put(
        "/fused-assets/{asset_id}",
        response_model=FusedAssetResponse,
    )
    async def put_fused_asset(
        asset_id: str,
        payload: FusedAssetUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return await invoke(
            application.update_fused_asset,
            actor,
            asset_id,
            payload.model_dump(),
        )

    @router.delete(
        "/fused-assets/{asset_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def remove_fused_asset(
        asset_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> Response:
        await invoke(application.delete_fused_asset, actor, asset_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/series/{series_id}/export")
    async def export_series(
        series_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return await invoke(application.export_series, actor, series_id)

    @router.post(
        "/series/import",
        response_model=ImportSeriesResponse,
    )
    async def import_series(
        payload: ImportSeriesRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, str]:
        return await invoke(
            application.import_series,
            actor,
            data=payload.data,
            requested_name=payload.name,
            requested_description=payload.description,
            team_id=payload.team_id,
            claim=payload.claim,
        )

    return router


def mount_series_exchange_routes(
    app: FastAPI,
    *,
    uow_factory: SeriesExchangeUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> None:
    app.include_router(
        build_series_exchange_router(
            uow_factory=uow_factory,
            resolve_actor=resolve_actor,
        )
    )
