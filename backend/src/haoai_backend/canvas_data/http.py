"""FastAPI routes for the two chapter canvas methods."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .errors import CanvasDataUnavailable
from .ports import UnitOfWorkFactory
from .schemas import CanvasDocumentResponse, CanvasDocumentUpdate

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_canvas_data_router(
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/chapters", tags=["canvas-data"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            error = CanvasDataUnavailable()
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            candidate = resolve_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            error = CanvasDataUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    def invoke(call: Callable[..., Any], *args: Any) -> Any:
        try:
            return call(uow_factory, *args)
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/{chapter_id}/canvas", response_model=CanvasDocumentResponse)
    def get_canvas(
        chapter_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(application.get_chapter_canvas, actor, chapter_id)

    @router.put("/{chapter_id}/canvas", response_model=CanvasDocumentResponse)
    def put_canvas(
        chapter_id: str,
        request: CanvasDocumentUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(
            application.save_chapter_canvas,
            actor,
            chapter_id,
            request.document_json,
            request.version,
        )

    return router


def mount_canvas_data_routes(
    app: FastAPI,
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> None:
    app.include_router(build_canvas_data_router(uow_factory=uow_factory, resolve_actor=resolve_actor))
