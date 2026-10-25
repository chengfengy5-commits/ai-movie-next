"""Pure validation and canonical identity rules for stored Canvas targets."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import fields

from .types import TargetSnapshot

CANVAS_FRAME_TARGET = "canvas_frame_preview"
CANVAS_ASSET_TARGET = "canvas_asset_image"
CANVAS_TARGET_KINDS = frozenset({CANVAS_FRAME_TARGET, CANVAS_ASSET_TARGET})
CANVAS_ASSET_TYPES = frozenset({"character", "scene", "prop"})
_TARGET_FIELDS = frozenset(field.name for field in fields(TargetSnapshot))
_REQUIRED_TARGET_FIELDS = frozenset({"kind", "chapter_id", "series_id"})
_ID_FIELDS = frozenset(
    {"chapter_id", "series_id", "stable_storyboard_asset_id", "asset_id"}
)
_DIGEST_FIELDS = frozenset(
    {
        "expected_text_digest",
        "content_digest",
        "full_frame_digest",
        "full_chapter_digest",
        "same_series_target_digest",
    }
)
_STRING_FIELDS = frozenset(
    {
        "kind",
        "chapter_id",
        "series_id",
        "stable_storyboard_asset_id",
        "asset_type",
        "asset_id",
        "expected_text_digest",
        "media_identity",
        "content_digest",
        "full_frame_digest",
        "full_chapter_digest",
        "expected_asset_image_url",
        "same_series_target_digest",
    }
)
_INTEGER_FIELDS = frozenset({"compat_frame_index", "notes_revision"})
_HEX = frozenset("0123456789abcdef")
_TARGET_DIGEST_PREFIX = b"haoai-canvas-target-snapshot-v1\x00"


class CanvasTargetParseError(ValueError):
    """A stored original target is absent, malformed, or semantically invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_valid_canvas_identifier(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 36
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or _has_control_characters(value)
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def target_snapshot_from_request_data(request_data: object) -> TargetSnapshot:
    if not isinstance(request_data, str):
        raise CanvasTargetParseError("missing")
    try:
        decoded = json.loads(
            request_data,
            object_pairs_hook=_unique_object_pairs,
            parse_constant=_reject_non_json_number,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CanvasTargetParseError("invalid") from exc
    if type(decoded) is not dict or not _contains_only_finite_numbers(decoded):
        raise CanvasTargetParseError("invalid")
    raw_target = decoded.get("target_snapshot")
    return target_snapshot_from_mapping(raw_target)


def target_snapshot_source(value: object) -> tuple[bool, TargetSnapshot | None, bool]:
    """Read an optional target snapshot while retaining malformed Canvas hints.

    The boolean values are ``(present, parsed, canvas_hint)``. A malformed
    source can be ignored for an ordinary image result, but a malformed source
    cannot silently disappear when another original source binds the result to
    Canvas.
    """

    if value is None:
        return False, None, False
    if isinstance(value, str):
        try:
            value = json.loads(
                value,
                object_pairs_hook=_unique_object_pairs,
                parse_constant=_reject_non_json_number,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return True, None, False
    if type(value) is not dict:
        return True, None, False

    has_target_key = "target_snapshot" in value
    raw_target = value.get("target_snapshot")
    if not has_target_key:
        nested = value.get("snapshot")
        if type(nested) is dict:
            has_target_key = "target_snapshot" in nested
            raw_target = nested.get("target_snapshot")
    if not has_target_key:
        return False, None, False

    canvas_hint = (
        type(raw_target) is dict
        and raw_target.get("kind") in CANVAS_TARGET_KINDS
    )
    if not _contains_only_finite_numbers(value):
        return True, None, canvas_hint
    if (
        type(raw_target) is dict
        and isinstance(raw_target.get("kind"), str)
        and not canvas_hint
    ):
        return True, None, False
    try:
        target = target_snapshot_from_mapping(raw_target)
    except CanvasTargetParseError:
        return True, None, canvas_hint
    return True, target, target.kind in CANVAS_TARGET_KINDS


def target_snapshot_from_mapping(raw_target: object) -> TargetSnapshot:
    if (
        type(raw_target) is not dict
        or not _REQUIRED_TARGET_FIELDS.issubset(raw_target)
        or not frozenset(raw_target).issubset(_TARGET_FIELDS)
    ):
        raise CanvasTargetParseError("invalid")
    target_values = {field: raw_target.get(field) for field in _TARGET_FIELDS}

    for field in _STRING_FIELDS:
        value = target_values[field]
        if value is not None and not isinstance(value, str):
            raise CanvasTargetParseError("invalid")
        if isinstance(value, str) and _has_control_characters(value):
            raise CanvasTargetParseError("invalid")
    for field in _ID_FIELDS:
        value = target_values[field]
        if value is not None and not is_valid_canvas_identifier(value):
            raise CanvasTargetParseError("invalid")
    for field in _DIGEST_FIELDS:
        value = target_values[field]
        if value is not None and not _is_sha256(value):
            raise CanvasTargetParseError("invalid")
    for field in _INTEGER_FIELDS:
        value = target_values[field]
        if value is not None and type(value) is not int:
            raise CanvasTargetParseError("invalid")

    target = TargetSnapshot(**target_values)
    if (
        target.kind not in CANVAS_TARGET_KINDS
        or not is_valid_canvas_identifier(target.chapter_id)
        or not is_valid_canvas_identifier(target.series_id)
        or target.compat_frame_index is not None and target.compat_frame_index < 0
        or target.notes_revision is not None and target.notes_revision < 0
    ):
        raise CanvasTargetParseError("invalid")

    if target.kind == CANVAS_FRAME_TARGET:
        if (
            target.asset_type not in {None, "storyboard"}
            or not _is_sha256(target.full_frame_digest)
            or (
                target.stable_storyboard_asset_id is None
                and not _is_sha256(target.full_chapter_digest)
            )
            or (
                target.same_series_target_digest is not None
                and not _is_sha256(target.same_series_target_digest)
            )
        ):
            raise CanvasTargetParseError("frame")
        if target.stable_storyboard_asset_id is None and (
            target.asset_type is not None
            or target.asset_id is not None
            or type(target.compat_frame_index) is not int
        ):
            raise CanvasTargetParseError("frame_identity")
    else:
        if (
            target.asset_type not in CANVAS_ASSET_TYPES
            or not is_valid_canvas_identifier(target.asset_id)
            or target.stable_storyboard_asset_id is not None
            or target.compat_frame_index is not None
            or not _is_sha256(target.same_series_target_digest)
        ):
            raise CanvasTargetParseError("asset")
    return target


def target_snapshot_mapping(target: TargetSnapshot) -> dict[str, object]:
    return {field.name: getattr(target, field.name) for field in fields(TargetSnapshot)}


def canonical_target_snapshot_digest(target: TargetSnapshot) -> str:
    encoded = json.dumps(
        target_snapshot_mapping(target),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(_TARGET_DIGEST_PREFIX + encoded).hexdigest()


def canvas_target_identity(target: TargetSnapshot) -> str:
    if target.kind == CANVAS_FRAME_TARGET:
        if target.stable_storyboard_asset_id is not None:
            identity = f"storyboard|{target.stable_storyboard_asset_id}"
        elif (
            type(target.compat_frame_index) is int
            and target.compat_frame_index >= 0
            and _is_sha256(target.full_chapter_digest)
        ):
            identity = (
                f"strict-no-id|chapter|{target.full_chapter_digest}"
                f"|index|{target.compat_frame_index}"
            )
        else:
            raise ValueError("Canvas frame target has no canonical identity")
    elif (
        target.kind == CANVAS_ASSET_TARGET
        and target.asset_type in CANVAS_ASSET_TYPES
        and is_valid_canvas_identifier(target.asset_id)
    ):
        identity = f"asset|{target.asset_type}|{target.asset_id}"
    else:
        raise ValueError("target is not a canonical Canvas target")
    if len(identity) > 255:
        raise ValueError("Canvas target identity exceeds its storage limit")
    return identity


def _unique_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def _has_control_characters(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _contains_only_finite_numbers(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_contains_only_finite_numbers(item) for item in value.values())
    if isinstance(value, list):
        return all(_contains_only_finite_numbers(item) for item in value)
    return True


__all__ = (
    "CANVAS_ASSET_TARGET",
    "CANVAS_ASSET_TYPES",
    "CANVAS_FRAME_TARGET",
    "CANVAS_TARGET_KINDS",
    "CanvasTargetParseError",
    "canonical_target_snapshot_digest",
    "canvas_target_identity",
    "is_valid_canvas_identifier",
    "target_snapshot_from_mapping",
    "target_snapshot_from_request_data",
    "target_snapshot_mapping",
    "target_snapshot_source",
)
