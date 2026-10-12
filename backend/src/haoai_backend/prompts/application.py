"""Prompt configuration and ordering use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import TypeVar
from uuid import uuid4

from .domain import (
    PromptActor,
    PromptCreateCommand,
    PromptOrderRecord,
    PromptRecord,
    prompt_list_item,
    prompt_response_values,
    transitioned_prompt_payload,
)
from .errors import PromptForbidden, PromptNotFound, PromptsUnavailable
from .ports import PromptsUnitOfWork, PromptsUnitOfWorkFactory

T = TypeVar("T")


def _invoke(
    factory: PromptsUnitOfWorkFactory | None,
    operation: Callable[[PromptsUnitOfWork], T],
) -> T:
    if factory is None:
        raise PromptsUnavailable()
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


def _next_order(maximum: int | None) -> int:
    return (maximum or 0) + 1


def _admin(actor: PromptActor) -> None:
    if not actor.is_superuser:
        raise PromptForbidden()


def _readback(record: PromptRecord | None, detail: str) -> PromptRecord:
    if record is None:
        raise RuntimeError(detail)
    return record


def list_prompt_configs(
    factory: PromptsUnitOfWorkFactory | None,
    actor: PromptActor,
    category: str | None,
) -> list[dict[str, object]]:
    def operation(unit_of_work: PromptsUnitOfWork) -> list[dict[str, object]]:
        personal = unit_of_work.list_personal_prompts(actor.user_id, category)
        system = unit_of_work.list_system_prompts(
            category,
            include_inactive=actor.is_superuser,
        )
        prompts = [prompt_list_item(prompt) for prompt in (*system, *personal)]
        prompts.sort(key=lambda item: item["sort_order"])

        orders = unit_of_work.list_prompt_orders(
            actor.user_id,
            category if category else None,
        )
        if not orders:
            return prompts

        ordered_ids = [order.prompt_id for order in orders]
        id_set = set(ordered_ids)
        ordered = [prompt for prompt in prompts if prompt["id"] in id_set]
        remainder = [prompt for prompt in prompts if prompt["id"] not in id_set]
        id_to_prompt = {prompt["id"]: prompt for prompt in ordered}
        ordered = [id_to_prompt[prompt_id] for prompt_id in ordered_ids if prompt_id in id_to_prompt]
        return ordered + remainder

    return _invoke(factory, operation)


def get_prompt_config(
    factory: PromptsUnitOfWorkFactory | None,
    actor: PromptActor,
    prompt_id: str,
) -> dict[str, object]:
    def operation(unit_of_work: PromptsUnitOfWork) -> dict[str, object]:
        prompt = unit_of_work.find_personal_prompt(prompt_id, actor.user_id)
        if prompt is None:
            raise PromptNotFound()
        return prompt_response_values(prompt)

    return _invoke(factory, operation)


def create_prompt_config(
    factory: PromptsUnitOfWorkFactory | None,
    actor: PromptActor,
    command: PromptCreateCommand,
    *,
    now: Callable[[], datetime] = datetime.utcnow,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    new_id = id_factory or (lambda: str(uuid4()))

    def operation(unit_of_work: PromptsUnitOfWork) -> dict[str, object]:
        timestamp = now()
        if command.is_system and actor.is_superuser:
            prompt = PromptRecord(
                id=new_id(),
                user_id="system",
                category=command.category,
                name=command.name,
                system_prompt=command.system_prompt,
                user_prompt=command.user_prompt,
                sort_order=_next_order(unit_of_work.max_system_sort_order(command.category)),
                created_at=timestamp,
                updated_at=timestamp,
                is_system=True,
                is_active=True,
            )
            unit_of_work.add_system_prompt(prompt)
            unit_of_work.commit()
            saved = _readback(
                unit_of_work.refresh_system_prompt(prompt.id),
                "系统提示词提交后无法读取",
            )
            # The old constructor omitted this field, so its response model default remains True.
            return prompt_response_values(replace(saved, is_active=None))

        prompt = PromptRecord(
            id=new_id(),
            user_id=actor.user_id,
            category=command.category,
            name=command.name,
            system_prompt=command.system_prompt,
            user_prompt=command.user_prompt,
            sort_order=_next_order(
                unit_of_work.max_personal_sort_order(command.category, actor.user_id)
            ),
            created_at=timestamp,
            updated_at=timestamp,
            is_system=False,
        )
        unit_of_work.add_personal_prompt(prompt)
        unit_of_work.commit()
        saved = _readback(
            unit_of_work.refresh_personal_prompt(prompt.id, actor.user_id),
            "个人提示词提交后无法读取",
        )
        return prompt_response_values(saved)

    return _invoke(factory, operation)


def update_prompt_config(
    factory: PromptsUnitOfWorkFactory | None,
    actor: PromptActor,
    prompt_id: str,
    changes: Mapping[str, object],
) -> dict[str, object]:
    def operation(unit_of_work: PromptsUnitOfWork) -> dict[str, object]:
        personal = unit_of_work.update_personal_prompt(prompt_id, actor.user_id, changes)
        if personal is not None:
            unit_of_work.commit()
            saved = _readback(
                unit_of_work.refresh_personal_prompt(prompt_id, actor.user_id),
                "个人提示词提交后无法读取",
            )
            response = prompt_response_values(saved)
            if "is_active" in changes:
                # The legacy ORM keeps this unmapped assignment on the response instance.
                response["is_active"] = changes["is_active"]
            return response

        if actor.is_superuser:
            system = unit_of_work.update_system_prompt(prompt_id, changes)
            if system is not None:
                unit_of_work.commit()
                saved = _readback(
                    unit_of_work.refresh_system_prompt(prompt_id),
                    "系统提示词提交后无法读取",
                )
                # The legacy system-update constructor omitted is_active even when disabled.
                return prompt_response_values(replace(saved, is_active=None))

        raise PromptNotFound()

    return _invoke(factory, operation)


def delete_prompt_config(
    factory: PromptsUnitOfWorkFactory | None,
    actor: PromptActor,
    prompt_id: str,
) -> dict[str, str]:
    def operation(unit_of_work: PromptsUnitOfWork) -> dict[str, str]:
        if unit_of_work.delete_personal_prompt(prompt_id, actor.user_id):
            unit_of_work.commit()
            return {"message": "提示词配置已删除"}
        if actor.is_superuser and unit_of_work.delete_system_prompt(prompt_id):
            unit_of_work.commit()
            return {"message": "系统提示词已删除"}
        raise PromptNotFound()

    return _invoke(factory, operation)


def promote_prompt_to_system(
    factory: PromptsUnitOfWorkFactory | None,
    actor: PromptActor,
    prompt_id: str,
    *,
    now: Callable[[], datetime] = datetime.utcnow,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    _admin(actor)
    new_id = id_factory or (lambda: str(uuid4()))

    def operation(unit_of_work: PromptsUnitOfWork) -> dict[str, object]:
        source = unit_of_work.find_any_personal_prompt(prompt_id)
        if source is None:
            raise PromptNotFound("用户提示词不存在")
        timestamp = now()
        promoted = PromptRecord(
            id=new_id(),
            user_id="system",
            category=source.category,
            name=source.name,
            system_prompt=source.system_prompt,
            user_prompt=source.user_prompt,
            sort_order=_next_order(unit_of_work.max_system_sort_order(source.category)),
            created_at=timestamp,
            updated_at=timestamp,
            is_system=True,
            is_active=True,
        )
        unit_of_work.add_system_prompt(promoted)
        unit_of_work.delete_any_personal_prompt(prompt_id)
        unit_of_work.delete_all_prompt_order_references(prompt_id)
        unit_of_work.commit()
        saved = _readback(
            unit_of_work.refresh_system_prompt(promoted.id),
            "系统提示词提交后无法读取",
        )
        return transitioned_prompt_payload(saved, is_system=True)

    return _invoke(factory, operation)


def demote_system_prompt_to_personal(
    factory: PromptsUnitOfWorkFactory | None,
    actor: PromptActor,
    prompt_id: str,
    *,
    now: Callable[[], datetime] = datetime.utcnow,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    _admin(actor)
    new_id = id_factory or (lambda: str(uuid4()))

    def operation(unit_of_work: PromptsUnitOfWork) -> dict[str, object]:
        source = unit_of_work.find_system_prompt(prompt_id)
        if source is None:
            raise PromptNotFound("系统提示词不存在")
        timestamp = now()
        personal = PromptRecord(
            id=new_id(),
            user_id=actor.user_id,
            category=source.category,
            name=source.name,
            system_prompt=source.system_prompt,
            user_prompt=source.user_prompt,
            sort_order=_next_order(
                unit_of_work.max_personal_sort_order(source.category, actor.user_id)
            ),
            created_at=timestamp,
            updated_at=timestamp,
            is_system=False,
            is_active=None,
        )
        unit_of_work.add_personal_prompt(personal)
        unit_of_work.delete_system_prompt(prompt_id)
        unit_of_work.delete_all_prompt_order_references(prompt_id)
        unit_of_work.commit()
        saved = _readback(
            unit_of_work.refresh_personal_prompt(personal.id, actor.user_id),
            "个人提示词提交后无法读取",
        )
        return transitioned_prompt_payload(saved, is_system=False)

    return _invoke(factory, operation)


def reorder_prompt_configs(
    factory: PromptsUnitOfWorkFactory | None,
    actor: PromptActor,
    category: str,
    prompt_ids: list[str],
    *,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, str]:
    new_id = id_factory or (lambda: str(uuid4()))

    def operation(unit_of_work: PromptsUnitOfWork) -> dict[str, str]:
        for index, prompt_id in enumerate(prompt_ids):
            unit_of_work.update_personal_sort_order(
                prompt_id,
                category,
                actor.user_id,
                index,
            )
        unit_of_work.delete_prompt_orders(actor.user_id, category)
        for index, prompt_id in enumerate(prompt_ids):
            unit_of_work.add_prompt_order(
                PromptOrderRecord(id=new_id(), prompt_id=prompt_id, sort_order=index),
                user_id=actor.user_id,
                category=category,
            )
        unit_of_work.commit()
        return {"message": "排序已更新"}

    return _invoke(factory, operation)
