"""Typed request bodies and DTO projections for legacy admin user routes."""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from pydantic import BaseModel

from .domain import UserRecord


class UserCreateBody(BaseModel):
    username: str
    email: str
    password: str
    is_superuser: bool = False
    membership_type: str = "free"
    membership_expires_at: str | None = None
    credits: int = 0


class UserPayload(TypedDict):
    id: str
    username: str
    email: str
    is_superuser: bool
    membership_type: str
    membership_expires_at: datetime | None
    credits: int
    created_at: datetime


class UserListPayload(TypedDict):
    data: list[UserPayload]
    total: int


class UserMessagePayload(TypedDict):
    message: str


class UserDeletePayload(TypedDict):
    success: bool
    message: str


def user_payload(record: UserRecord, *, credits: int | None = None) -> UserPayload:
    return {
        "id": record.id,
        "username": record.username,
        "email": record.email,
        "is_superuser": record.is_superuser,
        "membership_type": record.membership_type,
        "membership_expires_at": record.membership_expires_at,
        "credits": record.credits if credits is None else credits,
        "created_at": record.created_at,
    }
