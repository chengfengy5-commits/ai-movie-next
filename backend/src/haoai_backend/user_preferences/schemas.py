"""HTTP request schemas for user preferences."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PreferencesUpdateRequest(BaseModel):
    preferences: dict[str, Any]
