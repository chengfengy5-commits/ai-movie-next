"""Framework-independent chapter lock values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ChapterLockActor:
    """Authenticated identity with the username snapshot required by legacy locks."""

    user_id: str
    username: str


@dataclass(frozen=True, slots=True)
class ChapterRecord:
    id: str
    series_id: str


@dataclass(frozen=True, slots=True)
class ChapterLockRecord:
    id: str
    chapter_id: str
    user_id: str
    username: str
    acquired_at: datetime
    last_active_at: datetime
    expires_at: datetime
