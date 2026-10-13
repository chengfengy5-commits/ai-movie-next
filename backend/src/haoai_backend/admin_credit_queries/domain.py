"""Immutable actors and records for administrator credit queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AdminCreditQueriesActor:
    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class UserCreditRecord:
    id: str
    user_id: str
    credits: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CreditLogRecord:
    id: str
    user_id: str
    amount: int
    balance_after: int
    type: str
    description: str | None
    task_id: str | None
    created_at: datetime
