"""Request contracts for the five legacy series asset-maintenance routes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AssetRefCheckItem(BaseModel):
    type: str
    id: str


class AssetRefCheckRequest(BaseModel):
    items: list[AssetRefCheckItem] = Field(default_factory=list)
    exclude_chapter_id: str | None = None


class AssetDuplicateIgnoreRequest(BaseModel):
    type: str
    ids: list[str] = Field(default_factory=list)
    ignored: bool = True


class AssetMergeRequest(BaseModel):
    type: str
    keep_id: str
    merge_ids: list[str] = Field(default_factory=list)
    keep_aliases: list[str] | None = None
