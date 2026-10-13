"""Framework-independent system-prompt records and projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AdminSystemPromptActor:
    """Identity facts copied from the public authentication boundary."""

    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class SystemPromptRecord:
    id: str
    category: Any
    name: Any
    system_prompt: Any
    user_prompt: Any
    sort_order: Any
    is_active: Any
    created_at: datetime | None
    updated_at: datetime | None


def create_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """Apply only the defaults used by the legacy constructor."""

    return {
        "category": data.get("category", ""),
        "name": data.get("name", "系统提示词"),
        "system_prompt": data.get("system_prompt", ""),
        "user_prompt": data.get("user_prompt", ""),
        "sort_order": data.get("sort_order", 0),
        "is_active": data.get("is_active", True),
    }


def prompt_payload(prompt: SystemPromptRecord) -> dict[str, Any]:
    return {
        "id": prompt.id,
        "category": prompt.category,
        "name": prompt.name,
        "system_prompt": prompt.system_prompt,
        "user_prompt": prompt.user_prompt,
        "sort_order": prompt.sort_order,
        "is_active": prompt.is_active,
        "created_at": prompt.created_at,
        "updated_at": prompt.updated_at,
    }
