"""FastAPI adapters for the existing chapter chat and statistics routes."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .domain import NewChatMessage
from .errors import ChatDataUnavailable
from .ports import UnitOfWorkFactory
from .schemas import (
    AIStatsResponse,
    AssetChatMessageCreate,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatMessageUpdate,
)

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_chat_data_router(
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["chat-data"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            error = ChatDataUnavailable()
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            candidate = resolve_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            error = ChatDataUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return call(uow_factory, *args, **kwargs)
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get(
        "/chapters/{chapter_id}/chat-messages",
        response_model=list[ChatMessageResponse],
    )
    def get_chat_messages(
        chapter_id: str,
        frame_index: int | None = None,
        chat_mode: str = "chat",
        actor: TrustedActor = Depends(trusted_actor),
    ) -> list[dict[str, Any]]:
        return invoke(
            application.get_chat_messages,
            actor,
            chapter_id,
            frame_index=frame_index,
            chat_mode=chat_mode,
        )

    @router.post(
        "/chapters/{chapter_id}/chat-messages",
        response_model=ChatMessageResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_chat_message(
        chapter_id: str,
        request: ChatMessageCreate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        intent = NewChatMessage(
            chapter_id=request.chapter_id,
            frame_index=request.frame_index,
            asset_type=None,
            asset_id=None,
            chat_mode=request.chat_mode,
            role=request.role,
            content=request.content,
            model_name=request.model_name,
        )
        return invoke(application.create_chat_message, actor, chapter_id, intent)

    @router.put(
        "/chapters/{chapter_id}/chat-messages/{message_id}",
        response_model=ChatMessageResponse,
    )
    def update_chat_message(
        chapter_id: str,
        message_id: str,
        request: ChatMessageUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(
            application.update_chat_message,
            actor,
            chapter_id,
            message_id,
            request.content,
        )

    @router.put(
        "/chapters/{chapter_id}/asset-chat-messages/{message_id}",
        response_model=ChatMessageResponse,
    )
    def update_asset_chat_message(
        chapter_id: str,
        message_id: str,
        request: ChatMessageUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(
            application.update_chat_message,
            actor,
            chapter_id,
            message_id,
            request.content,
        )

    @router.get(
        "/chapters/{chapter_id}/asset-chat-messages",
        response_model=list[ChatMessageResponse],
    )
    def get_asset_chat_messages(
        chapter_id: str,
        asset_type: str,
        asset_id: str,
        chat_mode: str = "chat",
        actor: TrustedActor = Depends(trusted_actor),
    ) -> list[dict[str, Any]]:
        return invoke(
            application.get_asset_chat_messages,
            actor,
            chapter_id,
            asset_type,
            asset_id,
            chat_mode=chat_mode,
        )

    @router.post(
        "/chapters/{chapter_id}/asset-chat-messages",
        response_model=ChatMessageResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_asset_chat_message(
        chapter_id: str,
        request: AssetChatMessageCreate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        intent = NewChatMessage(
            chapter_id=request.chapter_id,
            frame_index=None,
            asset_type=request.asset_type,
            asset_id=request.asset_id,
            chat_mode=request.chat_mode,
            role=request.role,
            content=request.content,
            model_name=request.model_name,
        )
        return invoke(application.create_asset_chat_message, actor, chapter_id, intent)

    @router.delete(
        "/chapters/{chapter_id}/chat-messages",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_chat_messages(
        chapter_id: str,
        frame_index: int | None = None,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> Response:
        invoke(
            application.delete_chat_messages,
            actor,
            chapter_id,
            frame_index=frame_index,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete(
        "/chapters/{chapter_id}/chat-messages/single/{message_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_single_chat_message(
        chapter_id: str,
        message_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> Response:
        invoke(application.delete_single_chat_message, actor, chapter_id, message_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete(
        "/chapters/{chapter_id}/asset-chat-messages/single/{message_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_single_asset_chat_message(
        chapter_id: str,
        message_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> Response:
        invoke(application.delete_single_asset_chat_message, actor, chapter_id, message_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get(
        "/chapters/{chapter_id}/ai-stats",
        response_model=AIStatsResponse,
    )
    def get_ai_stats(
        chapter_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(application.get_ai_stats, actor, chapter_id)

    return router


def mount_chat_data_routes(
    app: FastAPI,
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> None:
    app.include_router(
        build_chat_data_router(
            uow_factory=uow_factory,
            resolve_actor=resolve_actor,
        )
    )
