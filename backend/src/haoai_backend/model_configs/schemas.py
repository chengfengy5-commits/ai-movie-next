"""Pydantic schemas preserving the legacy model-configuration wire contract."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ModelConfigBase(BaseModel):
    category: str
    name: str
    provider: str
    api_key: str
    model: str | None = None
    base_url: str | None = None
    sort_order: int | None = 0


class ModelConfigCreate(ModelConfigBase):
    params: str | None = None
    is_system: bool = False


class ModelConfigUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    sort_order: int | None = None
    params: str | None = None
    cost_per_call: int | None = None
    is_active: bool | None = None


class ModelConfigResponse(ModelConfigBase):
    id: str
    user_id: str
    is_system: bool = False
    cost_per_call: int = 0
    description: str | None = None
    params: str | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ModelReorderRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    category: str
    model_ids: list[str]


class TestConnectionRequest(BaseModel):
    provider: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    category: str = "text"
