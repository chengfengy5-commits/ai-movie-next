"""Process-local fixed windows for only the three legacy-limited routes."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateLimitExceeded(Exception):
    detail: str
    retry_after: int


class InMemoryFixedWindowLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def check(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        now: float,
    ) -> int | None:
        with self._lock:
            start, count = self._windows.get(key, (now, 0))
            if now - start >= window_seconds:
                start, count = now, 0
            if count >= limit:
                return max(0, math.ceil(window_seconds - (now - start)))
            self._windows[key] = (start, count + 1)
            return None


def window_label(window_seconds: int) -> str:
    if window_seconds % 3600 == 0:
        hours = window_seconds // 3600
        return f"{hours} per 1 hour" if hours == 1 else f"{hours} per {hours} hours"
    minutes = window_seconds // 60
    return f"{minutes} per 1 minute" if minutes == 1 else f"{minutes} per {minutes} minutes"
