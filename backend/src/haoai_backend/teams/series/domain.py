"""Pure projections used by the team-series application."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from haoai_backend.teams.domain import UserRecord


@dataclass(frozen=True, slots=True)
class TeamSeriesPage:
    total: int
    rows: tuple[Mapping[str, Any], ...]
    users: Mapping[str, UserRecord]
    counts: Mapping[str, Mapping[str, int]]


def clamp_series_page(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(max(1, page_size), 50)


def total_pages(total: int, page_size: int) -> int:
    return max(1, math.ceil(total / page_size))


def avatar_url_for_email(email: str | None) -> str | None:
    """Mirror the legacy QQ/Foxmail avatar mapping without network access."""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    if domain not in {"qq.com", "foxmail.com"}:
        return None
    qq = email.strip().lower().split("@", 1)[0]
    if not qq.isdigit():
        return None
    return f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=100"


def series_list_item(
    row: Mapping[str, Any],
    users: Mapping[str, UserRecord],
    counts: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    owner = users.get(row["user_id"])
    claimant = users.get(row["claimed_by"]) if row["claimed_by"] else None
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "image_url": row["image_url"],
        "user_id": row["user_id"],
        "owner_name": owner.username if owner else "",
        "owner_avatar_url": avatar_url_for_email(owner.email) if owner else None,
        "claimed_by": row["claimed_by"],
        "claimed_by_username": claimant.username if claimant else "",
        "claimed_by_avatar_url": avatar_url_for_email(claimant.email) if claimant else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "chapter_count": counts["chapter_count"].get(row["id"], 0),
        "asset_counts": {
            name: counts[name].get(row["id"], 0)
            for name in ("characters", "scenes", "props", "storyboard")
        },
    }
