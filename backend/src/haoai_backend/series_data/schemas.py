"""Pydantic wire models matching the fixed source DTO behavior."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from haoai_backend.canvas_applications.schemas import GenerationApplicationUpdate


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


class ChapterEditFrameRetained(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["retained"]
    source_index: StrictInt = Field(ge=0)
    storyboard_id: StrictStr | None


class ChapterEditFrameNew(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["new"]


ChapterEditFrameSource = Annotated[
    Union[ChapterEditFrameRetained, ChapterEditFrameNew],
    Field(discriminator="kind"),
]


class ChapterEditStoryboardBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    series_id: StrictStr
    chapter_id: StrictStr
    frame_index: StrictInt = Field(ge=0)


class ChapterEditLockIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_id: StrictStr
    acquired_at: StrictStr


def _is_finite_json(value: object) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, list):
        return all(_is_finite_json(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_finite_json(item) for key, item in value.items())
    return False


class ChapterEditGuard(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    expected_chapter_id: StrictStr
    expected_series_id: StrictStr
    expected_order: StrictInt
    expected_content: list[dict[str, Any]] | None
    expected_storyboards: list[ChapterEditStoryboardBaseline]
    expected_lock_identity: ChapterEditLockIdentity | None
    frame_sources: list[ChapterEditFrameSource]
    deleted_source_indexes: list[StrictInt]

    @model_validator(mode="after")
    def validate_json_and_deleted_sources(self) -> "ChapterEditGuard":
        if not _is_finite_json(self.expected_content):
            raise ValueError("expected_content must contain only finite JSON values")
        if len(self.deleted_source_indexes) != len(set(self.deleted_source_indexes)):
            raise ValueError("deleted_source_indexes must be unique")
        return self


class ChapterUpdate(GenerationApplicationUpdate):
    title: str | None = None
    content: list[dict[str, Any]] | None = None
    order: int | None = None
    chapter_edit_guard: ChapterEditGuard | None = None

    @model_validator(mode="before")
    @classmethod
    def select_strict_chapter_edit_mode(cls, value: object) -> object:
        if not isinstance(value, dict) or "chapter_edit_guard" not in value:
            return value
        if set(value) != {"content", "chapter_edit_guard"}:
            raise ValueError("chapter edit guard cannot be combined with legacy edits")
        if value.get("chapter_edit_guard") is None or value.get("content") is None:
            raise ValueError("chapter edit guard requires a non-null guard and content")
        return value

    @model_validator(mode="after")
    def validate_guarded_content_json(self) -> "ChapterUpdate":
        if self.chapter_edit_guard is not None and not _is_finite_json(self.content):
            raise ValueError("guarded content must contain only finite JSON values")
        return self


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
