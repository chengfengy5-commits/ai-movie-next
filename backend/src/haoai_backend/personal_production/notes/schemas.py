"""Strict request DTOs for personal production note patches."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from .domain import MAX_FRAME_UPDATES, MAX_NOTE_LENGTH


class FrameNoteChangeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storyboard_asset_id: StrictStr = Field(min_length=1, max_length=36)
    expected_media_revision: StrictInt = Field(ge=1)
    status: Literal["unmarked", "needs_revision", "approved"] | None = None
    note: StrictStr | None = Field(default=None, max_length=MAX_NOTE_LENGTH)

    @model_validator(mode="after")
    def require_a_change(self) -> FrameNoteChangeSchema:
        if self.status is None and self.note is None:
            raise ValueError("镜头更新至少需要状态或备注")
        return self


class PersonalProductionNotesUpdateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt = Field(ge=0)
    frames: list[FrameNoteChangeSchema] = Field(default_factory=list, max_length=MAX_FRAME_UPDATES)
    resume_frame_id: StrictStr | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def validate_patch(self) -> PersonalProductionNotesUpdateSchema:
        ids = [frame.storyboard_asset_id for frame in self.frames]
        if len(ids) != len(set(ids)):
            raise ValueError("同一请求不能重复更新同一镜头")
        if not self.frames and "resume_frame_id" not in self.model_fields_set:
            raise ValueError("请求必须包含镜头修改或续作位置修改")
        return self
