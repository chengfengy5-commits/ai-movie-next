from __future__ import annotations

from hashlib import sha256

from haoai_backend.storyboard_asset_generation.prompts import (
    CHARACTER_SYSTEM, CHARACTER_USER, PROP_SYSTEM, PROP_USER, SCENE_SYSTEM, SCENE_USER,
)
from haoai_backend.storyboard_asset_generation.domain import (
    pad_appearances,
    parse_appearances,
    parse_extracted,
    parse_json_array_lenient,
)


def test_extraction_parsers_keep_legacy_scene_prop_fields_and_string_rows() -> None:
    assert parse_extracted(
        "scene",
        '[{"name":"  客厅_夜 ","visualPrompt":"灯光"},"  走廊_白天  ",7]',
    ) == [
        {"title": "  客厅_夜 ", "description": "灯光"},
        {"title": "走廊_白天", "description": ""},
    ]
    assert parse_extracted(
        "prop",
        '[{"name":"茶盏","visualPrompt":"青瓷"},"  铜钥匙  ",false]',
    ) == [
        {"name": "茶盏", "description": "青瓷"},
        {"name": "铜钥匙", "description": ""},
    ]


def test_appearance_parsers_accept_old_nested_and_kind_specific_object_keys() -> None:
    assert parse_appearances(
        "scene", '[[null,{"title":"厅内"}], ["  后院  ", {"name":"廊下"}]]'
    ) == (["厅内", "后院"], True)
    assert parse_appearances(
        "character", '[[{"character":"顾宁"}, "阿叔"]]'
    ) == ([["顾宁", "阿叔"]], True)
    assert parse_appearances("prop", '[[{"prop":"旧钥匙"}]]') == ([["旧钥匙"]], True)


def test_lenient_parser_preserves_complete_items_and_ignores_trailing_explanation() -> None:
    assert parse_json_array_lenient('分析[第一项]结果：[{"name":"甲"}, {"name":"乙"}]说明[无遗漏]') == (
        [{"name": "甲"}, {"name": "乙"}],
        True,
    )
    assert parse_extracted("scene", '```json\n[{"name":"甲"}, {"name":"乙"}\n```') == [
        {"title": "甲", "description": ""},
        {"title": "乙", "description": ""},
    ]
    assert parse_json_array_lenient('```json\n{"2":["乙"],"1":["甲"]}\n```') == (
        [["甲"], ["乙"]],
        True,
    )


def test_only_valid_empty_array_clears_single_frame_and_parse_failures_stay_missing() -> None:
    empty_rows, valid_empty = parse_appearances("prop", "[]")
    assert pad_appearances(empty_rows, 1, [9], parsed_ok=valid_empty) == ([[]], [])

    for invalid in ('{"note": []}', "[说明]", '["截断'):
        rows, parsed_ok = parse_appearances("prop", invalid)
        assert pad_appearances(rows, 1, [9], parsed_ok=parsed_ok) == ([None], [9])

    rows, parsed_ok = parse_appearances("prop", '["完整项", "截断')
    assert pad_appearances(rows, 2, [4, 9], parsed_ok=parsed_ok) == (
        [["完整项"], None],
        [9],
    )


def test_numeric_key_objects_follow_legacy_int_conversion_without_unicode_digit_crash() -> None:
    assert parse_json_array_lenient('{" 2 ":["乙"],"+1":["甲"]}') == (
        [["甲"], ["乙"]],
        True,
    )
    assert parse_json_array_lenient('{" 2 ":["乙"],"+1":["甲"]}说明') == (
        [["甲"], ["乙"]],
        True,
    )
    assert parse_json_array_lenient('{"²":[]}') == (None, False)
    assert parse_json_array_lenient('{"note":[]}') == (None, False)
    assert parse_json_array_lenient('{"note":[]}说明') == (None, False)
    assert parse_json_array_lenient('{"note":[]') == (None, False)
    assert parse_json_array_lenient('{"note":[invalid]}说明') == (None, False)
    assert parse_json_array_lenient('"literal []"') == (None, False)
    assert parse_json_array_lenient("[]") == ([], True)
    assert parse_json_array_lenient('说明是背景。["正文数组"]') == (["正文数组"], True)


def test_six_default_appearance_prompt_literals_match_the_fixed_legacy_ast_oracle() -> None:
    # Digests come from ast.literal_eval(Return.value) in OLD ai_service.py at the fixed snapshot.
    # Source blob SHA-256: 3367b804c777e0e7f56dd35be32a97be69bff17f72bd2dbd0e4287dc9bf18574.
    expected = {
        "CHARACTER_SYSTEM": "05b0a17fea9e35cc4a7659b7273a43b36cf4ea4b0314cac10d971e96c2776404",
        "CHARACTER_USER": "b7db6cef4b8d3787ffba1d4ebeacef4f09b725139e2f1d69ee0636419dfde369",
        "SCENE_SYSTEM": "3ea11a6f588c6c692168dd5cf7b103f154b4894592fd97a66bee70eedc45defd",
        "SCENE_USER": "6fd3a993e2de45ade2076878c727c7753ed761b215ffa03cab610afc6009263e",
        "PROP_SYSTEM": "f3798319bc559cddf4d3109fddad21c16d077411c0e972de75ca14968506b052",
        "PROP_USER": "b9212de9c1d0fb0ebeb438e51745d02591d021708689a8b67c36921deef57ada",
    }
    actual = {
        "CHARACTER_SYSTEM": CHARACTER_SYSTEM,
        "CHARACTER_USER": CHARACTER_USER,
        "SCENE_SYSTEM": SCENE_SYSTEM,
        "SCENE_USER": SCENE_USER,
        "PROP_SYSTEM": PROP_SYSTEM,
        "PROP_USER": PROP_USER,
    }
    assert {
        name: sha256(value.encode("utf-8")).hexdigest()
        for name, value in actual.items()
    } == expected
