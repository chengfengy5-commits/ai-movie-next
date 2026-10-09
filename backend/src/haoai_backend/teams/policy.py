"""Shared team permission parsing and policy helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


TEAM_PERMISSIONS = {
    "view_tasks": "查看成员任务",
    "view_usage": "查看 AI 用量",
    "manage_invites": "管理邀请码",
    "remove_members": "移除成员",
    "delete_series": "删除团队剧集",
    "enter_claimed_series": "进入他人认领的剧集",
}
DEFAULT_ADMIN_PERMISSIONS = ["view_tasks", "manage_invites"]


def member_permissions(raw_permissions: str | None) -> list[Any]:
    """Decode the response list, retaining valid-key order and duplicates."""
    if not raw_permissions:
        return []
    try:
        decoded = json.loads(raw_permissions)
        if not isinstance(decoded, list):
            return []
        return [permission for permission in decoded if permission in TEAM_PERMISSIONS]
    except (TypeError, ValueError):
        return []


def has_team_permission(
    role: str,
    raw_permissions: str | None,
    permission: str,
) -> bool:
    if role == "owner":
        return True
    return permission in member_permissions(raw_permissions)


def permissions_for_update(values: Iterable[Any]) -> list[Any]:
    """Filter and deduplicate a bare request list in source order.

    An unhashable item intentionally raises TypeError, matching the legacy
    write path rather than converting the request into a validation error.
    """
    return list(dict.fromkeys(value for value in values if value in TEAM_PERMISSIONS))


def default_permissions_for_role(role: str) -> str | None:
    if role == "admin":
        return json.dumps(DEFAULT_ADMIN_PERMISSIONS)
    if role == "member":
        return None
    raise ValueError(f"unsupported team role: {role}")
