from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from haoai_backend.series_data.domain import ChapterRecord
from haoai_backend.series_data.errors import SeriesDataForbidden
from haoai_backend.series_data.persistence import SqlAlchemySeriesDataUnitOfWork
from haoai_backend.series_data.tables import (
    chapter_locks,
    chapters,
    metadata as series_metadata,
    series,
    system_configs,
    team_members,
    teams,
)
from haoai_backend.shared.identity import TrustedActor


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'series-persistence.sqlite'}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    auth_metadata.create_all(engine)
    series_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    now = datetime(2026, 1, 1)
    with engine.begin() as connection:
        connection.execute(insert(auth_users), [
            {"id": "user-a", "username": "作者", "email": "a@example.test", "hashed_password": "x",
             "is_superuser": False, "membership_type": "free", "created_at": now},
            {"id": "user-b", "username": "管理员", "email": "b@example.test", "hashed_password": "x",
             "is_superuser": True, "membership_type": "free", "created_at": now},
            {"id": "user-c", "username": "成员", "email": "c@example.test", "hashed_password": "x",
             "is_superuser": False, "membership_type": "free", "created_at": now},
        ])
        connection.execute(insert(series).values(
            id="series-a", user_id="user-a", name="原名", description=None, image_url=None,
            style_prompt_id=None, team_id=None, claimed_by=None, claimed_at=None,
            created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapters).values(
            id="chapter-a", series_id="series-a", title="第一集", content="[]", order=4,
            created_at=now, updated_at=now,
        ))
    sessions = sessionmaker(engine, expire_on_commit=False, future=True)
    yield engine, sessions
    engine.dispose()


def test_same_value_updates_issue_no_source_update_and_keep_timestamps(database) -> None:
    engine, sessions = database
    later = datetime(2027, 1, 1)
    updates: list[str] = []

    def capture_update(connection, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().lower().startswith("update series") or statement.lstrip().lower().startswith("update chapters"):
            updates.append(statement)

    event.listen(engine, "before_cursor_execute", capture_update)
    with sessions() as session:
        uow = SqlAlchemySeriesDataUnitOfWork(session, now=lambda: later)
        record = uow.load_series("series-a")
        assert record is not None
        same_series = uow.update_series("series-a", {"name": "原名", "description": None})
        chapter = uow.load_chapter("chapter-a")
        assert chapter is not None
        same_chapter = uow.update_chapter_fields(
            chapter,
            {"title": "第一集", "content": None, "order": 4},
            TrustedActor("user-a"),
            later,
        )
        uow.commit()
        assert same_series.updated_at == datetime(2026, 1, 1)
        assert same_chapter.updated_at == datetime(2026, 1, 1)
        changed = uow.update_series("series-a", {"name": "新名"})
        uow.commit()
        assert changed.updated_at == later
        uow.close()
    event.remove(engine, "before_cursor_execute", capture_update)
    assert len(updates) == 1
    assert updates[0].lstrip().lower().startswith("update series")


def test_delete_uses_database_superuser_and_explicit_team_permission(database) -> None:
    engine, sessions = database
    now = datetime(2026, 1, 1)
    with engine.begin() as connection:
        connection.execute(insert(series).values(
            id="personal-series", user_id="user-a", name="私人", description=None, image_url=None,
            style_prompt_id=None, team_id=None, claimed_by=None, claimed_at=None,
            created_at=now, updated_at=now,
        ))
        connection.execute(insert(teams).values(id="team-a", name="团队", owner_id="user-b"))
        connection.execute(insert(team_members).values(
            id="membership-c", team_id="team-a", user_id="user-c", role="member", permissions='["delete_series"]'
        ))
        connection.execute(insert(series).values(
            id="team-series", user_id="user-b", name="团队剧", description=None, image_url=None,
            style_prompt_id=None, team_id="team-a", claimed_by=None, claimed_at=None,
            created_at=now, updated_at=now,
        ))

    with sessions() as session:
        uow = SqlAlchemySeriesDataUnitOfWork(session)
        uow.delete_series(TrustedActor("user-b"), "personal-series")
        uow.commit()
        uow.close()
    with sessions() as session:
        uow = SqlAlchemySeriesDataUnitOfWork(session)
        uow.delete_series(TrustedActor("user-c"), "team-series")
        uow.commit()
        uow.close()
    with Session(engine, future=True) as session:
        assert session.execute(select(series.c.id).where(series.c.id.in_(["personal-series", "team-series"]))).all() == []


def test_nonmember_cannot_delete_team_series(database) -> None:
    engine, sessions = database
    now = datetime(2026, 1, 1)
    with engine.begin() as connection:
        connection.execute(insert(teams).values(id="team-a", name="团队", owner_id="user-b"))
        connection.execute(insert(series).values(
            id="team-series", user_id="user-b", name="团队剧", description=None, image_url=None,
            style_prompt_id=None, team_id="team-a", claimed_by=None, claimed_at=None,
            created_at=now, updated_at=now,
        ))
    with sessions() as session:
        uow = SqlAlchemySeriesDataUnitOfWork(session)
        with pytest.raises(SeriesDataForbidden, match="没有权限删除该剧集"):
            uow.delete_series(TrustedActor("user-c"), "team-series")
        uow.rollback()
        uow.close()
    with Session(engine, future=True) as session:
        assert session.execute(select(series.c.id).where(series.c.id == "team-series")).scalar_one() == "team-series"


@pytest.mark.parametrize(("lock_owner", "refreshed"), [("user-a", True), ("user-b", False)])
def test_same_value_chapter_update_keeps_source_timestamp_and_only_refreshes_owned_lock(
    database, lock_owner: str, refreshed: bool
) -> None:
    engine, sessions = database
    now = datetime(2026, 1, 1)
    later = datetime(2027, 1, 1)
    with engine.begin() as connection:
        connection.execute(insert(chapter_locks).values(
            id="lock-a",
            chapter_id="chapter-a",
            user_id=lock_owner,
            username=lock_owner,
            acquired_at=now,
            last_active_at=now,
            expires_at=now,
        ))
        connection.execute(insert(system_configs).values(
            id="lock-config", chapter_lock_idle_minutes=30,
        ))

    with sessions() as session:
        uow = SqlAlchemySeriesDataUnitOfWork(session, now=lambda: later)
        chapter = uow.load_chapter("chapter-a")
        assert chapter is not None
        uow.update_chapter_fields(
            chapter,
            {"title": "第一集", "content": None, "order": 4},
            TrustedActor("user-a"),
            later,
        )
        uow.commit()
        uow.close()

    with Session(engine, future=True) as session:
        stored_chapter = session.execute(select(chapters).where(chapters.c.id == "chapter-a")).mappings().one()
        stored_lock = session.execute(select(chapter_locks).where(chapter_locks.c.id == "lock-a")).mappings().one()
    assert stored_chapter["updated_at"] == now
    assert stored_lock["last_active_at"] == (later if refreshed else now)
    assert stored_lock["expires_at"] == (later + timedelta(minutes=30) if refreshed else now)
