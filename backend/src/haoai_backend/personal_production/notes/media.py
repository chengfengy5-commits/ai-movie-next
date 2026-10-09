"""Pure chapter media identity and URL-digest projection rules."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Literal, Mapping
from urllib.parse import unquote_plus, urlsplit, urlunsplit

_TEMPORARY_SIGNATURE_PARAMETERS = frozenset({
    "ossaccesskeyid",
    "signature",
    "expires",
    "security-token",
    "awsaccesskeyid",
    "x-oss-signature-version",
    "x-oss-credential",
    "x-oss-date",
    "x-oss-expires",
    "x-oss-security-token",
    "x-oss-signature",
    "x-amz-algorithm",
    "x-amz-credential",
    "x-amz-date",
    "x-amz-expires",
    "x-amz-signedheaders",
    "x-amz-signature",
    "x-amz-security-token",
    "x-amz-content-sha256",
    "x-amz-region-set",
})


@dataclass(frozen=True, slots=True)
class FrameMediaIdentity:
    frame_index: int
    storyboard_asset_id: str | None
    asset_image_identity: str | None
    preview_identity: str | None
    is_valid: bool
    invalid_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ChapterMediaProjection:
    state: Literal["ready", "empty", "unreadable"]
    chapter_id: str | None
    frames: tuple[FrameMediaIdentity, ...]


def normalize_media_identity(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return value

    remaining: list[str] = []
    for parameter in parts.query.split("&") if parts.query else ():
        raw_name = parameter.partition("=")[0]
        try:
            name = unquote_plus(raw_name).casefold()
        except (UnicodeDecodeError, ValueError):
            name = raw_name.casefold()
        if name not in _TEMPORARY_SIGNATURE_PARAMETERS:
            remaining.append(parameter)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(remaining), ""))


def digest_media_identity(identity: str | None) -> str | None:
    if identity is None:
        return None
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def project_chapter_storyboard_media(
    chapter_id: object,
    chapter_content: object,
    storyboard_assets: object,
) -> ChapterMediaProjection:
    if not isinstance(chapter_id, str) or not chapter_id.strip():
        return ChapterMediaProjection("unreadable", None, ())

    frames = chapter_content
    if isinstance(frames, str):
        try:
            frames = json.loads(frames)
        except (json.JSONDecodeError, TypeError):
            return ChapterMediaProjection("unreadable", chapter_id, ())
    if not isinstance(frames, list):
        return ChapterMediaProjection("unreadable", chapter_id, ())
    if not frames:
        return ChapterMediaProjection("empty", chapter_id, ())

    assets = list(storyboard_assets) if isinstance(storyboard_assets, (list, tuple)) else []
    frame_ids: list[str | None] = []
    for frame in frames:
        references = _read_field(frame, "storyboard")
        candidate = references[0] if isinstance(references, list) and references else None
        frame_ids.append(candidate if isinstance(candidate, str) and candidate.strip() else None)
    counts = Counter(frame_id for frame_id in frame_ids if frame_id is not None)

    projected: list[FrameMediaIdentity] = []
    for index, (frame, asset_id) in enumerate(zip(frames, frame_ids, strict=True)):
        references = _read_field(frame, "storyboard")
        if asset_id is None:
            reason = "invalid_storyboard_id" if isinstance(references, list) and references and references[0] is not None else "missing_storyboard_id"
            projected.append(_invalid_frame(index, reason))
            continue
        if counts[asset_id] != 1:
            projected.append(_invalid_frame(index, "duplicate_storyboard_id"))
            continue
        matching = [
            asset for asset in assets
            if _read_field(asset, "chapter_id") == chapter_id
            and _read_field(asset, "id") == asset_id
            and isinstance(_read_field(asset, "id"), str)
        ]
        if not matching:
            projected.append(_invalid_frame(index, "asset_not_in_chapter"))
            continue
        if len(matching) != 1:
            projected.append(_invalid_frame(index, "ambiguous_storyboard_asset"))
            continue
        projected.append(FrameMediaIdentity(
            frame_index=index,
            storyboard_asset_id=asset_id,
            asset_image_identity=normalize_media_identity(_read_field(matching[0], "image_url")),
            preview_identity=normalize_media_identity(_read_field(frame, "preview")),
            is_valid=True,
        ))
    return ChapterMediaProjection("ready", chapter_id, tuple(projected))


def _read_field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _invalid_frame(index: int, reason: str) -> FrameMediaIdentity:
    return FrameMediaIdentity(index, None, None, None, False, reason)
