from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from haoai_backend.admin_credit_queries import application
from haoai_backend.admin_credit_queries.domain import (
    AdminCreditQueriesActor,
    UserCreditRecord,
)
from haoai_backend.admin_credit_queries.errors import (
    AdminCreditQueriesForbidden,
    UserCreditNotFound,
)

NOW = datetime(2026, 10, 13, 10, 0)
ADMIN = AdminCreditQueriesActor("admin", True)


class FakeUnitOfWork:
    def __init__(self, *, row_exists: bool = True, fail_cleanup: bool = False) -> None:
        self.events: list[str] = []
        self.row = (
            SimpleNamespace(
                id="credit-1",
                user_id="user-1",
                credits=7,
                created_at=NOW,
                updated_at=NOW,
            )
            if row_exists
            else None
        )
        self.fail_cleanup = fail_cleanup
        self.refreshed_row: Any | None = None

    def list_user_credits(self, **kwargs):
        self.events.append("list")
        self.list_kwargs = kwargs
        return (
            [UserCreditRecord("credit-1", "user-1", 7, NOW, NOW)],
            1,
        )

    def get_user_credit(self, credit_id: str):
        self.events.append(f"get:{credit_id}")
        return UserCreditRecord("credit-1", "user-1", 7, NOW, NOW)

    def load_user_credit_for_update(self, credit_id: str):
        self.events.append(f"load:{credit_id}")
        return self.row

    def set_user_credit_amount(self, row, credits: int) -> None:
        self.events.append("set")
        row.credits = credits

    def refresh_user_credit(self, row):
        self.events.append("refresh")
        self.refreshed_row = row
        return UserCreditRecord(
            row.id,
            row.user_id,
            row.credits,
            row.created_at,
            row.updated_at,
        )

    def list_credit_logs(self, **kwargs):
        self.events.append("logs")
        return [], 0

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")
        if self.fail_cleanup:
            raise RuntimeError("rollback cleanup failed")

    def close(self) -> None:
        self.events.append("close")
        if self.fail_cleanup:
            raise RuntimeError("close cleanup failed")


def test_update_loads_before_conversion_and_preserves_primary_error_on_cleanup() -> None:
    unit = FakeUnitOfWork(row_exists=False, fail_cleanup=True)

    class MustNotConvert:
        def __int__(self) -> int:
            raise AssertionError("missing credit must be reported before conversion")

    with pytest.raises(UserCreditNotFound) as missing:
        application.update_user_credit(
            lambda: unit,
            ADMIN,
            "missing",
            {"credits": MustNotConvert()},
        )
    assert missing.value.status_code == 404
    assert missing.value.detail == "积分记录不存在"
    assert unit.events == ["load:missing", "rollback", "close"]

    unit = FakeUnitOfWork(fail_cleanup=True)
    with pytest.raises(TypeError):
        application.update_user_credit(
            lambda: unit,
            ADMIN,
            "credit-1",
            {"credits": None},
        )
    assert unit.events == ["load:credit-1", "rollback", "close"]


def test_empty_and_unknown_updates_commit_and_refresh_the_same_row_once() -> None:
    for body in ({}, {"ignored": 3}):
        unit = FakeUnitOfWork()
        payload = application.update_user_credit(
            lambda: unit,
            ADMIN,
            "credit-1",
            body,
        )
        assert payload == {
            "id": "credit-1",
            "user_id": "user-1",
            "credits": 7,
            "created_at": NOW,
            "updated_at": NOW,
        }
        assert unit.events == ["load:credit-1", "commit", "refresh", "close"]
        assert unit.refreshed_row is unit.row


def test_success_close_failure_propagates_and_admin_guard_precedes_factory() -> None:
    unit = FakeUnitOfWork(fail_cleanup=True)
    with pytest.raises(RuntimeError, match="close cleanup failed"):
        application.list_user_credits(
            lambda: unit,
            ADMIN,
            skip=0,
            limit=10,
            sort_field="updated_at",
            sort_order="desc",
        )
    assert unit.events == ["list", "close"]

    called = False

    def factory():
        nonlocal called
        called = True
        raise AssertionError("forbidden actors must not open a business unit")

    with pytest.raises(AdminCreditQueriesForbidden) as forbidden:
        application.list_user_credits(
            factory,
            AdminCreditQueriesActor("member", False),
            skip=0,
            limit=10,
            sort_field="updated_at",
            sort_order="desc",
        )
    assert forbidden.value.status_code == 403
    assert forbidden.value.detail == "权限不足，仅管理员可执行此操作"
    assert called is False
