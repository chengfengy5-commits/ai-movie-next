"""Deterministic rules for legacy storyboard asset filtering and saving."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

from haoai_backend.asset_data import naming

AssetKind = Literal["character", "scene", "prop"]

NAME_FIELDS: dict[AssetKind, str] = {
    "character": "name",
    "scene": "title",
    "prop": "name",
}


@dataclass(frozen=True, slots=True)
class ChapterRecord:
    id: str
    series_id: str
    title: str
    content: object


@dataclass(frozen=True, slots=True)
class AssetRecord:
    id: str
    series_id: str
    name: str
    aliases: object = None
    canonical_key: str | None = None
    description: str | None = None
    image_url: str | None = None
    audio_url: str | None = None
    voice_ref: str | None = None


def normalize_asset_name(name: object) -> str:
    return naming.normalize_name(name)


def fuzzy_match_name(
    new_name: str,
    existing_names: Sequence[str],
    *,
    threshold: float = 0.9,
) -> str | None:
    """Match only exact normalized values or names at the legacy 0.9 threshold."""
    normalized = normalize_asset_name(new_name)
    if not normalized or not existing_names:
        return None

    best_name: str | None = None
    best_ratio = 0.0
    for existing_name in existing_names:
        normalized_existing = normalize_asset_name(existing_name)
        if not normalized_existing:
            continue
        if normalized == normalized_existing:
            return existing_name
        ratio = SequenceMatcher(None, normalized, normalized_existing).ratio()
        if ratio > best_ratio:
            best_name = existing_name
            best_ratio = ratio
    return best_name if best_ratio >= threshold else None


def match_candidate_name(
    name: str,
    candidates: Sequence[str],
    *,
    alias_map: Mapping[str, Sequence[str]] | None = None,
    kind: AssetKind,
) -> str | None:
    """Align an appearance name to the approved list using the legacy order."""
    normalized = normalize_asset_name(name)
    if not normalized or not candidates:
        return None

    normalized_pairs: list[tuple[str, str]] = []
    for candidate in candidates:
        normalized_candidate = normalize_asset_name(candidate)
        if not normalized_candidate:
            continue
        if normalized_candidate == normalized:
            return candidate
        normalized_pairs.append((normalized_candidate, candidate))

    key_function = {
        "character": naming.character_key,
        "scene": naming.scene_key,
        "prop": naming.prop_key,
    }[kind]
    target_key = key_function(name)
    if target_key:
        for _, candidate in normalized_pairs:
            if key_function(candidate) == target_key:
                return candidate

    if alias_map:
        for _, candidate in normalized_pairs:
            if naming.alias_contains(alias_map.get(candidate), name):
                return candidate

    for normalized_candidate, candidate in normalized_pairs:
        if len(normalized_candidate) >= 2 and (
            normalized_candidate in normalized or normalized in normalized_candidate
        ):
            return candidate

    return fuzzy_match_name(name, [candidate for _, candidate in normalized_pairs])


def appearance_row_to_frame(target_frames: Sequence[object] | None) -> dict[int, int]:
    if not target_frames:
        return {}
    return {
        row: frame_index
        for row, frame_index in enumerate(target_frames)
        if isinstance(frame_index, int) and frame_index >= 0
    }


def resolve_target_frames(
    frames: Sequence[object], target_frames: Sequence[object] | None
) -> list[tuple[int, int]]:
    """Map appearance rows to absolute 0-based frame indices."""
    if not target_frames:
        return [(index, index) for index in range(len(frames))]
    return [
        (row, frame_index)
        for row, frame_index in enumerate(target_frames)
        if isinstance(frame_index, int) and 0 <= frame_index < len(frames)
    ]


def appearance_names(entry: object) -> list[object]:
    if isinstance(entry, list):
        return entry
    if isinstance(entry, str):
        return [entry]
    return []


def parse_chapter_frames(content: object) -> list[Any]:
    if isinstance(content, str):
        return json.loads(content)
    return content or []


def candidate_name(candidate: Mapping[str, Any], kind: AssetKind) -> str:
    return str(candidate.get(NAME_FIELDS[kind]) or "").strip()


def unique_names(candidates: Sequence[Mapping[str, Any]], kind: AssetKind) -> list[str]:
    values: list[str] = []
    for candidate in candidates:
        name = candidate_name(candidate, kind)
        if name and name not in values:
            values.append(name)
    return values


def aliases_for_candidate(candidate: Mapping[str, Any], kind: AssetKind) -> list[str]:
    aliases = naming.load_aliases(candidate.get("aliases"))
    original_name = str(candidate.get("original_name") or "").strip()
    current_name = candidate_name(candidate, kind)
    if original_name and original_name != current_name and original_name not in aliases:
        aliases.append(original_name)
    return aliases


def make_frame_map(frame_candidates: Mapping[int, list[str]]) -> list[dict[str, Any]]:
    return [
        {"frame": frame_index + 1, "candidates": names}
        for frame_index, names in sorted(frame_candidates.items())
    ]
