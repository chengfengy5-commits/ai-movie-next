"""Immutable records and pure compatibility helpers for source data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SeriesRecord:
    id: str
    user_id: str
    name: str
    description: str | None
    image_url: str | None
    style_prompt_id: str | None
    team_id: str | None
    claimed_by: str | None
    created_at: datetime
    updated_at: datetime


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
class StoryboardAssetRecord:
    id: str
    series_id: str
    chapter_id: str
    frame_index: int
    name: str
    description: str | None
    image_url: str | None
    created_at: datetime
    updated_at: datetime


def parse_chapter_frames(content: object) -> object:
    """Decode response content; only stored strings are parsed by the old route."""
    if not isinstance(content, str):
        return []
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return []


def parse_source_frames(content: object) -> list[Any]:
    """Decode a source for internal scans, retaining every list slot and index."""
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return []
    else:
        decoded = content
    return decoded if isinstance(decoded, list) else []


def serialize_content(value: object, *, ensure_ascii: bool = True) -> str:
    return json.dumps(value, ensure_ascii=ensure_ascii)


def source_asset_ids(content: object) -> dict[str, set[object]]:
    """Collect role/scene/prop identifiers for the legacy orphan cleanup."""
    result: dict[str, set[object]] = {"character": set(), "scene": set(), "prop": set()}
    frames = content
    if isinstance(frames, str):
        try:
            frames = json.loads(frames)
        except (json.JSONDecodeError, TypeError):
            return result
    if not isinstance(frames, list):
        return result
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        for kind in result:
            values = frame.get(kind, [])
            if isinstance(values, list):
                for value in values:
                    if value:
                        result[kind].add(value)
    return result


def chapter_create_content(content: list[dict[str, object]] | None, raw_content: str | None) -> list[dict[str, object]]:
    """Apply create DTO priority and raw-content line splitting."""
    if content:
        return content
    if raw_content:
        return [
            {"text": line.strip(), "character": None, "scene": None, "prop": None}
            for line in raw_content.split("\n")
            if line.strip()
        ]
    return []


