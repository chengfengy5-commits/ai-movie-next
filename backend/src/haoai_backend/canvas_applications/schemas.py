"""Strict wire contracts for explicit Canvas generation application PUTs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from haoai_backend.generation_contracts.types import GenerationApplicationCommand
from haoai_backend.task_observation.errors import TaskObservationBadRequest, TaskObservationUnavailable

TARGET_SNAPSHOT_FIELDS = frozenset(
    {
        "kind",
        "chapter_id",
        "series_id",
        "stable_storyboard_asset_id",
        "asset_type",
        "asset_id",
        "compat_frame_index",
        "expected_text_digest",
        "media_identity",
        "content_digest",
        "notes_revision",
        "full_frame_digest",
        "full_chapter_digest",
        "expected_asset_image_url",
        "same_series_target_digest",
    }
)


class GenerationApplicationRequest(BaseModel):
    """Client intent references a result and its original target, never its URL."""

    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: str
    result_ref: str
    expected_target: dict[str, Any]
    application_key: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_complete_expected_target(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        target = value.get("expected_target")
        if not isinstance(target, Mapping) or frozenset(target.keys()) != TARGET_SNAPSHOT_FIELDS:
            raise ValueError("expected_target must contain the complete canonical target snapshot")
        if target.get("kind") in ("canvas_frame_preview", "canvas_asset_image") and not target.get("chapter_id"):
            raise ValueError("Canvas generation targets must name their original chapter")
        return value

    def to_command(self) -> GenerationApplicationCommand:
        """Parse through the shared public target contract and build the typed P5 command."""
        try:
            from haoai_backend.generation_contracts.canvas_targets import (
                CanvasTargetParseError,
                is_valid_canvas_identifier,
                target_snapshot_from_mapping,
            )
        except ModuleNotFoundError as exc:
            if exc.name != "haoai_backend.generation_contracts.canvas_targets":
                raise
            raise TaskObservationUnavailable("画布目标解析服务尚未接线") from exc

        if not is_valid_canvas_identifier(self.task_id) or not is_valid_canvas_identifier(self.result_ref):
            raise TaskObservationBadRequest("任务或结果标识无效")
        try:
            target = target_snapshot_from_mapping(self.expected_target)
        except CanvasTargetParseError as exc:
            raise TaskObservationBadRequest("原始目标快照无效") from exc
        return GenerationApplicationCommand(
            task_id=self.task_id,
            result_ref=self.result_ref,
            expected_target=target,
            application_key=self.application_key,
        )


class GenerationApplicationUpdate(BaseModel):
    """Base for legacy update DTOs with a strict opt-in generation mode."""

    generation_application: GenerationApplicationRequest | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def select_strict_generation_mode(cls, value: object) -> object:
        if not isinstance(value, Mapping) or "generation_application" not in value:
            return value
        if frozenset(value.keys()) != frozenset({"generation_application"}):
            raise ValueError("generation application cannot be combined with ordinary edits")
        raw = value["generation_application"]
        if raw is None:
            raise ValueError("generation_application cannot be null")
        try:
            request = GenerationApplicationRequest.model_validate(raw)
        except ValidationError as exc:
            raise ValueError("generation_application is malformed") from exc
        return {"generation_application": request}


class ApplicationReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_ref: str
    target_revision: str
    committed_at: str


class GenerationApplicationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    result_ref: str
    status: Literal["applied", "already_applied"]
    application: ApplicationReceiptResponse
