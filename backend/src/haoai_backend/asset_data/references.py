"""Exact chapter-reference removal used by category delete operations."""

from __future__ import annotations

import json
from copy import deepcopy

from .domain import AssetKind, ReferenceRemoval


def remove_asset_references(
    content: object,
    asset_kind: AssetKind,
    asset_id: str,
) -> ReferenceRemoval:
    """Remove every exact ID from the requested list on dict frames.

    Invalid JSON and non-list chapter content are intentionally left untouched.
    Non-dict list members and all unrelated frame fields retain their positions
    and values.
    """
    frames = content
    if isinstance(content, str):
        try:
            frames = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return ReferenceRemoval(content=content, changed=False, removed_frames=0)

    if not isinstance(frames, list):
        return ReferenceRemoval(content=content, changed=False, removed_frames=0)

    updated_frames = deepcopy(frames)
    changed = False
    removed_frames = 0
    for frame in updated_frames:
        if not isinstance(frame, dict):
            continue
        identifiers = frame.get(asset_kind)
        if not isinstance(identifiers, list) or asset_id not in identifiers:
            continue
        frame[asset_kind] = [identifier for identifier in identifiers if identifier != asset_id]
        changed = True
        removed_frames += 1

    if not changed:
        return ReferenceRemoval(content=content, changed=False, removed_frames=0)
    return ReferenceRemoval(
        content=json.dumps(updated_frames, ensure_ascii=False),
        changed=True,
        removed_frames=removed_frames,
    )
