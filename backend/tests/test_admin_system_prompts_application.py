from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from haoai_backend.admin_system_prompts import application
from haoai_backend.admin_system_prompts.domain import (
    AdminSystemPromptActor,
    SystemPromptRecord,
    create_values,
)
from haoai_backend.admin_system_prompts.errors import (
    AdminSystemPromptNotFound,
    AdminSystemPromptPersistenceError,
    AdminSystemPromptsForbidden,
)

ADMIN = AdminSystemPromptActor(user_id="admin", is_superuser=True)
NON_ADMIN = AdminSystemPromptActor(user_id="member", is_superuser=False)
STAMP = datetime(2026, 10, 13, 9, 30)


class MemoryUnitOfWork:
    def __init__(self) -> None:
        self.rows = {
            "p1": SystemPromptRecord(
                id="p1",
                category="text",
                name="Existing",
                system_prompt="system",
                user_prompt="user",
                sort_order=1,
                is_active=True,
                created_at=STAMP,
                updated_at=STAMP,
            )
        }
        self.order_references = [("user-a", "p1"), ("user-b", "p1")]
        self.added: tuple[str, dict[str, Any]] | None = None
        self.updated: dict[str, Any] | None = None
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.commit_error: Exception | None = None
        self.hide_after_commit = False
        self.events: list[tuple[Any, ...]] = []
        self.get_error: Exception | None = None
        self.rollback_error: Exception | None = None
        self.close_error: Exception | None = None

    def list_prompts(self, *, category, is_active, skip, limit):
        rows = list(self.rows.values())
        if category:
            rows = [row for row in rows if row.category == category]
        if is_active is not None:
            rows = [row for row in rows if row.is_active == is_active]
        total = len(rows)
        rows.sort(key=lambda row: row.sort_order)
        return rows[skip : skip + limit], total

    def get_prompt(self, prompt_id):
        self.events.append(("get", prompt_id, self.commit_count))
        if self.get_error is not None:
            raise self.get_error
        if self.hide_after_commit and self.commit_count:
            return None
        return self.rows.get(prompt_id)

    def add_prompt(self, prompt_id, values):
        self.added = (prompt_id, dict(values))
        self.rows[prompt_id] = SystemPromptRecord(
            id=prompt_id,
            category=values["category"],
            name=values["name"],
            system_prompt=values["system_prompt"],
            user_prompt=values["user_prompt"],
            sort_order=values["sort_order"],
            is_active=values["is_active"],
            created_at=STAMP,
            updated_at=STAMP,
        )

    def update_prompt(self, prompt_id, changes):
        row = self.rows.get(prompt_id)
        if row is None:
            return False
        self.updated = dict(changes)
        self.rows[prompt_id] = replace(row, **changes)
        return True

    def delete_prompt(self, prompt_id):
        self.events.append(("delete", prompt_id))
        return self.rows.pop(prompt_id, None) is not None

    def commit(self):
        self.events.append(("commit",))
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self):
        self.events.append(("rollback",))
        self.rollback_count += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self):
        self.events.append(("close",))
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


def test_create_defaults_only_apply_to_missing_keys() -> None:
    assert create_values({}) == {
        "category": "",
        "name": "系统提示词",
        "system_prompt": "",
        "user_prompt": "",
        "sort_order": 0,
        "is_active": True,
    }
    assert create_values(
        {
            "category": None,
            "name": None,
            "system_prompt": None,
            "user_prompt": None,
            "sort_order": None,
            "is_active": False,
            "id": "client-id",
            "created_at": "client-time",
            "is_system": True,
        }
    ) == {
        "category": None,
        "name": None,
        "system_prompt": None,
        "user_prompt": None,
        "sort_order": None,
        "is_active": False,
    }


def test_update_uses_only_six_key_present_fields_and_commits_empty_update_once() -> None:
    unit = MemoryUnitOfWork()
    factory = lambda: unit

    result = application.update_system_prompt(
        factory,
        ADMIN,
        "p1",
        {
            "category": None,
            "name": "Changed",
            "system_prompt": None,
            "user_prompt": "new user prompt",
            "sort_order": 0,
            "is_active": False,
            "id": "client-id",
            "updated_at": "client-time",
            "is_system": True,
            "extra": "ignored",
        },
    )

    assert unit.updated == {
        "category": None,
        "name": "Changed",
        "system_prompt": None,
        "user_prompt": "new user prompt",
        "sort_order": 0,
        "is_active": False,
    }
    assert result["category"] is None
    assert result["is_active"] is False
    assert result["id"] == "p1"
    assert set(result) == {
        "id", "category", "name", "system_prompt", "user_prompt",
        "sort_order", "is_active", "created_at", "updated_at",
    }
    assert unit.commit_count == 1

    unit.updated = None
    application.update_system_prompt(factory, ADMIN, "p1", {"extra": "only"})
    assert unit.updated == {}
    assert unit.commit_count == 2
    assert unit.close_count == 2


def test_delete_does_not_touch_user_prompt_order_references() -> None:
    unit = MemoryUnitOfWork()

    assert application.delete_system_prompt(lambda: unit, ADMIN, "p1") == {
        "message": "删除成功"
    }
    assert "p1" not in unit.rows
    assert unit.order_references == [("user-a", "p1"), ("user-b", "p1")]
    assert unit.commit_count == 1
    assert unit.close_count == 1
    assert unit.events == [
        ("delete", "p1"),
        ("commit",),
        ("close",),
    ]


def test_admin_guard_missing_rows_and_postcommit_failures_do_not_fake_success() -> None:
    unit = MemoryUnitOfWork()
    factory = lambda: unit

    with pytest.raises(AdminSystemPromptsForbidden):
        application.list_system_prompts(
            factory,
            NON_ADMIN,
            category=None,
            is_active=None,
            skip=0,
            limit=10,
        )
    assert unit.close_count == 0

    with pytest.raises(AdminSystemPromptNotFound):
        application.update_system_prompt(factory, ADMIN, "absent", {})
    assert unit.commit_count == 0
    assert unit.close_count == 1

    unit.commit_error = RuntimeError("commit outcome unknown")
    with pytest.raises(RuntimeError, match="commit outcome unknown"):
        application.update_system_prompt(factory, ADMIN, "p1", {"name": "new"})
    assert unit.commit_count == 1
    assert unit.rollback_count == 2
    assert unit.close_count == 2

    failed_readback = MemoryUnitOfWork()
    failed_readback.hide_after_commit = True
    with pytest.raises(AdminSystemPromptPersistenceError, match="disappeared after create"):
        application.create_system_prompt(lambda: failed_readback, ADMIN, {})
    assert failed_readback.commit_count == 1
    assert failed_readback.rollback_count == 1
    assert failed_readback.close_count == 1



def test_prompt_primary_error_survives_rollback_and_close_failures() -> None:
    unit = MemoryUnitOfWork()
    primary = RuntimeError("post-commit readback failed")
    unit.get_error = primary
    unit.rollback_error = RuntimeError("rollback failed")
    unit.close_error = RuntimeError("close failed")

    with pytest.raises(RuntimeError) as raised:
        application.create_system_prompt(lambda: unit, ADMIN, {})

    assert raised.value is primary
    assert unit.commit_count == 1
    assert unit.rollback_count == 1
    assert unit.close_count == 1
    assert unit.events[-2:] == [("rollback",), ("close",)]


def test_close_failure_after_success_remains_visible() -> None:
    unit = MemoryUnitOfWork()
    close_error = RuntimeError("close failed")
    unit.close_error = close_error

    with pytest.raises(RuntimeError) as raised:
        application.list_system_prompts(
            lambda: unit,
            ADMIN,
            category=None,
            is_active=None,
            skip=0,
            limit=10,
        )

    assert raised.value is close_error
    assert unit.rollback_count == 0
