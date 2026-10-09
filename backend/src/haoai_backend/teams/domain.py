"""Framework-independent team records and explicit shared dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


DEFAULT_TEAM_CONFIG = {
    "team_created_limit": 3,
    "team_joined_limit": 3,
    "team_member_limit": 20,
    "invite_code_ttl_hours": 24,
    "chapter_lock_idle_minutes": 15,
}
INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True, slots=True)
class TeamRecord:
    id: str
    name: str
    owner_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TeamMembershipRecord:
    id: str
    team_id: str
    user_id: str
    role: str
    permissions: str | None
    joined_at: datetime


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: str
    username: str
    email: str | None
    avatar_url: str | None


@dataclass(frozen=True, slots=True)
class TeamConfig:
    team_created_limit: int = 3
    team_joined_limit: int = 3
    team_member_limit: int = 20
    invite_code_ttl_hours: int = 24
    chapter_lock_idle_minutes: int = 15

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | None) -> TeamConfig:
        values = row or {}
        return cls(
            team_created_limit=values.get("team_created_limit") or 3,
            team_joined_limit=values.get("team_joined_limit") or 3,
            team_member_limit=values.get("team_member_limit") or 20,
            invite_code_ttl_hours=values.get("invite_code_ttl_hours") or 24,
            chapter_lock_idle_minutes=values.get("chapter_lock_idle_minutes") or 15,
        )


@dataclass(frozen=True, slots=True)
class AssignmentPlan:
    """A first-snapshot-derived set of columns for one queued UPDATE."""

    table: Any
    primary_key: Mapping[str, Any]
    first_values: Mapping[str, Any]
    assigned_values: Mapping[str, Any]
    changed_values: Mapping[str, Any]
