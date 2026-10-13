from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from haoai_backend.admin_scene_prop_queries import application
from haoai_backend.admin_scene_prop_queries.domain import (
    AdminScenePropQueriesActor,
    PropRecord,
    SceneRecord,
)
from haoai_backend.admin_scene_prop_queries.errors import (
    AdminScenePropQueriesForbidden,
    PropNotFound,
    SceneNotFound,
)

ADMIN = AdminScenePropQueriesActor(user_id="admin", is_superuser=True)
MEMBER = AdminScenePropQueriesActor(user_id="member", is_superuser=False)
STAMP = datetime(2026, 10, 13, 4, 0)


class MemoryUnitOfWork:
    def __init__(self) -> None:
        self.scene = SceneRecord("scene-1", "series-1", "Kitchen", None, None, STAMP)
        self.prop = PropRecord("prop-1", "series-2", "Key", "Brass key", None, STAMP)
        self.scene_rows: list[SceneRecord] = [self.scene]
        self.prop_rows: list[PropRecord] = [self.prop]
        self.scene_query: dict[str, Any] | None = None
        self.prop_query: dict[str, Any] | None = None
        self.scene_lookup: str | None = None
        self.prop_lookup: str | None = None
        self.scene_error: BaseException | None = None
        self.rollback_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.rollback_count = 0
        self.close_count = 0

    def list_scenes(self, **query: Any):
        self.scene_query = query
        if self.scene_error is not None:
            raise self.scene_error
        return tuple(self.scene_rows), 14

    def get_scene(self, scene_id: str):
        self.scene_lookup = scene_id
        return next((row for row in self.scene_rows if row.id == scene_id), None)

    def list_props(self, **query: Any):
        self.prop_query = query
        return tuple(self.prop_rows), 6

    def get_prop(self, prop_id: str):
        self.prop_lookup = prop_id
        return next((row for row in self.prop_rows if row.id == prop_id), None)

    def rollback(self) -> None:
        self.rollback_count += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


def test_list_use_cases_keep_all_series_and_shape_only_legacy_fields() -> None:
    unit = MemoryUnitOfWork()
    factory_calls = 0

    def factory() -> MemoryUnitOfWork:
        nonlocal factory_calls
        factory_calls += 1
        return unit

    scenes = application.list_scenes(factory, ADMIN)
    props = application.list_props(
        factory,
        ADMIN,
        skip=3,
        limit=5,
        sort_field="canonical_key",
        sort_order="desc",
    )

    assert scenes == {
        "data": [{
            "id": "scene-1",
            "series_id": "series-1",
            "title": "Kitchen",
            "description": None,
            "image_url": None,
            "created_at": STAMP,
        }],
        "total": 14,
    }
    assert props == {
        "data": [{
            "id": "prop-1",
            "series_id": "series-2",
            "name": "Key",
            "description": "Brass key",
            "image_url": None,
            "created_at": STAMP,
        }],
        "total": 6,
    }
    assert unit.scene_query == {
        "sort_field": "created_at",
        "sort_order": "desc",
        "skip": 0,
        "limit": 10,
    }
    assert unit.prop_query == {
        "sort_field": "canonical_key",
        "sort_order": "desc",
        "skip": 3,
        "limit": 5,
    }
    assert factory_calls == 2
    assert unit.rollback_count == 0
    assert unit.close_count == 2


@pytest.mark.parametrize(
    ("use_case", "identifier", "expected", "detail"),
    [
        (application.get_scene, "missing-scene", SceneNotFound, "场景不存在"),
        (application.get_prop, "missing-prop", PropNotFound, "道具不存在"),
    ],
)
def test_detail_use_cases_return_legacy_not_found_errors(
    use_case, identifier: str, expected: type[Exception], detail: str
) -> None:
    unit = MemoryUnitOfWork()

    with pytest.raises(expected) as raised:
        use_case(lambda: unit, ADMIN, identifier)

    assert raised.value.status_code == 404
    assert raised.value.detail == detail
    assert unit.rollback_count == 1
    assert unit.close_count == 1


def test_non_admin_is_rejected_before_opening_a_business_unit() -> None:
    opened = False

    def factory():
        nonlocal opened
        opened = True
        raise AssertionError("business unit must not open")

    with pytest.raises(AdminScenePropQueriesForbidden) as raised:
        application.list_scenes(factory, MEMBER)

    assert raised.value.status_code == 403
    assert raised.value.detail == "权限不足，仅管理员可执行此操作"
    assert not opened


def test_primary_failure_survives_independent_cleanup_failures() -> None:
    unit = MemoryUnitOfWork()
    primary = RuntimeError("database read failed")
    unit.scene_error = primary
    unit.rollback_error = RuntimeError("rollback failed")
    unit.close_error = RuntimeError("close failed")

    with pytest.raises(RuntimeError) as raised:
        application.list_scenes(lambda: unit, ADMIN)

    assert raised.value is primary
    assert unit.rollback_count == 1
    assert unit.close_count == 1


def test_close_error_is_propagated_after_a_successful_read() -> None:
    unit = MemoryUnitOfWork()
    close_error = RuntimeError("close failed")
    unit.close_error = close_error

    with pytest.raises(RuntimeError) as raised:
        application.list_props(lambda: unit, ADMIN)

    assert raised.value is close_error
    assert unit.rollback_count == 0
    assert unit.close_count == 1
