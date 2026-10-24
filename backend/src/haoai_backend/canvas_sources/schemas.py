"""Typed request and response models for guarded Canvas source commands."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from haoai_backend.asset_data.schemas import (
    CharacterCreate,
    CharacterResponse,
    CharacterUpdate,
    PropCreate,
    PropResponse,
    PropUpdate,
    SceneCreate,
    SceneResponse,
    SceneUpdate,
    StoryboardAssetResponse,
)
from haoai_backend.series_data.schemas import ChapterResponse


CAPABILITY = "canvas-source-v1"


class CanvasSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrameTarget(CanvasSourceInput):
    index: int = Field(ge=0, strict=True)
    storyboard_id: StrictStr | None

    @field_validator("storyboard_id")
    @classmethod
    def nonblank_storyboard_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("storyboard_id must be nonblank when provided")
        return value


class FrameUpdateCommand(CanvasSourceInput):
    type: Literal["frame.update"]
    target: FrameTarget
    text: StrictStr


class FrameInsertCommand(CanvasSourceInput):
    type: Literal["frame.insert"]
    after: FrameTarget | None
    frame: dict[str, Any]

    @field_validator("frame")
    @classmethod
    def finite_json_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value)
        refs = value.get("storyboard")
        if refs not in (None, [], ()):
            raise ValueError("new frames cannot supply storyboard identities")
        return value


class FrameDeleteCommand(CanvasSourceInput):
    type: Literal["frame.delete"]
    target: FrameTarget


class AssetCreateCommand(CanvasSourceInput):
    type: Literal["asset.create"]
    kind: Literal["character", "scene", "prop"]
    values: CharacterCreate | SceneCreate | PropCreate

    @model_validator(mode="before")
    @classmethod
    def parse_kind_values(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        kind = value.get("kind")
        model = {"character": CharacterCreate, "scene": SceneCreate, "prop": PropCreate}.get(kind)
        if model is not None and not isinstance(value.get("values"), model):
            copied = dict(value)
            copied["values"] = model.model_validate(value.get("values"))
            return copied
        return value


class AssetUpdateCommand(CanvasSourceInput):
    type: Literal["asset.update"]
    kind: Literal["character", "scene", "prop"]
    id: StrictStr = Field(min_length=1)
    values: CharacterUpdate | SceneUpdate | PropUpdate

    @model_validator(mode="before")
    @classmethod
    def parse_kind_values(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        kind = value.get("kind")
        model = {"character": CharacterUpdate, "scene": SceneUpdate, "prop": PropUpdate}.get(kind)
        if model is not None and not isinstance(value.get("values"), model):
            copied = dict(value)
            copied["values"] = model.model_validate(value.get("values"))
            return copied
        return value


class MediaAppendCommand(CanvasSourceInput):
    type: Literal["media.append"]
    target: FrameTarget
    value: StrictStr = Field(min_length=1)


class MediaUpdateCommand(CanvasSourceInput):
    type: Literal["media.update"]
    target: FrameTarget
    field: Literal["preview", "video"]
    slot: int | None = Field(default=None, strict=True, ge=0)
    value: StrictStr

    @model_validator(mode="after")
    def validate_slot(self) -> "MediaUpdateCommand":
        if self.field == "preview" and self.slot is not None:
            raise ValueError("preview updates require slot=null")
        if self.field == "video" and self.slot is None:
            raise ValueError("video updates require a nonnegative slot")
        return self


class CreationUndoCommand(CanvasSourceInput):
    type: Literal["creation.undo"]
    receipt: StrictStr = Field(min_length=1)


CanvasSourceCommand = Annotated[
    Union[
        FrameUpdateCommand,
        FrameInsertCommand,
        FrameDeleteCommand,
        AssetCreateCommand,
        AssetUpdateCommand,
        MediaAppendCommand,
        MediaUpdateCommand,
        CreationUndoCommand,
    ],
    Field(discriminator="type"),
]


class CanvasSourceRequest(CanvasSourceInput):
    capability: Literal[CAPABILITY]
    request_id: UUID
    expected_series_id: StrictStr = Field(min_length=1)
    expected_version: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    command: CanvasSourceCommand

    @field_validator("expected_series_id")
    @classmethod
    def nonblank_series_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("expected_series_id must be nonblank")
        return value


class SourceSnapshot(BaseModel):
    capability: Literal[CAPABILITY]
    series_id: str
    chapter_id: str
    requires_lock: bool
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter: ChapterResponse
    storyboard_assets: list[StoryboardAssetResponse]
    characters: list[CharacterResponse]
    scenes: list[SceneResponse]
    props: list[PropResponse]


class CreationReceipt(BaseModel):
    token: str
    kind: Literal["frame", "character", "scene", "prop", "video"]
    target: dict[str, Any]


class CanvasSourceResponse(BaseModel):
    capability: Literal[CAPABILITY]
    request_id: UUID
    command_type: str
    snapshot: SourceSnapshot
    creation_receipt: CreationReceipt | None


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("frame must contain only finite JSON numbers")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("frame object keys must be strings")
        for item in value.values():
            _validate_json_value(item)
        return
    raise ValueError("frame must contain finite JSON values")


