"""Pure chapter lock policy and ordering checks."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from haoai_backend.chapter_locks import application
from haoai_backend.chapter_locks.domain import (
    ChapterLockActor,
    ChapterLockRecord,
    ChapterRecord,
)
from haoai_backend.chapter_locks.errors import LockOccupied, LockReleaseForbidden
from haoai_backend.series_access.domain import SeriesAccessRecord

NOW = datetime(2031, 1, 2, 3, 4, 5)
ACTOR = ChapterLockActor("user-a", "Alice")
TEAM_SERIES = SeriesAccessRecord(
    id="series-a",
    user_id="owner",
    team_id="team-a",
    claimed_by=None,
)
PERSONAL_SERIES = SeriesAccessRecord(
    id="series-a",
    user_id="user-a",
    team_id=None,
    claimed_by=None,
)


def lock_record(
    *,
    lock_id: str = "lock-a",
    user_id: str = "user-a",
    username: str = "Original Alice",
    expires_at: datetime = NOW + timedelta(minutes=1),
) -> ChapterLockRecord:
    return ChapterLockRecord(
        id=lock_id,
        chapter_id="chapter-a",
        user_id=user_id,
        username=username,
        acquired_at=NOW - timedelta(minutes=2),
        last_active_at=NOW - timedelta(minutes=1),
        expires_at=expires_at,
    )


class RecordingUnitOfWork:
    def __init__(
        self,
        *,
        series: SeriesAccessRecord = TEAM_SERIES,
        idle_minutes: int | None = 5,
        lock: ChapterLockRecord | None = None,
    ) -> None:
        self.series = series
        self.idle_minutes = idle_minutes
        self.lock = lock
        self.committed_lock: ChapterLockRecord | None = None
        self.events: list[str] = []
        self.closed = False

    def load_chapter(self, chapter_id: str):
        self.events.append("chapter")
        return ChapterRecord(chapter_id, "series-a")

    def require_series_access(self, actor, series_id):
        self.events.append("access")
        return self.series

    def load_first_idle_minutes(self):
        self.events.append("config")
        return self.idle_minutes

    def load_lock(self, chapter_id):
        self.events.append("lock")
        return self.lock

    def update_lock(self, lock, *, last_active_at, expires_at):
        self.events.append("update")
        self.committed_lock = ChapterLockRecord(
            id=lock.id,
            chapter_id=lock.chapter_id,
            user_id=lock.user_id,
            username=lock.username,
            acquired_at=lock.acquired_at,
            last_active_at=last_active_at,
            expires_at=expires_at,
        )

    def delete_lock(self, lock):
        self.events.append("delete")

    def add_lock(self, lock):
        self.events.append("add")
        self.committed_lock = lock

    def flush(self):
        self.events.append("flush")

    def commit(self):
        self.events.append("commit")

    def load_committed_lock(self, lock_id):
        self.events.append("readback")
        return self.committed_lock

    def close(self):
        self.events.append("close")
        self.closed = True


def test_personal_acquire_returns_before_config_or_lock_reads() -> None:
    uow = RecordingUnitOfWork(series=PERSONAL_SERIES)

    result = application.acquire_lock(
        lambda: uow,
        ACTOR,
        "chapter-a",
        clock=lambda: pytest.fail("personal acquire must not read the clock"),
        id_source=lambda: pytest.fail("personal acquire must not allocate a lock id"),
    )

    assert result == {"locked": False, "message": "个人剧集无需加锁"}
    assert uow.events == ["chapter", "access", "close"]


def test_new_team_lock_applies_zero_config_fallback_and_one_shared_timestamp() -> None:
    uow = RecordingUnitOfWork(idle_minutes=0)
    clock_calls: list[datetime] = []
    id_calls: list[str] = []

    def clock() -> datetime:
        clock_calls.append(NOW)
        return NOW

    def id_source() -> str:
        id_calls.append("lock-new")
        return id_calls[-1]

    result = application.acquire_lock(
        lambda: uow,
        ACTOR,
        "chapter-a",
        clock=clock,
        id_source=id_source,
    )

    assert result == {
        "locked": True,
        "username": "Alice",
        "expires_at": NOW + timedelta(minutes=15),
        "idle_minutes": 15,
    }
    assert clock_calls == [NOW]
    assert id_calls == ["lock-new"]
    assert uow.events == [
        "chapter",
        "access",
        "config",
        "lock",
        "add",
        "commit",
        "readback",
        "close",
    ]
    assert uow.committed_lock is not None
    assert uow.committed_lock.acquired_at == NOW
    assert uow.committed_lock.last_active_at == NOW
    assert uow.committed_lock.expires_at == NOW + timedelta(minutes=15)


def test_live_own_lock_keeps_username_and_acquisition_snapshot() -> None:
    existing = lock_record()
    uow = RecordingUnitOfWork(lock=existing)

    result = application.acquire_lock(
        lambda: uow,
        ACTOR,
        "chapter-a",
        clock=lambda: NOW,
    )

    assert result == {
        "locked": True,
        "username": "Original Alice",
        "expires_at": NOW + timedelta(minutes=5),
        "idle_minutes": 5,
    }
    assert uow.committed_lock is not None
    assert uow.committed_lock.id == existing.id
    assert uow.committed_lock.username == existing.username
    assert uow.committed_lock.acquired_at == existing.acquired_at
    assert uow.committed_lock.last_active_at == NOW
    assert uow.events == [
        "chapter",
        "access",
        "config",
        "lock",
        "update",
        "commit",
        "readback",
        "close",
    ]


def test_expired_lock_flush_precedes_new_id_and_negative_idle_is_preserved() -> None:
    expired = lock_record(expires_at=NOW)
    uow = RecordingUnitOfWork(idle_minutes=-2, lock=expired)
    id_calls: list[str] = []

    def id_source() -> str:
        id_calls.append("new-id")
        return id_calls[-1]

    result = application.acquire_lock(
        lambda: uow,
        ChapterLockActor("user-b", "Bob"),
        "chapter-a",
        clock=lambda: NOW,
        id_source=id_source,
    )

    assert result == {
        "locked": True,
        "username": "Bob",
        "expires_at": NOW - timedelta(minutes=2),
        "idle_minutes": -2,
    }
    assert id_calls == ["new-id"]
    assert uow.events == [
        "chapter",
        "access",
        "config",
        "lock",
        "delete",
        "flush",
        "add",
        "commit",
        "readback",
        "close",
    ]


def test_get_treats_expiration_equal_to_now_as_unlocked_without_commit() -> None:
    uow = RecordingUnitOfWork(lock=lock_record(expires_at=NOW))

    result = application.get_lock(lambda: uow, ACTOR, "chapter-a", clock=lambda: NOW)

    assert result == {"locked": False}
    assert uow.events == ["chapter", "access", "lock", "close"]


def test_other_user_cannot_release_even_an_expired_lock() -> None:
    expired = lock_record(user_id="user-a", expires_at=NOW - timedelta(days=1))
    uow = RecordingUnitOfWork(lock=expired)

    with pytest.raises(LockReleaseForbidden) as caught:
        application.release_lock(
            lambda: uow,
            ChapterLockActor("user-b", "Bob"),
            "chapter-a",
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == "只能释放自己持有的锁"
    assert uow.events == ["chapter", "access", "lock", "close"]


def test_live_lock_owned_by_another_user_returns_source_conflict_payload() -> None:
    uow = RecordingUnitOfWork(
        lock=lock_record(user_id="user-b", username="Bob")
    )

    with pytest.raises(LockOccupied) as caught:
        application.acquire_lock(
            lambda: uow,
            ACTOR,
            "chapter-a",
            clock=lambda: NOW,
        )

    assert caught.value.status_code == 409
    assert caught.value.payload == {
        "detail": "该章节正在被 Bob 编辑",
        "locked_by": "Bob",
        "expires_at": (NOW + timedelta(minutes=1)).isoformat(),
    }
    assert uow.events == ["chapter", "access", "config", "lock", "close"]
