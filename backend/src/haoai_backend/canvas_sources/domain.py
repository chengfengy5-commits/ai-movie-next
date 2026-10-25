"""Pure identity, JSON, fingerprint, and version rules for Canvas sources."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from haoai_backend.series_data.storyboard import first_storyboard_id

from .errors import CanvasSourceConflict


@dataclass(frozen=True, slots=True)
class SourceRows:
    series_context: Mapping[str, Any]
    chapter: Mapping[str, Any]
    storyboard_assets: tuple[Mapping[str, Any], ...]
    characters: tuple[Mapping[str, Any], ...]
    scenes: tuple[Mapping[str, Any], ...]
    props: tuple[Mapping[str, Any], ...]
    lock: Mapping[str, Any] | None

    @property
    def requires_lock(self) -> bool:
        return bool(self.series_context["team_id"])

    @property
    def version(self) -> str:
        return source_version(
            self.series_context,
            self.chapter,
            self.storyboard_assets,
            self.characters,
            self.scenes,
            self.props,
        )


def decode_frames(content: object, *, allow_null: bool = False) -> list[dict[str, Any]]:
    if content is None and allow_null:
        return []
    if not isinstance(content, str):
        raise CanvasSourceConflict("章节来源不是可编辑的 JSON 分镜数组")
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CanvasSourceConflict("章节来源不是可编辑的 JSON 分镜数组") from exc
    if not isinstance(value, list):
        raise CanvasSourceConflict("章节来源不是可编辑的 JSON 分镜数组")
    if any(not isinstance(frame, dict) for frame in value):
        raise CanvasSourceConflict("章节分镜包含无法安全定位的内容")
    ensure_finite_json(value)
    return value


def encode_frames(frames: list[dict[str, Any]]) -> str:
    ensure_finite_json(frames)
    return json.dumps(frames, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def ensure_finite_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanvasSourceConflict("章节来源包含非有限数字")
        return
    if isinstance(value, list):
        for item in value:
            ensure_finite_json(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise CanvasSourceConflict("章节来源包含无效 JSON 对象键")
        for item in value.values():
            ensure_finite_json(item)
        return
    raise CanvasSourceConflict("章节来源包含无法安全保存的 JSON 值")


def resolve_frame_target(
    frames: Sequence[Mapping[str, Any]],
    storyboard_rows: Sequence[Mapping[str, Any]],
    target: Any,
    *,
    chapter_id: str,
    series_id: str,
) -> tuple[int, dict[str, Any], Mapping[str, Any] | None]:
    index = target.index
    if index >= len(frames):
        raise CanvasSourceConflict("目标分镜位置已变化")
    frame = dict(frames[index])
    references = frame.get("storyboard", [])
    if not isinstance(references, list):
        raise CanvasSourceConflict("目标分镜的故事板身份无法确认")

    if target.storyboard_id is None:
        if any(reference not in (None, "") for reference in references):
            raise CanvasSourceConflict("目标分镜已具有稳定身份")
        return index, frame, None

    if references[:1] != [target.storyboard_id]:
        raise CanvasSourceConflict("目标故事板身份与分镜位置不匹配")
    matches = [
        frame_index
        for frame_index, candidate in enumerate(frames)
        if isinstance(candidate, Mapping)
        and first_storyboard_id(candidate) == target.storyboard_id
    ]
    if matches != [index]:
        raise CanvasSourceConflict("目标故事板身份重复或不唯一")
    assets = [row for row in storyboard_rows if row["id"] == target.storyboard_id]
    if len(assets) != 1:
        raise CanvasSourceConflict("目标故事板素材不存在或身份不唯一")
    asset = assets[0]
    if (
        asset["series_id"] != series_id
        or asset["chapter_id"] != chapter_id
        or asset["frame_index"] != index
    ):
        raise CanvasSourceConflict("目标故事板素材与分镜位置不匹配")
    return index, frame, asset


def referenced_storyboard_ids(
    frames: Sequence[Mapping[str, Any]],
    storyboard_rows: Sequence[Mapping[str, Any]],
    *,
    chapter_id: str,
    series_id: str,
) -> dict[int, str]:
    """Return only uniquely-linked source assets; leave unrelated orphan rows alone."""
    references_by_frame: list[str | None] = []
    for frame in frames:
        references = frame.get("storyboard", [])
        if not isinstance(references, list):
            raise CanvasSourceConflict("故事板身份无法安全确认")
        reference = references[0] if references else None
        if reference in (None, ""):
            if any(value not in (None, "") for value in references):
                raise CanvasSourceConflict("故事板身份无法安全确认")
            references_by_frame.append(None)
            continue
        if not isinstance(reference, str) or not reference.strip():
            raise CanvasSourceConflict("故事板身份无法安全确认")
        references_by_frame.append(reference)

    result: dict[int, str] = {}
    for index, asset_id in enumerate(references_by_frame):
        if asset_id is None:
            continue
        if references_by_frame.count(asset_id) != 1:
            raise CanvasSourceConflict("章节中存在重复的故事板身份")
        matching_rows = [row for row in storyboard_rows if row["id"] == asset_id]
        if len(matching_rows) != 1:
            raise CanvasSourceConflict("故事板素材不存在或身份不唯一")
        asset = matching_rows[0]
        if (
            asset["series_id"] != series_id
            or asset["chapter_id"] != chapter_id
            or asset["frame_index"] != index
        ):
            raise CanvasSourceConflict("故事板素材与分镜位置不匹配")
        result[index] = asset_id
    return result


def source_version(
    series_context: Mapping[str, Any],
    chapter: Mapping[str, Any],
    storyboard_assets: Sequence[Mapping[str, Any]],
    characters: Sequence[Mapping[str, Any]],
    scenes: Sequence[Mapping[str, Any]],
    props: Sequence[Mapping[str, Any]],
) -> str:
    payload = {
        "series": _jsonable(dict(series_context)),
        "chapter": _jsonable(dict(chapter)),
        "storyboard_assets": _sorted_rows(storyboard_assets),
        "characters": _sorted_rows(characters),
        "scenes": _sorted_rows(scenes),
        "props": _sorted_rows(props),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def row_fingerprint(value: object) -> str:
    encoded = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def chapter_canonical_digest(content: object) -> str | None:
    """Hash a strictly parsed chapter frame array without changing read semantics."""
    if not isinstance(content, str):
        return None
    try:
        frames = json.loads(
            content,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
        if not isinstance(frames, list) or any(not isinstance(frame, dict) for frame in frames):
            return None
        ensure_finite_json(frames)
        return row_fingerprint(frames)
    except (CanvasSourceConflict, TypeError, ValueError, OverflowError, RecursionError):
        return None


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _sorted_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (_jsonable(dict(row)) for row in rows),
        key=lambda row: str(row.get("id", "")),
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(timespec="microseconds") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
