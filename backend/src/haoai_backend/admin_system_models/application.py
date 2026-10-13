"""System-model CRUD use cases with legacy-specific transaction behavior."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping
from uuid import uuid4

from .domain import (
    AdminSystemModelActor,
    SystemModelRecord,
    create_values,
    model_payload,
)
from .errors import (
    AdminSystemModelNotFound,
    AdminSystemModelsForbidden,
    AdminSystemModelsUnavailable,
)
from .ports import Cipher, UnitOfWorkFactory, AdminSystemModelUnitOfWork

_UPDATE_FIELDS = (
    "category",
    "name",
    "provider",
    "model",
    "api_key",
    "base_url",
    "cost_per_call",
    "description",
    "is_active",
    "sort_order",
    "params",
)
_CIPHER_PREFIX = "gAAAAA"


@contextmanager
def _open(
    factory: UnitOfWorkFactory | None,
) -> Iterator[AdminSystemModelUnitOfWork]:
    if factory is None:
        raise AdminSystemModelsUnavailable()
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


def _require_admin(actor: AdminSystemModelActor) -> None:
    if not actor.is_superuser:
        raise AdminSystemModelsForbidden()


def list_system_models(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemModelActor,
    *,
    category: str | None,
    provider: str | None,
    is_active: bool | None,
    skip: int,
    limit: int,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        models, total = unit_of_work.list_models(
            category=category,
            provider=provider,
            is_active=is_active,
            skip=skip,
            limit=limit,
        )
        return {"data": [model_payload(model) for model in models], "total": total}


def get_system_model(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemModelActor,
    model_id: str,
    cipher: Cipher,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        model = unit_of_work.get_model(model_id)
        if model is None:
            raise AdminSystemModelNotFound()
        payload = model_payload(model)
        payload["api_key"] = cipher.decrypt(model.api_key)
        return payload


def create_system_model(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemModelActor,
    data: Mapping[str, Any],
    cipher: Cipher,
) -> dict[str, Any]:
    _require_admin(actor)
    values = create_values(data)
    # Creation always encrypts a non-empty input, including a cipher-looking string.
    values["api_key"] = cipher.encrypt(values["api_key"])
    model_id = str(uuid4())
    with _open(factory) as unit_of_work:
        row = unit_of_work.add_model(model_id, values)
        unit_of_work.commit()
        model = unit_of_work.refresh_model(row)
        return model_payload(model)


def update_system_model(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemModelActor,
    model_id: str,
    data: Mapping[str, Any],
    cipher: Cipher,
) -> dict[str, Any]:
    _require_admin(actor)
    changes = {field: data[field] for field in _UPDATE_FIELDS if field in data}
    requested_api_key = changes.pop("api_key", None)
    api_key_was_supplied = "api_key" in data

    with _open(factory) as unit_of_work:
        row = unit_of_work.load_model_for_update(model_id)
        if row is None:
            raise AdminSystemModelNotFound()
        if api_key_was_supplied and requested_api_key:
            if not requested_api_key.startswith(_CIPHER_PREFIX):
                changes["api_key"] = cipher.encrypt(requested_api_key)
        # The legacy update path preserves the stored value for falsey inputs
        # and strings that already look like Fernet tokens.
        unit_of_work.set_model_values(row, changes)
        unit_of_work.commit()
        model = unit_of_work.refresh_model(row)
        return model_payload(model)


def delete_system_model(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemModelActor,
    model_id: str,
) -> dict[str, str]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        if not unit_of_work.delete_model(model_id):
            raise AdminSystemModelNotFound()
        unit_of_work.commit()
        return {"message": "删除成功"}
