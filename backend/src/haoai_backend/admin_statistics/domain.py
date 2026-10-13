"""Framework-independent administrator statistics projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AdminStatisticsActor:
    user_id: str
    is_superuser: bool


def statistics_payload(values: dict[str, Any], upstream_balance: Any) -> dict[str, Any]:
    return {
        "upstream": {"balance": upstream_balance},
        "users": {
            "total": values["total_users"],
            "premium": values["premium_users"],
            "active_members": values["active_members"],
            "new_this_month": values["new_users_month"],
        },
        "credits": {
            "total_balance": values["total_credits"],
            "used_this_month": abs(values["credits_used_month"]),
            "charged_this_month": values["credits_charged_month"],
        },
        "tasks": {
            "total": values["total_tasks"],
            "this_month": values["tasks_month"],
            "completed": values["tasks_completed"],
            "failed": values["tasks_failed"],
        },
        "content": {
            "series": values["total_series"],
            "chapters": values["total_chapters"],
            "characters": values["total_characters"],
            "scenes": values["total_scenes"],
            "props": values["total_props"],
        },
        "revenue": {
            "total": 0,
            "this_month": 0,
            "subscriptions": values["premium_users"],
        },
    }
