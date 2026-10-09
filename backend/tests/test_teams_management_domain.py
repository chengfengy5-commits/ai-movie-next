from __future__ import annotations

from datetime import datetime

from haoai_backend.teams.management.domain import (
    invite_payload,
    member_detail_payload,
    team_list_payload,
    team_payload,
)


def test_team_payloads_keep_source_fields_and_list_projection() -> None:
    created_at = datetime(2026, 10, 9, 10, 0)
    row = {
        "id": "team-a",
        "name": "团队",
        "owner_id": "user-a",
        "created_at": created_at,
        "ignored": "not part of response",
    }

    assert team_payload(row) == {
        "id": "team-a",
        "name": "团队",
        "owner_id": "user-a",
        "created_at": created_at,
    }
    assert team_list_payload(row, member_count=2, my_role="admin") == {
        "id": "team-a",
        "name": "团队",
        "owner_id": "user-a",
        "created_at": created_at,
        "member_count": 2,
        "my_role": "admin",
    }


def test_member_detail_keeps_permission_order_duplicates_and_missing_user() -> None:
    member = {
        "user_id": "missing-user",
        "role": "admin",
        "permissions": '["manage_invites","unknown","view_tasks","manage_invites"]',
        "joined_at": datetime(2026, 10, 9),
    }

    assert member_detail_payload(member, None) == {
        "user_id": "missing-user",
        "username": "",
        "avatar_url": None,
        "role": "admin",
        "permissions": ["manage_invites", "view_tasks", "manage_invites"],
        "joined_at": member["joined_at"],
    }
