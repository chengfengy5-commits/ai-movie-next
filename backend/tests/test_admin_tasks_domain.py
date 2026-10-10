from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from datetime import datetime

import pytest

from haoai_backend.admin_tasks.domain import (
    AdminTaskDetail,
    AdminTaskFilters,
    AdminTaskListItem,
    AdminTaskPage,
)


def test_admin_task_filter_values_keep_raw_optional_inputs() -> None:
    filters = AdminTaskFilters(user_id="", task_type="image", status=None, model_name="%_")

    assert filters.user_id == ""
    assert filters.task_type == "image"
    assert filters.model_name == "%_"
    with pytest.raises(FrozenInstanceError):
        filters.status = "failed"  # type: ignore[misc]


def test_list_and_detail_dtos_have_distinct_exact_nine_field_shapes() -> None:
    now = datetime(2026, 10, 10, 12, 0, 0)
    item = AdminTaskListItem(
        id="task-a",
        user_id="user-a",
        type="image",
        status="completed",
        credit_cost=7,
        message_id="message-a",
        model_name="",
        created_at=now,
        updated_at=now,
    )
    detail = AdminTaskDetail(
        id="task-a",
        user_id="user-a",
        type="image",
        status="completed",
        message_id="message-a",
        request_data='{"raw":false}',
        result="not-json",
        created_at=now,
        updated_at=now,
    )
    page = AdminTaskPage(data=(item,), total=1)

    assert set(asdict(item)) == {
        "id", "user_id", "type", "status", "credit_cost", "message_id",
        "model_name", "created_at", "updated_at",
    }
    assert set(asdict(detail)) == {
        "id", "user_id", "type", "status", "message_id", "request_data",
        "result", "created_at", "updated_at",
    }
    assert page.data == (item,)
    assert page.total == 1
