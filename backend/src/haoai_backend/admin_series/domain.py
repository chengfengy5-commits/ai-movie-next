"""Framework-independent actors and payloads for administrator series access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AdminSeriesActor:
    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class NewSeries:
    id: str
    user_id: str
    name: Any
    description: Any
    image_url: Any


@dataclass(frozen=True, slots=True)
class SeriesRecord:
    id: Any
    user_id: Any
    name: Any
    description: Any
    image_url: Any
    created_at: datetime
    updated_at: datetime


def series_payload(series: SeriesRecord) -> dict[str, Any]:
    """Return the exact seven-field legacy series DTO."""
    return {
        "id": series.id,
        "user_id": series.user_id,
        "name": series.name,
        "description": series.description,
        "image_url": series.image_url,
        "created_at": series.created_at,
        "updated_at": series.updated_at,
    }
