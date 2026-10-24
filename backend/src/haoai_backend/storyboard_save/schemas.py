"""Lexical JSON boundary and semantic request validation for save routes."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, field_validator
from pydantic.types import JsonValue


class _StoryboardSaveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chapter_id: StrictStr | None = None
    appearances: list[Any] | None = None
    dry_run: Any = False
    target_frames: list[StrictInt | StrictBool] | None = None
    expected_content: list[JsonValue] | None = None


class ExpectedChapterContentBody(BaseModel):
    """Validate an explicitly supplied source snapshot; null is not a snapshot."""

    model_config = ConfigDict(extra="ignore")

    expected_content: list[JsonValue]

    @field_validator("expected_content")
    @classmethod
    def reject_non_json_numbers(cls, value: list[JsonValue]) -> list[JsonValue]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("expected_content must contain JSON values only") from exc
        return value


class ExpectedSeriesIdBody(BaseModel):
    """Validate an explicitly supplied chapter-series identity guard."""

    model_config = ConfigDict(extra="ignore")

    expected_series_id: StrictStr

    @field_validator("expected_series_id")
    @classmethod
    def require_non_empty_series_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("expected_series_id must be non-empty")
        return value


class CharacterSaveBody(_StoryboardSaveBody):
    merged_characters: list[dict[str, Any]] | None = None


class SceneSaveBody(_StoryboardSaveBody):
    scenes: list[dict[str, Any]] | None = None


class PropSaveBody(_StoryboardSaveBody):
    props: list[dict[str, Any]] | None = None
