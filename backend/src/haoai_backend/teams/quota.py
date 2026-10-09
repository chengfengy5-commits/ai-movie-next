"""Explicit process-local join quota with a first-hit anchored window."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .errors import JoinQuotaExceeded

JOIN_LIMIT = 10
JOIN_WINDOW_SECONDS = 60 * 60
DEFAULT_REMOTE_IP = "127.0.0.1"


@dataclass(slots=True)
class _Window:
    expiry: float
    hits: int


class InMemoryJoinQuota:
    def __init__(
        self,
        epoch_clock: Callable[[], float] = time.time,
        *,
        limit: int = JOIN_LIMIT,
        window_seconds: int = JOIN_WINDOW_SECONDS,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._epoch_clock = epoch_clock
        self._limit = limit
        self._window_seconds = window_seconds
        self._windows: dict[tuple[str, str], _Window] = {}
        self._lock = threading.Lock()

    def check(self, remote_ip: str | None, pathname: str) -> None:
        key = (remote_ip or DEFAULT_REMOTE_IP, pathname)
        now = self._epoch_clock()
        with self._lock:
            window = self._windows.get(key)
            if window is None or now >= window.expiry:
                self._windows[key] = _Window(
                    expiry=now + self._window_seconds,
                    hits=1,
                )
                return
            if window.hits >= self._limit:
                raise JoinQuotaExceeded()
            window.hits += 1

    def snapshot(self) -> dict[tuple[str, str], tuple[int, float]]:
        """Return immutable diagnostic values for deterministic unit tests."""
        with self._lock:
            return {
                key: (window.hits, window.expiry)
                for key, window in self._windows.items()
            }
