"""Framework-independent request and response values for admin task reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AdminTaskFilters:
    """Raw optional filters; adapters preserve source truthiness semantics."""

    user_id: str | None = None
    task_type: str | None = None
    status: str | None = None
    model_name: str | None = None


@dataclass(frozen=True, slots=True)
class AdminTaskListItem:
    """The exact nine-field list projection returned by the existing endpoint."""

    id: str
    user_id: str
    type: str
    status: str
    credit_cost: Any
    message_id: Any
    model_name: Any
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdminTaskDetail:
    """The exact nine-field detail projection, including raw JSON columns."""

    id: str
    user_id: str
    type: str
    status: str
    message_id: Any
    request_data: Any
    result: Any
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdminTaskPage:
    """A count-before-list result with source-compatible page values."""

    data: tuple[AdminTaskListItem, ...]
    total: int
