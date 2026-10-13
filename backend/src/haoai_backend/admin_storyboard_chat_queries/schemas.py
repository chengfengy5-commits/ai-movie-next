"""Typed HTTP payload projections for the legacy read-only endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from .domain import ChatMessageRecord, StoryboardAssetRecord


class StoryboardAssetPayload(TypedDict):
    id: str
    series_id: str
    chapter_id: str
    frame_index: int
    name: str
    description: str | None
    image_url: str | None
    created_at: datetime


class StoryboardAssetListPayload(TypedDict):
    data: list[StoryboardAssetPayload]
    total: int


class ChatMessageListItem(TypedDict):
    id: str
    chapter_id: str
    role: str
    chat_mode: str
    content: str
    created_at: datetime


class ChatMessageDetailPayload(TypedDict):
    id: str
    chapter_id: str
    role: str
    chat_mode: str
    content: str | None
    created_at: datetime


class ChatMessageListPayload(TypedDict):
    data: list[ChatMessageListItem]
    total: int


def storyboard_asset_payload(
    asset: StoryboardAssetRecord,
) -> StoryboardAssetPayload:
    return {
        "id": asset.id,
        "series_id": asset.series_id,
        "chapter_id": asset.chapter_id,
        "frame_index": asset.frame_index,
        "name": asset.name,
        "description": asset.description,
        "image_url": asset.image_url,
        "created_at": asset.created_at,
    }


def chat_message_list_item(message: ChatMessageRecord) -> ChatMessageListItem:
    return {
        "id": message.id,
        "chapter_id": message.chapter_id,
        "role": message.role,
        "chat_mode": message.chat_mode,
        "content": (message.content or "")[:200],
        "created_at": message.created_at,
    }


def chat_message_detail_payload(
    message: ChatMessageRecord,
) -> ChatMessageDetailPayload:
    return {
        "id": message.id,
        "chapter_id": message.chapter_id,
        "role": message.role,
        "chat_mode": message.chat_mode,
        "content": message.content,
        "created_at": message.created_at,
    }
