"""Explicit, non-secret settings captured by the application composition root."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeStatusConfiguration:
    """The legacy status endpoints' effective settings, supplied by the caller.

    This value is deliberately constructed outside this package. It never reads
    environment variables or creates database, HTTP, or object-storage clients.
    """

    service_name: str
    version: str
    readiness_initialized: bool
    object_storage_configured: bool
    probe_timeout_seconds: float
    ready_cache_seconds: float
    initial_failure_backoff_seconds: float
    max_failure_backoff_seconds: float

    def __post_init__(self) -> None:
        if not self.service_name:
            raise ValueError("service_name must be non-empty")
        if not self.version:
            raise ValueError("version must be non-empty")
        for field_name in (
            "probe_timeout_seconds",
            "ready_cache_seconds",
            "initial_failure_backoff_seconds",
            "max_failure_backoff_seconds",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field_name} must be a finite positive number")
