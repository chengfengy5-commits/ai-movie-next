"""Immutable administrator identity and user records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AdminUsersActor:
    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: str
    username: str
    email: str
    is_superuser: bool
    membership_type: str
    membership_expires_at: datetime | None
    credits: int
    created_at: datetime
