from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from haoai_backend.admin_model_configs import application
from haoai_backend.admin_model_configs.domain import (
    AdminModelConfigActor,
    create_values,
    model_config_payload,
)
from haoai_backend.admin_model_configs.errors import (
    AdminModelConfigNotFound,
    AdminModelConfigsForbidden,
)

ADMIN = AdminModelConfigActor(user_id="admin-id", is_superuser=True)
MEMBER = AdminModelConfigActor(user_id="member-id", is_superuser=False)
STAMP = datetime(2026, 10, 13, 9, 30)
PAYLOAD_KEYS = {
    "id", "user_id", "category", "name", "provider", "model", "api_key",
    "base_url", "sort_order", "created_at", "updated_at",
}


class MemoryUnitOfWork:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}
        self.events: list[tuple[Any, ...]] = []
        self.commit_error: Exception | None = None
        self.rollback_error: Exception | None = None
        self.close_error: Exception | None = None

    def list_model_configs(self, **kwargs):
        self.events.append(("list", kwargs))
        rows = list(self.rows.values())
        if kwargs["user_id"]:
            rows = [row for row in rows if row.user_id == kwargs["user_id"]]
        total = len(rows)
        return rows[kwargs["skip"]:kwargs["skip"] + kwargs["limit"]], total

    def get_model_config(self, model_id):
        self.events.append(("get", model_id))
        return self.rows.get(model_id)

    def add_model_config(self, model_id, values):
        self.events.append(("add", model_id, dict(values)))
        row = SimpleNamespace(
            id=model_id,
            params=None,
            created_at=STAMP,
            updated_at=STAMP,
            **dict(values),
        )
        self.rows[model_id] = row
        return row

    def update_model_config(self, model_id, changes):
        self.events.append(("update", model_id, dict(changes)))
        row = self.rows.get(model_id)
        if row is None:
            return None
        for key, value in changes.items():
            if hasattr(row, key) and key not in ("id", "user_id"):
                setattr(row, key, value)
        return row

    def delete_model_config(self, model_id):
        self.events.append(("delete", model_id))
        return self.rows.pop(model_id, None)

    def commit(self):
        self.events.append(("commit",))
        if self.commit_error is not None:
            raise self.commit_error

    def refresh(self, row):
        self.events.append(("refresh", row.id))

    def rollback(self):
        self.events.append(("rollback",))
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self):
        self.events.append(("close",))
        if self.close_error is not None:
            raise self.close_error


def test_create_defaults_actor_ownership_raw_key_and_exact_projection() -> None:
    assert create_values({}, user_id="admin-id") == {
        "user_id": "admin-id", "category": "text", "name": "", "provider": "",
        "api_key": "", "model": "", "base_url": "", "sort_order": 0,
    }
    assert create_values(
        {"category": None, "sort_order": None, "id": "client", "user_id": "other", "params": "ignored"},
        user_id="admin-id",
    ) == {
        "user_id": "admin-id", "category": None, "name": "", "provider": "",
        "api_key": "", "model": "", "base_url": "", "sort_order": None,
    }

    unit = MemoryUnitOfWork()
    result = application.create_model_config(
        lambda: unit,
        ADMIN,
        {"id": "client-id", "user_id": "client-user", "api_key": "plain-secret", "params": "ignored"},
    )
    assert result["id"] != "client-id"
    assert result["user_id"] == "admin-id"
    assert result["api_key"] == "plain-secret"
    assert set(result) == PAYLOAD_KEYS
    assert unit.events[-3:] == [("commit",), ("refresh", result["id"]), ("close",)]
    assert unit.events[0][0] == "add"
    assert "params" not in unit.events[0][2]


def test_update_delete_notfound_admin_guard_and_success_lifecycle() -> None:
    unit = MemoryUnitOfWork()
    created = application.create_model_config(lambda: unit, ADMIN, {"name": "before"})
    updated = application.update_model_config(
        lambda: unit,
        ADMIN,
        created["id"],
        {"id": "ignored", "user_id": "ignored", "name": "after", "params": "{}", "unknown": 4},
    )
    assert updated["id"] == created["id"]
    assert updated["name"] == "after"
    assert unit.rows[created["id"]].params == "{}"
    assert unit.events[-3:] == [("commit",), ("refresh", created["id"]), ("close",)]

    assert application.delete_model_config(lambda: unit, ADMIN, created["id"]) == {"message": "删除成功"}
    assert unit.events[-3:] == [("delete", created["id"]), ("commit",), ("close",)]
    with pytest.raises(AdminModelConfigNotFound, match="模型配置不存在"):
        application.get_model_config(lambda: unit, ADMIN, "missing")

    calls = 0
    def unused_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("unauthorized calls must not open a business unit")
    with pytest.raises(AdminModelConfigsForbidden):
        application.list_model_configs(
            unused_factory, MEMBER, skip=0, limit=10, sort_field="created_at",
            sort_order="desc", user_id=None,
        )
    assert calls == 0


def test_primary_exception_survives_rollback_and_close_failures() -> None:
    unit = MemoryUnitOfWork()
    primary = RuntimeError("commit outcome unknown")
    unit.commit_error = primary
    unit.rollback_error = RuntimeError("rollback failed")
    unit.close_error = RuntimeError("close failed")
    with pytest.raises(RuntimeError) as caught:
        application.create_model_config(lambda: unit, ADMIN, {})
    assert caught.value is primary
    assert unit.events[-3:] == [("commit",), ("rollback",), ("close",)]

    successful = MemoryUnitOfWork()
    close_error = RuntimeError("close failed after success")
    successful.close_error = close_error
    with pytest.raises(RuntimeError) as caught:
        application.create_model_config(lambda: successful, ADMIN, {})
    assert caught.value is close_error
    assert successful.events[-1] == ("close",)
