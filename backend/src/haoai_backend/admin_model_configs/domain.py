"""Framework-free actor and legacy model-configuration projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AdminModelConfigActor:
    """Administrator identity copied from the public authentication boundary."""

    user_id: str
    is_superuser: bool


def create_values(data: Mapping[str, Any], *, user_id: str) -> dict[str, Any]:
    """Apply only constructor defaults from the legacy ModelConfig route."""

    return {
        "user_id": user_id,
        "category": data.get("category", "text"),
        "name": data.get("name", ""),
        "provider": data.get("provider", ""),
        "api_key": data.get("api_key", ""),
        "model": data.get("model", ""),
        "base_url": data.get("base_url", ""),
        "sort_order": data.get("sort_order", 0),
    }


def model_config_payload(row: Any) -> dict[str, Any]:
    """Return the exact eleven-field legacy response, including raw API keys."""

    return {
        "id": row.id,
        "user_id": row.user_id,
        "category": row.category,
        "name": row.name,
        "provider": row.provider,
        "model": row.model,
        "api_key": row.api_key,
        "base_url": row.base_url,
        "sort_order": row.sort_order,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
