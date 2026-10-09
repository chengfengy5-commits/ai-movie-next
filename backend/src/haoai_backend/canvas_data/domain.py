"""Pure values and compatibility rules for shared chapter canvases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


EMPTY_CANVAS_DOCUMENT: dict[str, Any] = {
    "version": 1,
    "viewport": {"x": 0, "y": 0, "zoom": 1},
    "nodes": [],
    "edges": [],
    "notes": [],
}


@dataclass(frozen=True, slots=True)
class ChapterRecord:
    id: str
    series_id: str


@dataclass(frozen=True, slots=True)
class CanvasDocumentRecord:
    id: str
    series_id: str
    chapter_id: str
    version: int
    document_json: object
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


def empty_canvas_document(chapter_id: str) -> dict[str, Any]:
    """Return a fresh copy of the source route's literal empty canvas."""
    return {
        "id": None,
        "chapter_id": chapter_id,
        "version": 1,
        "document_json": {
            "version": 1,
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "nodes": [],
            "edges": [],
            "notes": [],
        },
        "updated_by": None,
        "updated_at": None,
    }


def parse_stored_document(value: object) -> object:
    """Decode stored JSON and preserve the legacy malformed-input fallback."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


def serialize_document(document_json: dict[str, Any]) -> str:
    """Use the legacy JSON encoding without mutating the request object."""
    return json.dumps(document_json, ensure_ascii=False)


def exceeds_document_limit(serialized: str) -> bool:
    """The legacy limit counts Python characters, not encoded bytes."""
    return len(serialized) > 1_000_000


def next_document_version(current_version: int | None) -> int:
    return (current_version or 1) + 1
