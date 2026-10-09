"""Immutable task observation records with source-compatible values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """The task columns needed by the existing read-only endpoints."""

    id: str
    user_id: str
    type: str
    status: str
    message_id: str | None = None
    result: Any = None
    request_data: Any = None
    credit_cost: Any = None
    billing_status: Any = None
    progress: Any = None
    progress_message: Any = None
    external_task_id: Any = None
    external_provider: Any = None
    model_name: Any = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    team_id: str | None = None


@dataclass(frozen=True, slots=True)
class BillingUnitRecord:
    """A task's billing projection, without applying falsey-value fallback."""

    task_id: str
    quoted_amount: Any
    billing_status: Any
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TaskSubmissionRecord:
    """A persisted idempotency receipt before task/unit consistency checks."""

    id: str
    user_id: str
    operation: str
    idempotency_key: str
    task_ids: Any
    status: Any = None
    quoted_amount: Any = None
    billing_status: Any = None
    request_digest: Any = None


@dataclass(frozen=True, slots=True)
class TeamMembershipRecord:
    """The membership columns consulted by task-request visibility policy."""

    team_id: str
    user_id: str
    role: str
    permissions: str | None
