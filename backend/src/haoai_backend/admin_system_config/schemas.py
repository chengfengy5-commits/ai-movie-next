"""Typed shapes for raw legacy system-config responses."""

from __future__ import annotations

from typing import TypedDict

from .domain import SystemConfigResponse


class SystemConfigListResponse(TypedDict):
    data: list[SystemConfigResponse]
    total: int
