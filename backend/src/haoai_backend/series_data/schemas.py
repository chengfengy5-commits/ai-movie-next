"""Pydantic wire models matching the fixed source DTO behavior."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SeriesCreate(BaseModel):
    name: str
    description: str | None = None
    image_url: str | None = None
    style_prompt_id: str | None = None


class SeriesUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image_url: str | None = None
    style_prompt_id: str | None = None


class SeriesResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str | None
    image_url: str | None
    style_prompt_id: str | None = None
    style_prompt: str | None = None
    style_prompt_name: str | None = None
    style_prompt_owner_name: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    owner_name: str | None = None
    claimed_by: str | None = None
    claimed_by_username: str | None = None
    claimed_by_avatar_url: str | None = None
    can_enter: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SceneFrame(BaseModel):
    text: str
    character: str | None = None
    scene: str | None = None
    prop: str | None = None


class ChapterCreate(BaseModel):
    title: str
    content: list[SceneFrame] | None = None
    raw_content: str | None = None
    order: int = 0


class ChapterUpdate(BaseModel):
    title: str | None = None
    content: list[dict[str, Any]] | None = None
    order: int | None = None


class ChapterResponse(BaseModel):
    id: str
    series_id: str
    title: str
    content: list[dict[str, Any]] | None
    order: int
    created_at: datetime
    updated_at: datetime
    lock: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class ReorderChaptersRequest(BaseModel):
    chapters: list[dict[str, Any]]


class DeleteFrameRequest(BaseModel):
    frame_index: int


class StoryboardAssetResponse(BaseModel):
    id: str
    series_id: str
    chapter_id: str
    frame_index: int
    name: str
    description: str | None = None
    image_url: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
