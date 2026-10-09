from __future__ import annotations

from haoai_backend.chat_data.statistics import merge_model_statistics


def test_statistics_merge_unknown_models_in_first_seen_group_order() -> None:
    rows = [
        {"model_name": "Alpha", "status": "failed", "calls": 2, "credits": 900},
        {"model_name": None, "status": "completed", "calls": 1, "credits": -3},
        {"model_name": "", "status": "failed", "calls": 3, "credits": 80},
        {"model_name": "未知模型", "status": "completed", "calls": 2, "credits": 5},
        {"model_name": "Beta", "status": "completed", "calls": 4, "credits": 12},
    ]

    assert merge_model_statistics(rows) == [
        {"model_name": "Alpha", "calls": 0, "credits": 0, "failed_calls": 2},
        {"model_name": "未知模型", "calls": 3, "credits": 2, "failed_calls": 3},
        {"model_name": "Beta", "calls": 4, "credits": 12, "failed_calls": 0},
    ]


def test_statistics_do_not_resort_after_unknown_models_are_merged() -> None:
    rows = [
        {"model_name": "First", "status": "completed", "calls": 1, "credits": 1},
        {"model_name": "Second", "status": "completed", "calls": 2, "credits": 2},
        {"model_name": "First", "status": "completed", "calls": 10, "credits": 10},
    ]

    result = merge_model_statistics(rows)

    assert [item["model_name"] for item in result] == ["First", "Second"]
    assert result[0]["calls"] == 11


def test_statistics_ignore_failed_credits_and_preserve_other_names() -> None:
    rows = [
        {"model_name": " raw name ", "status": "completed", "calls": 2, "credits": 7},
        {"model_name": " raw name ", "status": "failed", "calls": 1, "credits": 100},
    ]

    assert merge_model_statistics(rows) == [
        {
            "model_name": " raw name ",
            "calls": 2,
            "credits": 7,
            "failed_calls": 1,
        }
    ]
