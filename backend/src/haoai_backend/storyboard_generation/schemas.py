"""HTTP DTOs for the four legacy storyboard-generation endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class GenerateStoryboardRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    raw_content: str
    model_id: str
    prompt_id: str | None = None
    temperature: float = 0.7
    task_id: str | None = None


class CreateChapterWithStoryboardRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    series_id: str
    chapter_title: str
    raw_content: str
    model_id: str
    prompt_id: str | None = None
    order: int = 0


class TwoPassStoryboardRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    raw_content: str
    model_id: str
    scene_prompt_id: str | None = None
    frame_prompt_id: str | None = None
    temperature: float = 0.7
    enable_thinking: bool = False
    task_id: str | None = None
    skip_frame_generation: bool = False
