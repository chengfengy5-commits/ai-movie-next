from datetime import datetime

import pytest
from pydantic import ValidationError

from haoai_backend.series_data.domain import (
    ChapterRecord,
    chapter_create_content,
    parse_chapter_frames,
    parse_source_frames,
    source_asset_ids,
    serialize_content,
)
from haoai_backend.series_data.schemas import (
    ChapterResponse,
    ChapterUpdate,
    SceneFrame,
    SeriesResponse,
)
from haoai_backend.series_data.storyboard import chat_index_mapping
from haoai_backend.series_data.presentation import chapter_payload


def test_response_nullable_fields_remain_required() -> None:
    now = datetime(2026, 1, 1)
    with pytest.raises(ValidationError):
        SeriesResponse(
            id="s", user_id="u", name="name", created_at=now, updated_at=now
        )
    with pytest.raises(ValidationError):
        ChapterResponse(id="c", series_id="s", title="title", order=1, created_at=now, updated_at=now)


def test_create_scene_frame_drops_extra_fields_but_update_keeps_them() -> None:
    created = SceneFrame.model_validate({"text": "line", "storyboard": ["asset"], "extra": 1})
    updated = ChapterUpdate.model_validate({"content": [{"text": "line", "storyboard": ["asset"], "extra": 1}]})
    assert created.model_dump() == {"text": "line", "character": None, "scene": None, "prop": None}
    assert updated.content == [{"text": "line", "storyboard": ["asset"], "extra": 1}]


def test_create_content_prefers_typed_frames_then_splits_raw_lines() -> None:
    typed = [{"text": " typed ", "character": None, "scene": None, "prop": None}]
    assert chapter_create_content(typed, "raw\nignored") == typed
    assert chapter_create_content(None, "  one \n\n two  ") == [
        {"text": "one", "character": None, "scene": None, "prop": None},
        {"text": "two", "character": None, "scene": None, "prop": None},
    ]
    assert chapter_create_content(None, None) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('[{"text":"x"}, 4]', [{"text": "x"}, 4]),
        ("null", None),
        ("17", 17),
        ("{bad", []),
        (None, []),
        (17, []),
    ],
)
def test_response_decode_preserves_valid_json_shape(value: object, expected: object) -> None:
    assert parse_chapter_frames(value) == expected


def test_write_scanner_preserves_original_indices_and_skips_non_dict_for_mapping() -> None:
    content = '[{"storyboard":["a"]}, 7, {"storyboard":["b"]}]'
    frames = parse_source_frames(content)
    assert frames == [{"storyboard": ["a"]}, 7, {"storyboard": ["b"]}]
    mapping = chat_index_mapping(frames, [{"storyboard": ["b"]}, 9, {"storyboard": ["a"]}])
    assert mapping == {2: 0, 0: 2}


def test_response_projection_preserves_valid_shapes_for_response_validation() -> None:
    now = datetime(2026, 1, 1)
    cases = [
        ("null", None, False),
        ("17", 17, True),
        (None, [], False),
        (17, [], False),
        ('[{"text":"x"}, 4]', [{"text": "x"}, 4], True),
    ]
    for stored, decoded, invalid_response in cases:
        chapter = ChapterRecord(
            id="chapter", series_id="series", title="title", content=stored,
            order=1, created_at=now, updated_at=now,
        )
        payload = chapter_payload(chapter)
        assert payload["content"] == decoded
        if invalid_response:
            with pytest.raises(ValidationError):
                ChapterResponse.model_validate(payload)
        else:
            assert ChapterResponse.model_validate(payload).content == decoded


def test_internal_source_parser_keeps_list_members_but_ignores_non_list_shapes() -> None:
    assert parse_source_frames("null") == []
    assert parse_source_frames("17") == []
    assert parse_source_frames('{"text":"not a list"}') == []
    assert parse_source_frames('[null, 7, {"storyboard":["a"]}]') == [
        None, 7, {"storyboard": ["a"]}
    ]


def test_content_serialization_keeps_legacy_ascii_defaults_and_rewrite_mode() -> None:
    content = [{"text": "镜头"}]
    assert serialize_content(content) == r'[{"text": "\u955c\u5934"}]'
    assert serialize_content(content, ensure_ascii=False) == '[{"text": "镜头"}]'


def test_asset_reference_collection_keeps_legacy_unhashable_failure() -> None:
    assert source_asset_ids('[{"character":["a", "a"], "scene":["x"], "prop":[]}]') == {
        "character": {"a"}, "scene": {"x"}, "prop": set()
    }
    with pytest.raises(TypeError):
        source_asset_ids('[{"character":[{"x":1}]}]')


def test_repeated_storyboard_ids_map_to_the_last_old_position() -> None:
    old = [{"storyboard": ["same"]}, {"storyboard": ["same"]}]
    new = [{"storyboard": ["same"]}]
    assert chat_index_mapping(old, new) == {1: 0}
