"""Typed request and response schemas matching the legacy system-model API."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SystemModelCreateRequest(BaseModel):
    category: str
    name: str
    provider: str
    model: str | None = ""
    api_key: str | None = ""
    base_url: str | None = ""
    cost_per_call: int = 0
    description: str | None = ""
    is_active: bool = True
    sort_order: int = 0
    params: str | None = None


class SystemModelUpdateRequest(BaseModel):
    category: str | None = None
    name: str | None = None
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    cost_per_call: int | None = None
    description: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None
    params: str | None = None


class SystemModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category: str
    name: str
    provider: str
    model: str | None = ""
    api_key: str | None = ""
    base_url: str | None = ""
    cost_per_call: int = 0
    description: str | None = ""
    is_active: bool = True
    sort_order: int = 0
    params: str | None = None
    created_at: datetime
    updated_at: datetime
