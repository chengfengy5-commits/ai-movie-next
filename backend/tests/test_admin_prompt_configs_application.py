from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from haoai_backend.admin_prompt_configs import application
from haoai_backend.admin_prompt_configs.domain import (
    AdminPromptConfigsActor,
    PromptConfigRecord,
)
from haoai_backend.admin_prompt_configs.errors import (
    AdminPromptConfigNotFound,
    AdminPromptConfigsForbidden,
)


ADMIN = AdminPromptConfigsActor("admin-1", True)
MEMBER = AdminPromptConfigsActor("member-1", False)
NOW = datetime(2026, 10, 13, 9, 30)


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.records = {
            "existing": PromptConfigRecord(
                "existing", "owner", "text", "Existing", "system", None, 3, NOW, NOW
            )
        }
        self.events: list[tuple[Any, ...]] = []
        self.added: tuple[str, str, dict[str, Any]] | None = None
        self.updated: tuple[str, dict[str, Any]] | None = None
        self.deleted: str | None = None
        self.fail_refresh: BaseException | None = None
        self.close_error: BaseException | None = None

    def list_prompt_configs(self, **kwargs: Any):
        self.events.append(("list", kwargs))
        return (tuple(self.records.values()), len(self.records))

    def get_prompt_config(self, prompt_id: str):
        self.events.append(("get", prompt_id))
        return self.records.get(prompt_id)

    def add_prompt_config(self, prompt_id: str, user_id: str, values: Any) -> None:
        self.added = (prompt_id, user_id, dict(values))
        self.records[prompt_id] = PromptConfigRecord(
            prompt_id,
            user_id,
            values.get("category", ""),
            values.get("name", "提示词"),
            values.get("system_prompt", ""),
            values.get("user_prompt", ""),
            values.get("sort_order", 0),
            NOW,
            NOW,
        )
        self.events.append(("add", prompt_id))

    def update_prompt_config(self, prompt_id: str, changes: Any) -> bool:
        self.updated = (prompt_id, dict(changes))
        self.events.append(("update", prompt_id))
        return prompt_id in self.records

    def delete_prompt_config(self, prompt_id: str) -> bool:
        self.deleted = prompt_id
        self.events.append(("delete", prompt_id))
        return prompt_id in self.records

    def commit(self) -> None:
        self.events.append(("commit",))

    def refresh_prompt_config(self, prompt_id: str):
        self.events.append(("refresh", prompt_id))
        if self.fail_refresh is not None:
            raise self.fail_refresh
        return self.records.get(prompt_id)

    def rollback(self) -> None:
        self.events.append(("rollback",))

    def close(self) -> None:
        self.events.append(("close",))
        if self.close_error is not None:
            raise self.close_error


def test_list_and_detail_project_all_legacy_fields_without_writing() -> None:
    uow = FakeUnitOfWork()
    factory_calls = 0

    def factory() -> FakeUnitOfWork:
        nonlocal factory_calls
        factory_calls += 1
        return uow

    page = application.list_prompt_configs(
        factory,
        ADMIN,
        user_id="owner",
        sort_field="updated_at",
        sort_order="DESC",
        skip=2,
        limit=4,
    )
    assert page == {
        "data": [
            {
                "id": "existing",
                "user_id": "owner",
                "category": "text",
                "name": "Existing",
                "system_prompt": "system",
                "user_prompt": None,
                "sort_order": 3,
                "created_at": NOW,
                "updated_at": NOW,
            }
        ],
        "total": 1,
    }
    assert uow.events[0] == (
        "list",
        {
            "user_id": "owner",
            "sort_field": "updated_at",
            "sort_order": "DESC",
            "skip": 2,
            "limit": 4,
        },
    )
    assert not any(event[0] == "commit" for event in uow.events)
    assert application.get_prompt_config(factory, ADMIN, "existing")["id"] == "existing"
    assert factory_calls == 2


def test_create_uses_actor_owner_and_missing_only_legacy_defaults() -> None:
    uow = FakeUnitOfWork()
    payload = application.create_prompt_config(
        lambda: uow,
        ADMIN,
        {
            "id": "client-id",
            "user_id": "client-owner",
            "category": None,
            "name": "",
            "system_prompt": None,
            "user_prompt": False,
            "sort_order": 0,
            "created_at": "client timestamp",
        },
    )
    assert uow.added is not None
    prompt_id, owner, values = uow.added
    assert prompt_id != "client-id"
    assert owner == "admin-1"
    assert values == {
        "category": None,
        "name": "",
        "system_prompt": None,
        "user_prompt": False,
        "sort_order": 0,
    }
    assert payload["id"] == prompt_id
    assert payload["user_id"] == "admin-1"
    assert payload["category"] is None
    assert [event[0] for event in uow.events] == ["add", "commit", "refresh", "close"]


def test_update_forwards_raw_mapping_and_refreshes_after_one_commit() -> None:
    uow = FakeUnitOfWork()
    changes = {
        "id": "client-id",
        "user_id": "ignored by persistence adapter",
        "created_at": None,
        "updated_at": "not prevalidated",
        "user": {"invalid": "relationship assignment"},
        "unknown": 7,
    }
    result = application.update_prompt_config(lambda: uow, ADMIN, "existing", changes)
    assert result["id"] == "existing"
    assert uow.updated == ("existing", changes)
    assert [event[0] for event in uow.events] == ["update", "commit", "refresh", "close"]
    assert sum(event[0] == "commit" for event in uow.events) == 1


def test_delete_commits_once_without_refresh_and_missing_is_404() -> None:
    uow = FakeUnitOfWork()
    assert application.delete_prompt_config(lambda: uow, ADMIN, "existing") == {
        "message": "删除成功"
    }
    assert [event[0] for event in uow.events] == ["delete", "commit", "close"]

    absent = FakeUnitOfWork()
    with pytest.raises(AdminPromptConfigNotFound, match="提示词不存在"):
        application.delete_prompt_config(lambda: absent, ADMIN, "missing")
    assert [event[0] for event in absent.events] == ["delete", "rollback", "close"]


def test_failure_cleanup_preserves_primary_exception_and_success_close_failure() -> None:
    uow = FakeUnitOfWork()
    primary = RuntimeError("fresh readback failed")
    uow.fail_refresh = primary
    uow.close_error = RuntimeError("close failed")
    with pytest.raises(RuntimeError) as caught:
        application.create_prompt_config(lambda: uow, ADMIN, {})
    assert caught.value is primary
    assert [event[0] for event in uow.events] == [
        "add", "commit", "refresh", "rollback", "close"
    ]

    close_failure = FakeUnitOfWork()
    close_failure.close_error = RuntimeError("successful close failed")
    with pytest.raises(RuntimeError, match="successful close failed"):
        application.list_prompt_configs(lambda: close_failure, ADMIN)


def test_forbidden_actor_and_unavailable_service_do_not_open_unit_of_work() -> None:
    def unexpected_factory():
        raise AssertionError("a rejected request must not open business persistence")

    with pytest.raises(AdminPromptConfigsForbidden):
        application.list_prompt_configs(unexpected_factory, MEMBER)
    from haoai_backend.admin_prompt_configs.errors import AdminPromptConfigsUnavailable

    with pytest.raises(AdminPromptConfigsUnavailable):
        application.list_prompt_configs(None, ADMIN)
