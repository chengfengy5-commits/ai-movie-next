"""Model-configuration use cases preserving legacy ownership and ordering rules."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator
from uuid import uuid4

from .domain import (
    ModelConfigActor,
    ModelConfigCreateCommand,
    ModelConfigRecord,
    ModelOrderRecord,
    model_response_values,
    personal_orm_response_values,
)
from .errors import ModelConfigNotFound, ModelConfigsForbidden, ModelConfigsUnavailable
from .parameter_defaults import _fill_default_params
from .ports import ModelConfigsUnitOfWork, ModelConfigsUnitOfWorkFactory


@contextmanager
def _open(
    factory: ModelConfigsUnitOfWorkFactory | None,
) -> Iterator[ModelConfigsUnitOfWork]:
    if factory is None:
        raise ModelConfigsUnavailable()
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


def _require_admin(actor: ModelConfigActor) -> None:
    if not actor.is_superuser:
        raise ModelConfigsForbidden()


def _readback(record: ModelConfigRecord | None, detail: str) -> ModelConfigRecord:
    if record is None:
        raise RuntimeError(detail)
    return record


def _next_order(maximum: int | None) -> int:
    return (maximum or 0) + 1


def list_model_configs(
    factory: ModelConfigsUnitOfWorkFactory | None,
    actor: ModelConfigActor,
    category: str | None,
) -> list[dict[str, object]]:
    with _open(factory) as unit_of_work:
        personal = unit_of_work.list_personal_models(actor.user_id, category)
        system = unit_of_work.list_system_models(
            category,
            include_inactive=actor.is_superuser,
        )
        result = [
            model_response_values(model)
            for model in (*system, *personal)
        ]
        result.sort(key=lambda item: item["sort_order"])

        orders = unit_of_work.list_model_orders(
            actor.user_id,
            category if category else None,
        )
        if not orders:
            return result

        ordered_ids = [order.model_id for order in orders]
        id_set = set(ordered_ids)
        ordered = [item for item in result if item["id"] in id_set]
        remainder = [item for item in result if item["id"] not in id_set]
        id_to_item = {item["id"]: item for item in ordered}
        ordered = [
            id_to_item[model_id]
            for model_id in ordered_ids
            if model_id in id_to_item
        ]
        return ordered + remainder


def get_model_config(
    factory: ModelConfigsUnitOfWorkFactory | None,
    actor: ModelConfigActor,
    model_id: str,
) -> dict[str, object]:
    with _open(factory) as unit_of_work:
        system = unit_of_work.find_system_model(model_id)
        if system is not None:
            return model_response_values(system)
        personal = unit_of_work.find_personal_model(model_id, actor.user_id)
        if personal is None:
            raise ModelConfigNotFound()
        return model_response_values(personal)


def create_model_config(
    factory: ModelConfigsUnitOfWorkFactory | None,
    actor: ModelConfigActor,
    command: ModelConfigCreateCommand,
    *,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    params_value = _fill_default_params(
        command.params,
        command.provider,
        command.category,
        command.model,
    )
    new_id = id_factory or (lambda: str(uuid4()))

    with _open(factory) as unit_of_work:
        if command.is_system and actor.is_superuser:
            model = ModelConfigRecord(
                id=new_id(),
                user_id="system",
                category=command.category,
                name=command.name,
                provider=command.provider,
                api_key=command.api_key,
                model=command.model,
                base_url=command.base_url,
                sort_order=_next_order(
                    unit_of_work.max_system_sort_order(command.category)
                ),
                params=params_value,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                is_system=True,
                cost_per_call=0,
                description=None,
                is_active=True,
            )
            unit_of_work.add_system_model(model)
            unit_of_work.commit()
            saved = _readback(
                unit_of_work.refresh_system_model(model.id),
                "系统模型提交后无法读取",
            )
            return model_response_values(saved)

        model = ModelConfigRecord(
            id=new_id(),
            user_id=actor.user_id,
            category=command.category,
            name=command.name,
            provider=command.provider,
            api_key=command.api_key,
            model=command.model,
            base_url=command.base_url,
            sort_order=_next_order(
                unit_of_work.max_personal_sort_order(
                    command.category,
                    actor.user_id,
                )
            ),
            params=params_value,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        unit_of_work.add_personal_model(model)
        unit_of_work.commit()
        saved = _readback(
            unit_of_work.refresh_personal_model(model.id, actor.user_id),
            "个人模型提交后无法读取",
        )
        return personal_orm_response_values(saved)


def update_model_config(
    factory: ModelConfigsUnitOfWorkFactory | None,
    actor: ModelConfigActor,
    model_id: str,
    changes: Mapping[str, object],
) -> dict[str, object]:
    with _open(factory) as unit_of_work:
        personal = unit_of_work.update_personal_model(
            model_id,
            actor.user_id,
            changes,
        )
        if personal is not None:
            params_value = _fill_default_params(
                personal.params,
                personal.provider,
                personal.category,
                personal.model,
            )
            unit_of_work.set_personal_params(
                model_id,
                actor.user_id,
                params_value,
            )
            unit_of_work.commit()
            saved = _readback(
                unit_of_work.refresh_personal_model(model_id, actor.user_id),
                "个人模型提交后无法读取",
            )
            return personal_orm_response_values(saved)

        if actor.is_superuser:
            system = unit_of_work.update_system_model(model_id, changes)
            if system is not None:
                params_value = _fill_default_params(
                    system.params,
                    system.provider,
                    system.category,
                    system.model,
                )
                unit_of_work.set_system_params(model_id, params_value)
                unit_of_work.commit()
                saved = _readback(
                    unit_of_work.refresh_system_model(model_id),
                    "系统模型提交后无法读取",
                )
                return model_response_values(saved)

        raise ModelConfigNotFound()


def delete_model_config(
    factory: ModelConfigsUnitOfWorkFactory | None,
    actor: ModelConfigActor,
    model_id: str,
) -> dict[str, str]:
    with _open(factory) as unit_of_work:
        if unit_of_work.delete_personal_model(model_id, actor.user_id):
            unit_of_work.commit()
            return {"message": "模型配置已删除"}

        if actor.is_superuser and unit_of_work.delete_system_model(model_id):
            unit_of_work.commit()
            return {"message": "系统模型已删除"}

        raise ModelConfigNotFound()


def promote_model_to_system(
    factory: ModelConfigsUnitOfWorkFactory | None,
    actor: ModelConfigActor,
    model_id: str,
    *,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    _require_admin(actor)
    new_id = id_factory or (lambda: str(uuid4()))

    with _open(factory) as unit_of_work:
        source = unit_of_work.find_any_personal_model(model_id)
        if source is None:
            raise ModelConfigNotFound("用户模型不存在")
        promoted = ModelConfigRecord(
            id=new_id(),
            user_id="system",
            category=source.category,
            name=source.name,
            provider=source.provider,
            api_key=source.api_key,
            model=source.model,
            base_url=source.base_url,
            sort_order=_next_order(
                unit_of_work.max_system_sort_order(source.category)
            ),
            params=source.params,
            created_at=source.created_at,
            updated_at=source.updated_at,
            is_system=True,
            cost_per_call=0,
            description=None,
            is_active=True,
        )
        unit_of_work.add_system_model(promoted)
        unit_of_work.delete_any_personal_model(model_id)
        unit_of_work.delete_all_model_order_references(model_id)
        unit_of_work.commit()
        saved = _readback(
            unit_of_work.refresh_system_model(promoted.id),
            "系统模型提交后无法读取",
        )
        return model_response_values(saved)


def demote_system_model_to_personal(
    factory: ModelConfigsUnitOfWorkFactory | None,
    actor: ModelConfigActor,
    model_id: str,
    *,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    _require_admin(actor)
    new_id = id_factory or (lambda: str(uuid4()))

    with _open(factory) as unit_of_work:
        source = unit_of_work.find_system_model(model_id)
        if source is None:
            raise ModelConfigNotFound("系统模型不存在")
        personal = ModelConfigRecord(
            id=new_id(),
            user_id=actor.user_id,
            category=source.category,
            name=source.name,
            provider=source.provider,
            api_key=source.api_key,
            model=source.model,
            base_url=source.base_url,
            sort_order=_next_order(
                unit_of_work.max_personal_sort_order(
                    source.category,
                    actor.user_id,
                )
            ),
            params=source.params,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )
        unit_of_work.add_personal_model(personal)
        unit_of_work.delete_system_model(model_id)
        unit_of_work.delete_all_model_order_references(model_id)
        unit_of_work.commit()
        saved = _readback(
            unit_of_work.refresh_personal_model(personal.id, actor.user_id),
            "个人模型提交后无法读取",
        )
        return model_response_values(saved)


def reorder_model_configs(
    factory: ModelConfigsUnitOfWorkFactory | None,
    actor: ModelConfigActor,
    category: str,
    model_ids: list[str],
    *,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, str]:
    new_id = id_factory or (lambda: str(uuid4()))

    with _open(factory) as unit_of_work:
        for index, model_id in enumerate(model_ids):
            unit_of_work.update_personal_sort_order(
                model_id,
                category,
                actor.user_id,
                index,
            )

        unit_of_work.delete_model_orders(actor.user_id, category)
        for index, model_id in enumerate(model_ids):
            unit_of_work.add_model_order(
                ModelOrderRecord(
                    id=new_id(),
                    user_id=actor.user_id,
                    model_id=model_id,
                    category=category,
                    sort_order=index,
                )
            )

        unit_of_work.commit()
        return {"message": "排序已更新"}
