"""Prompt-config CRUD use cases with explicit transaction ownership."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar
from uuid import uuid4

from .domain import (
    AdminPromptConfigsActor,
    PromptConfigRecord,
    create_values,
    prompt_config_payload,
)
from .errors import (
    AdminPromptConfigNotFound,
    AdminPromptConfigsForbidden,
    AdminPromptConfigsUnavailable,
)
from .ports import AdminPromptConfigsUnitOfWork, AdminPromptConfigsUnitOfWorkFactory

_Result = TypeVar("_Result")


def _with_unit_of_work(
    factory: AdminPromptConfigsUnitOfWorkFactory | None,
    operation: Callable[[AdminPromptConfigsUnitOfWork], _Result],
) -> _Result:
    if factory is None:
        raise AdminPromptConfigsUnavailable()
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


def _require_admin(actor: AdminPromptConfigsActor) -> None:
    if not actor.is_superuser:
        raise AdminPromptConfigsForbidden()


def list_prompt_configs(
    factory: AdminPromptConfigsUnitOfWorkFactory | None,
    actor: AdminPromptConfigsActor,
    *,
    user_id: str | None = None,
    sort_field: str = "created_at",
    sort_order: str = "desc",
    skip: int = 0,
    limit: int = 10,
) -> dict[str, Any]:
    _require_admin(actor)

    def list_with(unit_of_work: AdminPromptConfigsUnitOfWork) -> dict[str, Any]:
        records, total = unit_of_work.list_prompt_configs(
            user_id=user_id,
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        return {
            "data": [prompt_config_payload(item) for item in records],
            "total": total,
        }

    return _with_unit_of_work(factory, list_with)


def get_prompt_config(
    factory: AdminPromptConfigsUnitOfWorkFactory | None,
    actor: AdminPromptConfigsActor,
    prompt_id: str,
) -> dict[str, Any]:
    _require_admin(actor)

    def get_with(unit_of_work: AdminPromptConfigsUnitOfWork) -> dict[str, Any]:
        record = unit_of_work.get_prompt_config(prompt_id)
        if record is None:
            raise AdminPromptConfigNotFound()
        return prompt_config_payload(record)

    return _with_unit_of_work(factory, get_with)


def create_prompt_config(
    factory: AdminPromptConfigsUnitOfWorkFactory | None,
    actor: AdminPromptConfigsActor,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    _require_admin(actor)

    def create_with(unit_of_work: AdminPromptConfigsUnitOfWork) -> dict[str, Any]:
        prompt_id = str(uuid4())
        unit_of_work.add_prompt_config(
            prompt_id,
            actor.user_id,
            create_values(data),
        )
        unit_of_work.commit()
        record = unit_of_work.refresh_prompt_config(prompt_id)
        if record is None:
            raise RuntimeError("committed prompt config could not be refreshed")
        return prompt_config_payload(record)

    return _with_unit_of_work(factory, create_with)


def update_prompt_config(
    factory: AdminPromptConfigsUnitOfWorkFactory | None,
    actor: AdminPromptConfigsActor,
    prompt_id: str,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    _require_admin(actor)

    def update_with(unit_of_work: AdminPromptConfigsUnitOfWork) -> dict[str, Any]:
        if not unit_of_work.update_prompt_config(prompt_id, data):
            raise AdminPromptConfigNotFound()
        unit_of_work.commit()
        record = unit_of_work.refresh_prompt_config(prompt_id)
        if record is None:
            raise RuntimeError("committed prompt config could not be refreshed")
        return prompt_config_payload(record)

    return _with_unit_of_work(factory, update_with)


def delete_prompt_config(
    factory: AdminPromptConfigsUnitOfWorkFactory | None,
    actor: AdminPromptConfigsActor,
    prompt_id: str,
) -> dict[str, str]:
    _require_admin(actor)

    def delete_with(unit_of_work: AdminPromptConfigsUnitOfWork) -> dict[str, str]:
        if not unit_of_work.delete_prompt_config(prompt_id):
            raise AdminPromptConfigNotFound()
        unit_of_work.commit()
        return {"message": "删除成功"}

    return _with_unit_of_work(factory, delete_with)
