"""Immutable query results and actors for administrator scene and prop reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AdminScenePropQueriesActor:
    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class SceneRecord:
    id: str
    series_id: str
    title: str
    description: str | None
    image_url: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PropRecord:
    id: str
    series_id: str
    name: str
    description: str | None
    image_url: str | None
    created_at: datetime
