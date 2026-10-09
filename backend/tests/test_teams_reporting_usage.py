from __future__ import annotations

from datetime import datetime

from haoai_backend.teams.domain import UserRecord
from haoai_backend.teams.reporting.usage import (
    UNKNOWN_MODEL,
    aggregate_usage_rows,
    avatar_url_for_email,
    fill_usage_members,
    member_model_names,
    order_export_models,
    parse_date_range,
)


def test_date_range_parses_each_iso_endpoint_as_utc_naive() -> None:
    start, end = parse_date_range(
        "2026-10-09T12:30:00+02:00",
        "2026-10-09T12:00:00Z",
    )

    assert start == datetime(2026, 10, 9, 10, 30)
    assert end == datetime(2026, 10, 9, 12, 0)
    assert parse_date_range("not a date", "2026-10-09T12:00:00")[0] is None


def test_aggregation_merges_falsey_models_and_excludes_failed_credits() -> None:
    completed_and_failed = [
        ("series", None, "user-a", "completed", 2, 7),
        ("series", "", "user-a", "failed", 3, 900),
        ("series", UNKNOWN_MODEL, "user-b", "completed", 1, -2),
    ]

    calls, credits, failed, by_model, by_member = aggregate_usage_rows(
        completed_and_failed
    )

    assert (calls, credits, failed) == (3, 5, 3)
    assert by_model == [
        {
            "model_name": UNKNOWN_MODEL,
            "calls": 3,
            "credits": 5,
            "failed_calls": 3,
        }
    ]
    assert [member["user_id"] for member in by_member] == ["user-a", "user-b"]
    assert by_member[0]["calls"] == 2
    assert by_member[0]["credits"] == 7
    assert by_member[0]["failed_calls"] == 3
    assert by_member[1]["credits"] == -2


def test_export_sort_keeps_lowercase_ties_stable_and_member_sort_independent() -> None:
    model_types = {"a": "video", "A": "video", "other": "image"}
    source_order = ["a", "A", "other"]

    assert order_export_models(source_order, model_types) == [
        "a",
        "A",
        "other",
    ]
    assert member_model_names({"a", "A", "other"}) == ["A", "a", "other"]


def test_avatar_resolution_is_pure_and_matches_only_qq_domains() -> None:
    assert (
        avatar_url_for_email(" 123456789@QQ.COM ")
        == "https://q1.qlogo.cn/g?b=qq&nk=123456789&s=100"
    )
    assert avatar_url_for_email("alias@foxmail.com") is None
    assert avatar_url_for_email("name@example.com") is None


def test_member_fill_uses_id_fallback_for_missing_users() -> None:
    members = [{"user_id": "missing", "username": "", "avatar_url": None}]
    users = {
        "known": UserRecord(
            id="known",
            username="Known",
            email="123456789@qq.com",
            avatar_url="ignored-original-avatar",
        )
    }

    fill_usage_members(members, users)

    assert members[0] == {
        "user_id": "missing",
        "username": "missing",
        "avatar_url": None,
    }
