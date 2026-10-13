from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import ArgumentError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_scene_prop_queries.persistence import (
    SqlAlchemyAdminScenePropQueriesUnitOfWork,
)
from haoai_backend.admin_scene_prop_queries.tables import (
    Base,
    PropRow,
    SceneRow,
    SeriesRow,
)


class CountingSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_attempts = 0
        self.rollback_attempts = 0
        self.closed = False

    def commit(self) -> None:
        self.commit_attempts += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_attempts += 1
        super().rollback()

    def close(self) -> None:
        self.closed = True
        super().close()


@pytest.fixture
def database():
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    stamp = datetime(2026, 10, 13, 4, 0)
    scenes = [
        {
            "id": f"scene-{number:02}",
            "series_id": "series-a" if number % 2 == 0 else "series-b",
            "title": f"Scene {number}",
            "description": None if number % 2 else f"Description {number}",
            "image_url": None,
            "aliases": f"alias-{(number * 7) % 11:02}",
            "canonical_key": f"key-{(number * 3) % 11:02}",
            "created_at": stamp + timedelta(minutes=number),
            "updated_at": stamp + timedelta(minutes=10 - number),
        }
        for number in range(11)
    ]
    props = [
        {
            "id": "prop-a",
            "series_id": "series-a",
            "name": "Lantern",
            "description": None,
            "image_url": "/lantern.png",
            "aliases": "z-lamp",
            "canonical_key": "prop|lantern",
            "created_at": stamp,
            "updated_at": stamp + timedelta(minutes=2),
        },
        {
            "id": "prop-b",
            "series_id": "series-b",
            "name": "Key",
            "description": "Brass key",
            "image_url": None,
            "aliases": "a-key",
            "canonical_key": "prop|key",
            "created_at": stamp + timedelta(minutes=1),
            "updated_at": stamp + timedelta(minutes=1),
        },
    ]
    with engine.begin() as connection:
        connection.execute(
            insert(SeriesRow.__table__),
            [{"id": "series-a"}, {"id": "series-b"}],
        )
        connection.execute(insert(SceneRow.__table__), scenes)
        connection.execute(insert(PropRow.__table__), props)

    maker = sessionmaker(
        engine,
        class_=CountingSession,
        expire_on_commit=False,
        autoflush=False,
    )
    sessions: list[CountingSession] = []

    def factory() -> SqlAlchemyAdminScenePropQueriesUnitOfWork:
        session = maker()
        sessions.append(session)
        return SqlAlchemyAdminScenePropQueriesUnitOfWork(session)

    try:
        yield engine, factory, sessions
    finally:
        engine.dispose()


def test_real_sqlite_default_page_counts_all_eleven_scene_rows(database) -> None:
    _engine, factory, sessions = database
    unit = factory()
    try:
        records, total = unit.list_scenes(
            sort_field="created_at",
            sort_order="desc",
            skip=0,
            limit=10,
        )
        assert total == 11
        assert len(records) == 10
        assert records[0].id == "scene-10"
        assert {record.series_id for record in records} == {"series-a", "series-b"}
        assert set(records[0].__dataclass_fields__) == {
            "id", "series_id", "title", "description", "image_url", "created_at"
        }
        assert sessions[0].commit_attempts == 0
    finally:
        unit.close()
    assert sessions[0].closed


def test_raw_dynamic_sort_supports_hidden_columns_direction_and_id_fallback(database) -> None:
    _engine, factory, sessions = database
    unit = factory()
    try:
        alias_rows, _ = unit.list_scenes(
            sort_field="aliases", sort_order="asc", skip=0, limit=11
        )
        expected_alias_order = sorted(
            range(11), key=lambda number: f"alias-{(number * 7) % 11:02}"
        )
        assert [row.id for row in alias_rows] == [
            f"scene-{number:02}" for number in expected_alias_order
        ]

        canonical_rows, _ = unit.list_scenes(
            sort_field="canonical_key", sort_order="desc", skip=0, limit=11
        )
        expected_canonical_order = sorted(
            range(11),
            key=lambda number: f"key-{(number * 3) % 11:02}",
            reverse=True,
        )
        assert [row.id for row in canonical_rows] == [
            f"scene-{number:02}" for number in expected_canonical_order
        ]

        updated_rows, _ = unit.list_props(
            sort_field="updated_at", sort_order="desc", skip=0, limit=10
        )
        assert [row.id for row in updated_rows] == ["prop-a", "prop-b"]

        unknown_rows, _ = unit.list_props(
            sort_field="not_a_model_attribute", sort_order="DESC", skip=0, limit=10
        )
        assert [row.id for row in unknown_rows] == ["prop-a", "prop-b"]
        assert sessions[0].commit_attempts == 0
    finally:
        unit.close()


@pytest.mark.parametrize("sort_field", ["metadata", "registry"])
def test_metadata_and_registry_keep_legacy_sort_errors(database, sort_field) -> None:
    _engine, factory, sessions = database
    unit = factory()
    try:
        with pytest.raises(ArgumentError):
            unit.list_scenes(
                sort_field=sort_field,
                sort_order="desc",
                skip=0,
                limit=10,
            )
        assert sessions[0].commit_attempts == 0
    finally:
        unit.close()


def test_relationship_sort_keeps_legacy_sql_expression_and_sqlite_error(database) -> None:
    _engine, factory, sessions = database
    unit = factory()
    try:
        with pytest.raises(OperationalError) as raised:
            unit.list_scenes(
                sort_field="series",
                sort_order="desc",
                skip=0,
                limit=10,
            )
        assert "ORDER BY series.id = scenes.series_id DESC" in raised.value.statement
        assert "no such column: series.id" in str(raised.value.orig)
        assert sessions[0].commit_attempts == 0
    finally:
        unit.close()


def test_detail_projection_omits_private_alias_and_updated_columns(database) -> None:
    _engine, factory, sessions = database
    unit = factory()
    try:
        scene = unit.get_scene("scene-01")
        prop = unit.get_prop("prop-b")
        assert scene is not None and scene.series_id == "series-b"
        assert scene.title == "Scene 1"
        assert prop is not None and prop.name == "Key"
        assert prop.description == "Brass key"
        assert sessions[0].commit_attempts == 0
    finally:
        unit.close()
