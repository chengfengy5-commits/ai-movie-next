"""Thread-safe in-process email-code storage scoped to one explicit runtime."""

from __future__ import annotations

import threading
from typing import Any


class InMemoryEmailCodeStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}

    def cleanup_expired(self, now_timestamp: float) -> None:
        with self._lock:
            expired = [
                email
                for email, record in self._records.items()
                if float(record["expires_at"]) < now_timestamp
            ]
            for email in expired:
                del self._records[email]

    def get(self, email: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(email)
            return dict(record) if record is not None else None

    def put(self, email: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._records[email] = dict(record)

    def pop(self, email: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.pop(email, None)
            return dict(record) if record is not None else None

    def mark_verified(self, email: str) -> bool:
        with self._lock:
            record = self._records.get(email)
            if record is None:
                return False
            updated = dict(record)
            updated["verified"] = True
            self._records[email] = updated
            return True

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {email: dict(record) for email, record in self._records.items()}
