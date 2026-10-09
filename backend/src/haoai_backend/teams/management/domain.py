"""Pure response projections for the team management family."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from haoai_backend.teams.policy import member_permissions


def team_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "owner_id": row["owner_id"],
        "created_at": row["created_at"],
    }


def team_list_payload(
    row: Mapping[str, Any],
    *,
    member_count: int,
    my_role: str,
) -> dict[str, Any]:
    return {
        **team_payload(row),
        "member_count": member_count,
        "my_role": my_role,
    }


def member_detail_payload(
    member: Mapping[str, Any],
    user: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "user_id": member["user_id"],
        "username": user["username"] if user is not None else "",
        "avatar_url": user["avatar_url"] if user is not None else None,
        "role": member["role"],
        "permissions": member_permissions(member["permissions"]),
        "joined_at": member["joined_at"],
    }


def invite_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "code": row["code"],
        "status": row["status"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "used_by": row["used_by"],
        "used_at": row["used_at"],
    }
