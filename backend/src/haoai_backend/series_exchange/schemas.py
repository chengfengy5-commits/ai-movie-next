"""Wire schemas matching the fixed legacy DTOs for series exchange."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class FusedMember(BaseModel):
    kind: str = "asset"
    id: str | None = None
    url: str | None = None
    name: str | None = None


class FusedAssetUpdate(BaseModel):
    name: str | None = None
    members: list[FusedMember] | None = None
    member_type: str | None = None
    image_url: str | None = None
    size: str | None = None


class FusedAssetResponse(BaseModel):
    id: str
    series_id: str
    member_type: str
    members: list[dict[str, Any]] | None = None
    name: str
    image_url: str | None = None
    size: str | None = None
    status: str = "completed"
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("members", mode="before")
    @classmethod
    def parse_members(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return []
        return value


class ImportSeriesRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    data: dict[str, Any]
    team_id: str | None = None
    claim: bool = False


class ImportSeriesResponse(BaseModel):
    id: str
    name: str
    message: str
