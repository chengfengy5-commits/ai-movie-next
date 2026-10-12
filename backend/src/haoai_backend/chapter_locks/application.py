"""Framework-independent chapter lock use cases."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from haoai_backend.series_access.errors import (
    SeriesAccessDenied as PolicyAccessDenied,
    SeriesNotFound as PolicySeriesNotFound,
)

from .domain import ChapterLockActor, ChapterLockRecord
from .errors import (
    ChapterLockUnavailable,
    ChapterNotFound,
    LockReleaseForbidden,
    LockOccupied,
    SeriesAccessDenied,
    SeriesNotFound,
)
from .ports import Clock, IdSource, UnitOfWorkFactory


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_lock_id() -> str:
    return str(uuid4())


def _open(factory: UnitOfWorkFactory | None):
    if factory is None:
        raise ChapterLockUnavailable()
    return factory()


def _require_chapter(uow, actor: ChapterLockActor, chapter_id: str):
    chapter = uow.load_chapter(chapter_id)
    if chapter is None:
        raise ChapterNotFound()
    try:
        series = uow.require_series_access(actor, chapter.series_id)
    except PolicySeriesNotFound as exc:
        raise SeriesNotFound() from exc
    except PolicyAccessDenied as exc:
        raise SeriesAccessDenied(exc.detail) from exc
    return chapter, series


def acquire_lock(
    factory: UnitOfWorkFactory | None,
    actor: ChapterLockActor,
    chapter_id: str,
    *,
    clock: Clock = utcnow_naive,
    id_source: IdSource = new_lock_id,
) -> dict[str, object]:
    uow = _open(factory)
    try:
        chapter, series = _require_chapter(uow, actor, chapter_id)
        if not series.team_id:
            return {"locked": False, "message": "个人剧集无需加锁"}

        idle_minutes = uow.load_first_idle_minutes() or 15
        now = clock()
        lock = uow.load_lock(chapter.id)

        if lock is not None and lock.expires_at > now:
            if lock.user_id == actor.user_id:
                uow.update_lock(
                    lock,
                    last_active_at=now,
                    expires_at=now + timedelta(minutes=idle_minutes),
                )
                uow.commit()
                persisted = uow.load_committed_lock(lock.id)
                if persisted is None:
                    raise RuntimeError("chapter lock disappeared after commit")
                return {
                    "locked": True,
                    "username": persisted.username,
                    "expires_at": persisted.expires_at,
                    "idle_minutes": idle_minutes,
                }
            raise LockOccupied(lock.username, lock.expires_at)

        if lock is not None:
            uow.delete_lock(lock)
            uow.flush()

        new_lock = ChapterLockRecord(
            id=id_source(),
            chapter_id=chapter.id,
            user_id=actor.user_id,
            username=actor.username,
            acquired_at=now,
            last_active_at=now,
            expires_at=now + timedelta(minutes=idle_minutes),
        )
        uow.add_lock(new_lock)
        uow.commit()
        persisted = uow.load_committed_lock(new_lock.id)
        if persisted is None:
            raise RuntimeError("chapter lock disappeared after commit")
        return {
            "locked": True,
            "username": actor.username,
            "expires_at": persisted.expires_at,
            "idle_minutes": idle_minutes,
        }
    finally:
        uow.close()


def get_lock(
    factory: UnitOfWorkFactory | None,
    actor: ChapterLockActor,
    chapter_id: str,
    *,
    clock: Clock = utcnow_naive,
) -> dict[str, object]:
    uow = _open(factory)
    try:
        chapter, _series = _require_chapter(uow, actor, chapter_id)
        lock = uow.load_lock(chapter.id)
        now = clock()
        if lock is None or lock.expires_at <= now:
            return {"locked": False}
        return {
            "locked": True,
            "user_id": lock.user_id,
            "username": lock.username,
            "acquired_at": lock.acquired_at,
            "last_active_at": lock.last_active_at,
            "expires_at": lock.expires_at,
            "is_mine": lock.user_id == actor.user_id,
        }
    finally:
        uow.close()


def release_lock(
    factory: UnitOfWorkFactory | None,
    actor: ChapterLockActor,
    chapter_id: str,
) -> dict[str, str]:
    uow = _open(factory)
    try:
        chapter, _series = _require_chapter(uow, actor, chapter_id)
        lock = uow.load_lock(chapter.id)
        if lock is None:
            return {"message": "当前无锁"}
        if lock.user_id != actor.user_id:
            raise LockReleaseForbidden()
        uow.delete_lock(lock)
        uow.commit()
        return {"message": "锁已释放"}
    finally:
        uow.close()
