from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from haoai_backend.admin_tasks.application import get_task, list_tasks
from haoai_backend.admin_tasks.domain import (
    AdminTaskDetail,
    AdminTaskFilters,
    AdminTaskListItem,
)
from haoai_backend.admin_tasks.errors import AdminTaskError


NOW = datetime(2026, 10, 10, 12, 0, 0)


class RecordingUnitOfWork:
    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        self.events = events
        self.item = AdminTaskListItem(
            id="task-a",
            user_id="user-a",
            type="image",
            status="completed",
            credit_cost=2,
            message_id="message-a",
            model_name="",
            created_at=NOW,
            updated_at=NOW,
        )
        self.detail = AdminTaskDetail(
            id="task-a",
            user_id="user-a",
            type="image",
            status="completed",
            message_id="message-a",
            request_data="raw",
            result="",
            created_at=NOW,
            updated_at=NOW,
        )
        self.count_error: Exception | None = None

    def count_tasks(self, filters: AdminTaskFilters) -> int:
        self.events.append(("count", filters))
        if self.count_error is not None:
            raise self.count_error
        return 4

    def list_tasks(self, filters: AdminTaskFilters, **kwargs: Any) -> tuple[AdminTaskListItem, ...]:
        self.events.append(("list", filters, kwargs))
        return (self.item,)

    def load_task_by_id(self, task_id: str) -> AdminTaskDetail | None:
        self.events.append(("detail", task_id))
        return self.detail if task_id == "task-a" else None

    def rollback(self) -> None:
        self.events.append(("rollback",))

    def close(self) -> None:
        self.events.append(("close",))


def test_list_counts_before_listing_and_closes_read_unit() -> None:
    events: list[tuple[Any, ...]] = []
    unit = RecordingUnitOfWork(events)

    page = list_tasks(lambda: unit, AdminTaskFilters(task_type="image"))

    assert page.total == 4
    assert page.data == (unit.item,)
    assert [event[0] for event in events] == ["count", "list", "rollback", "close"]
    assert events[1][2] == {
        "sort_field": "created_at",
        "sort_order": "desc",
        "offset": 0,
        "limit": 10,
    }


def test_detail_returns_raw_values_and_missing_task_is_404_with_cleanup() -> None:
    events: list[tuple[Any, ...]] = []
    unit = RecordingUnitOfWork(events)

    assert get_task(lambda: unit, "task-a").request_data == "raw"
    with pytest.raises(AdminTaskError) as failure:
        get_task(lambda: unit, "missing")

    assert failure.value.status_code == 404
    assert failure.value.detail == "任务不存在"
    assert [event[0] for event in events] == [
        "detail", "rollback", "close",
        "detail", "rollback", "close",
    ]


def test_read_failure_still_rolls_back_and_closes_and_unwired_is_503() -> None:
    events: list[tuple[Any, ...]] = []
    unit = RecordingUnitOfWork(events)
    unit.count_error = RuntimeError("count failed")

    with pytest.raises(RuntimeError, match="count failed"):
        list_tasks(lambda: unit, AdminTaskFilters())
    assert [event[0] for event in events] == ["count", "rollback", "close"]

    with pytest.raises(AdminTaskError) as failure:
        list_tasks(None, AdminTaskFilters())
    assert failure.value.status_code == 503
