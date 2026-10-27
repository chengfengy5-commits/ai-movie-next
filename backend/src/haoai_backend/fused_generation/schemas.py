"""HTTP request and response schemas for fused generation."""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic import field_validator, model_validator


class FusedMember(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str = "asset"
    id: str | None = None
    url: str | None = None
    name: str | None = None


class FusedAssetGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    series_id: str
    member_type: str = "prop"
    members: list[FusedMember]
    # Retained for wire compatibility. Model choice is server-owned.
    model_id: str
    prompt_id: str | None = None
    prompt_system: str | None = None
    prompt_user: str | None = None
    size: str | None = None
    chapter_id: str | None = None
    frame_index: int | None = None
    name: str | None = None
    storyboard_asset_id: str | None = None
    expected_frame: dict[str, Any] | None = None
    expected_content: list[Any] | None = None

    @field_validator("storyboard_asset_id")
    @classmethod
    def validate_storyboard_asset_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value != value.strip() or len(value) > 255:
            raise ValueError("storyboard_asset_id 无效")
        return value

    @model_validator(mode="after")
    def validate_target_json(self) -> FusedAssetGenerateRequest:
        _require_finite_json(self.expected_frame)
        _require_finite_json(self.expected_content)
        return self


def _require_finite_json(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("分镜目标只能包含有限 JSON 数值")
        return
    if isinstance(value, list):
        for item in value:
            _require_finite_json(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("分镜目标必须是 JSON 对象")
        for item in value.values():
            _require_finite_json(item)
        return
    raise ValueError("分镜目标必须是有限 JSON 值")


class FusedAssetGenerateResponse(BaseModel):
    task_id: str
    message_id: str | None
