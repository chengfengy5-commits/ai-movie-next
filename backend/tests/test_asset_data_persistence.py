from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.asset_data.naming import make_key
from haoai_backend.asset_data.persistence import SqlAlchemyAssetDataUnitOfWork
from haoai_backend.asset_data.tables import (
    ASSET_TABLES,
    chapter_locks,
    metadata as asset_metadata,
    system_configs,
)
from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from haoai_backend.series_data.tables import metadata as series_metadata, series, chapters


@pytest.fixture
def asset_database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'asset-data-persistence.sqlite'}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    auth_metadata.create_all(engine)
    series_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    asset_metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False, future=True)
    now = datetime(2026, 1, 1)
    with engine.begin() as connection:
        connection.execute(
            insert(auth_users).values(
                id="user-a",
                username="作者",
                email="author@example.test",
                hashed_password="x",
                is_superuser=False,
                membership_type="free",
                created_at=now,
                password_updated_at=None,
            )
        )
        connection.execute(
            insert(auth_users).values(
                id="user-b",
                username="另一位作者",
                email="another-author@example.test",
                hashed_password="x",
                is_superuser=False,
                membership_type="free",
                created_at=now,
                password_updated_at=None,
            )
        )
        connection.execute(
            insert(series).values(
                id="series-a",
                user_id="user-a",
                name="剧集",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-a",
                series_id="series-a",
                title="第一集",
                content='[{"text":"镜头一"}]',
                order=1,
                created_at=now,
                updated_at=now,
            )
        )
    yield engine, sessions
    engine.dispose()


def test_create_sets_legacy_derived_fields_and_duplicate_keys_are_allowed(asset_database) -> None:
    engine, sessions = asset_database
    with sessions() as session:
        uow = SqlAlchemyAssetDataUnitOfWork(
            session,
            now=lambda: datetime(2026, 2, 1),
            new_id=iter(["character-1", "character-2"]).__next__,
        )
        first = uow.create_asset(
            "character",
            {"series_id": "series-a", "name": "周晴", "gender": None},
        )
        second = uow.create_asset(
            "character",
            {"series_id": "series-a", "name": "周 晴"},
        )
        uow.commit()
        uow.close()

    table = ASSET_TABLES["character"]
    with Session(engine, future=True) as session:
        rows = session.execute(select(table).order_by(table.c.id)).mappings().all()
    assert first["aliases"] == "[]"
    assert first["canonical_key"] == make_key("character", "周晴")
    assert second["canonical_key"] == first["canonical_key"]
    assert len(rows) == 2


def test_empty_update_does_not_repair_listener_fields_or_change_timestamp(asset_database) -> None:
    engine, sessions = asset_database
    table = ASSET_TABLES["scene"]
    now = datetime(2026, 1, 1)
    with engine.begin() as connection:
        connection.execute(
            insert(table).values(
                id="scene-stale",
                series_id="series-a",
                title="学校室内夜",
                description=None,
                image_url=None,
                aliases=None,
                canonical_key="stale",
                created_at=now,
                updated_at=now,
            )
        )

    statements: list[str] = []

    def record_update(connection, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().lower().startswith("update scenes"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_update)
    with sessions() as session:
        uow = SqlAlchemyAssetDataUnitOfWork(session, now=lambda: datetime(2027, 1, 1))
        row = uow.update_asset("scene", "scene-stale", {}, assigned_fields=frozenset())
        uow.commit()
        uow.close()
    event.remove(engine, "before_cursor_execute", record_update)

    with Session(engine, future=True) as session:
        stored = session.execute(select(table).where(table.c.id == "scene-stale")).mappings().one()
    assert row is not None
    assert stored["canonical_key"] == "stale"
    assert stored["aliases"] is None
    assert stored["updated_at"] == now
    assert statements == []


def test_adapter_applies_only_the_first_read_write_set(asset_database) -> None:
    engine, sessions = asset_database
    table = ASSET_TABLES["scene"]
    seeded_at = datetime(2026, 1, 1)
    changed_at = datetime(2027, 1, 1)
    with engine.begin() as connection:
        connection.execute(
            insert(table).values(
                id="scene-stale",
                series_id="series-a",
                title="学校室内夜",
                description=None,
                image_url=None,
                aliases="",
                canonical_key="stale",
                created_at=seeded_at,
                updated_at=seeded_at,
            )
        )

    with sessions() as session:
        uow = SqlAlchemyAssetDataUnitOfWork(session, now=lambda: changed_at)
        updated = uow.update_asset(
            "scene",
            "scene-stale",
            {
                "canonical_key": make_key("scene", "学校室内夜"),
                "aliases": "[]",
            },
            assigned_fields=frozenset({"canonical_key", "aliases"}),
        )
        uow.commit()
        uow.close()

    with Session(engine, future=True) as session:
        stored = session.execute(select(table).where(table.c.id == "scene-stale")).mappings().one()
    assert updated is not None
    assert stored["canonical_key"] == make_key("scene", "学校室内夜")
    assert stored["aliases"] == "[]"
    assert stored["updated_at"] == changed_at


@pytest.mark.parametrize(("owner", "refresh"), [("user-a", True), ("user-b", False)])
def test_storyboard_lock_refresh_only_updates_a_lock_held_by_current_actor(
    asset_database,
    owner: str,
    refresh: bool,
) -> None:
    engine, sessions = asset_database
    now = datetime(2026, 1, 1)
    current = datetime(2027, 1, 1)
    with engine.begin() as connection:
        connection.execute(insert(chapter_locks).values(
            id="lock-a",
            chapter_id="chapter-a",
            user_id=owner,
            username=owner,
            acquired_at=now,
            last_active_at=now,
            expires_at=now - timedelta(days=1),
        ))
        connection.execute(insert(system_configs).values(
            id="lock-config",
            chapter_lock_idle_minutes=30,
        ))

    with sessions() as session:
        uow = SqlAlchemyAssetDataUnitOfWork(session, now=lambda: current)
        uow.refresh_chapter_lock("chapter-a", "user-a")
        uow.commit()
        uow.close()

    with Session(engine, future=True) as session:
        stored = session.execute(select(chapter_locks).where(chapter_locks.c.id == "lock-a")).mappings().one()
    assert stored["last_active_at"] == (current if refresh else now)
    assert stored["expires_at"] == (
        current + timedelta(minutes=30) if refresh else now - timedelta(days=1)
    )


def test_expected_chapter_content_update_is_compare_and_set(asset_database) -> None:
    engine, sessions = asset_database
    with sessions() as session:
        uow = SqlAlchemyAssetDataUnitOfWork(session)
        changed = uow.update_chapter_content(
            "chapter-a",
            expected_content='[{"text":"stale"}]',
            content="[]",
            updated_at=datetime(2027, 1, 1),
        )
        assert changed is False
        uow.rollback()
        uow.close()
    with Session(engine, future=True) as session:
        assert session.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one() == '[{"text":"镜头一"}]'
