"""Framework-independent model-configuration records and response projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ModelConfigActor:
    """Identity facts needed by the model-configuration use cases."""

    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class ModelConfigRecord:
    """A snapshot of either a personal or system model row."""

    id: str
    user_id: str
    category: str
    name: str
    provider: str
    api_key: str
    model: str | None
    base_url: str | None
    sort_order: int
    params: str | None
    created_at: datetime
    updated_at: datetime
    is_system: bool = False
    cost_per_call: int | None = None
    description: str | None = None
    is_active: bool | None = None
    transient_values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelOrderRecord:
    """One persisted user-specific ordering entry."""

    id: str
    user_id: str
    model_id: str
    category: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class ModelConfigCreateCommand:
    category: str
    name: str
    provider: str
    api_key: str
    model: str | None
    base_url: str | None
    sort_order: int | None
    params: str | None
    is_system: bool


@dataclass(frozen=True, slots=True)
class ConnectionConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    category: str


def model_response_values(
    record: ModelConfigRecord,
    *,
    is_system: bool | None = None,
    cost_per_call: int | None = None,
) -> dict[str, Any]:
    """Return the legacy response projection used by list/get/transition routes."""

    system_model = record.is_system if is_system is None else is_system
    if "is_active" in record.transient_values:
        is_active = record.transient_values["is_active"]
    elif record.is_active is not None:
        is_active = record.is_active
    else:
        is_active = True

    if system_model:
        response_cost = record.cost_per_call if cost_per_call is None else cost_per_call
        description = record.description
    else:
        response_cost = 0
        description = None

    return {
        "id": record.id,
        "user_id": record.user_id,
        "category": record.category,
        "name": record.name,
        "provider": record.provider,
        "api_key": record.api_key,
        "model": record.model,
        "base_url": record.base_url,
        "sort_order": record.sort_order,
        "is_system": system_model,
        "cost_per_call": response_cost,
        "description": description,
        "params": record.params,
        "is_active": is_active,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def personal_orm_response_values(record: ModelConfigRecord) -> dict[str, Any]:
    """Mirror direct ORM responses, including only transient assigned attributes."""

    values: dict[str, Any] = {
        "id": record.id,
        "user_id": record.user_id,
        "category": record.category,
        "name": record.name,
        "provider": record.provider,
        "api_key": record.api_key,
        "model": record.model,
        "base_url": record.base_url,
        "sort_order": record.sort_order,
        "params": record.params,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    values.update(record.transient_values)
    return values
