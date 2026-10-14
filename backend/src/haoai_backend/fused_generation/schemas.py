"""HTTP request and response schemas for fused generation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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


class FusedAssetGenerateResponse(BaseModel):
    task_id: str
    message_id: str | None
