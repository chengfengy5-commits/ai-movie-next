"""Read-only administrator scene and prop use cases."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from .domain import AdminScenePropQueriesActor, PropRecord, SceneRecord
from .errors import (
    AdminScenePropQueriesForbidden,
    PropNotFound,
    SceneNotFound,
)
from .ports import AdminScenePropQueriesUnitOfWork, UnitOfWorkFactory
from .schemas import (
    PropListPayload,
    PropPayload,
    SceneListPayload,
    ScenePayload,
)


@contextmanager
def _open_read_unit_of_work(
    factory: UnitOfWorkFactory,
) -> Iterator[AdminScenePropQueriesUnitOfWork]:
    unit_of_work = factory()
    try:
        yield unit_of_work
    except BaseException:
        try:
            unit_of_work.rollback()
        except BaseException:
            pass
        try:
            unit_of_work.close()
        except BaseException:
            pass
        raise
    else:
        unit_of_work.close()


def _require_admin(actor: AdminScenePropQueriesActor) -> None:
    if not actor.is_superuser:
        raise AdminScenePropQueriesForbidden()


def _scene_payload(record: SceneRecord) -> ScenePayload:
    return {
        "id": record.id,
        "series_id": record.series_id,
        "title": record.title,
        "description": record.description,
        "image_url": record.image_url,
        "created_at": record.created_at,
    }


def _prop_payload(record: PropRecord) -> PropPayload:
    return {
        "id": record.id,
        "series_id": record.series_id,
        "name": record.name,
        "description": record.description,
        "image_url": record.image_url,
        "created_at": record.created_at,
    }


def list_scenes(
    factory: UnitOfWorkFactory,
    actor: AdminScenePropQueriesActor,
    *,
    skip: int = 0,
    limit: int = 10,
    sort_field: str = "created_at",
    sort_order: str = "desc",
) -> SceneListPayload:
    _require_admin(actor)
    with _open_read_unit_of_work(factory) as unit_of_work:
        records, total = unit_of_work.list_scenes(
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        return {"data": [_scene_payload(record) for record in records], "total": total}


def get_scene(
    factory: UnitOfWorkFactory,
    actor: AdminScenePropQueriesActor,
    scene_id: str,
) -> ScenePayload:
    _require_admin(actor)
    with _open_read_unit_of_work(factory) as unit_of_work:
        record = unit_of_work.get_scene(scene_id)
        if record is None:
            raise SceneNotFound()
        return _scene_payload(record)


def list_props(
    factory: UnitOfWorkFactory,
    actor: AdminScenePropQueriesActor,
    *,
    skip: int = 0,
    limit: int = 10,
    sort_field: str = "created_at",
    sort_order: str = "desc",
) -> PropListPayload:
    _require_admin(actor)
    with _open_read_unit_of_work(factory) as unit_of_work:
        records, total = unit_of_work.list_props(
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        return {"data": [_prop_payload(record) for record in records], "total": total}


def get_prop(
    factory: UnitOfWorkFactory,
    actor: AdminScenePropQueriesActor,
    prop_id: str,
) -> PropPayload:
    _require_admin(actor)
    with _open_read_unit_of_work(factory) as unit_of_work:
        record = unit_of_work.get_prop(prop_id)
        if record is None:
            raise PropNotFound()
        return _prop_payload(record)
