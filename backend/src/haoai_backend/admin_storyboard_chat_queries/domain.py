"""Framework-independent records for administrator storyboard and chat reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AdminStoryboardChatQueriesActor:
    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class StoryboardAssetRecord:
    id: str
    series_id: str
    chapter_id: str
    frame_index: int
    name: str
    description: str | None
    image_url: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChatMessageRecord:
    id: str
    chapter_id: str
    role: str
    chat_mode: str
    content: str | None
    created_at: datetime
