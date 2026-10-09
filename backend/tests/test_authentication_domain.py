"""Framework-free authentication rules and response compatibility."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from haoai_backend.authentication.avatar import resolve_avatar_url
from haoai_backend.authentication.domain import (
    UserRecord,
    UserSessionRecord,
    access_expiry,
    membership_denial,
    password_version,
    reset_expiry,
    resolve_client_ip,
    resolve_device_name,
    session_response,
    sort_sessions_for_response,
    user_response,
    validate_complex_password,
)


def user(**changes) -> UserRecord:
    values = {
        "id": "u-1",
        "username": "alice",
        "email": "alice@example.test",
        "hashed_password": "hash",
        "is_superuser": False,
        "membership_type": "free",
        "membership_expires_at": None,
        "avatar_url": "ignored",
        "bio": "bio",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "password_updated_at": None,
    }
    values.update(changes)
    return UserRecord(**values)


def session(index: int, *, current: bool = False, seen: datetime | None = None) -> UserSessionRecord:
    timestamp = seen or datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    return UserSessionRecord(
        id=f"s-{index}", user_id="u-1", jti="current" if current else f"j-{index}",
        user_agent=None, ip_address=None, device_name=None, is_active=True,
        created_at=timestamp, last_seen_at=timestamp, revoked_at=None,
    )


def test_registration_password_uses_eight_unicode_codepoints_and_unicode_letters() -> None:
    validate_complex_password("密码123456")
    validate_complex_password("Abcdefg١")

    for value, message in [
        ("Abc1234", "密码长度不能少于8位"),
        ("12345678", "密码必须包含至少一个字母"),
        ("Password", "密码必须包含至少一个数字"),
    ]:
        with pytest.raises(ValueError, match=message):
            validate_complex_password(value)


def test_password_version_uses_timestamp_integer_and_fallbacks() -> None:
    fallback_now = datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    assert password_version(user(password_updated_at=None), fallback_now) == str(
        int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    )
    updated = datetime(2026, 1, 1, 0, 0, 7, 999_999, tzinfo=timezone.utc)
    assert password_version(user(password_updated_at=updated), fallback_now) == str(
        int(updated.timestamp())
    )
    no_dates = user(created_at=None, password_updated_at=None)
    assert password_version(no_dates, fallback_now) == str(int(fallback_now.timestamp()))


def test_membership_preserves_superuser_and_expiry_boundary() -> None:
    now = datetime(2026, 1, 1, 12)
    assert membership_denial(user(is_superuser=True, membership_type="free"), now) is None
    assert membership_denial(user(membership_type="free"), now) == "需要会员才能使用此功能，请前往购买会员"
    assert membership_denial(user(membership_type="premium", membership_expires_at=now), now) is None
    assert membership_denial(
        user(membership_type="premium", membership_expires_at=now - timedelta(microseconds=1)), now
    ) == "会员已过期，请续费后使用"
    assert membership_denial(user(membership_type="premium"), now) is None


def test_device_and_client_ip_keep_legacy_precedence() -> None:
    assert resolve_device_name(None) == "未知设备"
    assert resolve_device_name("Mozilla Windows Chrome Edg") == "Windows · Edge"
    assert resolve_device_name("iPad Mac Safari") == "iPhone/iPad · Safari"
    assert resolve_client_ip({"x-forwarded-for": " 198.51.100.1, 10.0.0.2", "x-real-ip": "192.0.2.1"}, "127.0.0.1") == "198.51.100.1"
    assert resolve_client_ip({"x-real-ip": " 192.0.2.9 "}, "127.0.0.1") == "192.0.2.9"
    assert resolve_client_ip({}, None) == "unknown"


def test_user_avatar_and_login_bio_projection_are_field_compatible() -> None:
    assert resolve_avatar_url("123456@qq.com") == "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100"
    assert resolve_avatar_url(" 00123@FOXMAIL.COM ") == "https://q1.qlogo.cn/g?b=qq&nk=00123&s=100"
    assert resolve_avatar_url("123@other@qq.com") == "https://q1.qlogo.cn/g?b=qq&nk=123&s=100"
    assert resolve_avatar_url("123@ qq.com") == "https://q1.qlogo.cn/g?b=qq&nk=123&s=100"
    assert resolve_avatar_url("user@example.test") is None
    profile = user_response(user(email="00123@foxmail.com", bio="private bio"))
    login = user_response(user(email="00123@foxmail.com", bio="private bio"), include_bio=False)
    assert profile["avatar_url"] == "https://q1.qlogo.cn/g?b=qq&nk=00123&s=100"
    assert profile["bio"] == "private bio"
    assert login["bio"] is None


def test_session_list_keeps_noncurrent_before_current_and_limits_twenty() -> None:
    current = session(0, current=True, seen=datetime(2026, 1, 1, tzinfo=timezone.utc))
    others = [session(index, seen=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)) for index in range(1, 22)]
    ordered = sort_sessions_for_response([current, *others], current_jti="current")
    assert len(ordered) == 20
    assert ordered[0].jti == "j-21"
    assert current not in ordered
    assert session_response(session(5), current_jti="j-5")["is_current"] is True
    assert session_response(session(5), current_jti="other")["is_active"] is True


def test_token_expiries_keep_fourteen_day_and_thirty_minute_defaults() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert access_expiry(now) == now + timedelta(days=14)
    assert access_expiry(now, 5) == now + timedelta(minutes=5)
    assert reset_expiry(now) == now + timedelta(minutes=30)
