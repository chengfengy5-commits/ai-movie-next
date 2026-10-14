from __future__ import annotations

import pytest

from haoai_backend.storyboard_generation.domain import (
    parse_all_frames,
    parse_scene_segments,
    parse_storyboard,
)


def test_marker_parser_returns_full_frame_shape_without_losing_frame_text() -> None:
    raw = "_::~OUTPUT_START::~_第一场。_::~RECORD::~_第二场。_::~OUTPUT_END::~_"
    assert parse_storyboard(raw) == [
        {"text": "第一场。", "character": "", "scene": "", "prop": ""},
        {"text": "第二场。", "character": "", "scene": "", "prop": ""},
    ]


def test_scene_segments_keep_the_record_index_and_trim_legacy_code_fences() -> None:
    fenced = "```json\n片段甲\n```"
    assert parse_scene_segments(f"  {fenced} _::~RECORD::~_片段乙") == [
        {"index": 0, "text": "片段甲"},
        {"index": 1, "text": "片段乙"},
    ]


@pytest.mark.parametrize(
    ("raw_frames", "expected_texts"),
    [
        ("生成画面甲_::~RECORD::~_生成画面乙", ["生成画面甲", "生成画面乙"]),
        ("生成画面甲_::~RECORD::~_", ["生成画面甲", "原始片段乙"]),
    ],
)
def test_all_frames_keep_segment_order_fallback_missing_output_and_fixed_duration(
    raw_frames: str,
    expected_texts: list[str],
) -> None:
    segments = [{"index": 0, "text": "原始片段甲"}, {"index": 1, "text": "原始片段乙"}]

    assert parse_all_frames(raw_frames, segments) == [
        {"text": expected_texts[0], "duration": 5},
        {"text": expected_texts[1], "duration": 5},
    ]
