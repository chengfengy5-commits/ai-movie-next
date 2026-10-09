"""Pure response projection helpers for series and chapter records."""

from __future__ import annotations

from typing import Any

from .domain import ChapterRecord, SeriesRecord, StoryboardAssetRecord, parse_chapter_frames


def series_detail(
    record: SeriesRecord,
    prompt: dict[str, Any] | None,
    *,
    team_name: str | None,
    owner_name: str | None,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": record.id,
        "user_id": record.user_id,
        "name": record.name,
        "description": record.description,
        "image_url": record.image_url,
        "style_prompt_id": record.style_prompt_id,
        "team_id": record.team_id,
        "team_name": team_name,
        "owner_name": owner_name,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if prompt:
        values.update(
            style_prompt_name=prompt.get("name"),
            style_prompt_owner_name=prompt.get("owner_name"),
            style_prompt=prompt.get("content"),
        )
    return values


def series_list_item(record: dict[str, Any]) -> dict[str, Any]:
    return dict(record)


def chapter_payload(record: ChapterRecord, lock: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": record.id,
        "series_id": record.series_id,
        "title": record.title,
        "content": parse_chapter_frames(record.content),
        "order": record.order,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "lock": lock,
    }


def storyboard_asset_payload(record: StoryboardAssetRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "series_id": record.series_id,
        "chapter_id": record.chapter_id,
        "frame_index": record.frame_index,
        "name": record.name,
        "description": record.description,
        "image_url": record.image_url,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
