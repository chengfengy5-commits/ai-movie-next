"""Non-streaming compatibility workflow for legacy character filtering."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from haoai_backend.shared.identity import TrustedActor

from .application import _reject_foreign_asset_references
from .errors import StoryboardSaveChapterNotFound
from .ports import StoryboardSaveStore

logger = logging.getLogger(__name__)


def filter_and_save_characters(
    store: StoryboardSaveStore,
    actor: TrustedActor,
    chapter_id: str,
    characters: Sequence[Mapping[str, Any]],
    appearances: Sequence[object],
) -> dict[str, Any]:
    """Save exact-name matches, then persist frame bindings as a second phase."""
    chapter = store.load_chapter(chapter_id)
    if chapter is None:
        raise StoryboardSaveChapterNotFound()
    store.require_access(actor, chapter.series_id)
    _reject_foreign_asset_references(store, "character", characters, chapter.series_id)

    try:
        appeared_names = {
            name.strip()
            for row in appearances
            if isinstance(row, list)
            for name in row
            if isinstance(name, str) and name.strip()
        }
        appeared_existing: list[Mapping[str, Any]] = []
        appeared_new: list[Mapping[str, Any]] = []
        filtered_out: list[Mapping[str, Any]] = []
        for candidate in characters:
            name = candidate.get("name", "")
            if name in appeared_names:
                target = appeared_existing if candidate.get("is_existing", False) else appeared_new
                target.append(candidate)
            else:
                filtered_out.append(candidate)

        character_name_to_id: dict[object, object] = {}
        for candidate in appeared_existing:
            asset_id = candidate.get("existing_id")
            if not asset_id:
                continue
            existing = store.find_asset("character", str(asset_id), chapter.series_id)
            if existing is not None:
                character_name_to_id[candidate.get("name", "")] = existing.id

        for candidate in appeared_new:
            name = candidate.get("name", "")
            asset_id = str(uuid4())
            store.insert_asset("character", asset_id, chapter.series_id, candidate)
            character_name_to_id[name] = asset_id

        store.commit()
    except Exception as exc:
        logger.exception("非流式第三阶段保存角色失败")
        store.rollback()
        return {
            "success": False,
            "message": f"保存角色失败: {exc}",
            "saved_new_characters": [],
            "reused_existing_count": 0,
            "filtered_out_count": 0,
            "total_count": len(characters),
        }

    kept_binding_frames: list[int] = []
    binding_error: Exception | None = None
    try:
        current_chapter = store.load_chapter(chapter_id)
        if current_chapter is None:
            raise StoryboardSaveChapterNotFound()
        store.require_access(actor, current_chapter.series_id)
        if current_chapter.series_id != chapter.series_id:
            raise RuntimeError("章节所属剧集已变化，无法安全写入角色绑定")
        _reject_foreign_asset_references(
            store, "character", appeared_existing, current_chapter.series_id
        )
        verified_character_ids: dict[object, object] = {}
        for name, asset_id in character_name_to_id.items():
            if not asset_id:
                continue
            existing = store.find_asset(
                "character", str(asset_id), current_chapter.series_id
            )
            if existing is not None:
                verified_character_ids[name] = existing.id
        character_name_to_id = verified_character_ids

        frames = (
            json.loads(current_chapter.content)
            if isinstance(current_chapter.content, str)
            else current_chapter.content or []
        )
        for frame_index, frame in enumerate(frames):
            if frame_index >= len(appearances):
                kept_binding_frames.append(frame_index + 1)
                continue

            frame_character_names = appearances[frame_index]
            if not isinstance(frame_character_names, list):
                kept_binding_frames.append(frame_index + 1)
                continue

            frame_character_ids = []
            for name in frame_character_names:
                if not name or not isinstance(name, str):
                    continue
                character_id = character_name_to_id.get(name.strip())
                if character_id:
                    frame_character_ids.append(character_id)

            if not frame_character_ids and frame_character_names:
                kept_binding_frames.append(frame_index + 1)
                continue
            frame["character"] = frame_character_ids

        content = json.dumps(frames, ensure_ascii=False)
        store.update_chapter_content(current_chapter, actor, content)
        store.commit()
    except Exception as exc:
        logger.exception("非流式角色帧绑定阶段未能确认")
        store.rollback()
        binding_error = exc

    new_count = len(appeared_new)
    reused_count = len(appeared_existing)
    filtered_count = len(filtered_out)
    message = (
        f"成功保存 {new_count} 个新角色，复用 {reused_count} 个已有角色，"
        f"过滤掉 {filtered_count} 个未出场的角色"
    )
    if kept_binding_frames:
        message += f"；{len(kept_binding_frames)} 个分镜未匹配到角色，已保留原有绑定"
    if binding_error is not None:
        message += "；只能确认角色资产提交，分镜角色绑定未确认持久化"

    return {
        "success": True,
        "message": message,
        "saved_new_characters": list(appeared_new),
        "reused_existing_count": reused_count,
        "filtered_out_count": filtered_count,
        "total_count": len(characters),
        "filtered_out_characters": list(filtered_out),
        "kept_binding_frames": kept_binding_frames,
    }
