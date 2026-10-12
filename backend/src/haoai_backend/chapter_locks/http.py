"""FastAPI routes for the chapter editing lock capability."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from haoai_backend.shared.errors import BusinessError

from . import application
from .domain import ChapterLockActor
from .errors import ChapterLockUnavailable, LockOccupied
from .ports import UnitOfWorkFactory

ChapterLockActorResolver = Callable[
    [Request],
    ChapterLockActor | Awaitable[ChapterLockActor],
]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_chapter_locks_router(
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ChapterLockActorResolver | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/chapters", tags=["locks"])

    async def trusted_actor(request: Request) -> ChapterLockActor:
        if uow_factory is None or resolve_actor is None:
            raise _http_error(ChapterLockUnavailable())
        try:
            candidate = resolve_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise _http_error(exc) from exc
        if (
            not isinstance(actor, ChapterLockActor)
            or not actor.user_id
            or not isinstance(actor.username, str)
        ):
            raise _http_error(ChapterLockUnavailable("可信身份端口未正确接线"))
        return actor

    def invoke(call: Callable[..., Any], *args: Any) -> Any:
        try:
            return call(uow_factory, *args)
        except LockOccupied as exc:
            return JSONResponse(status_code=409, content=exc.payload)
        except BusinessError as exc:
            raise _http_error(exc) from exc

    @router.post("/{chapter_id}/lock")
    def acquire(
        chapter_id: str,
        actor: ChapterLockActor = Depends(trusted_actor),
    ):
        return invoke(application.acquire_lock, actor, chapter_id)

    @router.get("/{chapter_id}/lock")
    def read(
        chapter_id: str,
        actor: ChapterLockActor = Depends(trusted_actor),
    ):
        return invoke(application.get_lock, actor, chapter_id)

    @router.delete("/{chapter_id}/lock")
    def release(
        chapter_id: str,
        actor: ChapterLockActor = Depends(trusted_actor),
    ):
        return invoke(application.release_lock, actor, chapter_id)

    return router
