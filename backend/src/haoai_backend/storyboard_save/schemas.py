"""Lexical JSON boundary and semantic request validation for save routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr


class _StoryboardSaveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chapter_id: StrictStr | None = None
    appearances: list[Any] | None = None
    dry_run: Any = False
    target_frames: list[StrictInt | StrictBool] | None = None


class CharacterSaveBody(_StoryboardSaveBody):
    merged_characters: list[dict[str, Any]] | None = None


class SceneSaveBody(_StoryboardSaveBody):
    scenes: list[dict[str, Any]] | None = None


class PropSaveBody(_StoryboardSaveBody):
    props: list[dict[str, Any]] | None = None
