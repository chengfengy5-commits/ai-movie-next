"""Explicit application ports for chapter editing locks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from haoai_backend.series_access.domain import SeriesAccessRecord

from .domain import ChapterLockActor, ChapterLockRecord, ChapterRecord


class ChapterLockUnitOfWork(Protocol):
    def load_chapter(self, chapter_id: str) -> ChapterRecord | None: ...

    def require_series_access(
        self,
        actor: ChapterLockActor,
        series_id: str,
    ) -> SeriesAccessRecord: ...

    def load_first_idle_minutes(self) -> int | None: ...

    def load_lock(self, chapter_id: str) -> ChapterLockRecord | None: ...

    def update_lock(
        self,
        lock: ChapterLockRecord,
        *,
        last_active_at: datetime,
        expires_at: datetime,
    ) -> None: ...

    def delete_lock(self, lock: ChapterLockRecord) -> None: ...

    def add_lock(self, lock: ChapterLockRecord) -> None: ...

    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def load_committed_lock(self, lock_id: str) -> ChapterLockRecord | None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], ChapterLockUnitOfWork]
Clock = Callable[[], datetime]
IdSource = Callable[[], str]
