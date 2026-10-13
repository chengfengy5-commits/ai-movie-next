from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from haoai_backend.admin_statistics import application
from haoai_backend.admin_statistics.domain import AdminStatisticsActor
from haoai_backend.admin_statistics.errors import (
    AdminStatisticsForbidden,
    AdminStatisticsUnavailable,
)


ACTOR = AdminStatisticsActor("admin", True)
NOW = datetime(2026, 10, 13, 12, 30, 15)


class Uow:
    def __init__(self):
        self.events = []
        self.values = {
            "total_users": 4, "premium_users": 2, "active_members": 1,
            "new_users_month": 3, "total_credits": 88,
            "credits_used_month": -12, "credits_charged_month": 5,
            "total_tasks": 6, "tasks_month": 4, "tasks_completed": 2,
            "tasks_failed": 1, "total_series": 7, "total_chapters": 8,
            "total_characters": 9, "total_scenes": 10, "total_props": 11,
        }
        self.fail_aggregate = None
        self.fail_token = None
        self.fail_close = None

    def aggregate_statistics(self, now, month_start):
        self.events.append(("aggregate", now, month_start))
        if self.fail_aggregate:
            raise self.fail_aggregate
        return dict(self.values)

    def get_muse_token(self):
        self.events.append("token")
        if self.fail_token:
            raise self.fail_token
        return "fake-token"

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.events.append("close")
        if self.fail_close:
            raise self.fail_close


class Balance:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = []

    def read_balance(self, token):
        self.calls.append(token)
        if self.error:
            raise self.error
        return self.value


def test_statistics_shape_query_clock_and_raw_balance_value_are_preserved():
    uow = Uow()
    reader = Balance("12.75")
    result = application.get_admin_statistics(lambda: uow, ACTOR, reader, lambda: NOW)
    assert result == {
        "upstream": {"balance": "12.75"},
        "users": {"total": 4, "premium": 2, "active_members": 1, "new_this_month": 3},
        "credits": {"total_balance": 88, "used_this_month": 12, "charged_this_month": 5},
        "tasks": {"total": 6, "this_month": 4, "completed": 2, "failed": 1},
        "content": {"series": 7, "chapters": 8, "characters": 9, "scenes": 10, "props": 11},
        "revenue": {"total": 0, "this_month": 0, "subscriptions": 2},
    }
    assert uow.events[0] == (
        "aggregate", NOW, datetime(2026, 10, 1, 0, 0, 0)
    )
    assert uow.events[1:] == ["token", "close"]
    assert reader.calls == ["fake-token"]


def test_only_upstream_stage_errors_are_suppressed_and_primary_failure_survives_cleanup():
    uow = Uow()
    reader = Balance(error=ValueError("transport failure"))
    result = application.get_admin_statistics(lambda: uow, ACTOR, reader, lambda: NOW)
    assert result["upstream"] == {"balance": None}
    assert uow.events[-1] == "close"

    query_error = RuntimeError("aggregate failed")
    uow.events.clear()
    uow.fail_aggregate = query_error
    with pytest.raises(RuntimeError) as error:
        application.get_admin_statistics(lambda: uow, ACTOR, reader, lambda: NOW)
    assert error.value is query_error
    assert uow.events[-2:] == ["rollback", "close"]

    uow.fail_aggregate = None
    uow.fail_token = OSError("config read failed")
    result = application.get_admin_statistics(lambda: uow, ACTOR, reader, lambda: NOW)
    assert result["upstream"] == {"balance": None}


def test_forbidden_and_unavailable_fail_before_business_factory():
    opened = []
    with pytest.raises(AdminStatisticsForbidden):
        application.get_admin_statistics(
            lambda: opened.append(Uow()),
            AdminStatisticsActor("member", False),
            Balance(), lambda: NOW,
        )
    assert opened == []
    with pytest.raises(AdminStatisticsUnavailable) as error:
        application.get_admin_statistics(None, ACTOR, Balance(), lambda: NOW)
    assert error.value.status_code == 503
