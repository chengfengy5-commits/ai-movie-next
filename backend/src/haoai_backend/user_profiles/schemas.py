"""HTTP request schemas for user-profile compatibility endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProfileUpdateRequest(BaseModel):
    # Retained for clients that still send it; avatars are derived from email.
    avatar_url: str | None = None
    bio: str | None = None

    model_config = ConfigDict(extra="ignore")
