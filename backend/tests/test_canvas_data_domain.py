from __future__ import annotations

import json

from haoai_backend.canvas_data.domain import (
    empty_canvas_document,
    exceeds_document_limit,
    next_document_version,
    parse_stored_document,
    serialize_document,
)


def test_missing_canvas_returns_fresh_literal_default() -> None:
    first = empty_canvas_document("chapter-a")
    second = empty_canvas_document("chapter-a")

    assert first == {
        "id": None,
        "chapter_id": "chapter-a",
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
    assert first is not second
    assert first["document_json"] is not second["document_json"]


def test_stored_json_only_falls_back_for_decode_errors() -> None:
    assert parse_stored_document("{") == {}
    assert parse_stored_document("null") is None
    assert parse_stored_document("[1]") == [1]
    assert parse_stored_document('"value"') == "value"
    assert parse_stored_document(None) is None


def test_serializer_matches_legacy_unicode_encoding_without_mutating_input() -> None:
    payload = {"标题": "画布", "nodes": [{"id": "节点"}]}
    original = json.loads(json.dumps(payload, ensure_ascii=False))

    encoded = serialize_document(payload)

    assert encoded == json.dumps(payload, ensure_ascii=False)
    assert r"\u" not in encoded
    assert payload == original


def test_size_limit_counts_characters_and_uses_strictly_greater_than() -> None:
    assert not exceeds_document_limit("界" * 1_000_000)
    assert exceeds_document_limit("界" * 1_000_001)
    assert len(("界" * 1_000_000).encode("utf-8")) > 1_000_000


def test_version_increment_preserves_zero_fallback() -> None:
    assert next_document_version(None) == 2
    assert next_document_version(0) == 2
    assert next_document_version(7) == 8
