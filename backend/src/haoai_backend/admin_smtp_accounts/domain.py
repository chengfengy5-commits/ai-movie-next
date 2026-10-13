"""Framework-independent SMTP-account records and projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AdminSmtpAccountsActor:
    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class SmtpAccountRecord:
    id: str
    host: Any
    port: Any
    user: Any
    password: Any
    daily_sent: Any
    is_active: Any
    created_at: datetime | None
    updated_at: datetime | None


def account_payload(account: SmtpAccountRecord, password: str) -> dict[str, Any]:
    return {
        "id": account.id,
        "host": account.host,
        "port": account.port,
        "user": account.user,
        "password": password,
        "daily_sent": account.daily_sent,
        "is_active": bool(account.is_active),
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


def create_values(data: Mapping[str, Any], encrypted_password: str) -> dict[str, Any]:
    return {
        "host": data.get("host", "smtp.qq.com"),
        "port": int(data.get("port", 465)),
        "user": data["user"],
        "password": encrypted_password,
        "daily_sent": 0,
        "is_active": 1 if data.get("is_active", True) else 0,
    }


def account_created_payload(account_id: str, user: str) -> dict[str, str]:
    return {"id": account_id, "user": user, "message": "添加成功"}
