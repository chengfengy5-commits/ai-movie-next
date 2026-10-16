"""Framework-independent records and commands for redemption codes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class RedemptionCodesActor:
    """Minimal authentication snapshot copied before the auth session closes."""

    user_id: str
    is_superuser: bool
    membership_type: str
    membership_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class GenerateRedemptionCodesCommand:
    type: str
    value: int
    count: int
    expires_at: str | None


@dataclass(frozen=True, slots=True)
class RedeemRedemptionCodeCommand:
    code: str


@dataclass(frozen=True, slots=True)
class NewRedemptionCode:
    id: str
    code: str
    type: str
    value: int
    created_by: str
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class RedemptionCode:
    id: str
    code: str
    type: str
    value: int
    is_used: bool
    used_by: str | None
    used_at: datetime | None
    created_by: str
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class UserCredit:
    id: str
    user_id: str
    credits: int


@dataclass(frozen=True, slots=True)
class UserMembership:
    membership_type: str
    membership_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class NewUserCredit:
    id: str
    user_id: str
    credits: int


@dataclass(frozen=True, slots=True)
class NewCreditLog:
    id: str
    user_id: str
    amount: int
    balance_after: int
    type: str
    description: str


def redemption_code_payload(row: Any) -> dict[str, Any]:
    """Project a stored code to the fixed ten-field response shape."""

    def format_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat() + "Z"

    return {
        "id": row.id,
        "code": row.code,
        "type": row.type,
        "value": row.value,
        "is_used": row.is_used,
        "used_by": row.used_by,
        "used_at": format_datetime(row.used_at),
        "created_by": row.created_by,
        "expires_at": format_datetime(row.expires_at),
        "created_at": format_datetime(row.created_at),
    }
