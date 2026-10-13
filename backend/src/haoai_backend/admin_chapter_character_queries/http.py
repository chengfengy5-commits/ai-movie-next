"""FastAPI adapter for the four administrator chapter and character routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_chapter_character_actor
from .domain import AdminChapterCharacterActor
from .persistence import SqlAlchemyAdminChapterCharacterUnitOfWork
from .ports import AdminChapterCharacterUnitOfWorkFactory
from .schemas import AdminChapterCharacterSortField, AdminChapterCharacterSortOrder


SessionFactory = Callable[[], Any]
ADMIN_CHAPTER_CHARACTER_QUERIES_UNAVAILABLE = "章节和角色查询服务尚未接线"


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_chapter_character_queries_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
) -> APIRouter:
    """Build an inert router; sessions are created only while serving a request."""

    router = APIRouter(prefix="/api/admin", tags=["admin-chapter-character-queries"])
    uow_factory: AdminChapterCharacterUnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_uow() -> SqlAlchemyAdminChapterCharacterUnitOfWork:
            return SqlAlchemyAdminChapterCharacterUnitOfWork(session_factory())

        uow_factory = make_uow

    def administrator(request: Request) -> AdminChapterCharacterActor:
        if authentication_service is None:
            raise HTTPException(
                status_code=503,
                detail=ADMIN_CHAPTER_CHARACTER_QUERIES_UNAVAILABLE,
            )
        try:
            return authenticate_admin_chapter_character_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers=error.headers,
            ) from error

    async def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return await run_in_threadpool(call, uow_factory, *args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/chapters", response_model=None)
    async def list_chapters(
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: AdminChapterCharacterSortField = Query(default="created_at"),
        sort_order: AdminChapterCharacterSortOrder = Query(default="desc"),
        actor: AdminChapterCharacterActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(
            application.list_chapters,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
        )

    @router.get("/chapters/{chapter_id}", response_model=None)
    async def get_chapter(
        chapter_id: str,
        actor: AdminChapterCharacterActor = Depends(administrator),
    ) -> dict[str, object]:
        return await invoke(application.get_chapter, actor, chapter_id)

    @router.get("/characters", response_model=None)
    async def list_characters(
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: AdminChapterCharacterSortField = Query(default="created_at"),
        sort_order: AdminChapterCharacterSortOrder = Query(default="desc"),
        actor: AdminChapterCharacterActor = Depends(administrator),
    ) -> dict[str, Any]:
        return await invoke(
            application.list_characters,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
        )

    @router.get("/characters/{character_id}", response_model=None)
    async def get_character(
        character_id: str,
        actor: AdminChapterCharacterActor = Depends(administrator),
    ) -> dict[str, object]:
        return await invoke(application.get_character, actor, character_id)

    return router
