"""Wire schemas preserving the legacy prompt request and response shapes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PromptConfigBase(BaseModel):
    category: str
    name: str
    system_prompt: str | None = None
    user_prompt: str | None = None
    sort_order: int | None = 0


class PromptConfigCreate(PromptConfigBase):
    is_system: bool = False


class PromptConfigUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class PromptConfigResponse(PromptConfigBase):
    id: str
    user_id: str
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PromptReorderRequest(BaseModel):
    category: str
    prompt_ids: list[str]
