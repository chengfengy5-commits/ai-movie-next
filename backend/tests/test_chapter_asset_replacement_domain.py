from __future__ import annotations

import json

import pytest

from haoai_backend.chapter_asset_replacement.domain import (
    ASSET_TYPES,
    collect_asset_ids_from_content,
    parse_chapter_frames,
    replace_asset_references,
    serialize_chapter_content,
)


@pytest.mark.parametrize("content", [None, "", 0, False, [], {}])
def test_parser_preserves_legacy_falsey_content_fallback(content: object) -> None:
    assert parse_chapter_frames(content) == []


def test_parser_uses_empty_frames_for_legacy_decode_errors() -> None:
    content = "not-json"
    assert parse_chapter_frames(content) == []


def test_parser_rejects_truthy_non_string_non_list_content() -> None:
    with pytest.raises(ValueError, match="章节内容格式异常"):
        parse_chapter_frames(object())


@pytest.mark.parametrize("content", ['{"frame": 1}', '"frame"', "17", "null"])
def test_parser_rejects_decoded_non_list_json(content: str) -> None:
    with pytest.raises(ValueError, match="章节内容格式异常"):
        parse_chapter_frames(content)


def test_replace_counts_frames_and_preserves_unrelated_values() -> None:
    source = [
        {
            "character": ["old", "old", "other"],
            "scene": ["old"],
            "prop": ["old"],
            "storyboard": ["board-a", "board-b"],
            "preview": "https://media.invalid/preview.png",
            "unknown": {"preserve": [1, 2]},
        },
        {"character": ["other", "old"], "unknown": "keep"},
        {"character": ["other"]},
        "not a frame",
        {"character": "old"},
    ]

    updated, replaced_count, observed_ids = replace_asset_references(
        source,
        old_asset_id="old",
        new_asset_id="new",
        asset_type="character",
    )

    assert replaced_count == 2
    assert updated[0]["character"] == ["new", "new", "other"]
    assert updated[1]["character"] == ["other", "new"]
    assert updated[0]["scene"] == ["old"]
    assert updated[0]["prop"] == ["old"]
    assert updated[0]["storyboard"] == ["board-a", "board-b"]
    assert updated[0]["preview"] == "https://media.invalid/preview.png"
    assert updated[0]["unknown"] == {"preserve": [1, 2]}
    assert updated[3] == "not a frame"
    assert updated[4] is source[4]
    assert source[0]["character"] == ["old", "old", "other"]
    assert observed_ids == [
        ["old", "old", "other"],
        ["other", "old"],
        ["other"],
        "old",
    ]


def test_existing_new_reference_removes_old_duplicates_without_adding_more() -> None:
    updated, count, _ = replace_asset_references(
        [{"scene": ["old", "new", "old", "new"]}],
        old_asset_id="old",
        new_asset_id="new",
        asset_type="scene",
    )

    assert count == 1
    assert updated == [{"scene": ["new", "new"]}]


def test_replacement_does_not_claim_unrelated_categories_or_absent_references() -> None:
    frames = [
        {"character": ["old"]},
        {"scene": ["old"]},
        {"prop": ["old"]},
        {"unknown": ["old"]},
    ]

    for asset_type in ASSET_TYPES:
        updated, count, _ = replace_asset_references(
            frames,
            old_asset_id="old",
            new_asset_id=f"new-{asset_type}",
            asset_type=asset_type,
        )
        assert count == 1
        assert updated[ASSET_TYPES.index(asset_type)][asset_type] == [f"new-{asset_type}"]

    _, count, observed_ids = replace_asset_references(
        [{"character": []}, {"character": None}, {"character": "old"}],
        old_asset_id="old",
        new_asset_id="new",
        asset_type="character",
    )
    assert count == 0
    assert observed_ids == [[], [], "old"]


def test_serializer_keeps_unicode_and_legacy_json_spacing() -> None:
    serialized = serialize_chapter_content([{"text": "保留中文", "ids": ["a", "b"]}])

    assert serialized == '[{"text": "保留中文", "ids": ["a", "b"]}]'
    assert json.loads(serialized) == [{"text": "保留中文", "ids": ["a", "b"]}]


def test_phase_two_reference_collection_uses_truthy_ids_from_all_categories() -> None:
    content = [
        {
            "character": ["c1", "c1", "", None, 0],
            "scene": ["s1"],
            "prop": ["p1"],
            "character_ids": ["ignored-legacy-key"],
        },
        {"character": "not-a-list", "scene": [], "prop": [False]},
        "not a frame",
    ]

    assert collect_asset_ids_from_content(content) == {
        "character": {"c1"},
        "scene": {"s1"},
        "prop": {"p1"},
    }
    assert collect_asset_ids_from_content("invalid-json") == {
        "character": set(),
        "scene": set(),
        "prop": set(),
    }


@pytest.mark.parametrize("unhashable", [["nested"], {"id": "nested"}])
def test_phase_two_reference_collection_keeps_truthy_unhashable_failure(unhashable: object) -> None:
    with pytest.raises(TypeError):
        collect_asset_ids_from_content([{"character": [unhashable]}])
