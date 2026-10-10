"""Framework-independent values shared by generic task use cases and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class GenericTaskRecord:
    """Source-compatible task values required by generic task operations."""

    id: str
    user_id: str
    type: str
    status: str
    credit_cost: Any
    message_id: Any
    request_data: Any
    result: Any
    progress: Any
    progress_message: Any
    external_task_id: Any
    external_provider: Any
    model_name: Any
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class GenericTaskDraft:
    """Values for an ORM-staged processing task before its first commit."""

    id: str
    user_id: str
    type: str
    status: str
    credit_cost: Any
    message_id: str
    request_data: str
    model_name: Any
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CreditWallet:
    """The locked balance row used by the legacy generic-task ledger."""

    id: str
    user_id: str
    credits: Any


@dataclass(frozen=True, slots=True)
class CreditLedgerEntry:
    """A usage debit or refund entry, preserving raw persisted values."""

    id: str
    user_id: str
    task_id: str
    type: str
    amount: Any
    business_key: str
    related_debit_id: str | None = None


@dataclass(frozen=True, slots=True)
class TaskSubmissionRecord:
    """A submission projection whose task_ids value remains unnormalized."""

    id: str
    user_id: str
    task_ids: Any
