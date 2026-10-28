"""Strict, storage-independent validation for continuous chapter edits."""

from __future__ import annotations

import json
import math
from typing import Any


class ChapterEditGuardInvalid(ValueError):
    """The submitted source map cannot safely describe the requested edit."""


class ChapterEditGuardConflict(ValueError):
    """The stored chapter is malformed or no longer matches its captured base."""


_EDITABLE_FRAME_FIELDS = frozenset({"text", "character", "scene", "prop", "storyboard"})
_ASSET_FIELDS = frozenset({"character", "scene", "prop"})


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _is_json_value(value: object) -> bool:
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(_is_json_value(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _is_json_value(item) for key, item in value.items())
    return False


def strict_json_equal(left: object, right: object) -> bool:
    """Compare JSON values using JavaScript-compatible number semantics."""
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if type(left) in (int, float) and type(right) in (int, float):
        if type(left) is int and type(right) is int:
            return left == right
        if (type(left) is float and not math.isfinite(left)) or (type(right) is float and not math.isfinite(right)):
            return False
        return left == right
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(strict_json_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(strict_json_equal(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def decode_chapter_content(raw_content: object) -> list[dict[str, Any]] | None:
    if raw_content is None:
        return None
    if not isinstance(raw_content, str):
        raise ChapterEditGuardConflict("章节内容不是可核对的JSON文本")
    try:
        value = json.loads(
            raw_content,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_non_finite,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ChapterEditGuardConflict("章节内容含重复键或无法核对的JSON值") from exc
    if not isinstance(value, list) or not _is_json_value(value):
        raise ChapterEditGuardConflict("章节内容不是可完整核对的JSON分镜列表")
    if any(not isinstance(frame, dict) for frame in value):
        raise ChapterEditGuardConflict("章节包含无法安全编辑的非对象分镜")
    return value


def _storyboard_id(frame: dict[str, Any], *, label: str) -> str | None:
    if "storyboard" not in frame:
        return None
    value = frame["storyboard"]
    if value == []:
        return None
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], str) or value[0] == "":
        raise ChapterEditGuardConflict(f"{label}的分镜素材引用缺失、重复或格式异常")
    return value[0]


def _validate_retained_frame(
    old_frame: dict[str, Any],
    new_frame: dict[str, Any],
    storyboard_id: str | None,
) -> None:
    for key in old_frame.keys() | new_frame.keys():
        if key in _EDITABLE_FRAME_FIELDS:
            continue
        if key not in old_frame or key not in new_frame or not strict_json_equal(old_frame[key], new_frame[key]):
            raise ChapterEditGuardInvalid("保留镜头不得改写原文、媒体或未知字段")

    if not isinstance(new_frame.get("text"), str):
        raise ChapterEditGuardInvalid("分镜文本必须是纯文本")
    requested_storyboard = new_frame.get("storyboard", [])
    expected_storyboard = [] if storyboard_id is None else [storyboard_id]
    if requested_storyboard != expected_storyboard:
        raise ChapterEditGuardInvalid("保留镜头不能更换或伪造分镜素材身份")

    for field in _ASSET_FIELDS:
        original = old_frame.get(field, [])
        requested = new_frame.get(field, [])
        if not isinstance(original, list):
            if not strict_json_equal(original, requested):
                raise ChapterEditGuardInvalid("无法识别的旧素材字段必须原样保留")
            continue
        if any(not isinstance(asset_id, str) or asset_id == "" for asset_id in original):
            if not strict_json_equal(original, requested):
                raise ChapterEditGuardInvalid("无法识别的旧素材引用必须原样保留")
            continue
        if not isinstance(requested, list) or any(not isinstance(asset_id, str) or asset_id == "" for asset_id in requested):
            raise ChapterEditGuardInvalid("新素材绑定必须使用明确的素材编号")


def _validate_new_frame(frame: dict[str, Any]) -> None:
    if set(frame) - _EDITABLE_FRAME_FIELDS - {"preview"}:
        raise ChapterEditGuardInvalid("新镜头不能携带旧原文、媒体或未知字段")
    if not isinstance(frame.get("text"), str):
        raise ChapterEditGuardInvalid("新镜头文本必须是纯文本")
    if frame.get("storyboard", []) != []:
        raise ChapterEditGuardInvalid("新镜头不能伪造分镜素材身份")
    if "preview" in frame and frame["preview"] is not None:
        raise ChapterEditGuardInvalid("新镜头不能携带旧媒体预览")
    for field in _ASSET_FIELDS:
        value = frame.get(field, [])
        if not isinstance(value, list) or any(not isinstance(asset_id, str) or asset_id == "" for asset_id in value):
            raise ChapterEditGuardInvalid("新镜头素材绑定必须使用明确的素材编号")


def validate_frame_sources(
    old_content: list[dict[str, Any]] | None,
    new_content: list[dict[str, Any]],
    frame_sources: list[dict[str, Any]],
    deleted_source_indexes: list[int],
) -> dict[int, int | None]:
    """Validate a total, one-to-one old-to-new map and return chat index targets."""
    old_frames = old_content or []
    if len(frame_sources) != len(new_content):
        raise ChapterEditGuardInvalid("来源映射数量与新镜头数量不一致")

    old_storyboard_ids: dict[int, str | None] = {}
    seen_storyboard_ids: set[str] = set()
    for source_index, frame in enumerate(old_frames):
        storyboard_id = _storyboard_id(frame, label=f"第{source_index + 1}个镜头")
        if storyboard_id is not None and storyboard_id in seen_storyboard_ids:
            raise ChapterEditGuardConflict("原章节存在重复分镜素材身份")
        if storyboard_id is not None:
            seen_storyboard_ids.add(storyboard_id)
        old_storyboard_ids[source_index] = storyboard_id

    retained_sources: set[int] = set()
    old_to_new: dict[int, int | None] = {index: None for index in range(len(old_frames))}
    for new_index, (frame, source) in enumerate(zip(new_content, frame_sources, strict=True)):
        if not isinstance(frame, dict):
            raise ChapterEditGuardInvalid("新章节包含非对象镜头")
        if source.get("kind") == "new":
            _validate_new_frame(frame)
            continue
        if source.get("kind") != "retained":
            raise ChapterEditGuardInvalid("镜头来源类型无效")
        source_index = source.get("source_index")
        if type(source_index) is not int or source_index < 0 or source_index >= len(old_frames):
            raise ChapterEditGuardInvalid("保留镜头来源位置无效")
        if source_index in retained_sources:
            raise ChapterEditGuardInvalid("一个原镜头不能被保留多次")
        storyboard_id = old_storyboard_ids[source_index]
        if source.get("storyboard_id") != storyboard_id:
            raise ChapterEditGuardInvalid("来源映射中的分镜素材身份与原镜头不一致")
        _validate_retained_frame(old_frames[source_index], frame, storyboard_id)
        retained_sources.add(source_index)
        old_to_new[source_index] = new_index

    expected_deleted = set(range(len(old_frames))) - retained_sources
    if set(deleted_source_indexes) != expected_deleted:
        raise ChapterEditGuardInvalid("删除来源必须精确覆盖所有未保留的原镜头")
    return old_to_new
