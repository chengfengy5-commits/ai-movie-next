"""Canonical full-row digests for Canvas asset image compare-and-set."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType


CANVAS_ASSET_TARGET_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "character": (
            "id",
            "series_id",
            "name",
            "aliases",
            "canonical_key",
            "gender",
            "age",
            "role",
            "appearance",
            "description",
            "image_url",
            "audio_url",
            "voice_ref",
        ),
        "scene": (
            "id",
            "series_id",
            "title",
            "description",
            "image_url",
            "aliases",
            "canonical_key",
        ),
        "prop": (
            "id",
            "series_id",
            "name",
            "description",
            "image_url",
            "aliases",
            "canonical_key",
        ),
    }
)


def canvas_asset_target_digest(
    asset_type: str,
    row: Mapping[str, object],
) -> str:
    """Digest the complete mutable asset projection used by Canvas CAS."""

    fields = CANVAS_ASSET_TARGET_FIELDS.get(asset_type)
    if fields is None:
        raise ValueError("only character, scene, and prop images are canvas asset targets")
    value = {name: row.get(name) for name in fields}
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ("CANVAS_ASSET_TARGET_FIELDS", "canvas_asset_target_digest")
