"""HTTP routes for guarded Canvas source reads and commands."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .errors import CanvasSourceUnavailable
from .ports import CanvasSourceUnitOfWorkFactory
from .schemas import CanvasSourceRequest, CanvasSourceResponse, SourceSnapshot

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_canvas_sources_router(
    *,
    uow_factory: CanvasSourceUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
    receipt_secret: str | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/chapters", tags=["canvas-source"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            error = CanvasSourceUnavailable("画布来源身份或数据端口未接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            candidate = resolve_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            error = CanvasSourceUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return call(*args, **kwargs)
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/{chapter_id}/canvas-source", response_model=SourceSnapshot)
    def read_source(
        chapter_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> SourceSnapshot:
        return invoke(
            application.get_source_snapshot,
            uow_factory,
            actor,
            chapter_id,
        )

    @router.post("/{chapter_id}/canvas-source", response_model=CanvasSourceResponse)
    def write_source(
        chapter_id: str,
        body: CanvasSourceRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> CanvasSourceResponse:
        return invoke(
            application.execute_source_command,
            uow_factory,
            actor,
            chapter_id,
            body,
            receipt_secret=receipt_secret,
        )

    return router
