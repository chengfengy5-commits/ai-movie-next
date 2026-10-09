"""Pure values and permission rules for series access."""

from __future__ import annotations

import json
from dataclasses import dataclass

CLAIMED_SERIES_PERMISSION = "enter_claimed_series"
KNOWN_TEAM_PERMISSIONS = frozenset(
    {
        "view_tasks",
        "view_usage",
        "manage_invites",
        "remove_members",
        "delete_series",
        CLAIMED_SERIES_PERMISSION,
    }
)


@dataclass(frozen=True, slots=True)
class ActorIdentity:
    user_id: str


@dataclass(frozen=True, slots=True)
class SeriesAccessRecord:
    id: str
    user_id: str
    team_id: str | None
    claimed_by: str | None


@dataclass(frozen=True, slots=True)
class TeamMembership:
    role: str
    permissions: object


def parse_team_permissions(raw_permissions: object) -> frozenset[str]:
    """Keep only known exact keys; any malformed list invalidates the whole set."""
    if not raw_permissions:
        return frozenset()

    try:
        parsed = json.loads(raw_permissions)
    except (TypeError, ValueError):
        return frozenset()

    if not isinstance(parsed, list):
        return frozenset()

    try:
        return frozenset(
            permission
            for permission in parsed
            if permission in KNOWN_TEAM_PERMISSIONS
        )
    except TypeError:
        # The legacy list comprehension was inside one try block. An unhashable
        # value therefore invalidated the whole list, including earlier entries.
        return frozenset()


def can_enter_claimed_series(membership: TeamMembership | None) -> bool:
    if membership is None:
        return False
    if membership.role == "owner":
        return True
    return CLAIMED_SERIES_PERMISSION in parse_team_permissions(membership.permissions)
