"""Pure records and small transformations for asset-data operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AssetKind = Literal["character", "scene", "prop"]
ASSET_KINDS: tuple[AssetKind, ...] = ("character", "scene", "prop")

ASSET_NOT_FOUND_DETAILS: dict[AssetKind, str] = {
    "character": "角色不存在",
    "scene": "场景不存在",
    "prop": "道具不存在",
}


@dataclass(frozen=True, slots=True)
class ReferenceRemoval:
    content: object
    changed: bool
    removed_frames: int


@dataclass(frozen=True, slots=True)
class ChapterReferenceRecord:
    id: str
    series_id: str
    content: object
