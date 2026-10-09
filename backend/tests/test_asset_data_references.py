from __future__ import annotations

import json

import pytest

from haoai_backend.asset_data.references import remove_asset_references


def test_removes_every_exact_duplicate_from_target_category_and_preserves_other_values() -> None:
    source = [
        {
            "text": "保留文本",
            "character": ["target", "keep", "target"],
            "scene": ["target"],
            "storyboard": ["asset-1"],
        },
        None,
        {"character": ["target"]},
    ]
    result = remove_asset_references(source, "character", "target")

    assert result.changed is True
    assert result.removed_frames == 2
    assert json.loads(result.content) == [
        {
            "text": "保留文本",
            "character": ["keep"],
            "scene": ["target"],
            "storyboard": ["asset-1"],
        },
        None,
        {"character": []},
    ]
    assert source[0]["character"] == ["target", "keep", "target"]


def test_changed_content_uses_legacy_unicode_json_serialization() -> None:
    source = '[ { "text" : "未变", "prop" : ["目标", "其他", "目标"] } ]'
    result = remove_asset_references(source, "prop", "目标")

    assert result.changed
    assert result.removed_frames == 1
    assert result.content == '[{"text": "未变", "prop": ["其他"]}]'


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "null",
        "7",
        {"text": "not a frame list"},
        None,
    ],
)
def test_invalid_or_non_list_content_is_left_untouched(content: object) -> None:
    result = remove_asset_references(content, "scene", "target")
    assert result.content is content
    assert result.changed is False
    assert result.removed_frames == 0


def test_no_match_preserves_original_bytes_instead_of_reserializing() -> None:
    source = '[  {"text":"same","scene":["other"]}  ]'
    result = remove_asset_references(source, "scene", "target")

    assert result.content == source
    assert result.changed is False
    assert result.removed_frames == 0


def test_list_members_that_are_not_dicts_keep_their_positions() -> None:
    source = '[{"character":["target"]},false,{"character":["stay"]}]'
    result = remove_asset_references(source, "character", "target")

    assert json.loads(result.content) == [
        {"character": []},
        False,
        {"character": ["stay"]},
    ]
    assert result.removed_frames == 1
