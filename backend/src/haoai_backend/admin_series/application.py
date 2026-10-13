"""Legacy administrator series use cases with explicit worker transactions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar
from uuid import uuid4

from .domain import (
    AdminSeriesActor,
    NewSeries,
    SeriesRecord,
    series_payload,
)
from .errors import (
    AdminSeriesForbidden,
    AdminSeriesNotFound,
    AdminSeriesUnavailable,
)
from .ports import UnitOfWorkFactory

_Result = TypeVar("_Result")


def _with_unit_of_work(
    factory: UnitOfWorkFactory | None,
    operation: Callable[[object], _Result],
) -> _Result:
    if factory is None:
        raise AdminSeriesUnavailable()

    unit_of_work = factory()
    try:
        result = operation(unit_of_work)
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

    unit_of_work.close()
    return result


def _require_admin(actor: AdminSeriesActor) -> None:
    if not actor.is_superuser:
        raise AdminSeriesForbidden()


def list_series(
    factory: UnitOfWorkFactory | None,
    actor: AdminSeriesActor,
    *,
    skip: int = 0,
    limit: int = 10,
    sort_field: str = "created_at",
    sort_order: str = "desc",
    user_id: str | None = None,
    name: str | None = None,
) -> dict[str, object]:
    _require_admin(actor)

    def list_with(unit_of_work):
        rows, total = unit_of_work.list_series(
            user_id=user_id,
            name=name,
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        return {"data": [series_payload(row) for row in rows], "total": total}

    return _with_unit_of_work(factory, list_with)


def get_series(
    factory: UnitOfWorkFactory | None,
    actor: AdminSeriesActor,
    series_id: str,
) -> dict[str, object]:
    _require_admin(actor)

    def get_with(unit_of_work):
        row = unit_of_work.load_series(series_id)
        if row is None:
            raise AdminSeriesNotFound()
        return series_payload(row)

    return _with_unit_of_work(factory, get_with)


def create_series(
    factory: UnitOfWorkFactory | None,
    actor: AdminSeriesActor,
    data: Mapping[str, object],
) -> dict[str, object]:
    _require_admin(actor)
    series = NewSeries(
        id=str(uuid4()),
        user_id=actor.user_id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        image_url=data.get("image_url"),
    )

    def create_with(unit_of_work):
        row = unit_of_work.add_series(series)
        unit_of_work.commit()
        return series_payload(unit_of_work.refresh_series(row))

    return _with_unit_of_work(factory, create_with)


def update_series(
    factory: UnitOfWorkFactory | None,
    actor: AdminSeriesActor,
    series_id: str,
    data: Mapping[str, object],
) -> dict[str, object]:
    _require_admin(actor)

    def update_with(unit_of_work):
        row = unit_of_work.load_series_for_update(series_id)
        if row is None:
            raise AdminSeriesNotFound()
        for key, value in data.items():
            if hasattr(row, key):
                setattr(row, key, value)
        unit_of_work.commit()
        return series_payload(unit_of_work.refresh_series(row))

    return _with_unit_of_work(factory, update_with)
