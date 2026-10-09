"""Pure legacy storyboard reference and chat-index mapping rules."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def first_storyboard_id(frame: object) -> object | None:
    if not isinstance(frame, Mapping):
        return None
    refs = frame.get("storyboard", [])
    return refs[0] if isinstance(refs, list) and refs else None


def chat_index_mapping(
    old_frames: list[Any], new_frames: list[Any]
) -> dict[int, int]:
    old_by_id: dict[object, int] = {}
    for index, frame in enumerate(old_frames):
        asset_id = first_storyboard_id(frame)
        if asset_id:
            old_by_id[asset_id] = index
    result: dict[int, int] = {}
    for new_index, frame in enumerate(new_frames):
        asset_id = first_storyboard_id(frame)
        if asset_id:
            old_index = old_by_id.get(asset_id)
            if old_index is not None:
                result[old_index] = new_index
    return result


def asset_refs_from_frames(frames: list[Any]) -> set[object]:
    refs: set[object] = set()
    for frame in frames:
        asset_id = first_storyboard_id(frame)
        if asset_id:
            refs.add(asset_id)
    return refs
