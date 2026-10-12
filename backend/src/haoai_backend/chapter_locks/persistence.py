"""SQLAlchemy ORM adapter using only the request-supplied Session."""

from __future__ import annotations

from datetime import datetime
from sqlalchemy.orm import Session, registry

from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity, SeriesAccessRecord
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_access.ports import SeriesAccessReader

from .domain import ChapterLockActor, ChapterLockRecord, ChapterRecord
from . import tables

_mapper_registry = registry()


class _ChapterRow:
    pass


class _ChapterLockRow:
    pass


class _SystemConfigRow:
    pass


_mapper_registry.map_imperatively(_ChapterRow, tables.chapters)
_mapper_registry.map_imperatively(_ChapterLockRow, tables.chapter_locks)
_mapper_registry.map_imperatively(_SystemConfigRow, tables.system_configs)


class _CapturingSeriesAccessReader:
    """Retain the series snapshot loaded by the shared access policy."""

    def __init__(self, delegate: SeriesAccessReader) -> None:
        self._delegate = delegate
        self.series: SeriesAccessRecord | None = None

    def load_series(self, series_id: str) -> SeriesAccessRecord | None:
        self.series = self._delegate.load_series(series_id)
        return self.series

    def load_team_membership(self, team_id: str, user_id: str):
        return self._delegate.load_team_membership(team_id, user_id)

    def load_username(self, user_id: str) -> str | None:
        return self._delegate.load_username(user_id)


def _lock_record(row: _ChapterLockRow) -> ChapterLockRecord:
    return ChapterLockRecord(
        id=row.id,
        chapter_id=row.chapter_id,
        user_id=row.user_id,
        username=row.username,
        acquired_at=row.acquired_at,
        last_active_at=row.last_active_at,
        expires_at=row.expires_at,
    )


class SqlAlchemyChapterLocksUnitOfWork:
    """One family-supplied Session with the source route's ORM flush behavior."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._loaded_locks: dict[str, _ChapterLockRow] = {}

    def load_chapter(self, chapter_id: str) -> ChapterRecord | None:
        row = (
            self._session.query(_ChapterRow)
            .filter(_ChapterRow.id == chapter_id)
            .first()
        )
        if row is None:
            return None
        return ChapterRecord(id=row.id, series_id=row.series_id)

    def require_series_access(
        self,
        actor: ChapterLockActor,
        series_id: str,
    ) -> SeriesAccessRecord:
        reader = _CapturingSeriesAccessReader(
            SqlAlchemySeriesAccessReader(self._session)
        )
        require_series_access(reader, ActorIdentity(actor.user_id), series_id)
        if reader.series is None:
            raise RuntimeError("series access did not retain its loaded series")
        return reader.series

    def load_first_idle_minutes(self) -> int | None:
        config = self._session.query(_SystemConfigRow).first()
        return None if config is None else config.chapter_lock_idle_minutes

    def load_lock(self, chapter_id: str) -> ChapterLockRecord | None:
        row = (
            self._session.query(_ChapterLockRow)
            .filter(_ChapterLockRow.chapter_id == chapter_id)
            .first()
        )
        if row is None:
            return None
        self._loaded_locks[row.id] = row
        return _lock_record(row)

    def update_lock(
        self,
        lock: ChapterLockRecord,
        *,
        last_active_at: datetime,
        expires_at: datetime,
    ) -> None:
        row = self._loaded_locks[lock.id]
        row.last_active_at = last_active_at
        row.expires_at = expires_at

    def delete_lock(self, lock: ChapterLockRecord) -> None:
        self._session.delete(self._loaded_locks[lock.id])

    def add_lock(self, lock: ChapterLockRecord) -> None:
        row = _ChapterLockRow(
            id=lock.id,
            chapter_id=lock.chapter_id,
            user_id=lock.user_id,
            username=lock.username,
            acquired_at=lock.acquired_at,
            last_active_at=lock.last_active_at,
            expires_at=lock.expires_at,
        )
        self._loaded_locks[lock.id] = row
        self._session.add(row)

    def flush(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def load_committed_lock(self, lock_id: str) -> ChapterLockRecord | None:
        row = (
            self._session.query(_ChapterLockRow)
            .populate_existing()
            .filter(_ChapterLockRow.id == lock_id)
            .first()
        )
        if row is None:
            return None
        self._loaded_locks[row.id] = row
        return _lock_record(row)

    def close(self) -> None:
        self._session.close()
