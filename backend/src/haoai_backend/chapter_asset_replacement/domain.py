"""Pure compatibility rules for replacing chapter asset references."""

from __future__ import annotations

import json
from copy import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any


ASSET_TYPES = ("character", "scene", "prop")


@dataclass(frozen=True, slots=True)
class ChapterRecord:
    id: str
    series_id: str
    title: str
    content: object
    order: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChapterAsset:
    id: str
    series_id: str
    display_name: str | None


@dataclass(frozen=True, slots=True)
class ReplaceAssetCommand:
    old_asset_id: str
    new_asset_id: str
    asset_type: str


def parse_chapter_frames(content: object) -> object:
    """Match the legacy response parser and its narrow JSON fallback."""
    frames: object = []
    if content:
        try:
            frames = json.loads(content) if isinstance(content, str) else content
        except (json.JSONDecodeError, TypeError):
            frames = []
    if not isinstance(frames, list):
        raise ValueError("章节内容格式异常")
    return frames


def replace_asset_references(
    frames: list[object],
    *,
    old_asset_id: str,
    new_asset_id: str,
    asset_type: str,
) -> tuple[list[object], int, list[object]]:
    """Replace IDs in only the requested category, counting changed frames."""
    updated = list(frames)
    replaced_count = 0
    observed_ids: list[object] = []

    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            continue
        ids = frame.get(asset_type) or []
        observed_ids.append(ids)
        if not isinstance(ids, list) or old_asset_id not in ids:
            continue

        changed_frame = copy(frame)
        if new_asset_id in ids:
            changed_frame[asset_type] = [asset_id for asset_id in ids if asset_id != old_asset_id]
        else:
            changed_frame[asset_type] = [
                new_asset_id if asset_id == old_asset_id else asset_id
                for asset_id in ids
            ]
        updated[index] = changed_frame
        replaced_count += 1

    return updated, replaced_count, observed_ids


def serialize_chapter_content(frames: object) -> str:
    return json.dumps(frames, ensure_ascii=False)


def collect_asset_ids_from_content(content: object) -> dict[str, set[object]]:
    """Collect truthy references using the legacy orphan-scan behavior."""
    result: dict[str, set[object]] = {
        "character": set(),
        "scene": set(),
        "prop": set(),
    }
    if not content:
        return result

    try:
        frames = json.loads(content) if isinstance(content, str) else (content or [])
    except (json.JSONDecodeError, TypeError):
        return result
    if not isinstance(frames, list):
        return result

    for frame in frames:
        if not isinstance(frame, dict):
            continue
        for asset_type in ASSET_TYPES:
            ids = frame.get(asset_type, [])
            if isinstance(ids, list):
                for asset_id in ids:
                    if asset_id:
                        result[asset_type].add(asset_id)
    return result
