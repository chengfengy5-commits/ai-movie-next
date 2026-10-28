"""Pure canonicalization and media-source validation for history proofs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlsplit


_CONTROL_OR_WHITESPACE = re.compile(r"[\s\x00-\x1f\x7f]", re.UNICODE)
_AMBIGUOUS_UPLOAD_ENCODING = re.compile(r"%(?:2f|5c|25)", re.IGNORECASE)
_INVALID_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def timestamp_wire(value: object) -> str:
    if not isinstance(value, datetime):
        raise ValueError("history timestamp is not a datetime")
    current = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return current.isoformat().replace("+00:00", "Z")


def parse_chapter_frames(raw_content: object) -> list[Any] | None:
    if not isinstance(raw_content, str):
        return None

    try:
        value = json.loads(
            raw_content,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, list) else None


def storyboard_references(frame: object) -> object:
    return frame.get("storyboard") if isinstance(frame, dict) else None


def first_storyboard_id(frame: object) -> str | None:
    references = storyboard_references(frame)
    if not isinstance(references, list) or not references:
        return None
    first = references[0]
    if not isinstance(first, str) or not first.strip():
        return None
    return first


def safe_generation_media_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or value == ""
        or _CONTROL_OR_WHITESPACE.search(value) is not None
        or "\\" in value
        or "#" in value
    ):
        return False

    if re.match(r"^https?://", value, re.IGNORECASE):
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme.lower() in {"http", "https"}
            and hostname is not None
            and parsed.username is None
            and parsed.password is None
            and parsed.fragment == ""
        )

    if value.startswith("//") or _has_scheme(value) or "?" in value:
        return False
    raw_path = value if value.startswith("/") else f"/{value}"
    if not raw_path.startswith("/uploads/") or _AMBIGUOUS_UPLOAD_ENCODING.search(raw_path):
        return False
    if _INVALID_ESCAPE.search(raw_path):
        return False
    segments = raw_path[1:].split("/")
    if any(segment == "" for segment in segments):
        return False
    for segment in segments:
        try:
            decoded = unquote(segment, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return False
        if decoded in {".", ".."} or "/" in decoded or "\\" in decoded:
            return False
        if _CONTROL_OR_WHITESPACE.search(decoded):
            return False
    return True


def message_digest_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "chapter_id": row["chapter_id"],
        "frame_index": row["frame_index"],
        "asset_type": row["asset_type"],
        "asset_id": row["asset_id"],
        "chat_mode": row["chat_mode"],
        "role": row["role"],
        "content": row["content"],
        "model_name": row["model_name"],
        "created_at": timestamp_wire(row["created_at"]),
    }


def message_wire(row: dict[str, Any]) -> dict[str, Any]:
    payload = message_digest_payload(row)
    return {**payload, "message_digest": canonical_digest(payload)}


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _has_scheme(value: str) -> bool:
    return re.match(r"^[a-z][a-z\d+.-]*:", value, re.IGNORECASE) is not None
