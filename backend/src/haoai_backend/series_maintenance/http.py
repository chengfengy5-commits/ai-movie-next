"""FastAPI adapter for the five series asset-maintenance methods."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .errors import SeriesMaintenanceUnavailable
from .ports import SeriesMaintenanceUnitOfWorkFactory
from .schemas import (
    AssetDuplicateIgnoreRequest,
    AssetMergeRequest,
    AssetRefCheckRequest,
)

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_series_maintenance_router(
    *,
    uow_factory: SeriesMaintenanceUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    """Build an inert router; authentication and SQL sessions remain request-lazy."""
    router = APIRouter(prefix="/api", tags=["series-maintenance"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            raise _http_error(SeriesMaintenanceUnavailable())
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
                SeriesMaintenanceUnavailable("可信身份端口未正确接线")
            )
        return actor

    async def invoke(call: Callable[..., Any], *args: Any) -> Any:
        if uow_factory is None:
            raise _http_error(SeriesMaintenanceUnavailable())
        try:
            return await run_in_threadpool(call, uow_factory, *args)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.post("/series/{series_id}/assets/orphan-check")
    async def check_orphans(
        series_id: str,
        payload: AssetRefCheckRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return await invoke(application.check_orphan_assets, actor, series_id, payload)

    @router.get("/series/{series_id}/assets/duplicates")
    async def list_duplicates(
        series_id: str,
        asset_type: str = Query(default="scene", alias="type"),
        threshold: float = Query(default=0.82),
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return await invoke(
            application.list_duplicate_assets,
            actor,
            series_id,
            asset_type,
            threshold,
        )

    @router.post("/series/{series_id}/assets/duplicates/ignore")
    async def ignore_duplicates(
        series_id: str,
        payload: AssetDuplicateIgnoreRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return await invoke(
            application.ignore_duplicate_assets,
            actor,
            series_id,
            payload,
        )

    @router.get("/series/{series_id}/assets/duplicates/ignored")
    async def list_ignored(
        series_id: str,
        asset_type: str = Query(default="scene", alias="type"),
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return await invoke(
            application.list_ignored_duplicate_pairs,
            actor,
            series_id,
            asset_type,
        )

    @router.post("/series/{series_id}/assets/merge")
    async def merge_assets(
        series_id: str,
        payload: AssetMergeRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return await invoke(
            application.merge_duplicate_assets,
            actor,
            series_id,
            payload,
        )

    return router


def mount_series_maintenance_routes(
    app: FastAPI,
    *,
    uow_factory: SeriesMaintenanceUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> None:
    app.include_router(
        build_series_maintenance_router(
            uow_factory=uow_factory,
            resolve_actor=resolve_actor,
        )
    )
