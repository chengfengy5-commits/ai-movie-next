"""FastAPI routes for the two chapter canvas methods."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .errors import CanvasDataUnavailable
from .ports import UnitOfWorkFactory
from .domain import CanvasSaveGuard
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

    def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return call(uow_factory, *args, **kwargs)
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/{chapter_id}/canvas", response_model=CanvasDocumentResponse, response_model_exclude_unset=True)
    def get_canvas(
        chapter_id: str,
        save_mode: str | None = Query(default=None),
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        if save_mode not in (None, "guarded-v1"):
            raise HTTPException(status_code=400, detail="画布读取模式无效")
        return invoke(application.get_chapter_canvas, actor, chapter_id, guarded=save_mode == "guarded-v1")

    @router.put("/{chapter_id}/canvas", response_model=CanvasDocumentResponse, response_model_exclude_unset=True)
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
            CanvasSaveGuard(
                mode=request.save_guard.mode,
                document_id=request.save_guard.id,
                version=request.save_guard.version,
            ) if request.save_guard is not None else None,
        )

    return router


def mount_canvas_data_routes(
    app: FastAPI,
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> None:
    app.include_router(build_canvas_data_router(uow_factory=uow_factory, resolve_actor=resolve_actor))
