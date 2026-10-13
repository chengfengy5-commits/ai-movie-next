from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, insert, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_series import application
from haoai_backend.admin_series.domain import AdminSeriesActor
from haoai_backend.admin_series.persistence import admin_series_uow_factory
from haoai_backend.admin_series.tables import (
    AssetDuplicateExclusionRow,
    CharacterRow,
    ChapterRow,
    ChatMessageRow,
    FusedAssetRow,
    PropRow,
    SceneRow,
    SeriesRow,
    StoryboardAssetRow,
    TeamReferenceRow,
    UserRow,
    metadata,
)

ADMIN = AdminSeriesActor(user_id="admin", is_superuser=True)
STAMP = datetime(2026, 10, 13, 9, 30)


class CountingSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_attempts = 0
        self.rollback_attempts = 0
        self.refresh_instances = []
        self.closed = False

    def commit(self):
        self.commit_attempts += 1
        return super().commit()

    def rollback(self):
        self.rollback_attempts += 1
        return super().rollback()

    def refresh(self, instance, *args, **kwargs):
        self.refresh_instances.append(instance)
        return super().refresh(instance, *args, **kwargs)

    def close(self):
        self.closed = True
        return super().close()


@event.listens_for(CountingSession, "loaded_as_persistent")
def _record_loaded(session, instance):
    session.info.setdefault("loaded_instances", []).append(instance)


def _enable_foreign_keys(connection, _record):
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def build_database(path: Path, *, seed_series=True):
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", _enable_foreign_keys)
    metadata.create_all(engine)
    maker = sessionmaker(
        engine,
        class_=CountingSession,
        expire_on_commit=False,
        autoflush=False,
        future=True,
    )
    sessions: list[CountingSession] = []

    def session_factory():
        session = maker()
        sessions.append(session)
        return session

    with engine.begin() as connection:
        for user_id in ("admin", "owner", "other"):
            connection.execute(
                insert(UserRow.__table__).values(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.test",
                    hashed_password="unused",
                    is_superuser=user_id == "admin",
                    membership_type="free",
                    membership_expires_at=None,
                    avatar_url=None,
                    bio=None,
                    created_at=STAMP,
                    password_updated_at=None,
                )
            )
        connection.execute(insert(TeamReferenceRow.__table__).values(id="team"))
        if seed_series:
            for series_id, user_id, name, created_at in (
                ("a", "owner", "Space_A%One", STAMP),
                ("b", "owner", "Space_A%Two", STAMP + timedelta(seconds=1)),
                ("c", "other", "space_A%Two", STAMP + timedelta(seconds=2)),
            ):
                connection.execute(
                    insert(SeriesRow.__table__).values(
                        id=series_id,
                        user_id=user_id,
                        name=name,
                        description=None if series_id == "a" else "description",
                        image_url=None,
                        style_prompt_id=None,
                        team_id="team" if series_id == "c" else None,
                        claimed_by=None,
                        claimed_at=None,
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )

    return engine, admin_series_uow_factory(session_factory), sessions


def seed_complete_series_graph(engine):
    with engine.begin() as connection:
        connection.execute(
            insert(SeriesRow.__table__).values(
                id="parent",
                user_id="owner",
                name="Graph",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        connection.execute(
            insert(ChapterRow.__table__).values(
                id="chapter",
                series_id="parent",
                title="Chapter",
                content="content",
                order=0,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        connection.execute(
            insert(ChatMessageRow.__table__).values(
                id="chat",
                chapter_id="chapter",
                frame_index=None,
                asset_type=None,
                asset_id=None,
                chat_mode="chat",
                role="user",
                content="message",
                model_name=None,
                created_at=STAMP,
            )
        )
        connection.execute(
            insert(CharacterRow.__table__).values(
                id="character",
                series_id="parent",
                name="Character",
                gender=None,
                age=None,
                role=None,
                appearance=None,
                description=None,
                image_url=None,
                audio_url=None,
                voice_ref=None,
                aliases="[]",
                canonical_key="character",
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        connection.execute(
            insert(SceneRow.__table__).values(
                id="scene",
                series_id="parent",
                title="Scene",
                description=None,
                image_url=None,
                aliases="[]",
                canonical_key="scene",
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        connection.execute(
            insert(PropRow.__table__).values(
                id="prop",
                series_id="parent",
                name="Prop",
                description=None,
                image_url=None,
                aliases="[]",
                canonical_key="prop",
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        connection.execute(
            insert(StoryboardAssetRow.__table__).values(
                id="storyboard",
                series_id="parent",
                chapter_id="chapter",
                frame_index=0,
                name="Storyboard",
                description=None,
                image_url=None,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        connection.execute(
            insert(FusedAssetRow.__table__).values(
                id="fused",
                series_id="parent",
                member_type="prop",
                members="[]",
                name="Fused",
                image_url=None,
                size=None,
                status="completed",
                error_message=None,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        connection.execute(
            insert(AssetDuplicateExclusionRow.__table__).values(
                id="exclusion",
                series_id="parent",
                asset_type="character",
                asset_id_a="a",
                asset_id_b="b",
                created_by="owner",
                created_at=STAMP,
            )
        )


def test_filtered_count_precedes_page_and_preserves_raw_wildcards_sort_and_nulls(tmp_path: Path):
    engine, factory, sessions = build_database(tmp_path / "list.sqlite")
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        page = application.list_series(
            factory,
            ADMIN,
            user_id="owner",
            name="space_%",
            sort_field="name",
            sort_order="desc",
            limit=5000,
        )
        assert page["total"] == 2
        assert [row["id"] for row in page["data"]] == ["b", "a"]
        assert page["data"][1]["description"] is None
        assert page["data"][1]["image_url"] is None
        assert set(page["data"][0]) == {
            "id",
            "user_id",
            "name",
            "description",
            "image_url",
            "created_at",
            "updated_at",
        }
        selects = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        assert len(selects) == 2
        assert "count(" in selects[0].lower()
        assert "FROM series" in selects[0]
        assert "FROM series" in selects[1]
        assert sessions[0].commit_attempts == len(sessions[0].refresh_instances) == 0
        assert sessions[0].closed

        fallback = application.list_series(
            factory,
            ADMIN,
            sort_field="not_a_column",
            sort_order="DESC",
        )
        assert fallback["total"] == 3
        assert [row["id"] for row in fallback["data"]] == ["a", "b", "c"]
        with pytest.raises(SQLAlchemyError):
            application.list_series(
                factory,
                ADMIN,
                sort_field="metadata",
                sort_order="desc",
            )
        assert all(session.closed for session in sessions)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        engine.dispose()


def test_private_rows_keep_the_full_series_graph_and_no_team_relationship():
    assert len(SeriesRow.__table__.columns) == 11
    assert len(UserRow.__table__.columns) == 11
    assert set(inspect(SeriesRow).relationships.keys()) == {
        "user",
        "chapters",
        "characters",
        "scenes",
        "props",
        "storyboard_assets",
        "fused_assets",
        "duplicate_exclusions",
    }
    assert set(inspect(ChapterRow).relationships.keys()) == {"series", "chat_messages"}
    assert "team" not in inspect(SeriesRow).relationships
    assert not hasattr(SeriesRow, "team")
    assert {"aliases", "canonical_key"} <= set(CharacterRow.__table__.columns.keys())
    assert {"aliases", "canonical_key"} <= set(SceneRow.__table__.columns.keys())
    assert {"aliases", "canonical_key"} <= set(PropRow.__table__.columns.keys())


def test_create_uses_only_missing_defaults_forced_owner_and_refreshes_inserted_row(tmp_path: Path):
    engine, factory, sessions = build_database(tmp_path / "create.sqlite", seed_series=False)
    try:
        payload = application.create_series(
            factory,
            ADMIN,
            {
                "id": "caller-id",
                "user_id": "owner",
                "created_at": "caller-time",
                "updated_at": "caller-time",
            },
        )
        assert payload["id"] != "caller-id"
        assert payload["user_id"] == "admin"
        assert payload["name"] == ""
        assert payload["description"] == ""
        assert payload["image_url"] is None
        assert len(payload) == 7
        assert sessions[0].commit_attempts == 1
        assert len(sessions[0].refresh_instances) == 1
        assert sessions[0].refresh_instances[0].id == payload["id"]
        assert sessions[0].closed
    finally:
        engine.dispose()


def test_update_refreshes_same_row_after_primary_key_change_and_keeps_unmapped_attributes(tmp_path: Path):
    engine, factory, sessions = build_database(tmp_path / "update.sqlite")
    try:
        payload = application.update_series(
            factory,
            ADMIN,
            "a",
            {
                "id": "changed-id",
                "user_id": "other",
                "name": "changed",
                "metadata": "metadata-shadow",
                "registry": "registry-shadow",
                "__table__": "table-shadow",
            },
        )
        session = sessions[0]
        assert payload["id"] == "changed-id"
        assert payload["user_id"] == "other"
        assert payload["name"] == "changed"
        assert session.commit_attempts == 1
        assert len(session.refresh_instances) == 1
        refreshed = session.refresh_instances[0]
        assert refreshed.id == "changed-id"
        assert refreshed.metadata == "metadata-shadow"
        assert refreshed.registry == "registry-shadow"
        assert refreshed.__table__ == "table-shadow"
        assert session.info["loaded_instances"] == [refreshed]
        assert session.closed
        with Session(engine) as verify:
            assert verify.query(SeriesRow).filter_by(id="a").first() is None
            saved = verify.query(SeriesRow).filter_by(id="changed-id").one()
            assert saved.name == "changed"
    finally:
        engine.dispose()


def test_put_empty_relationship_lists_keep_legacy_orphan_deletes_and_invalid_values_fail(tmp_path: Path):
    engine, factory, sessions = build_database(tmp_path / "relations.sqlite")
    seed_complete_series_graph(engine)
    try:
        application.update_series(
            factory,
            ADMIN,
            "parent",
            {
                "chapters": [],
                "characters": [],
                "scenes": [],
                "props": [],
                "storyboard_assets": [],
                "fused_assets": [],
                "duplicate_exclusions": [],
            },
        )
        assert sessions[0].commit_attempts == 1
        assert sessions[0].closed
        with Session(engine) as verify:
            for row_type in (
                ChapterRow,
                ChatMessageRow,
                CharacterRow,
                SceneRow,
                PropRow,
                StoryboardAssetRow,
                FusedAssetRow,
                AssetDuplicateExclusionRow,
            ):
                assert verify.query(row_type).count() == 0

        with pytest.raises(AttributeError):
            application.update_series(
                factory,
                ADMIN,
                "parent",
                {"chapters": [{"not": "a mapped row"}]},
            )
        assert sessions[1].commit_attempts == 0
        assert sessions[1].rollback_attempts >= 1
        assert sessions[1].closed
        with Session(engine) as verify:
            assert verify.query(SeriesRow).filter_by(id="parent").count() == 1
            assert verify.query(ChapterRow).filter_by(id="chapter").count() == 0
    finally:
        engine.dispose()
