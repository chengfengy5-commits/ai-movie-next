"""Pure projection and complete-set rules for personal rough-cut drafts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .errors import InvalidSource, InvalidUpdate, SourceTooLarge

MAX_ROUGH_CUT_FRAMES = 500
VIDEO_URL_PATTERN = re.compile(r"\.(mp4|webm|mov|avi|mkv|m4v)(?:\?|$)", re.IGNORECASE)
LEGACY_ID_REASON = "缺少唯一有效的稳定分镜 ID，暂不可编排"
MISSING_VIDEO_REASON = "当前分镜没有可用视频"


@dataclass(frozen=True, slots=True)
class ChapterRecord:
    id: str
    series_id: str
    content: Any


@dataclass(frozen=True, slots=True)
class SourceAsset:
    id: str


@dataclass(frozen=True, slots=True)
class SavedDraft:
    revision: int
    frames: Any


@dataclass(frozen=True, slots=True)
class UpdateFrame:
    asset_id: str
    included: bool


@dataclass(frozen=True, slots=True)
class RoughCutUpdate:
    expected_revision: int
    frames: tuple[UpdateFrame, ...]


def parse_chapter_frames(content: Any) -> list[dict[str, Any]]:
    """Mirror the existing route's falsy/string/list interpretation."""
    if not content:
        return []
    try:
        frames = json.loads(content) if isinstance(content, str) else content
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidSource("章节分镜内容无法解析，暂不可使用粗剪") from exc
    if not isinstance(frames, list):
        raise InvalidSource("章节分镜内容格式不正确，暂不可使用粗剪")
    return [frame if isinstance(frame, dict) else {} for frame in frames]


def project_source_frames(
    content_frames: Sequence[Mapping[str, Any]],
    source_asset_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Resolve first storyboard references without position or series fallback."""
    assets = set(source_asset_ids)
    candidate_ids: list[str | None] = []
    counts: dict[str, int] = {}
    for frame in content_frames:
        references = frame.get("storyboard")
        candidate: str | None = None
        if (
            isinstance(references, list)
            and references
            and isinstance(references[0], str)
            and references[0]
        ):
            candidate = references[0]
            counts[candidate] = counts.get(candidate, 0) + 1
        candidate_ids.append(candidate)

    result: list[dict[str, Any]] = []
    for index, (frame, candidate) in enumerate(zip(content_frames, candidate_ids, strict=True)):
        stable_id = (
            candidate
            if candidate is not None and counts[candidate] == 1 and candidate in assets
            else None
        )
        preview = frame.get("preview")
        preview_url = (
            preview
            if isinstance(preview, str) and VIDEO_URL_PATTERN.search(preview)
            else None
        )
        reasons: list[str] = []
        if stable_id is None:
            reasons.append(LEGACY_ID_REASON)
        if preview_url is None:
            reasons.append(MISSING_VIDEO_REASON)
        text = frame.get("text") or frame.get("original_text") or ""
        result.append(
            {
                "asset_id": stable_id,
                "frame_index": index,
                "text": text if isinstance(text, str) else str(text),
                "preview_url": preview_url,
                "missing_reason": "；".join(reasons) if reasons else None,
            }
        )
    return result


def has_invalid_source_identity(source_frames: Sequence[Mapping[str, Any]]) -> bool:
    return any(frame.get("asset_id") is None for frame in source_frames)


def project_snapshot(
    chapter_id: str,
    source_frames: Sequence[Mapping[str, Any]],
    draft: SavedDraft | None,
) -> dict[str, Any]:
    if draft is None:
        return {
            "chapter_id": chapter_id,
            "revision": 0,
            "saved": False,
            "frames": [
                {
                    **frame,
                    "asset_id": frame["asset_id"] or "",
                    "included": frame["asset_id"] is not None,
                    "pending": False,
                }
                for frame in source_frames
            ],
            "removed_asset_ids": [],
        }

    source_by_id = {
        frame["asset_id"]: frame
        for frame in source_frames
        if frame.get("asset_id") is not None
    }
    used_ids: set[str] = set()
    removed_ids: list[str] = []
    result_frames: list[dict[str, Any]] = []
    persisted_frames = draft.frames if isinstance(draft.frames, list) else []

    for saved_frame in persisted_frames:
        if not isinstance(saved_frame, dict):
            continue
        asset_id = saved_frame.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id or asset_id in used_ids:
            continue
        source = source_by_id.get(asset_id)
        if source is None:
            removed_ids.append(asset_id)
            continue
        used_ids.add(asset_id)
        result_frames.append(
            {
                **source,
                "included": saved_frame.get("included") is True,
                "pending": False,
            }
        )

    for frame in source_frames:
        asset_id = frame.get("asset_id")
        if asset_id is None:
            result_frames.append(
                {**frame, "asset_id": "", "included": False, "pending": False}
            )
        elif asset_id not in used_ids:
            used_ids.add(asset_id)
            result_frames.append({**frame, "included": False, "pending": True})

    return {
        "chapter_id": chapter_id,
        "revision": draft.revision,
        "saved": True,
        "frames": result_frames,
        "removed_asset_ids": removed_ids,
    }


def validate_complete_update(
    update: RoughCutUpdate,
    source_frames: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, object], ...]:
    if has_invalid_source_identity(source_frames):
        raise InvalidSource("章节包含缺少唯一稳定 ID 的历史镜头，无法保存粗剪")
    if len(source_frames) > MAX_ROUGH_CUT_FRAMES:
        raise SourceTooLarge()

    current_ids = [str(frame["asset_id"]) for frame in source_frames]
    submitted_ids = [frame.asset_id for frame in update.frames]
    if len(submitted_ids) != len(set(submitted_ids)) or set(submitted_ids) != set(current_ids):
        raise InvalidUpdate("镜头 ID 必须与当前章节全部有效分镜一致，且不可重复")
    return tuple({"asset_id": frame.asset_id, "included": frame.included} for frame in update.frames)
