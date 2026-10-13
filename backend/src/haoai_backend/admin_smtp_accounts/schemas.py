"""Static shape aliases for the legacy SMTP-account JSON payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict


class SmtpAccountPayload(TypedDict):
    id: str
    host: Any
    port: Any
    user: Any
    password: str
    daily_sent: Any
    is_active: bool
    created_at: datetime | None
    updated_at: datetime | None


class SmtpAccountListPayload(TypedDict):
    data: list[SmtpAccountPayload]
    total: int


class SmtpAccountCreatedPayload(TypedDict):
    id: str
    user: str
    message: str


class SmtpAccountMessagePayload(TypedDict):
    message: str
