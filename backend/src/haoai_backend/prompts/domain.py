"""Framework-independent prompt records and actors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PromptActor:
    """A prompt-local identity carrying only the authorization facts this module needs."""

    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class PromptRecord:
    id: str
    user_id: str
    category: str
    name: str
    system_prompt: str | None
    user_prompt: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime
    is_system: bool
    # Personal ORM rows do not map is_active; an explicitly assigned update value is transient.
    is_active: bool | None = None


@dataclass(frozen=True, slots=True)
class PromptOrderRecord:
    id: str
    prompt_id: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class PromptCreateCommand:
    category: str
    name: str
    system_prompt: str | None
    user_prompt: str | None
    is_system: bool


def prompt_list_item(prompt: PromptRecord) -> dict[str, object]:
    """Build the legacy list projection, omitting is_active for personal prompts."""

    item: dict[str, object] = {
        "id": prompt.id,
        "user_id": prompt.user_id,
        "category": prompt.category,
        "name": prompt.name,
        "system_prompt": prompt.system_prompt,
        "user_prompt": prompt.user_prompt,
        "sort_order": prompt.sort_order,
        "is_system": prompt.is_system,
        "created_at": prompt.created_at,
        "updated_at": prompt.updated_at,
    }
    if prompt.is_system:
        item["is_active"] = bool(prompt.is_active)
    return item


def prompt_response_values(prompt: PromptRecord) -> dict[str, object]:
    """Build the PromptConfigResponse-compatible projection."""

    values: dict[str, object] = {
        "id": prompt.id,
        "user_id": prompt.user_id,
        "category": prompt.category,
        "name": prompt.name,
        "system_prompt": prompt.system_prompt,
        "user_prompt": prompt.user_prompt,
        "sort_order": prompt.sort_order,
        "created_at": prompt.created_at,
        "updated_at": prompt.updated_at,
    }
    if prompt.is_active is not None:
        values["is_active"] = prompt.is_active
    return values


def transitioned_prompt_payload(
    prompt: PromptRecord,
    *,
    is_system: bool,
) -> dict[str, object]:
    """Build the distinct legacy promote/demote response shape."""

    payload: dict[str, object] = {
        "id": prompt.id,
        "user_id": prompt.user_id,
        "category": prompt.category,
        "name": prompt.name,
        "system_prompt": prompt.system_prompt,
        "user_prompt": prompt.user_prompt,
        "sort_order": prompt.sort_order,
        "is_system": is_system,
        "created_at": prompt.created_at.isoformat() if prompt.created_at else None,
        "updated_at": prompt.updated_at.isoformat() if prompt.updated_at else None,
    }
    if not is_system:
        payload["is_active"] = True
    return payload
