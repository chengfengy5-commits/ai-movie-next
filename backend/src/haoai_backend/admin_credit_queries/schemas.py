"""Typed payload projections for the legacy credit endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

from .domain import CreditLogRecord, UserCreditRecord


AdminCreditUpdateBody = dict[str, Any]


class UserCreditPayload(TypedDict):
    id: str
    user_id: str
    credits: int
    created_at: datetime
    updated_at: datetime


class UserCreditListPayload(TypedDict):
    data: list[UserCreditPayload]
    total: int


class CreditLogPayload(TypedDict):
    id: str
    user_id: str
    amount: int
    balance_after: int
    type: str
    description: str | None
    task_id: str | None
    created_at: datetime


class CreditLogListPayload(TypedDict):
    data: list[CreditLogPayload]
    total: int


def user_credit_payload(credit: UserCreditRecord) -> UserCreditPayload:
    return {
        "id": credit.id,
        "user_id": credit.user_id,
        "credits": credit.credits,
        "created_at": credit.created_at,
        "updated_at": credit.updated_at,
    }


def credit_log_payload(log: CreditLogRecord) -> CreditLogPayload:
    return {
        "id": log.id,
        "user_id": log.user_id,
        "amount": log.amount,
        "balance_after": log.balance_after,
        "type": log.type,
        "description": log.description,
        "task_id": log.task_id,
        "created_at": log.created_at,
    }
