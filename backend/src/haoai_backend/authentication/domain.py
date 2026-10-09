"""Framework-independent authentication records and compatibility rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from .avatar import resolve_avatar_url


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: str
    username: str
    email: str
    hashed_password: str
    is_superuser: bool
    membership_type: str
    membership_expires_at: datetime | None
    avatar_url: str | None
    bio: str | None
    created_at: datetime
    password_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class UserSessionRecord:
    id: str
    user_id: str
    jti: str
    user_agent: str | None
    ip_address: str | None
    device_name: str | None
    is_active: bool
    created_at: datetime
    last_seen_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class UserCreditRecord:
    id: str
    user_id: str
    credits: int
    created_at: datetime
    updated_at: datetime


def validate_complex_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("密码长度不能少于8位")
    if not any(character.isalpha() for character in password):
        raise ValueError("密码必须包含至少一个字母")
    if not any(character.isdigit() for character in password):
        raise ValueError("密码必须包含至少一个数字")


def password_version(user: UserRecord, now: datetime) -> str:
    timestamp = user.password_updated_at or user.created_at or now
    return str(int(timestamp.timestamp()))


def membership_denial(user: UserRecord, now: datetime) -> str | None:
    if user.is_superuser:
        return None
    if user.membership_type != "premium":
        return "需要会员才能使用此功能，请前往购买会员"
    if user.membership_expires_at is not None and now > user.membership_expires_at:
        return "会员已过期，请续费后使用"
    return None


def resolve_device_name(user_agent: str | None) -> str:
    if not user_agent:
        return "未知设备"
    value = user_agent.lower()
    if "windows" in value:
        label = "Windows"
    elif "android" in value:
        label = "Android"
    elif "iphone" in value or "ipad" in value:
        label = "iPhone/iPad"
    elif "mac" in value:
        label = "Mac"
    elif "linux" in value:
        label = "Linux"
    else:
        label = "Web"

    if "edg" in value:
        return f"{label} · Edge"
    if "chrome" in value:
        return f"{label} · Chrome"
    if "firefox" in value:
        return f"{label} · Firefox"
    if "safari" in value:
        return f"{label} · Safari"
    return label


def resolve_client_ip(headers: Mapping[str, str], client_host: str | None) -> str:
    forwarded = headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    real_ip = headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return client_host or "unknown"


def user_response(user: UserRecord, *, include_bio: bool = True) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_superuser": user.is_superuser,
        "membership_type": user.membership_type,
        "membership_expires_at": user.membership_expires_at,
        "avatar_url": resolve_avatar_url(user.email),
        "bio": user.bio if include_bio else None,
        "created_at": user.created_at,
    }


def session_response(
    session: UserSessionRecord,
    *,
    current_jti: str | None,
) -> dict[str, Any]:
    return {
        "id": session.id,
        "jti": session.jti,
        "device_name": session.device_name or "未知设备",
        "ip_address": session.ip_address or "未知",
        "user_agent": session.user_agent or "",
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "last_seen_at": session.last_seen_at.isoformat() if session.last_seen_at else None,
        "revoked_at": session.revoked_at.isoformat() if session.revoked_at else None,
        "is_active": True,
        "is_current": session.jti == current_jti,
    }


def sort_sessions_for_response(
    sessions: list[UserSessionRecord],
    *,
    current_jti: str | None,
) -> list[UserSessionRecord]:
    return sorted(
        sessions,
        key=lambda item: (
            item.jti != current_jti,
            item.last_seen_at or item.created_at,
        ),
        reverse=True,
    )[:20]


def access_expiry(now: datetime, ttl_minutes: int = 14 * 24 * 60) -> datetime:
    return now + timedelta(minutes=ttl_minutes)


def reset_expiry(now: datetime) -> datetime:
    return now + timedelta(minutes=30)
