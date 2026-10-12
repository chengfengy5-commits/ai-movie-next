"""Real SQLite checks for chapter lock ORM transaction behavior."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import users
from haoai_backend.chapter_locks import application
from haoai_backend.chapter_locks.domain import ChapterLockActor
from haoai_backend.chapter_locks.persistence import (
    SqlAlchemyChapterLocksUnitOfWork,
    _SystemConfigRow,
)
from haoai_backend.chapter_locks.tables import (
    chapter_locks,
    chapters,
    metadata as chapter_locks_metadata,
    system_configs,
)
from haoai_backend.series_access.tables import (
    metadata as series_access_metadata,
    series,
    team_members,
)

NOW = datetime(2031, 1, 2, 3, 4, 5)
ACTOR = ChapterLockActor("user-a", "Alice")


class TrackingSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.events: list[str] = []

    def flush(self, *args: Any, **kwargs: Any) -> None:
        self.events.append("flush")
        super().flush(*args, **kwargs)

    def commit(self) -> None:
        self.events.append("commit")
        super().commit()


class LostCommitAcknowledgementSession(TrackingSession):
    def commit(self) -> None:
        super().commit()
        raise RuntimeError("chapter lock commit acknowledgement lost")


def make_database(path: Path, *, session_class=TrackingSession):
    engine = create_engine(f"sqlite:///{path}", future=True)
    authentication_metadata.create_all(engine)
    series_access_metadata.create_all(engine)
    chapter_locks_metadata.create_all(engine)
    factory = sessionmaker(
        engine,
        class_=session_class,
        expire_on_commit=True,
        future=True,
    )
    return engine, factory


def seed_chapter(
    engine,
    *,
    idle_minutes: int | None = 5,
    existing_lock: dict[str, object] | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(users).values(
                id="user-a",
                username="Alice",
                email="alice@example.test",
                hashed_password="test-hash",
                is_superuser=False,
                membership_type="premium",
                membership_expires_at=NOW + timedelta(days=10),
                created_at=NOW - timedelta(days=10),
            )
        )
        connection.execute(
            insert(series).values(
                id="series-a",
                user_id="series-owner",
                team_id="team-a",
                claimed_by=None,
            )
        )
        connection.execute(
            insert(team_members).values(
                id="member-a",
                team_id="team-a",
                user_id="user-a",
                role="member",
                permissions=None,
            )
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-a",
                series_id="series-a",
            )
        )
        if idle_minutes is not None:
            connection.execute(
                insert(system_configs).values(
                    id="config-a",
                    chapter_lock_idle_minutes=idle_minutes,
                )
            )
        if existing_lock is not None:
            connection.execute(insert(chapter_locks).values(**existing_lock))


def factory_for(session_factory, sessions: list[Session]):
    def create_uow():
        session = session_factory()
        sessions.append(session)
        return SqlAlchemyChapterLocksUnitOfWork(session)

    return create_uow


def lock_values(
    *,
    lock_id: str = "lock-a",
    user_id: str = "user-a",
    username: str = "Stored Alice",
    expires_at: datetime = NOW + timedelta(minutes=1),
) -> dict[str, object]:
    return {
        "id": lock_id,
        "chapter_id": "chapter-a",
        "user_id": user_id,
        "username": username,
        "acquired_at": NOW - timedelta(minutes=3),
        "last_active_at": NOW - timedelta(minutes=2),
        "expires_at": expires_at,
    }


def test_real_sqlite_renewal_preserves_lock_identity_and_reads_back_after_commit(
    tmp_path: Path,
) -> None:
    engine, session_factory = make_database(tmp_path / "chapter-lock-renew.sqlite")
    seed_chapter(engine, existing_lock=lock_values())
    sessions: list[TrackingSession] = []
    try:
        result = application.acquire_lock(
            factory_for(session_factory, sessions),
            ACTOR,
            "chapter-a",
            clock=lambda: NOW,
            id_source=lambda: pytest.fail("renewal must keep the existing id"),
        )

        assert result == {
            "locked": True,
            "username": "Stored Alice",
            "expires_at": NOW + timedelta(minutes=5),
            "idle_minutes": 5,
        }
        assert len(sessions) == 1
        assert sessions[0].events.count("commit") == 1
        with engine.connect() as connection:
            persisted = connection.execute(
                select(chapter_locks).where(chapter_locks.c.chapter_id == "chapter-a")
            ).mappings().one()
        assert persisted["id"] == "lock-a"
        assert persisted["username"] == "Stored Alice"
        assert persisted["acquired_at"] == NOW - timedelta(minutes=3)
        assert persisted["last_active_at"] == NOW
        assert persisted["expires_at"] == NOW + timedelta(minutes=5)
    finally:
        engine.dispose()


def test_expired_replacement_flushes_delete_before_unique_insert_and_commits_once(
    tmp_path: Path,
) -> None:
    engine, session_factory = make_database(tmp_path / "chapter-lock-expired.sqlite")
    seed_chapter(
        engine,
        idle_minutes=-2,
        existing_lock=lock_values(expires_at=NOW),
    )
    with engine.begin() as connection:
        connection.execute(
            insert(team_members).values(
                id="member-b",
                team_id="team-a",
                user_id="user-b",
                role="member",
                permissions=None,
            )
        )
    sessions: list[TrackingSession] = []
    try:
        result = application.acquire_lock(
            factory_for(session_factory, sessions),
            ChapterLockActor("user-b", "Bob"),
            "chapter-a",
            clock=lambda: NOW,
            id_source=lambda: "lock-b",
        )

        assert result == {
            "locked": True,
            "username": "Bob",
            "expires_at": NOW - timedelta(minutes=2),
            "idle_minutes": -2,
        }
        assert sessions[0].events.count("commit") == 1
        assert "flush" in sessions[0].events
        assert sessions[0].events.index("flush") < sessions[0].events.index("commit")
        with engine.connect() as connection:
            rows = connection.execute(select(chapter_locks)).mappings().all()
        assert len(rows) == 1
        assert rows[0]["id"] == "lock-b"
        assert rows[0]["user_id"] == "user-b"
        assert rows[0]["acquired_at"] == NOW
        assert rows[0]["last_active_at"] == NOW
    finally:
        engine.dispose()


def test_caller_autoflush_and_pending_orm_state_are_preserved(tmp_path: Path) -> None:
    engine, session_factory = make_database(tmp_path / "chapter-lock-pending.sqlite")
    seed_chapter(engine, idle_minutes=None)
    session = session_factory()
    session.add(_SystemConfigRow(id="caller-config", chapter_lock_idle_minutes=7))
    assert session.autoflush is True
    assert len(session.new) == 1
    sessions = [session]
    try:
        def create_uow():
            return SqlAlchemyChapterLocksUnitOfWork(session)

        result = application.acquire_lock(
            create_uow,
            ACTOR,
            "chapter-a",
            clock=lambda: NOW,
            id_source=lambda: "lock-pending",
        )

        assert result["idle_minutes"] == 7
        assert session.autoflush is True
        assert session.events.index("flush") < session.events.index("commit")
        with engine.connect() as connection:
            saved_config = connection.execute(
                select(system_configs.c.chapter_lock_idle_minutes).where(
                    system_configs.c.id == "caller-config"
                )
            ).scalar_one()
        assert saved_config == 7
    finally:
        engine.dispose()


def test_unknown_commit_acknowledgement_propagates_without_retry(tmp_path: Path) -> None:
    engine, session_factory = make_database(
        tmp_path / "chapter-lock-lost-ack.sqlite",
        session_class=LostCommitAcknowledgementSession,
    )
    seed_chapter(engine)
    sessions: list[LostCommitAcknowledgementSession] = []
    try:
        with pytest.raises(RuntimeError, match="commit acknowledgement lost"):
            application.acquire_lock(
                factory_for(session_factory, sessions),
                ACTOR,
                "chapter-a",
                clock=lambda: NOW,
                id_source=lambda: "lock-committed",
            )

        assert len(sessions) == 1
        assert sessions[0].events.count("commit") == 1
        with engine.connect() as connection:
            saved = connection.execute(
                select(chapter_locks.c.id).where(
                    chapter_locks.c.chapter_id == "chapter-a"
                )
            ).scalar_one()
        assert saved == "lock-committed"
    finally:
        engine.dispose()


def test_read_only_get_does_not_commit_or_issue_dml(tmp_path: Path) -> None:
    engine, session_factory = make_database(tmp_path / "chapter-lock-read.sqlite")
    seed_chapter(engine, existing_lock=lock_values())
    sessions: list[TrackingSession] = []
    statements: list[str] = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        result = application.get_lock(
            factory_for(session_factory, sessions),
            ACTOR,
            "chapter-a",
            clock=lambda: NOW,
        )

        assert result == {
            "locked": True,
            "user_id": "user-a",
            "username": "Stored Alice",
            "acquired_at": NOW - timedelta(minutes=3),
            "last_active_at": NOW - timedelta(minutes=2),
            "expires_at": NOW + timedelta(minutes=1),
            "is_mine": True,
        }
        assert sessions[0].events.count("commit") == 0
        assert not any(
            statement.startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
        engine.dispose()


def test_database_enforces_one_lock_per_chapter(tmp_path: Path) -> None:
    engine, _session_factory = make_database(tmp_path / "chapter-lock-unique.sqlite")
    seed_chapter(engine, existing_lock=lock_values())
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    insert(chapter_locks).values(**lock_values(lock_id="lock-b"))
                )
    finally:
        engine.dispose()
