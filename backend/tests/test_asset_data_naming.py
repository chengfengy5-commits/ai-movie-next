from __future__ import annotations

import json

from haoai_backend.asset_data.naming import (
    alias_contains,
    append_alias,
    character_key,
    dump_aliases,
    load_aliases,
    make_key,
    normalize_name,
    prop_key,
    scene_key,
    split_scene_space_time,
)


def test_normalization_uses_fixed_punctuation_table_and_lower_not_casefold() -> None:
    assert normalize_name(" A_B- C·D，E ") == "abcd,e"
    assert normalize_name("İ") == "i̇"
    assert normalize_name("ß") == "ß"


def test_scene_key_removes_noise_and_recognizes_longest_time_suffix() -> None:
    assert split_scene_space_time("恒泰盐宴厅内景夜") == ("恒泰盐宴厅", "夜晚")
    assert scene_key("恒泰盐宴厅内景夜") == "恒泰盐宴厅|夜晚"
    assert scene_key("恒泰盐宴厅·深夜") == "恒泰盐宴厅|夜晚"
    assert scene_key("学校室外") == "学校|未知"
    assert scene_key("   ") == ""


def test_key_dispatch_preserves_kind_specific_behavior_and_codepoint_limit() -> None:
    assert make_key("CHARACTERS", " 周晴_成年 ") == character_key("周晴_成年")
    assert make_key("scenes", "盐宴厅_夜晚") == "盐宴厅|夜晚"
    assert make_key("props", "茶 杯") == prop_key("茶 杯")
    assert make_key("unknown", "A_B") == "ab"
    assert len(character_key("人" * 256)) == 255


def test_aliases_trim_dedupe_exactly_keep_case_and_use_unicode_json() -> None:
    assert load_aliases([" 周晴 ", "", "晴晴", "周晴"]) == ["周晴", "晴晴"]
    assert load_aliases("周晴;晴晴，晴晴") == ["周晴", "晴晴"]
    assert dump_aliases(["周晴", "晴晴"]) == json.dumps(
        ["周晴", "晴晴"], ensure_ascii=False
    )
    assert append_alias(["周晴"], "周晴", primary_name="周晴") is None
    assert append_alias(["晴晴"], " 晴晴 ") is None
    assert append_alias(["晴晴"], "晴晴2") == '["晴晴", "晴晴2"]'
    assert alias_contains(["晴晴"], " 晴晴 ")
    assert not alias_contains(["晴晴"], "晴晴2")


def test_malformed_alias_text_falls_back_to_legacy_delimiters() -> None:
    assert load_aliases("甲,乙;丙，丁、戊；己\n庚") == [
        "甲",
        "乙",
        "丙",
        "丁",
        "戊",
        "己",
        "庚",
    ]
