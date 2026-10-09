"""Temporary SQLite fixtures for the isolated rough-cut module tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import Column, MetaData, String, Table, event, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine

from haoai_backend.personal_production.rough_cut.errors import Forbidden, Unauthorized
from haoai_backend.personal_production.rough_cut.ports import TrustedActor
from haoai_backend.personal_production.rough_cut.tables import (
    chapters,
    metadata as rough_cut_metadata,
    rough_cut_drafts,
    storyboard_assets,
    users,
)

TEST_METADATA = MetaData()
series_memberships = Table(
    "test_series_memberships",
    TEST_METADATA,
    Column("user_id", String(36), primary_key=True),
    Column("series_id", String(36), primary_key=True),
)


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@dataclass
class TestDatabase:
    __test__: ClassVar[bool] = False

    engine: Engine
    session_factory: sessionmaker[Session]

    def seed_user(self, user_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(insert(users).values(id=user_id))

    def grant_series(self, user_id: str, series_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(series_memberships).values(user_id=user_id, series_id=series_id)
            )

    def seed_chapter(
        self,
        chapter_id: str,
        series_id: str,
        content: Any,
        asset_ids: tuple[str, ...] = (),
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(chapters).values(
                    id=chapter_id,
                    series_id=series_id,
                    title=f"标题 {chapter_id}",
                    content=content if content is None or isinstance(content, str) else json.dumps(content),
                )
            )
            for index, asset_id in enumerate(asset_ids):
                connection.execute(
                    insert(storyboard_assets).values(
                        id=asset_id,
                        series_id=series_id,
                        chapter_id=chapter_id,
                        frame_index=index,
                        image_url=None,
                    )
                )

    def read_draft(self, chapter_id: str, user_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.execute(
                select(rough_cut_drafts).where(
                    rough_cut_drafts.c.chapter_id == chapter_id,
                    rough_cut_drafts.c.user_id == user_id,
                )
            ).mappings().first()
            return dict(row) if row is not None else None


@pytest.fixture

def database(tmp_path: Path) -> TestDatabase:
    database_path = tmp_path / "rough-cut.sqlite"
    engine = create_engine(f"sqlite:///{database_path}", future=True)
    enable_sqlite_foreign_keys(engine)
    rough_cut_metadata.create_all(engine)
    TEST_METADATA.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, future=True)
    db = TestDatabase(engine=engine, session_factory=factory)
    db.seed_user("user-a")
    db.seed_user("user-b")
    db.grant_series("user-a", "series-a")
    db.grant_series("user-b", "series-a")
    yield db
    engine.dispose()


def chapter_frames(*asset_ids: str) -> list[dict[str, Any]]:
    return [
        {
            "storyboard": [asset_id, "ignored-secondary-id"],
            "text": f"镜头 {index + 1}",
            "preview": f"https://media.invalid/{asset_id}.MP4?token=fixture",
        }
        for index, asset_id in enumerate(asset_ids)
    ]


def make_actor_resolver(*, denied_user_ids: set[str] | None = None):
    denied = denied_user_ids or set()

    def resolve_actor(request) -> TrustedActor:
        user_id = request.headers.get("x-test-user")
        if not user_id:
            raise Unauthorized()
        if user_id in denied:
            raise Forbidden("用户当前没有有效会员")
        return TrustedActor(user_id=user_id)

    return resolve_actor


def make_series_access_policy():
    def require_access(session: Session, actor: TrustedActor, series_id: str) -> None:
        membership = session.execute(
            select(series_memberships.c.user_id).where(
                series_memberships.c.user_id == actor.user_id,
                series_memberships.c.series_id == series_id,
            )
        ).first()
        if membership is None:
            raise Forbidden()

    return require_access
