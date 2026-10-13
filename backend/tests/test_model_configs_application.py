from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from haoai_backend.model_configs import application
from haoai_backend.model_configs.application import (
    create_model_config,
    delete_model_config,
    demote_system_model_to_personal,
    get_model_config,
    list_model_configs,
    promote_model_to_system,
    reorder_model_configs,
    update_model_config,
)
from haoai_backend.model_configs.domain import (
    ModelConfigActor,
    ModelConfigCreateCommand,
    ModelConfigRecord,
    ModelOrderRecord,
)
from haoai_backend.model_configs.errors import ModelConfigNotFound


NOW = datetime(2026, 10, 13, 9, 30)


def model(
    model_id: str,
    *,
    user_id: str = "user-a",
    category: str = "image",
    name: str | None = None,
    sort_order: int = 0,
    is_system: bool = False,
    is_active: bool | None = True,
    params: str | None = None,
) -> ModelConfigRecord:
    return ModelConfigRecord(
        id=model_id,
        user_id="system" if is_system else user_id,
        category=category,
        name=name or model_id,
        provider="custom",
        api_key="test-key",
        model="test-model",
        base_url="https://provider.example.test",
        sort_order=sort_order,
        params=params,
        created_at=NOW,
        updated_at=NOW,
        is_system=is_system,
        cost_per_call=7 if is_system else None,
        description="system detail" if is_system else None,
        is_active=is_active,
    )


class FakeUnitOfWork:
    def __init__(
        self,
        *,
        personal: list[ModelConfigRecord] = (),
        system: list[ModelConfigRecord] = (),
        orders: list[ModelOrderRecord] = (),
    ) -> None:
        self.personal = {row.id: row for row in personal}
        self.system = {row.id: row for row in system}
        self.orders = list(orders)
        self.events: list[tuple[Any, ...]] = []
        self.close_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.fail_on_add_order = False

    def list_personal_models(self, user_id: str, category: str | None):
        rows = [
            row for row in self.personal.values()
            if row.user_id == user_id and (not category or row.category == category)
        ]
        return tuple(sorted(rows, key=lambda row: row.sort_order))

    def list_system_models(self, category: str | None, *, include_inactive: bool):
        rows = [
            row for row in self.system.values()
            if (not category or row.category == category)
            and (include_inactive or row.is_active)
        ]
        return tuple(sorted(rows, key=lambda row: row.sort_order))

    def list_model_orders(self, user_id: str, category: str | None):
        rows = [
            row for row in self.orders
            if row.user_id == user_id and (not category or row.category == category)
        ]
        return tuple(sorted(rows, key=lambda row: row.sort_order))

    def find_personal_model(self, model_id: str, user_id: str):
        self.events.append(("find-personal", model_id, user_id))
        row = self.personal.get(model_id)
        return row if row is not None and row.user_id == user_id else None

    def find_any_personal_model(self, model_id: str):
        self.events.append(("find-any-personal", model_id))
        return self.personal.get(model_id)

    def find_system_model(self, model_id: str):
        self.events.append(("find-system", model_id))
        return self.system.get(model_id)

    def max_personal_sort_order(self, category: str, user_id: str):
        values = [
            row.sort_order for row in self.personal.values()
            if row.category == category and row.user_id == user_id
        ]
        self.events.append(("max-personal", category, user_id))
        return max(values) if values else None

    def max_system_sort_order(self, category: str):
        values = [row.sort_order for row in self.system.values() if row.category == category]
        self.events.append(("max-system", category))
        return max(values) if values else None

    def add_personal_model(self, row: ModelConfigRecord) -> None:
        self.events.append(("add-personal", row.id, row.user_id))
        self.personal[row.id] = row

    def add_system_model(self, row: ModelConfigRecord) -> None:
        self.events.append(("add-system", row.id))
        self.system[row.id] = row

    def update_personal_model(self, model_id: str, user_id: str, changes):
        self.events.append(("update-personal", model_id, dict(changes)))
        row = self.personal.get(model_id)
        if row is None or row.user_id != user_id:
            return None
        fields = {}
        transient = dict(row.transient_values)
        for field, value in changes.items():
            if field in {"cost_per_call", "is_active"}:
                transient[field] = value
            else:
                fields[field] = value
        fields["transient_values"] = transient
        updated = replace(row, **fields)
        self.personal[model_id] = updated
        return updated

    def update_system_model(self, model_id: str, changes):
        self.events.append(("update-system", model_id, dict(changes)))
        row = self.system.get(model_id)
        if row is None:
            return None
        updated = replace(row, **changes)
        self.system[model_id] = updated
        return updated

    def set_personal_params(self, model_id: str, user_id: str, params: str | None) -> None:
        self.events.append(("set-personal-params", model_id, params))
        row = self.personal[model_id]
        self.personal[model_id] = replace(row, params=params)

    def set_system_params(self, model_id: str, params: str | None) -> None:
        self.events.append(("set-system-params", model_id, params))
        self.system[model_id] = replace(self.system[model_id], params=params)

    def delete_personal_model(self, model_id: str, user_id: str) -> bool:
        self.events.append(("delete-personal", model_id, user_id))
        row = self.personal.get(model_id)
        if row is None or row.user_id != user_id:
            return False
        del self.personal[model_id]
        return True

    def delete_any_personal_model(self, model_id: str) -> bool:
        self.events.append(("delete-any-personal", model_id))
        return self.personal.pop(model_id, None) is not None

    def delete_system_model(self, model_id: str) -> bool:
        self.events.append(("delete-system", model_id))
        return self.system.pop(model_id, None) is not None

    def update_personal_sort_order(
        self, model_id: str, category: str, user_id: str, sort_order: int
    ) -> None:
        self.events.append(("update-sort", model_id, category, user_id, sort_order))
        row = self.personal.get(model_id)
        if row is not None and row.category == category and row.user_id == user_id:
            self.personal[model_id] = replace(row, sort_order=sort_order)

    def delete_model_orders(self, user_id: str, category: str) -> None:
        self.events.append(("delete-orders", user_id, category))
        self.orders = [
            row for row in self.orders
            if not (row.user_id == user_id and row.category == category)
        ]

    def delete_all_model_order_references(self, model_id: str) -> None:
        self.events.append(("delete-order-references", model_id))
        self.orders = [row for row in self.orders if row.model_id != model_id]

    def add_model_order(self, order: ModelOrderRecord) -> None:
        self.events.append(("add-order", order.model_id, order.category, order.sort_order))
        if self.fail_on_add_order:
            raise RuntimeError("injected constraint failure")
        self.orders.append(order)

    def commit(self) -> None:
        self.events.append(("commit",))
        self.commit_count += 1

    def rollback(self) -> None:
        self.events.append(("rollback",))
        self.rollback_count += 1

    def refresh_personal_model(self, model_id: str, user_id: str):
        self.events.append(("refresh-personal", model_id, user_id))
        row = self.personal.get(model_id)
        return row if row is not None and row.user_id == user_id else None

    def refresh_system_model(self, model_id: str):
        self.events.append(("refresh-system", model_id))
        return self.system.get(model_id)

    def close(self) -> None:
        self.events.append(("close",))
        self.close_count += 1


def actor(*, user_id: str = "user-a", admin: bool = False) -> ModelConfigActor:
    return ModelConfigActor(user_id, admin)


def test_list_reuses_duplicate_order_ids_and_cross_category_collisions() -> None:
    unit = FakeUnitOfWork(
        system=[
            model("shared", is_system=True, name="system shared", sort_order=1),
            model("system-only", is_system=True, sort_order=5, is_active=False),
        ],
        personal=[
            model("shared", name="personal shared", sort_order=1),
            model("personal-only", sort_order=3),
        ],
        orders=[
            ModelOrderRecord("o1", "user-a", "shared", "text", 0),
            ModelOrderRecord("o2", "user-a", "shared", "video", 1),
            ModelOrderRecord("o3", "user-a", "missing", "image", 2),
        ],
    )

    result = list_model_configs(lambda: unit, actor(admin=True), None)

    assert [item["id"] for item in result] == [
        "shared",
        "shared",
        "personal-only",
        "system-only",
    ]
    assert [item["name"] for item in result[:2]] == [
        "personal shared",
        "personal shared",
    ]
    assert result[-1]["is_active"] is False
    assert unit.close_count == 1


def test_get_prefers_even_an_inactive_system_model_over_same_id_personal() -> None:
    unit = FakeUnitOfWork(
        system=[model("same", is_system=True, is_active=False, name="system")],
        personal=[model("same", name="personal")],
    )

    result = get_model_config(lambda: unit, actor(), "same")

    assert result["name"] == "system"
    assert result["is_system"] is True
    assert result["is_active"] is False
    assert [event[0] for event in unit.events] == ["find-system", "close"]


def test_create_computes_params_once_and_ignores_requested_sort_for_personal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = FakeUnitOfWork(personal=[model("old", sort_order=8)])
    calls: list[tuple[Any, ...]] = []

    def fill(params, provider, category, model_name):
        calls.append((params, provider, category, model_name))
        return '{"filled":true}'

    monkeypatch.setattr(application, "_fill_default_params", fill)
    result = create_model_config(
        lambda: unit,
        actor(),
        ModelConfigCreateCommand(
            category="image",
            name="new",
            provider="custom",
            api_key="new-key",
            model=None,
            base_url=None,
            sort_order=999,
            params=None,
            is_system=True,
        ),
        id_factory=lambda: "new-personal",
    )

    assert calls == [(None, "custom", "image", None)]
    assert unit.personal["new-personal"].sort_order == 9
    assert unit.personal["new-personal"].params == '{"filled":true}'
    assert result["sort_order"] == 9
    assert "is_active" not in result
    assert "cost_per_call" not in result
    assert [event[0] for event in unit.events] == [
        "max-personal",
        "add-personal",
        "commit",
        "refresh-personal",
        "close",
    ]


def test_admin_create_system_model_uses_system_sort_and_default_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = FakeUnitOfWork(system=[model("old", is_system=True, sort_order=4)])
    calls: list[int] = []
    monkeypatch.setattr(
        application,
        "_fill_default_params",
        lambda *args: calls.append(1) or None,
    )

    result = create_model_config(
        lambda: unit,
        actor(admin=True),
        ModelConfigCreateCommand(
            category="image",
            name="new",
            provider="custom",
            api_key="key",
            model="model",
            base_url="https://example.test",
            sort_order=77,
            params=None,
            is_system=True,
        ),
        id_factory=lambda: "system-new",
    )

    assert calls == [1]
    assert unit.system["system-new"].sort_order == 5
    assert result["is_system"] is True
    assert result["cost_per_call"] == 0
    assert result["description"] is None
    assert result["is_active"] is True


def test_update_personal_first_preserves_explicit_null_transient_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_personal = model("same", params='{"billing_mode":"legacy"}')
    shared_system = model("same", is_system=True, name="system")
    unit = FakeUnitOfWork(personal=[shared_personal], system=[shared_system])
    fill_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        application,
        "_fill_default_params",
        lambda *args: fill_calls.append(args) or '{"defaulted":true}',
    )

    result = update_model_config(
        lambda: unit,
        actor(admin=True),
        "same",
        {"name": "updated", "params": None, "cost_per_call": None, "is_active": None},
    )

    assert unit.personal["same"].name == "updated"
    assert unit.system["same"].name == "system"
    assert fill_calls == [
        (None, "custom", "image", "test-model")
    ]
    assert unit.events[0] == (
        "update-personal",
        "same",
        {"name": "updated", "params": None, "cost_per_call": None, "is_active": None},
    )
    assert [event[0] for event in unit.events] == [
        "update-personal",
        "set-personal-params",
        "commit",
        "refresh-personal",
        "close",
    ]
    assert result["cost_per_call"] is None
    assert result["is_active"] is None


def test_delete_personal_does_not_clean_order_references() -> None:
    order = ModelOrderRecord("o1", "user-a", "same", "image", 0)
    unit = FakeUnitOfWork(
        personal=[model("same")],
        system=[model("same", is_system=True)],
        orders=[order],
    )

    result = delete_model_config(lambda: unit, actor(admin=True), "same")

    assert result == {"message": "模型配置已删除"}
    assert "same" not in unit.personal
    assert "same" in unit.system
    assert unit.orders == [order]
    assert [event[0] for event in unit.events] == [
        "delete-personal",
        "commit",
        "close",
    ]


def test_promote_copies_model_and_clears_every_order_reference_in_source_order() -> None:
    source = model("personal", user_id="owner", sort_order=22, params='{"x":1}')
    unit = FakeUnitOfWork(
        personal=[source],
        system=[model("old-system", is_system=True, sort_order=6)],
        orders=[
            ModelOrderRecord("o1", "user-a", "personal", "image", 0),
            ModelOrderRecord("o2", "user-b", "personal", "text", 1),
        ],
    )

    result = promote_model_to_system(
        lambda: unit,
        actor(admin=True),
        "personal",
        id_factory=lambda: "promoted",
    )

    assert unit.system["promoted"].sort_order == 7
    assert unit.system["promoted"].api_key == "test-key"
    assert unit.system["promoted"].params == '{"x":1}'
    assert unit.orders == []
    assert result["is_system"] is True
    assert [event[0] for event in unit.events] == [
        "find-any-personal",
        "max-system",
        "add-system",
        "delete-any-personal",
        "delete-order-references",
        "commit",
        "refresh-system",
        "close",
    ]


def test_demote_copies_to_admin_and_clears_references() -> None:
    unit = FakeUnitOfWork(
        system=[model("system-id", is_system=True, sort_order=50, params='{"x":1}')],
        personal=[model("old", user_id="admin-a", sort_order=3)],
        orders=[ModelOrderRecord("o1", "user-a", "system-id", "image", 0)],
    )

    result = demote_system_model_to_personal(
        lambda: unit,
        actor(user_id="admin-a", admin=True),
        "system-id",
        id_factory=lambda: "demoted",
    )

    assert unit.personal["demoted"].user_id == "admin-a"
    assert unit.personal["demoted"].sort_order == 4
    assert unit.system == {}
    assert unit.orders == []
    assert result["user_id"] == "admin-a"
    assert result["is_system"] is False
    assert [event[0] for event in unit.events] == [
        "find-system",
        "max-personal",
        "add-personal",
        "delete-system",
        "delete-order-references",
        "commit",
        "refresh-personal",
        "close",
    ]


def test_reorder_keeps_duplicate_and_missing_ids_in_all_writes() -> None:
    unit = FakeUnitOfWork(personal=[model("existing")])
    result = reorder_model_configs(
        lambda: unit,
        actor(),
        "image",
        ["existing", "missing", "existing"],
        id_factory=iter(["o1", "o2", "o3"]).__next__,
    )

    assert result == {"message": "排序已更新"}
    assert [event[1] for event in unit.events if event[0] == "update-sort"] == [
        "existing",
        "missing",
        "existing",
    ]
    assert [
        (event[1], event[3]) for event in unit.events if event[0] == "add-order"
    ] == [
        ("existing", 0),
        ("missing", 1),
        ("existing", 2),
    ]
    assert [event[0] for event in unit.events] == [
        "update-sort",
        "update-sort",
        "update-sort",
        "delete-orders",
        "add-order",
        "add-order",
        "add-order",
        "commit",
        "close",
    ]


def test_reorder_failure_rolls_back_without_retry_or_deduplication() -> None:
    unit = FakeUnitOfWork()
    unit.fail_on_add_order = True
    ids = ["duplicate", "duplicate"]

    with pytest.raises(RuntimeError, match="injected constraint failure"):
        reorder_model_configs(lambda: unit, actor(), "image", ids)

    assert ids == ["duplicate", "duplicate"]
    assert [event[0] for event in unit.events] == [
        "update-sort",
        "update-sort",
        "delete-orders",
        "add-order",
        "rollback",
        "close",
    ]
    assert unit.commit_count == 0


def test_missing_model_uses_legacy_detail() -> None:
    unit = FakeUnitOfWork()

    with pytest.raises(ModelConfigNotFound) as error:
        get_model_config(lambda: unit, actor(), "missing")

    assert error.value.detail == "模型配置不存在"
    assert unit.rollback_count == 1
    assert unit.close_count == 1
