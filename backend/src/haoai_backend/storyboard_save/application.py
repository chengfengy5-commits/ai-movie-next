"""Application workflows for legacy storyboard filter-and-save stages."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import uuid4

from haoai_backend.asset_data import naming
from haoai_backend.shared.identity import TrustedActor

from .domain import (
    AssetKind,
    AssetRecord,
    ChapterRecord,
    appearance_names,
    appearance_row_to_frame,
    candidate_name,
    fuzzy_match_name,
    make_frame_map,
    match_candidate_name,
    normalize_asset_name,
    parse_chapter_frames,
    resolve_target_frames,
    unique_names,
)
from .errors import StoryboardSaveChapterNotFound, StoryboardSaveForeignAssetReference
from .ports import StoryboardSaveStore

logger = logging.getLogger(__name__)

_CHARACTER_EMPTY_APPEARANCES = (
    "AI 没有判定出任何出场的角色，本次未保存任何资产。请重试；若持续出现，请检查该章节分镜内容与所选提示词。"
)
_CHARACTER_NO_MATCH = (
    "AI 判定出的角色与本次提取清单对不上，本次未保存任何资产。请重试；若持续出现，请检查该章节分镜内容与所选提示词。"
)
_SCENE_EMPTY_APPEARANCES = (
    "AI 没有判定出任何出场的场景，本次未保存任何资产。请重试；若持续出现，请检查该章节分镜内容与所选提示词。"
)
_PROP_EMPTY_APPEARANCES = (
    "AI 没有判定出任何出场的道具，本次未保存任何资产。请重试；若持续出现，请检查该章节分镜内容与所选提示词。"
)


def save_characters_stream(
    store: StoryboardSaveStore,
    actor: TrustedActor,
    chapter_id: str,
    merged_characters: Sequence[Mapping[str, Any]],
    appearances: Sequence[object],
    *,
    dry_run: bool = False,
    target_frames: Sequence[object] | None = None,
):
    """Yield legacy character-save events while retaining its commit phases."""
    try:
        yield {"type": "status", "message": "正在验证章节..."}
        chapter = store.load_chapter(chapter_id)
        if chapter is None:
            yield {"type": "error", "message": "章节不存在"}
            return
        _reject_foreign_asset_references(
            store, "character", merged_characters, chapter.series_id
        )
        yield {"type": "status", "message": f"找到章节: {chapter.title}"}
        yield {"type": "status", "message": "正在收集实际出场的角色..."}

        name_lookup: dict[str, str] = {}
        for character in merged_characters:
            name = candidate_name(character, "character")
            if not name:
                continue
            name_lookup.setdefault(normalize_asset_name(name), name)
            for alias in naming.load_aliases(character.get("aliases")):
                name_lookup.setdefault(normalize_asset_name(alias), name)
            original_name = str(character.get("original_name") or "").strip()
            if original_name:
                name_lookup.setdefault(normalize_asset_name(original_name), name)

        appeared_names: set[str] = set()
        for entry in appearances:
            if not isinstance(entry, list):
                continue
            for raw_name in entry:
                if isinstance(raw_name, str) and raw_name.strip():
                    official = name_lookup.get(normalize_asset_name(raw_name))
                    appeared_names.add(official or raw_name.strip())

        yield {"type": "status", "message": f"发现 {len(appeared_names)} 个出场角色"}
        if merged_characters and not appeared_names:
            yield {"type": "error", "message": _CHARACTER_EMPTY_APPEARANCES}
            return
        yield {"type": "status", "message": "正在分类角色..."}

        appeared_existing = []
        appeared_new = []
        filtered_out = []
        for character in merged_characters:
            name = character.get("name", "")
            if name in appeared_names:
                if character.get("is_existing", False):
                    appeared_existing.append(character)
                else:
                    appeared_new.append(character)
            else:
                filtered_out.append(character)

        extracted_count = sum(1 for item in merged_characters if not item.get("is_backfill"))
        adopted_count = len(appeared_existing) + len(appeared_new)
        dropped_items = [item for item in filtered_out if not item.get("is_backfill")]
        dropped_names = [str(item.get("name") or "") for item in dropped_items if item.get("name")]
        dropped_hint = ""
        if dropped_names:
            preview = "、".join(dropped_names[:5])
            dropped_hint = f"，未采用 {len(dropped_names)} 个（{preview}{'…' if len(dropped_names) > 5 else ''}）"

        yield {
            "type": "status",
            "message": (
                f"本次提取 {extracted_count} 个角色，判定出场 {adopted_count} 个"
                f"（复用已有 {len(appeared_existing)}，新建 {len(appeared_new)}）{dropped_hint}"
            ),
        }
        if merged_characters and not appeared_existing and not appeared_new:
            yield {"type": "error", "message": _CHARACTER_NO_MATCH}
            return

        row_to_frame = appearance_row_to_frame(target_frames)
        frame_candidates: dict[int, list[str]] = {}
        for row, entry in enumerate(appearances or []):
            if entry is None:
                continue
            names = entry if isinstance(entry, list) else ([entry] if isinstance(entry, str) else [])
            found: list[str] = []
            for raw_name in names:
                if not isinstance(raw_name, str) or not raw_name.strip():
                    continue
                matched = name_lookup.get(normalize_asset_name(raw_name.strip())) or raw_name.strip()
                if matched and matched not in found:
                    found.append(matched)
            frame_candidates[row_to_frame.get(row, row)] = found

        plan_items = []
        for character in merged_characters:
            name = candidate_name(character, "character")
            if not name or character.get("is_backfill"):
                continue
            is_existing = bool(character.get("is_existing", False))
            appeared = name in appeared_names
            plan_items.append({
                "name": name,
                "status": ("reuse" if is_existing else "create") if appeared else "drop",
                "reason": "" if appeared else "AI 判定该角色未在任何分镜出场",
                "existing_id": character.get("existing_id") if appeared and is_existing else None,
                "existing_name": name if appeared and is_existing else None,
                "frames": [
                    index + 1
                    for index, bound_names in sorted(frame_candidates.items())
                    if name in bound_names
                ] if appeared else [],
                "is_backfill": False,
                "is_existing": is_existing,
                "gender": character.get("gender") or "",
                "age": character.get("age") or "",
                "role": character.get("role") or "",
                "appearance": character.get("appearance") or "",
                "voice_ref": character.get("voice_ref") or "",
                "aliases": character.get("aliases") or [],
            })

        frame_map = make_frame_map(frame_candidates)
        if dry_run:
            plan = {"asset_type": "character", "items": plan_items, "frame_map": frame_map}
            yield {"type": "plan", **plan}
            yield {
                "type": "complete",
                "success": True,
                "dry_run": True,
                "plan": plan,
                "message": (
                    f"已生成待确认清单：{len(plan_items)} 条候选"
                    f"（复用已有 {len(appeared_existing)}，拟新建 {len(appeared_new)}"
                    + (f"，未采用 {len(dropped_names)}" if dropped_names else "") + "）"
                ),
                "total_count": extracted_count,
                "extracted_count": extracted_count,
                "adopted_count": adopted_count,
                "dropped_count": len(dropped_names),
                "dropped_names": dropped_names,
                "candidate_count": len(merged_characters),
            }
            return

        series_id = chapter.series_id
        yield {"type": "status", "message": "正在保存新角色数据..."}
        character_name_to_id: dict[str, object] = {}
        for character in appeared_existing:
            character_name_to_id[character.get("name", "")] = character.get("existing_id")
        for index, character in enumerate(appeared_new):
            name = str(character.get("name") or "")
            asset_id = str(uuid4())
            store.insert_asset("character", asset_id, series_id, character)
            character_name_to_id[character.get("name", "")] = asset_id
            if (index + 1) % 5 == 0:
                yield {"type": "status", "message": f"已保存 {index + 1}/{len(appeared_new)} 个新角色..."}
        store.commit()

        for character in merged_characters:
            original_name = str(character.get("original_name") or "").strip()
            name = str(character.get("name") or "").strip()
            if not original_name or not name or original_name == name:
                continue
            asset_id = character_name_to_id.get(name)
            if not asset_id:
                continue
            row = store.find_asset("character", str(asset_id), series_id)
            if row is not None:
                aliases = naming.append_alias(row.aliases, original_name, name)
                if aliases:
                    store.update_aliases("character", row.id, series_id, aliases)
        store.commit()

        yield {"type": "status", "message": "正在更新分镜角色关联..."}
        try:
            current_chapter = store.load_chapter(chapter_id)
            if current_chapter is None:
                raise StoryboardSaveChapterNotFound()
            frames = parse_chapter_frames(current_chapter.content)
            for appearance_index, frame_index in resolve_target_frames(frames, target_frames):
                frame = frames[frame_index]
                if appearance_index < len(appearances):
                    frame_names = appearances[appearance_index]
                    if frame_names is None or not isinstance(frame_names, list):
                        continue
                    frame_ids = []
                    for raw_name in frame_names:
                        if not raw_name or not isinstance(raw_name, str):
                            continue
                        asset_id = character_name_to_id.get(
                            name_lookup.get(normalize_asset_name(raw_name)) or raw_name.strip()
                        )
                        if asset_id:
                            frame_ids.append(asset_id)
                    frame["character"] = frame_ids
            encoded = json.dumps(frames, ensure_ascii=False)
            store.update_chapter_content(current_chapter, actor, encoded)
            store.commit()
        except Exception:
            logger.exception("分镜角色关联保存失败")
            store.rollback()
            yield {"type": "error", "message": "更新分镜角色关联失败"}
            return

        yield {"type": "status", "message": "正在清理未被引用的角色..."}
        cleaned_up_count = _cleanup_unreferenced_characters(store, series_id)
        yield {
            "type": "complete",
            "success": True,
            "message": (
                f"成功保存 {len(appeared_new)} 个新角色，复用 {len(appeared_existing)} 个已有角色；"
                f"本次提取 {extracted_count} 个，判定出场 {adopted_count} 个{dropped_hint}，"
                f"清理了 {cleaned_up_count} 个未被引用的角色"
            ),
            "saved_new_characters": list(appeared_new),
            "reused_existing_count": len(appeared_existing),
            "filtered_out_count": len(filtered_out),
            "total_count": extracted_count,
            "extracted_count": extracted_count,
            "adopted_count": adopted_count,
            "dropped_count": len(dropped_names),
            "dropped_names": dropped_names,
            "filtered_out_characters": list(filtered_out),
            "cleaned_up_count": cleaned_up_count,
        }
    except Exception as exc:
        logger.exception("流式第三阶段角色保存异常")
        store.rollback()
        yield {"type": "error", "message": str(exc)}


def save_scenes_stream(
    store: StoryboardSaveStore,
    actor: TrustedActor,
    chapter_id: str,
    scenes: Sequence[Mapping[str, Any]],
    appearances: Sequence[object],
    *,
    dry_run: bool = False,
    target_frames: Sequence[object] | None = None,
):
    yield from _save_named_assets_stream(
        "scene", store, actor, chapter_id, scenes, appearances,
        dry_run=dry_run, target_frames=target_frames,
    )


def save_props_stream(
    store: StoryboardSaveStore,
    actor: TrustedActor,
    chapter_id: str,
    props: Sequence[Mapping[str, Any]],
    appearances: Sequence[object],
    *,
    dry_run: bool = False,
    target_frames: Sequence[object] | None = None,
):
    yield from _save_named_assets_stream(
        "prop", store, actor, chapter_id, props, appearances,
        dry_run=dry_run, target_frames=target_frames,
    )


def _save_named_assets_stream(
    kind: Literal["scene", "prop"],
    store: StoryboardSaveStore,
    actor: TrustedActor,
    chapter_id: str,
    candidates: Sequence[Mapping[str, Any]],
    appearances: Sequence[object],
    *,
    dry_run: bool,
    target_frames: Sequence[object] | None,
):
    noun = "场景" if kind == "scene" else "道具"
    asset_type = kind
    name_field = "title" if kind == "scene" else "name"
    route_kind = "scene" if kind == "scene" else "prop"
    empty_appearances = _SCENE_EMPTY_APPEARANCES if kind == "scene" else _PROP_EMPTY_APPEARANCES
    not_adopted_reason = f"AI 判定该{noun}未在任何分镜出场"
    try:
        yield {"type": "status", "message": "正在验证章节..."}
        chapter = store.load_chapter(chapter_id)
        if chapter is None:
            yield {"type": "error", "message": "章节不存在"}
            return
        series_id = chapter.series_id
        _reject_foreign_asset_references(store, route_kind, candidates, series_id)
        yield {"type": "status", "message": f"找到章节: {chapter.title}"}

        official_names = unique_names(candidates, asset_type)
        own_names = [
            name for candidate in candidates
            if not candidate.get("is_backfill")
            for name in [candidate_name(candidate, asset_type)]
            if name
        ]
        own_names = list(dict.fromkeys(own_names))

        alias_map: dict[str, list[str]] = {}
        for candidate in candidates:
            name = candidate_name(candidate, asset_type)
            if not name:
                continue
            aliases = alias_map.setdefault(name, [])
            for alias in naming.load_aliases(candidate.get("aliases")):
                if alias not in aliases:
                    aliases.append(alias)
            original_name = str(candidate.get("original_name") or "").strip()
            if original_name and original_name != name and original_name not in aliases:
                aliases.append(original_name)

        raw_appearance_names: list[str] = []
        for entry in appearances or []:
            for raw_name in appearance_names(entry):
                if isinstance(raw_name, str) and raw_name.strip():
                    raw_appearance_names.append(raw_name.strip())

        appeared_names: set[str] = set()
        for raw_name in raw_appearance_names:
            matched = match_candidate_name(
                raw_name, official_names, alias_map=alias_map, kind=asset_type
            )
            if matched:
                appeared_names.add(matched)

        adopted_names = [name for name in own_names if name in appeared_names]
        dropped_names = [name for name in own_names if name not in appeared_names]
        dropped_hint = ""
        if dropped_names:
            preview = "、".join(dropped_names[:5])
            dropped_hint = f"；未采用 {len(dropped_names)} 个（{preview}{'…' if len(dropped_names) > 5 else ''}）"
        yield {
            "type": "status",
            "message": f"本次提取 {len(own_names)} 个{noun}，判定出场 {len(adopted_names)} 个{dropped_hint}",
        }
        if official_names and not appeared_names:
            yield {"type": "error", "message": empty_appearances}
            return

        existing_assets = store.list_assets(route_kind, series_id)
        existing_by_name = {row.name: row for row in existing_assets}

        def resolve_id(name: str, existing_id: object = None) -> object | None:
            if existing_id:
                return existing_id
            exact = existing_by_name.get(name)
            if exact is not None:
                return exact.id
            normalized = normalize_asset_name(name)
            for row in existing_assets:
                if normalize_asset_name(row.name) == normalized:
                    return row.id
            matched_name = fuzzy_match_name(name, [row.name or "" for row in existing_assets])
            if matched_name:
                for row in existing_assets:
                    if row.name == matched_name:
                        return row.id
            return None

        row_to_frame = appearance_row_to_frame(target_frames)
        frame_candidates: dict[int, list[str]] = {}
        for row, entry in enumerate(appearances or []):
            if entry is None:
                continue
            matched_names: list[str] = []
            for raw_name in appearance_names(entry):
                if not isinstance(raw_name, str) or not raw_name.strip():
                    continue
                matched = match_candidate_name(
                    raw_name.strip(), official_names, alias_map=alias_map, kind=asset_type
                )
                if matched and matched not in matched_names:
                    matched_names.append(matched)
            frame_candidates[row_to_frame.get(row, row)] = matched_names

        frame_bound_name: dict[int, str] = {}
        if kind == "scene":
            for frame_index, frame_names in frame_candidates.items():
                for name in frame_names:
                    if name in appeared_names:
                        frame_bound_name[frame_index] = name
                        break

        def frames_of(name: str) -> list[int]:
            if kind == "scene":
                return [
                    frame_index + 1
                    for frame_index, bound_name in frame_bound_name.items()
                    if bound_name == name
                ]
            return [
                frame_index + 1
                for frame_index, names in sorted(frame_candidates.items())
                if name in names
            ]

        new_assets: list[Mapping[str, Any]] = []
        name_to_id: dict[str, object] = {}
        plan_items: list[dict[str, Any]] = []
        for candidate in candidates:
            name = str(candidate.get(name_field) or "").strip()
            if not name:
                continue
            is_backfill = bool(candidate.get("is_backfill"))
            if name not in appeared_names:
                if not is_backfill:
                    plan_items.append({
                        "name": name,
                        "description": candidate.get("description", "") or "",
                        "status": "drop",
                        "reason": not_adopted_reason,
                        "frames": [],
                        "is_backfill": False,
                    })
                continue
            existing_id = resolve_id(name, candidate.get("existing_id"))
            existing_name = None
            if existing_id:
                name_to_id[name] = existing_id
                row = next((item for item in existing_assets if item.id == existing_id), None)
                existing_name = row.name if row else None
            else:
                new_assets.append(candidate)
            if not is_backfill:
                plan_items.append({
                    "name": name,
                    "description": candidate.get("description", "") or "",
                    "status": "reuse" if existing_id else "create",
                    "reason": "",
                    "existing_id": existing_id,
                    "existing_name": existing_name,
                    "frames": frames_of(name),
                    "is_backfill": False,
                })

        yield {
            "type": "status",
            "message": f"复用 {len(name_to_id)} 个已有{noun}，新增 {len(new_assets)} 个新{noun}",
        }
        frame_map = make_frame_map(frame_candidates)
        if dry_run:
            plan = {"asset_type": asset_type, "items": plan_items, "frame_map": frame_map}
            yield {"type": "plan", **plan}
            yield {
                "type": "complete",
                "success": True,
                "dry_run": True,
                "plan": plan,
                "message": (
                    f"已生成待确认清单：{len(plan_items)} 条候选"
                    f"（复用已有 {len(name_to_id)}，拟新建 {len(new_assets)}"
                    + (f"，未采用 {len(dropped_names)}" if dropped_names else "") + "）"
                ),
                "total_count": len(own_names),
                "extracted_count": len(own_names),
                "adopted_count": len(adopted_names),
                "dropped_count": len(dropped_names),
                "dropped_names": dropped_names,
                "candidate_count": len(candidates),
            }
            return

        created_count = 0
        for candidate in new_assets:
            name = candidate_name(candidate, asset_type)
            if not name:
                continue
            existing_id = resolve_id(name)
            if existing_id is not None:
                name_to_id[name] = existing_id
                continue
            asset_id = str(uuid4())
            store.insert_asset(asset_type, asset_id, series_id, candidate)
            created_count += 1
            name_to_id[name] = asset_id
        store.commit()

        for candidate in candidates:
            original_name = str(candidate.get("original_name") or "").strip()
            name = candidate_name(candidate, asset_type)
            if not original_name or not name or original_name == name:
                continue
            asset_id = name_to_id.get(name)
            if not asset_id:
                continue
            row = store.find_asset(asset_type, str(asset_id), series_id)
            if row is not None:
                aliases = naming.append_alias(row.aliases, original_name, name)
                if aliases:
                    store.update_aliases(asset_type, row.id, series_id, aliases)
        store.commit()

        yield {"type": "status", "message": f"正在更新分镜{noun}关联..."}
        unmatched_frames: list[int] = []
        frames_without_scene: list[int] = []
        try:
            current_chapter = store.load_chapter(chapter_id)
            if current_chapter is None:
                raise StoryboardSaveChapterNotFound()
            frames = parse_chapter_frames(current_chapter.content)
            targets = resolve_target_frames(frames, target_frames)
            for appearance_index, frame_index in targets:
                if appearance_index >= len(appearances):
                    continue
                entry = appearances[appearance_index]
                if entry is None:
                    continue
                frame = frames[frame_index]
                names = appearance_names(entry)
                if kind == "scene":
                    if (isinstance(entry, list) and not entry) or (
                        isinstance(entry, str) and not entry.strip()
                    ):
                        frame["scene"] = []
                        continue
                    matched_id = None
                    for raw_name in names:
                        if not isinstance(raw_name, str) or not raw_name.strip():
                            continue
                        matched = match_candidate_name(
                            raw_name.strip(), official_names, alias_map=alias_map, kind=asset_type
                        )
                        asset_id = name_to_id.get(matched) if matched else None
                        if asset_id:
                            matched_id = asset_id
                            break
                    if matched_id:
                        frame["scene"] = [matched_id]
                    else:
                        unmatched_frames.append(frame_index + 1)
                else:
                    frame_ids = []
                    for raw_name in names:
                        if not isinstance(raw_name, str) or not raw_name.strip():
                            continue
                        matched = match_candidate_name(
                            raw_name.strip(), official_names, alias_map=alias_map, kind=asset_type
                        )
                        asset_id = name_to_id.get(matched) if matched else None
                        if asset_id and asset_id not in frame_ids:
                            frame_ids.append(asset_id)
                    frame["prop"] = frame_ids

            if kind == "scene":
                processed_frames = [frame_index for _, frame_index in targets]
                frames_without_scene = [
                    frame_index + 1
                    for frame_index in processed_frames
                    if isinstance(frames[frame_index], dict)
                    and not (frames[frame_index].get("scene") or [])
                ]
            encoded = json.dumps(frames, ensure_ascii=False)
            store.update_chapter_content(current_chapter, actor, encoded)
            store.commit()
        except Exception:
            logger.exception("分镜%s关联保存失败", noun)
            store.rollback()
            yield {"type": "error", "message": f"更新分镜{noun}关联失败"}
            return

        message = (
            f"成功保存 {created_count} 个新{noun}，复用 {len(name_to_id) - created_count} 个已有{noun}；"
            f"本次提取 {len(own_names)} 个，判定出场 {len(adopted_names)} 个"
            + (f"，未采用 {len(dropped_names)} 个" if dropped_names else "")
        )
        if kind == "scene":
            message += (
                f"；{len(unmatched_frames)} 个分镜未匹配到场景，已保留原有绑定"
                if unmatched_frames else ""
            )
            message += (
                f"；注意：{len(frames_without_scene)} 个分镜暂无场景"
                if frames_without_scene else ""
            )
            yield {
                "type": "complete",
                "success": True,
                "message": message,
                "saved_scenes": list(new_assets),
                "reused_count": len(name_to_id) - created_count,
                "created_count": created_count,
                "total_count": len(own_names),
                "extracted_count": len(own_names),
                "adopted_count": len(adopted_names),
                "dropped_count": len(dropped_names),
                "dropped_names": dropped_names,
                "candidate_count": len(candidates),
                "unmatched_frames": unmatched_frames,
                "frames_without_scene": frames_without_scene,
            }
        else:
            yield {
                "type": "complete",
                "success": True,
                "message": message,
                "saved_props": list(new_assets),
                "reused_count": len(name_to_id) - created_count,
                "created_count": created_count,
                "total_count": len(own_names),
                "extracted_count": len(own_names),
                "adopted_count": len(adopted_names),
                "dropped_count": len(dropped_names),
                "dropped_names": dropped_names,
                "candidate_count": len(candidates),
            }
    except Exception as exc:
        logger.exception("流式第三阶段%s保存异常", noun)
        store.rollback()
        yield {"type": "error", "message": str(exc)}


def _reject_foreign_asset_references(
    store: StoryboardSaveStore,
    kind: AssetKind,
    candidates: Sequence[Mapping[str, Any]],
    series_id: str,
) -> None:
    """Reject explicit IDs from another series before any save phase commits."""
    checked_ids: set[str] = set()
    for candidate in candidates:
        raw_asset_id = candidate.get("existing_id")
        if not raw_asset_id:
            continue
        asset_id = str(raw_asset_id)
        if asset_id in checked_ids:
            continue
        checked_ids.add(asset_id)
        referenced_series_id = store.asset_series_id(kind, asset_id)
        if referenced_series_id is not None and referenced_series_id != series_id:
            raise StoryboardSaveForeignAssetReference()


def _cleanup_unreferenced_characters(
    store: StoryboardSaveStore,
    series_id: str,
) -> int:
    try:
        all_characters = store.list_assets("character", series_id)
        chapters = store.list_chapters(series_id)
        referenced_ids: set[object] = set()
        for chapter in chapters:
            try:
                frames = parse_chapter_frames(chapter.content)
                for frame in frames:
                    if not isinstance(frame, dict) or "character" not in frame:
                        continue
                    character_ids = frame.get("character", [])
                    if isinstance(character_ids, list):
                        referenced_ids.update(value for value in character_ids if value)
            except Exception:
                continue

        removable = [
            row.id
            for row in all_characters
            if row.id not in referenced_ids
            and not (row.image_url or row.audio_url or row.voice_ref)
        ]
        if removable:
            store.delete_assets("character", series_id, removable)
            store.commit()
        return len(removable)
    except Exception:
        store.rollback()
        return 0
