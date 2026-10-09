"""Pure rough-cut projection and complete-save contract tests."""

from __future__ import annotations

import pytest

from haoai_backend.personal_production.rough_cut.domain import (
    LEGACY_ID_REASON,
    MISSING_VIDEO_REASON,
    RoughCutUpdate,
    SavedDraft,
    UpdateFrame,
    parse_chapter_frames,
    project_snapshot,
    project_source_frames,
    validate_complete_update,
)
from haoai_backend.personal_production.rough_cut.errors import InvalidSource, InvalidUpdate, SourceTooLarge


def test_projection_uses_only_unique_first_reference_and_legacy_video_pattern() -> None:
    content = [
        {
            "storyboard": ["dup", "ignored"],
            "text": "first duplicate",
            "preview": "https://media.invalid/first.MP4?x=1",
        },
        {"storyboard": ["dup"], "original_text": "second duplicate", "preview": "clip.webm"},
        {"storyboard": ["stable-b", "unrelated"], "original_text": "fallback"},
        {"storyboard": ["missing"], "text": "not backed by asset", "preview": "clip.mov"},
        {"storyboard": [""], "text": "no first id", "preview": "clip.MKV?token=x"},
    ]

    projected = project_source_frames(content, ["dup", "stable-b"])

    assert [frame["asset_id"] for frame in projected] == [None, None, "stable-b", None, None]
    assert projected[0]["preview_url"] == "https://media.invalid/first.MP4?x=1"
    assert projected[2]["text"] == "fallback"
    assert projected[2]["preview_url"] is None
    assert projected[2]["missing_reason"] == MISSING_VIDEO_REASON
    no_draft = project_snapshot("chapter-a", projected, None)
    assert no_draft["frames"][2]["included"] is True
    assert no_draft["frames"][2]["pending"] is False
    assert projected[3]["missing_reason"] == LEGACY_ID_REASON
    assert projected[4]["preview_url"] == "clip.MKV?token=x"
    assert "unrelated" not in {frame["asset_id"] for frame in projected}


def test_chapter_content_falsy_values_are_empty_but_malformed_values_are_422() -> None:
    assert parse_chapter_frames(None) == []
    assert parse_chapter_frames("") == []
    assert parse_chapter_frames([]) == []
    assert parse_chapter_frames("[]") == []
    assert parse_chapter_frames('[{"storyboard":["a"]}, 3]') == [
        {"storyboard": ["a"]},
        {},
    ]

    with pytest.raises(InvalidSource, match="无法解析"):
        parse_chapter_frames("not-json")
    with pytest.raises(InvalidSource, match="格式不正确"):
        parse_chapter_frames('{"not":"an array"}')


def test_saved_projection_preserves_order_repeats_removed_ids_and_pending_source() -> None:
    source = project_source_frames(
        [
            {"storyboard": ["a"], "text": "A"},
            {"storyboard": ["b"], "text": "B"},
            {"storyboard": ["c"], "text": "new source"},
            {"storyboard": ["legacy"], "text": "legacy"},
        ],
        ["a", "b", "c"],
    )
    saved = SavedDraft(
        revision=7,
        frames=[
            {"asset_id": "b", "included": True},
            {"asset_id": "removed", "included": False},
            {"asset_id": "removed", "included": True},
            {"asset_id": "a", "included": False},
            {"asset_id": "b", "included": False},
            {"asset_id": "", "included": True},
        ],
    )

    response = project_snapshot("chapter-a", source, saved)

    assert response["revision"] == 7
    assert response["saved"] is True
    assert response["removed_asset_ids"] == ["removed", "removed"]
    assert [(frame["asset_id"], frame["included"], frame["pending"]) for frame in response["frames"]] == [
        ("b", True, False),
        ("a", False, False),
        ("c", False, True),
        ("", False, False),
    ]


def test_empty_first_save_is_valid_and_complete_order_is_preserved() -> None:
    empty_update = RoughCutUpdate(expected_revision=0, frames=())
    assert validate_complete_update(empty_update, []) == ()

    update = RoughCutUpdate(
        expected_revision=0,
        frames=(UpdateFrame("b", True), UpdateFrame("a", False)),
    )
    source = project_source_frames(
        [{"storyboard": ["a"]}, {"storyboard": ["b"]}],
        ["a", "b"],
    )
    persisted = validate_complete_update(update, source)
    assert persisted == ({"asset_id": "b", "included": True}, {"asset_id": "a", "included": False})


def test_source_identity_is_checked_before_valid_source_limit() -> None:
    content = [{"storyboard": [f"asset-{index}"]} for index in range(501)]
    source = project_source_frames(content, [f"asset-{index}" for index in range(500)])
    update = RoughCutUpdate(expected_revision=0, frames=())

    with pytest.raises(InvalidSource):
        validate_complete_update(update, source)

    valid_large_source = project_source_frames(content, [f"asset-{index}" for index in range(501)])
    with pytest.raises(SourceTooLarge):
        validate_complete_update(update, valid_large_source)


def test_complete_set_rejects_duplicate_and_missing_ids() -> None:
    source = project_source_frames(
        [{"storyboard": ["a"]}, {"storyboard": ["b"]}],
        ["a", "b"],
    )
    duplicate = RoughCutUpdate(
        expected_revision=0,
        frames=(UpdateFrame("a", True), UpdateFrame("a", False)),
    )
    missing = RoughCutUpdate(expected_revision=0, frames=(UpdateFrame("a", True),))

    with pytest.raises(InvalidUpdate):
        validate_complete_update(duplicate, source)
    with pytest.raises(InvalidUpdate):
        validate_complete_update(missing, source)
