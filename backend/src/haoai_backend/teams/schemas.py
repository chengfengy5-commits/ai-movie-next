"""Team-local request DTOs with the original Pydantic validation behavior."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TeamRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TeamCreate(TeamRequest):
    name: str = Field(..., min_length=1, max_length=255)


class TeamUpdate(TeamRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)


class InviteCreate(TeamRequest):
    count: int = Field(default=1, ge=1, le=5)


class JoinRequest(TeamRequest):
    code: str = Field(..., min_length=6, max_length=20)


class RoleUpdate(TeamRequest):
    role: str


class PermissionsUpdate(TeamRequest):
    permissions: list = Field(default_factory=list)


class TeamSeriesCreate(TeamRequest):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    style_prompt_id: str | None = None
    image_url: str | None = None
    claim: bool = False


class ShareSeriesRequest(TeamRequest):
    team_id: str = Field(..., min_length=1)
    claim: bool = False


class TransferRequest(TeamRequest):
    user_id: str = Field(..., min_length=1)
