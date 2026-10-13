"""FastAPI routes for administrator storyboard and chat-message queries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_storyboard_chat_queries_actor
from .domain import AdminStoryboardChatQueriesActor
from .errors import AdminStoryboardChatQueriesUnavailable
from .persistence import SqlAlchemyAdminStoryboardChatQueriesUnitOfWork
from .ports import UnitOfWorkFactory
from .schemas import (
    ChatMessageDetailPayload,
    ChatMessageListPayload,
    StoryboardAssetListPayload,
    StoryboardAssetPayload,
)

SessionFactory = Callable[[], Any]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_storyboard_chat_queries_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
) -> APIRouter:
    """Build an inert router; both request-scoped sessions stay lazy."""

    router = APIRouter(prefix="/api/admin", tags=["admin-storyboard-chat-queries"])
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_uow() -> SqlAlchemyAdminStoryboardChatQueriesUnitOfWork:
            return SqlAlchemyAdminStoryboardChatQueriesUnitOfWork(session_factory())

        uow_factory = make_uow

    def administrator(request: Request) -> AdminStoryboardChatQueriesActor:
        if authentication_service is None or uow_factory is None:
            raise _http_error(AdminStoryboardChatQueriesUnavailable())
        try:
            return authenticate_admin_storyboard_chat_queries_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except BusinessError as error:
            raise _http_error(error) from error

    async def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if uow_factory is None:
            raise _http_error(AdminStoryboardChatQueriesUnavailable())
        try:
            return await run_in_threadpool(call, uow_factory, *args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.get("/storyboard-assets", response_model=None)
    async def list_storyboard_assets(
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
        actor: AdminStoryboardChatQueriesActor = Depends(administrator),
    ) -> StoryboardAssetListPayload:
        return await invoke(
            application.list_storyboard_assets,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
        )

    @router.get("/storyboard-assets/{asset_id}", response_model=None)
    async def get_storyboard_asset(
        asset_id: str,
        actor: AdminStoryboardChatQueriesActor = Depends(administrator),
    ) -> StoryboardAssetPayload:
        return await invoke(application.get_storyboard_asset, actor, asset_id)

    @router.get("/chat-messages", response_model=None)
    async def list_chat_messages(
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
        actor: AdminStoryboardChatQueriesActor = Depends(administrator),
    ) -> ChatMessageListPayload:
        return await invoke(
            application.list_chat_messages,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
        )

    @router.get("/chat-messages/{message_id}", response_model=None)
    async def get_chat_message(
        message_id: str,
        actor: AdminStoryboardChatQueriesActor = Depends(administrator),
    ) -> ChatMessageDetailPayload:
        return await invoke(application.get_chat_message, actor, message_id)

    return router
