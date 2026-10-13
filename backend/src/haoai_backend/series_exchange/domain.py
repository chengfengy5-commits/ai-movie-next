"""Framework-free values and transformations for series exchange."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from haoai_backend.asset_data.naming import dump_aliases, make_key


@dataclass(frozen=True, slots=True)
class FusedAssetRecord:
    id: str
    series_id: str
    member_type: str
    members: str | None
    name: str
    image_url: str | None
    size: str | None
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "series_id": self.series_id,
            "member_type": self.member_type,
            "members": self.members,
            "name": self.name,
            "image_url": self.image_url,
            "size": self.size,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class ImportPlan:
    series: Mapping[str, Any]
    characters: tuple[Mapping[str, Any], ...]
    scenes: tuple[Mapping[str, Any], ...]
    props: tuple[Mapping[str, Any], ...]
    chapters: tuple[Mapping[str, Any], ...]
    storyboard_assets: tuple[Mapping[str, Any], ...]


class InvalidSeriesExport(ValueError):
    """The version-one JSON shape cannot be imported safely."""


def build_export_payload(
    *,
    series: Mapping[str, Any],
    chapters: Sequence[Mapping[str, Any]],
    characters: Sequence[Mapping[str, Any]],
    scenes: Sequence[Mapping[str, Any]],
    props: Sequence[Mapping[str, Any]],
    storyboard_assets: Sequence[Mapping[str, Any]],
    exported_at: datetime,
) -> dict[str, Any]:
    """Build the exact version-one export projection from SQL row mappings."""
    chapter_indexes = {row["id"]: index for index, row in enumerate(chapters)}
    chapter_titles = {row["id"]: row["title"] for row in chapters}

    return {
        "version": 1,
        "exported_at": exported_at.isoformat(),
        "series": {
            "name": series["name"],
            "description": series.get("description") or "",
        },
        "chapters": [
            {
                "title": chapter["title"],
                "frames": _parse_content(chapter.get("content")),
            }
            for chapter in chapters
        ],
        "characters": [
            {
                "id": row["id"],
                "name": row["name"],
                "gender": row.get("gender") or "",
                "age": row.get("age") or "",
                "role": row.get("role") or "",
                "appearance": row.get("appearance") or "",
                "description": row.get("description") or "",
                "image_url": row.get("image_url") or "",
                "audio_url": row.get("audio_url") or "",
            }
            for row in characters
        ],
        "scenes": [
            {
                "id": row["id"],
                "title": row.get("title") or row.get("name") or "",
                "description": row.get("description") or "",
                "image_url": row.get("image_url") or "",
            }
            for row in scenes
        ],
        "props": [
            {
                "id": row["id"],
                "name": row["name"] or "",
                "description": row.get("description") or "",
                "image_url": row.get("image_url") or "",
            }
            for row in props
        ],
        "storyboard_assets": [
            {
                "id": row["id"],
                "name": row.get("name") or "",
                "description": row.get("description") or "",
                "image_url": row.get("image_url") or "",
                "frame_index": row["frame_index"],
                "chapter_index": chapter_indexes.get(row["chapter_id"], 0),
                "chapter_title": chapter_titles.get(row["chapter_id"], ""),
            }
            for row in storyboard_assets
        ],
    }


def build_import_plan(
    *,
    data: Mapping[str, Any],
    user_id: str,
    requested_name: str | None,
    requested_description: str | None,
    team_id: str | None,
    claim: bool,
    now: datetime,
    new_id: Callable[[], str],
) -> ImportPlan:
    """Prepare remapped insert rows while retaining arbitrary frame JSON fields."""
    imported_series = data.get("series", {})
    if imported_series is None:
        imported_series = {}
    if not isinstance(imported_series, Mapping):
        raise InvalidSeriesExport("导出数据格式错误")

    characters = _mapping_items(data, "characters")
    scenes = _mapping_items(data, "scenes")
    props = _mapping_items(data, "props")
    chapters_data = _mapping_items(data, "chapters")
    exported_storyboards = _mapping_items(data, "storyboard_assets")

    series_id = new_id()
    series_row = {
        "id": series_id,
        "user_id": user_id,
        "name": requested_name or imported_series.get("name", "导入的剧集"),
        "description": requested_description or imported_series.get("description", ""),
        "image_url": None,
        "style_prompt_id": None,
        "team_id": team_id,
        "claimed_by": user_id if claim else None,
        "claimed_at": now if claim else None,
        "created_at": now,
        "updated_at": now,
    }

    character_ids: dict[Any, str] = {}
    character_rows: list[Mapping[str, Any]] = []
    for item in characters:
        new_character_id = new_id()
        old_id = item.get("id", item.get("name", ""))
        character_ids[old_id] = new_character_id
        character_rows.append(
            {
                "id": new_character_id,
                "series_id": series_id,
                "name": item.get("name", ""),
                "gender": item.get("gender", ""),
                "age": item.get("age", ""),
                "role": item.get("role", ""),
                "appearance": item.get("appearance", ""),
                "description": item.get("description", ""),
                "image_url": item.get("image_url", ""),
                "audio_url": item.get("audio_url", ""),
                "aliases": dump_aliases([]),
                "canonical_key": make_key("character", item.get("name", "")),
                "created_at": now,
                "updated_at": now,
            }
        )

    scene_ids: dict[Any, str] = {}
    scene_rows: list[Mapping[str, Any]] = []
    for item in scenes:
        new_scene_id = new_id()
        old_id = item.get("id", item.get("title", item.get("name", "")))
        scene_ids[old_id] = new_scene_id
        scene_rows.append(
            {
                "id": new_scene_id,
                "series_id": series_id,
                "title": item.get("title", item.get("name", "")),
                "description": item.get("description", ""),
                "image_url": item.get("image_url", ""),
                "aliases": dump_aliases([]),
                "canonical_key": make_key(
                    "scene", item.get("title", item.get("name", ""))
                ),
                "created_at": now,
                "updated_at": now,
            }
        )

    prop_ids: dict[Any, str] = {}
    prop_rows: list[Mapping[str, Any]] = []
    for item in props:
        new_prop_id = new_id()
        old_id = item.get("id", item.get("name", ""))
        prop_ids[old_id] = new_prop_id
        prop_rows.append(
            {
                "id": new_prop_id,
                "series_id": series_id,
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "image_url": item.get("image_url", ""),
                "aliases": dump_aliases([]),
                "canonical_key": make_key("prop", item.get("name", "")),
                "created_at": now,
                "updated_at": now,
            }
        )

    storyboard_groups, match_by_title = _group_storyboards(exported_storyboards)
    chapter_rows: list[Mapping[str, Any]] = []
    storyboard_rows: list[Mapping[str, Any]] = []
    chapter_count = len(chapters_data)

    for chapter_index, chapter in enumerate(chapters_data):
        frames = chapter.get("frames", [])
        if not isinstance(frames, list) or any(
            not isinstance(frame, dict) for frame in frames
        ):
            raise InvalidSeriesExport("导出数据格式错误")

        chapter_id = new_id()
        title = chapter.get("title", f"章节 {chapter_index + 1}")
        group_key = (
            chapter.get("title", "") if match_by_title else chapter_index
        )
        chapter_storyboards = sorted(
            storyboard_groups.get(group_key, []),
            key=lambda item: item.get("frame_index", 0),
        )

        remapped_frames: list[dict[str, Any]] = []
        for frame_index, original_frame in enumerate(frames):
            frame = deepcopy(original_frame)
            storyboard = (
                chapter_storyboards[frame_index]
                if frame_index < len(chapter_storyboards)
                else None
            )
            storyboard_id = new_id()
            if storyboard is None:
                storyboard_rows.append(
                    {
                        "id": storyboard_id,
                        "series_id": series_id,
                        "chapter_id": chapter_id,
                        "frame_index": frame_index,
                        "name": f"分镜 {frame_index + 1}",
                        "description": frame.get("text", ""),
                        "image_url": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            else:
                storyboard_rows.append(
                    {
                        "id": storyboard_id,
                        "series_id": series_id,
                        "chapter_id": chapter_id,
                        "frame_index": frame_index,
                        "name": storyboard.get("name", f"分镜 {frame_index + 1}"),
                        "description": storyboard.get(
                            "description", frame.get("text", "")
                        ),
                        "image_url": storyboard.get("image_url", ""),
                        "created_at": now,
                        "updated_at": now,
                    }
                )

            frame["storyboard"] = [storyboard_id]
            _remap_refs(frame, "character", character_ids)
            _remap_refs(frame, "scene", scene_ids)
            _remap_refs(frame, "prop", prop_ids)
            remapped_frames.append(frame)

        chapter_rows.append(
            {
                "id": chapter_id,
                "series_id": series_id,
                "title": title,
                "content": json.dumps(remapped_frames),
                "order": chapter_count - chapter_index,
                "created_at": now,
                "updated_at": now,
            }
        )

    return ImportPlan(
        series=series_row,
        characters=tuple(character_rows),
        scenes=tuple(scene_rows),
        props=tuple(prop_rows),
        chapters=tuple(chapter_rows),
        storyboard_assets=tuple(storyboard_rows),
    )


def _parse_content(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return []
    return content or []


def _mapping_items(data: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise InvalidSeriesExport("导出数据格式错误")
    return value


def _group_storyboards(
    storyboards: Sequence[Mapping[str, Any]],
) -> tuple[dict[Any, list[Mapping[str, Any]]], bool]:
    match_by_title = bool(
        storyboards and storyboards[0].get("chapter_index") is None
    )
    groups: dict[Any, list[Mapping[str, Any]]] = {}
    for storyboard in storyboards:
        key = (
            storyboard.get("chapter_title", "")
            if match_by_title
            else storyboard.get("chapter_index", 0)
        )
        groups.setdefault(key, []).append(storyboard)
    return groups, match_by_title


def _remap_refs(frame: dict[str, Any], field: str, id_map: Mapping[Any, str]) -> None:
    references = frame.get(field)
    if isinstance(references, list):
        frame[field] = [
            id_map.get(str(reference), reference) for reference in references
        ]
