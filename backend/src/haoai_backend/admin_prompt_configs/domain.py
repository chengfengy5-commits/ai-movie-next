"""Framework-independent prompt-config records and projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AdminPromptConfigsActor:
    """Identity facts copied from the public authentication boundary."""

    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class PromptConfigRecord:
    id: Any
    user_id: Any
    category: Any
    name: Any
    system_prompt: Any
    user_prompt: Any
    sort_order: Any
    created_at: datetime | None
    updated_at: datetime | None


def create_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """Apply only defaults from the legacy PromptConfig constructor."""

    return {
        "category": data.get("category", ""),
        "name": data.get("name", "提示词"),
        "system_prompt": data.get("system_prompt", ""),
        "user_prompt": data.get("user_prompt", ""),
        "sort_order": data.get("sort_order", 0),
    }


def prompt_config_payload(record: PromptConfigRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "category": record.category,
        "name": record.name,
        "system_prompt": record.system_prompt,
        "user_prompt": record.user_prompt,
        "sort_order": record.sort_order,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
