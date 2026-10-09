"""FastAPI adapter for the chapter asset replacement endpoint."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from .application import replace_chapter_asset
from .domain import ReplaceAssetCommand
from .errors import ChapterAssetReplacementUnavailable
from .ports import UnitOfWorkFactory
from .schemas import ReplaceAssetRequest

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_chapter_asset_replacement_router(
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/chapters/{chapter_id}/replace-asset",
        tags=["chapter-asset-replacement"],
    )

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            error = ChapterAssetReplacementUnavailable()
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
            error = ChapterAssetReplacementUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    @router.post("")
    def replace_asset(
        chapter_id: str,
        request: ReplaceAssetRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        command = ReplaceAssetCommand(
            old_asset_id=request.old_asset_id,
            new_asset_id=request.new_asset_id,
            asset_type=request.asset_type,
        )
        try:
            return replace_chapter_asset(
                uow_factory,
                actor,
                chapter_id,
                command,
            )
        except BusinessError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc

    return router


def mount_chapter_asset_replacement_routes(
    app: FastAPI,
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> None:
    app.include_router(
        build_chapter_asset_replacement_router(
            uow_factory=uow_factory,
            resolve_actor=resolve_actor,
        )
    )
