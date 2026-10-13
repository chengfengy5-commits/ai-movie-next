"""Framework-independent administrator and redemption-code records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AdminRedemptionCodesActor:
    """Administrator identity copied from the public authentication boundary."""

    user_id: str
    is_superuser: bool


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


def redemption_code_payload(row: Any) -> dict[str, Any]:
    """Return the exact ten-field legacy administrator response."""

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
