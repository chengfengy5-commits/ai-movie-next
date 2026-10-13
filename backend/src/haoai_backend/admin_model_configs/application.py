"""Administrator model-configuration CRUD orchestration."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping
from uuid import uuid4

from .domain import AdminModelConfigActor, create_values, model_config_payload
from .errors import (
    AdminModelConfigNotFound,
    AdminModelConfigsForbidden,
    AdminModelConfigsUnavailable,
)
from .ports import AdminModelConfigUnitOfWork, UnitOfWorkFactory


@contextmanager
def _open(factory: UnitOfWorkFactory | None) -> Iterator[AdminModelConfigUnitOfWork]:
    if factory is None:
        raise AdminModelConfigsUnavailable()
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


def _require_admin(actor: AdminModelConfigActor) -> None:
    if not actor.is_superuser:
        raise AdminModelConfigsForbidden()


def list_model_configs(
    factory: UnitOfWorkFactory | None,
    actor: AdminModelConfigActor,
    *,
    skip: int,
    limit: int,
    sort_field: str,
    sort_order: str,
    user_id: str | None,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        rows, total = unit_of_work.list_model_configs(
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
            user_id=user_id,
        )
        return {"data": [model_config_payload(row) for row in rows], "total": total}


def get_model_config(
    factory: UnitOfWorkFactory | None,
    actor: AdminModelConfigActor,
    model_id: str,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        row = unit_of_work.get_model_config(model_id)
        if row is None:
            raise AdminModelConfigNotFound()
        return model_config_payload(row)


def create_model_config(
    factory: UnitOfWorkFactory | None,
    actor: AdminModelConfigActor,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    _require_admin(actor)
    model_id = str(uuid4())
    values = create_values(data, user_id=actor.user_id)
    with _open(factory) as unit_of_work:
        row = unit_of_work.add_model_config(model_id, values)
        unit_of_work.commit()
        unit_of_work.refresh(row)
        return model_config_payload(row)


def update_model_config(
    factory: UnitOfWorkFactory | None,
    actor: AdminModelConfigActor,
    model_id: str,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        row = unit_of_work.update_model_config(model_id, data)
        if row is None:
            raise AdminModelConfigNotFound()
        unit_of_work.commit()
        unit_of_work.refresh(row)
        return model_config_payload(row)


def delete_model_config(
    factory: UnitOfWorkFactory | None,
    actor: AdminModelConfigActor,
    model_id: str,
) -> dict[str, str]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        row = unit_of_work.delete_model_config(model_id)
        if row is None:
            raise AdminModelConfigNotFound()
        unit_of_work.commit()
        return {"message": "删除成功"}
