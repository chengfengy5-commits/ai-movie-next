"""Concrete response shapes for the legacy administrator read endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict


class ScenePayload(TypedDict):
    id: str
    series_id: str
    title: str
    description: str | None
    image_url: str | None
    created_at: datetime


class PropPayload(TypedDict):
    id: str
    series_id: str
    name: str
    description: str | None
    image_url: str | None
    created_at: datetime


class SceneListPayload(TypedDict):
    data: list[ScenePayload]
    total: int


class PropListPayload(TypedDict):
    data: list[PropPayload]
    total: int
