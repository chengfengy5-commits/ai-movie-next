"""Immutable chat records and request values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ChatMessageRecord:
    id: str
    chapter_id: str
    frame_index: int | None
    asset_type: str | None
    asset_id: str | None
    chat_mode: str
    role: str
    content: str
    model_name: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewChatMessage:
    chapter_id: str
    frame_index: int | None
    asset_type: str | None
    asset_id: str | None
    chat_mode: str
    role: str
    content: str
    model_name: str | None

