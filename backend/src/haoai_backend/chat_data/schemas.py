"""Pydantic wire models preserving the existing chat request contract."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ChatDataModel(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())


class ChatMessageCreate(ChatDataModel):
    chapter_id: str
    frame_index: int = None
    chat_mode: str = "chat"
    role: str
    content: str
    model_name: str | None = None


class AssetChatMessageCreate(ChatDataModel):
    chapter_id: str
    asset_type: str
    asset_id: str
    chat_mode: str = "chat"
    role: str
    content: str
    model_name: str | None = None


class ChatMessageUpdate(ChatDataModel):
    content: str


class ChatMessageResponse(ChatDataModel):
    id: str
    chapter_id: str
    frame_index: int | None = None
    asset_type: str | None = None
    asset_id: str | None = None
    chat_mode: str = "chat"
    role: str
    content: str
    model_name: str | None = None
    created_at: datetime

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        protected_namespaces=(),
    )


class AIStatsResponse(ChatDataModel):
    chapter: list
    series: list
