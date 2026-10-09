"""Pure note patch and strict DTO contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from haoai_backend.personal_production.notes.domain import (
    FrameMediaSnapshot,
    FrameNoteChange,
    apply_frame_note_changes,
)
from haoai_backend.personal_production.notes.schemas import (
    FrameNoteChangeSchema,
    PersonalProductionNotesUpdateSchema,
)


def test_note_only_reconfirms_approved_at_current_media_revision_and_preserves_unknown_fields() -> None:
    media = FrameMediaSnapshot(
        frame_index=0,
        storyboard_asset_id="frame-a",
        media_revision=4,
        source_valid=True,
        asset_image_digest="image-digest",
        preview_digest=None,
        invalid_reason=None,
    )
    original = {
        "frame-a": {
            "status": "approved",
            "note": "旧内容",
            "approved_media_revision": 2,
            "needs_reconfirmation": True,
            "legacy_field": {"kept": True},
        },
        "unknown-id": {"other": "kept"},
    }

    result = apply_frame_note_changes(
        original,
        (FrameNoteChange("frame-a", 4, None, "更新备注"),),
        {"frame-a": media},
    )

    assert result["frame-a"] == {
        "status": "approved",
        "note": "更新备注",
        "approved_media_revision": 4,
        "needs_reconfirmation": False,
        "legacy_field": {"kept": True},
    }
    assert result["unknown-id"] == {"other": "kept"}
    assert original["frame-a"]["approved_media_revision"] == 2


def test_explicit_status_only_patch_keeps_note_and_clears_reconfirmation_marker() -> None:
    media = FrameMediaSnapshot(0, "frame-a", 2, True, "image", None, None)
    result = apply_frame_note_changes(
        {
            "frame-a": {
                "status": "approved",
                "note": "保留的备注",
                "approved_media_revision": 1,
                "needs_reconfirmation": True,
                "unknown": "unchanged",
            }
        },
        (FrameNoteChange("frame-a", 2, "needs_revision", None),),
        {"frame-a": media},
    )
    assert result["frame-a"] == {
        "status": "needs_revision",
        "note": "保留的备注",
        "approved_media_revision": None,
        "unknown": "unchanged",
    }


def test_strict_patch_supports_500_codepoints_and_rejects_501() -> None:
    assert len(FrameNoteChangeSchema(
        storyboard_asset_id="frame-a",
        expected_media_revision=1,
        note="字" * 2000,
    ).note) == 2000
    with pytest.raises(ValidationError):
        FrameNoteChangeSchema(
            storyboard_asset_id="frame-a",
            expected_media_revision=1,
            note="字" * 2001,
        )


def test_strict_dto_rejects_bool_extra_keys_duplicate_ids_and_empty_patch() -> None:
    with pytest.raises(ValidationError):
        PersonalProductionNotesUpdateSchema(expected_revision=True, frames=[])
    with pytest.raises(ValidationError):
        FrameNoteChangeSchema(
            storyboard_asset_id="frame-a",
            expected_media_revision=True,
            status="approved",
        )
    with pytest.raises(ValidationError):
        FrameNoteChangeSchema(
            storyboard_asset_id="frame-a",
            expected_media_revision=1,
            status="approved",
            owner_id="forged",
        )
    with pytest.raises(ValidationError):
        PersonalProductionNotesUpdateSchema(
            expected_revision=0,
            frames=[
                {"storyboard_asset_id": "same", "expected_media_revision": 1, "note": "a"},
                {"storyboard_asset_id": "same", "expected_media_revision": 1, "note": "b"},
            ],
        )
    with pytest.raises(ValidationError):
        PersonalProductionNotesUpdateSchema(expected_revision=0, frames=[])


def test_strict_dto_accepts_exactly_500_unique_frame_updates() -> None:
    updates = [
        {"storyboard_asset_id": f"frame-{index}", "expected_media_revision": 1, "note": "x"}
        for index in range(500)
    ]
    parsed = PersonalProductionNotesUpdateSchema(
        expected_revision=9_007_199_254_740_993,
        frames=updates,
    )

    assert len(parsed.frames) == 500


def test_explicit_null_resume_is_a_valid_empty_first_save_patch() -> None:
    update = PersonalProductionNotesUpdateSchema(expected_revision=0, resume_frame_id=None)
    assert update.frames == []
    assert "resume_frame_id" in update.model_fields_set


def test_backend_revision_integers_are_not_limited_to_javascript_safe_range() -> None:
    update = PersonalProductionNotesUpdateSchema(
        expected_revision=9_007_199_254_740_993,
        frames=[],
        resume_frame_id=None,
    )

    assert update.expected_revision == 9_007_199_254_740_993
