"""Pure reference and duplicate grouping rules for series asset maintenance."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any

from haoai_backend.asset_data.naming import normalize_name, split_scene_space_time

ASSET_KINDS = ("character", "scene", "prop")
ASSET_NAME_FIELDS = {"character": "name", "scene": "title", "prop": "name"}


def collect_asset_ids_from_content(content: object) -> dict[str, set[object]]:
    refs: dict[str, set[object]] = {kind: set() for kind in ASSET_KINDS}
    frames = content
    if isinstance(content, str):
        try:
            frames = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return refs
    if not isinstance(frames, list):
        return refs
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        for kind in ASSET_KINDS:
            ids = frame.get(kind, [])
            if not isinstance(ids, list):
                continue
            for asset_id in ids:
                if asset_id:
                    try:
                        refs[kind].add(asset_id)
                    except TypeError:
                        continue
    return refs


def asset_frame_ref_counts(
    chapters: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, object], int]:
    counts: dict[tuple[str, object], int] = {}
    for chapter in chapters:
        content = chapter.get("content")
        frames = content
        if isinstance(content, str):
            try:
                frames = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            for kind in ASSET_KINDS:
                ids = frame.get(kind) or []
                if isinstance(ids, str):
                    ids = [ids]
                if not isinstance(ids, list):
                    continue
                for asset_id in ids:
                    if asset_id:
                        try:
                            key = (kind, asset_id)
                            counts[key] = counts.get(key, 0) + 1
                        except TypeError:
                            continue
    return counts


def redirect_asset_references(
    content: object, asset_kind: str, old_ids: set[str], keep_id: str
) -> tuple[object, int]:
    if asset_kind not in ASSET_KINDS or not old_ids:
        return content, 0
    frames = content
    if isinstance(content, str):
        try:
            frames = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content, 0
    if not isinstance(frames, list):
        return content, 0

    updated_frames = deepcopy(frames)
    affected = 0
    for frame in updated_frames:
        if not isinstance(frame, dict):
            continue
        ids = frame.get(asset_kind) or []
        if isinstance(ids, str):
            ids = [ids]
        if not isinstance(ids, list) or not any(
            isinstance(asset_id, str) and asset_id in old_ids for asset_id in ids
        ):
            continue
        redirected: list[object] = []
        for asset_id in ids:
            target = keep_id if asset_id in old_ids else asset_id
            if target and target not in redirected:
                redirected.append(target)
        frame[asset_kind] = redirected
        affected += 1
    if not affected:
        return content, 0
    return json.dumps(updated_frames, ensure_ascii=False), affected


def redirect_fused_members(
    members_value: object, old_ids: set[str], keep_id: str, keep_name: str
) -> tuple[object, bool]:
    members = members_value
    if isinstance(members_value, str):
        try:
            members = json.loads(members_value)
        except (json.JSONDecodeError, TypeError):
            return members_value, False
    if not isinstance(members, list):
        return members_value, False
    updated = deepcopy(members)
    changed = False
    for member in updated:
        if (
            isinstance(member, dict)
            and member.get("kind") == "asset"
            and member.get("id") in old_ids
        ):
            member["id"] = keep_id
            if keep_name:
                member["name"] = keep_name
            changed = True
    if not changed:
        return members_value, False
    return json.dumps(updated, ensure_ascii=False), True


def pair_key(left: object, right: object) -> tuple[str, str] | None:
    first, second = str(left or ""), str(right or "")
    if not first or not second or first == second:
        return None
    return (first, second) if first < second else (second, first)


def _similarity(left: object, right: object) -> float:
    first, second = normalize_name(left), normalize_name(right)
    if not first or not second:
        return 0.0
    if first == second:
        return 1.0
    if first in second or second in first:
        return 0.9
    return SequenceMatcher(None, first, second).ratio()


def _is_duplicate(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    name_field: str,
    threshold: float,
    scene_prefix_min: int,
) -> bool:
    left_key = left.get("canonical_key") or normalize_name(left.get(name_field, ""))
    right_key = right.get("canonical_key") or normalize_name(right.get(name_field, ""))
    if left_key and left_key == right_key:
        return True
    if _similarity(left.get(name_field, ""), right.get(name_field, "")) >= threshold:
        return True
    if scene_prefix_min:
        left_space, left_time = split_scene_space_time(left.get(name_field, ""))
        right_space, right_time = split_scene_space_time(right.get(name_field, ""))
        common_prefix = 0
        for a, b in zip(left_space, right_space):
            if a != b:
                break
            common_prefix += 1
        if (
            left_space and right_space and left_time == right_time
            and common_prefix >= scene_prefix_min
        ):
            return True
    return False


def find_duplicate_groups(
    rows: Iterable[Mapping[str, Any]],
    name_field: str,
    *,
    threshold: float = 0.82,
    scene_prefix_min: int = 0,
    excluded_pairs: Iterable[tuple[str, str]] = (),
) -> list[list[Mapping[str, Any]]]:
    assets = [row for row in rows if str(row.get(name_field) or "").strip()]
    exclusions = set(excluded_pairs)
    parents = list(range(len(assets)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for left_index in range(len(assets)):
        for right_index in range(left_index + 1, len(assets)):
            if find(left_index) == find(right_index):
                continue
            left_id, right_id = assets[left_index].get("id"), assets[right_index].get("id")
            if exclusions and pair_key(left_id, right_id) in exclusions:
                continue
            if _is_duplicate(
                assets[left_index], assets[right_index], name_field,
                threshold, scene_prefix_min,
            ):
                left_root, right_root = find(left_index), find(right_index)
                if left_root != right_root:
                    parents[right_root] = left_root

    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for index, row in enumerate(assets):
        grouped.setdefault(find(index), []).append(row)

    result: list[list[Mapping[str, Any]]] = []
    for group in grouped.values():
        if len(group) < 2:
            continue
        has_exclusion = any(
            pair_key(group[i].get("id"), group[j].get("id")) in exclusions
            for i in range(len(group))
            for j in range(i + 1, len(group))
        )
        if not has_exclusion:
            result.append(group)
            continue

        subgroups: list[list[Mapping[str, Any]]] = []
        for row in group:
            row_id = row.get("id")
            for subgroup in subgroups:
                if any(pair_key(row_id, other.get("id")) in exclusions for other in subgroup):
                    continue
                if any(
                    _is_duplicate(row, other, name_field, threshold, scene_prefix_min)
                    for other in subgroup
                ):
                    subgroup.append(row)
                    break
            else:
                subgroups.append([row])
        result.extend(subgroup for subgroup in subgroups if len(subgroup) > 1)
    return result
