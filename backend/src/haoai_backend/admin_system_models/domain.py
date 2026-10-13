"""Framework-independent system-model records and legacy projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AdminSystemModelActor:
    """Identity facts copied from public authentication before its session closes."""

    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class SystemModelRecord:
    id: str
    category: Any
    name: Any
    provider: Any
    api_key: Any
    model: Any
    base_url: Any
    cost_per_call: Any
    description: Any
    is_active: Any
    sort_order: Any
    params: Any
    created_at: datetime | None
    updated_at: datetime | None


def create_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the constructor defaults used by the fixed legacy route."""

    return {
        "category": data["category"],
        "name": data["name"],
        "provider": data["provider"],
        "model": data.get("model") or "",
        "api_key": data.get("api_key") or "",
        "base_url": data.get("base_url") or "",
        "cost_per_call": data.get("cost_per_call", 0),
        "description": data.get("description") or "",
        "is_active": data.get("is_active", True),
        "sort_order": data.get("sort_order", 0),
    }


def model_payload(model: SystemModelRecord) -> dict[str, Any]:
    """Return all fourteen legacy columns in their original public shape."""

    return {
        "id": model.id,
        "category": model.category,
        "name": model.name,
        "provider": model.provider,
        "api_key": model.api_key,
        "model": model.model,
        "base_url": model.base_url,
        "cost_per_call": model.cost_per_call,
        "description": model.description,
        "is_active": model.is_active,
        "sort_order": model.sort_order,
        "params": model.params,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
    }
