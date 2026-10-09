"""Pydantic request and response models matching the fixed DTO behavior."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class AssetNamingResponse(BaseModel):
    aliases: list[str] | None = None
    canonical_key: str | None = None

    @field_validator("aliases", mode="before")
    @classmethod
    def parse_aliases(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []


class CharacterCreate(BaseModel):
    name: str
    gender: str | None = None
    age: str | None = None
    role: str | None = None
    appearance: str | None = None
    description: str | None = None
    image_url: str | None = None
    audio_url: str | None = None
    voice_ref: str | None = None


class CharacterUpdate(BaseModel):
    name: str | None = None
    gender: str | None = None
    age: str | None = None
    role: str | None = None
    appearance: str | None = None
    description: str | None = None
    image_url: str | None = None
    audio_url: str | None = None
    voice_ref: str | None = None
    aliases: list[str] | None = None


class CharacterResponse(AssetNamingResponse):
    id: str
    series_id: str
    name: str
    gender: str | None
    age: str | None
    role: str | None
    appearance: str | None
    description: str | None
    image_url: str | None
    audio_url: str | None
    voice_ref: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SceneCreate(BaseModel):
    title: str
    description: str | None = None
    image_url: str | None = None


class SceneUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    aliases: list[str] | None = None


class SceneResponse(AssetNamingResponse):
    id: str
    series_id: str
    title: str
    description: str | None
    image_url: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PropCreate(BaseModel):
    name: str
    description: str | None = None
    image_url: str | None = None


class PropUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image_url: str | None = None
    aliases: list[str] | None = None


class PropResponse(AssetNamingResponse):
    id: str
    series_id: str
    name: str
    description: str | None
    image_url: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StoryboardAssetCreate(BaseModel):
    chapter_id: str
    frame_index: int
    name: str
    description: str | None = None
    image_url: str | None = None


class StoryboardAssetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image_url: str | None = None


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


class ChapterResponse(BaseModel):
    id: str
    series_id: str
    title: str
    content: Any
    order: int
    created_at: datetime
    updated_at: datetime
    lock: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


ASSET_RESPONSE_MODELS = {
    "character": CharacterResponse,
    "scene": SceneResponse,
    "prop": PropResponse,
}
