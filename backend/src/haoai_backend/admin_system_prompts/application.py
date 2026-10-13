"""System-prompt CRUD use cases with explicit write and readback ownership."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping
from uuid import uuid4

from .domain import (
    AdminSystemPromptActor,
    create_values,
    prompt_payload,
)
from .errors import (
    AdminSystemPromptNotFound,
    AdminSystemPromptPersistenceError,
    AdminSystemPromptsForbidden,
    AdminSystemPromptsUnavailable,
)
from .ports import AdminSystemPromptUnitOfWork, UnitOfWorkFactory

_UPDATE_FIELDS = (
    "category",
    "name",
    "system_prompt",
    "user_prompt",
    "sort_order",
    "is_active",
)


@contextmanager
def _open(factory: UnitOfWorkFactory | None) -> Iterator[AdminSystemPromptUnitOfWork]:
    if factory is None:
        raise AdminSystemPromptsUnavailable()
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


def _require_admin(actor: AdminSystemPromptActor) -> None:
    if not actor.is_superuser:
        raise AdminSystemPromptsForbidden()


def list_system_prompts(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemPromptActor,
    *,
    category: str | None,
    is_active: bool | None,
    skip: int,
    limit: int,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        prompts, total = unit_of_work.list_prompts(
            category=category,
            is_active=is_active,
            skip=skip,
            limit=limit,
        )
        return {"data": [prompt_payload(prompt) for prompt in prompts], "total": total}


def get_system_prompt(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemPromptActor,
    prompt_id: str,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        prompt = unit_of_work.get_prompt(prompt_id)
        if prompt is None:
            raise AdminSystemPromptNotFound()
        return prompt_payload(prompt)


def create_system_prompt(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemPromptActor,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    _require_admin(actor)
    prompt_id = str(uuid4())
    with _open(factory) as unit_of_work:
        unit_of_work.add_prompt(prompt_id, create_values(data))
        unit_of_work.commit()
        prompt = unit_of_work.get_prompt(prompt_id)
        if prompt is None:
            raise AdminSystemPromptPersistenceError(
                "system prompt disappeared after create commit"
            )
        return prompt_payload(prompt)


def update_system_prompt(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemPromptActor,
    prompt_id: str,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    _require_admin(actor)
    changes = {field: data[field] for field in _UPDATE_FIELDS if field in data}
    with _open(factory) as unit_of_work:
        if not unit_of_work.update_prompt(prompt_id, changes):
            raise AdminSystemPromptNotFound()
        unit_of_work.commit()
        prompt = unit_of_work.get_prompt(prompt_id)
        if prompt is None:
            raise AdminSystemPromptPersistenceError(
                "system prompt disappeared after update commit"
            )
        return prompt_payload(prompt)


def delete_system_prompt(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemPromptActor,
    prompt_id: str,
) -> dict[str, str]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        if not unit_of_work.delete_prompt(prompt_id):
            raise AdminSystemPromptNotFound()
        unit_of_work.commit()
        return {"message": "删除成功"}
