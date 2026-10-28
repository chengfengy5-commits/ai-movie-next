"""Strict payloads for original continuous chapter operations."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from .schemas import ChapterEditGuard


class SaveChapterCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["save-chapter"]
    content: list[dict[str, Any]]
    chapter_edit_guard: ChapterEditGuard
    draft_frame_ids: list[StrictStr]

    @model_validator(mode="after")
    def validate_draft_manifest(self) -> "SaveChapterCommand":
        if len(self.draft_frame_ids) != len(self.content):
            raise ValueError("draft_frame_ids must map every frame")
        if any(not value for value in self.draft_frame_ids) or len(set(self.draft_frame_ids)) != len(self.draft_frame_ids):
            raise ValueError("draft_frame_ids must be non-empty and unique")
        return self


class CreateAssetCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["create-character", "create-scene", "create-prop"]
    input: dict[str, Any]
    target_draft_id: StrictStr
    source_context: ChapterEditGuard
    draft_frame_ids: list[StrictStr]

    @model_validator(mode="after")
    def validate_draft_target(self) -> "CreateAssetCommand":
        if not self.target_draft_id or not self.draft_frame_ids:
            raise ValueError("asset creation requires a target draft and frame manifest")
        if len(set(self.draft_frame_ids)) != len(self.draft_frame_ids):
            raise ValueError("draft_frame_ids must be unique")
        if self.target_draft_id not in self.draft_frame_ids:
            raise ValueError("target_draft_id must belong to the draft manifest")
        return self


ContinuousChapterOperationCommand = Annotated[
    Union[SaveChapterCommand, CreateAssetCommand],
    Field(discriminator="kind"),
]


class ContinuousChapterOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocol_version: Literal[1]
    editor_session_id: StrictStr
    command: ContinuousChapterOperationCommand
