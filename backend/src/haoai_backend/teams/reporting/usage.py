"""Pure date, aggregation, and ordering transforms for team usage reports."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from haoai_backend.teams.domain import UserRecord


UNKNOWN_MODEL = "未知模型"
TYPE_PRIORITY = {
    "video": 0,
    "image": 1,
    "chat": 2,
    "optimize-frame": 3,
}


def parse_date_range(
    start_time: str | None = None,
    end_time: str | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Parse each ISO endpoint independently, returning naive UTC when aware."""
    def parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    return parse(start_time), parse(end_time)


def aggregate_usage_rows(
    rows: Iterable[Sequence[Any]],
) -> tuple[int, Any, int, list[dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate (key, model, user, status, calls, credits) source rows."""
    by_model: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"model_name": "", "calls": 0, "credits": 0, "failed_calls": 0}
    )
    by_member: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "user_id": "",
            "username": "",
            "avatar_url": None,
            "calls": 0,
            "credits": 0,
            "failed_calls": 0,
        }
    )
    total_calls = 0
    total_credits: Any = 0
    total_failed = 0

    for _key, model_name, user_id, status, calls, credits in rows:
        normalized_model = model_name or UNKNOWN_MODEL
        normalized_user_id = user_id or ""
        if status == "failed":
            total_failed += calls
            by_model[normalized_model]["model_name"] = normalized_model
            by_model[normalized_model]["failed_calls"] += calls
            by_member[normalized_user_id]["user_id"] = normalized_user_id
            by_member[normalized_user_id]["failed_calls"] += calls
        else:
            total_calls += calls
            total_credits += credits
            by_model[normalized_model]["model_name"] = normalized_model
            by_model[normalized_model]["calls"] += calls
            by_model[normalized_model]["credits"] += credits
            by_member[normalized_user_id]["user_id"] = normalized_user_id
            by_member[normalized_user_id]["calls"] += calls
            by_member[normalized_user_id]["credits"] += credits

    return (
        total_calls,
        total_credits,
        total_failed,
        list(by_model.values()),
        list(by_member.values()),
    )


def avatar_url_for_email(email: str | None) -> str | None:
    """Match the fixed legacy QQ/Foxmail avatar resolver without external calls."""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    if domain not in {"qq.com", "foxmail.com"}:
        return None
    qq_number = email.strip().lower().split("@", 1)[0]
    if not qq_number.isdigit():
        return None
    return f"https://q1.qlogo.cn/g?b=qq&nk={qq_number}&s=100"


def fill_usage_members(
    members: Sequence[dict[str, Any]],
    users: Mapping[str, UserRecord],
) -> None:
    """Fill the legacy username fallback and derived avatar URL in place."""
    for member in members:
        user = users.get(member["user_id"])
        member["username"] = user.username if user else member["user_id"]
        member["avatar_url"] = avatar_url_for_email(user.email) if user else None


def member_model_names(model_names: Iterable[str]) -> list[str]:
    """Keep the export member list’s independent ordinary string sort."""
    return sorted(model_names)


def order_export_models(
    model_names: Iterable[str],
    model_type_by_name: Mapping[str, str],
) -> list[str]:
    """Stable legacy type-priority/lower-name order without a tie-breaker."""
    return sorted(
        model_names,
        key=lambda name: (
            TYPE_PRIORITY.get(model_type_by_name.get(name, ""), 9),
            (name or "").lower(),
        ),
    )
