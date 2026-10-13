"""Typing aliases for legacy raw-object requests and response projections."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypeAlias, TypedDict


AdminPromptConfigBody: TypeAlias = dict[str, Any]


class PromptConfigPayload(TypedDict):
    id: Any
    user_id: Any
    category: Any
    name: Any
    system_prompt: Any
    user_prompt: Any
    sort_order: Any
    created_at: datetime | None
    updated_at: datetime | None


class PromptConfigListPayload(TypedDict):
    data: list[PromptConfigPayload]
    total: int
