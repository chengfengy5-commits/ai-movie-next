from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, insert, select, update as sql_update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.asset_data.application import (
    create_asset,
    create_storyboard_asset,
    list_assets,
    update_asset,
    update_storyboard_asset,
)
from haoai_backend.asset_data.errors import AssetDataForbidden, AssetDataUnavailable
from haoai_backend.asset_data.persistence import SqlAlchemyAssetDataUnitOfWork
from haoai_backend.asset_data.schemas import (
    CharacterCreate,
    CharacterUpdate,
    PropCreate,
    PropUpdate,
    SceneCreate,
    SceneUpdate,
    StoryboardAssetCreate,
    StoryboardAssetUpdate,
)
from haoai_backend.asset_data.tables import characters, metadata as asset_metadata, storyboard_assets
from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from haoai_backend.series_data.tables import chapters, metadata as series_metadata, series
from haoai_backend.shared.identity import TrustedActor


NOW = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture
def application_database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'asset-data-application.sqlite'}", future=True)

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
    with engine.begin() as connection:
        connection.execute(
            insert(auth_users),
            [
                {
                    "id": user_id,
                    "username": user_id,
                    "email": f"{user_id}@example.test",
                    "hashed_password": "test-hash",
                    "is_superuser": False,
                    "membership_type": "free",
                    "created_at": NOW,
                    "password_updated_at": None,
                }
                for user_id in ("owner", "other")
            ],
        )
        connection.execute(
            insert(series).values(
                id="series-a",
                user_id="owner",
                name="剧集",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-a",
                series_id="series-a",
                title="第一集",
                content="[]",
                order=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(characters).values(
                id="character-a",
                series_id="series-a",
                name="林岚",
                gender="女",
                age=None,
                role="主角",
                appearance=None,
                description="保留说明",
                image_url=None,
                audio_url="voice://old",
                voice_ref=None,
                aliases='["旧名"]',
                canonical_key="林岚",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(storyboard_assets).values(
                id="frame-a",
                series_id="series-a",
                chapter_id="chapter-a",
                frame_index=4,
                name="镜头A",
                description="原说明",
                image_url=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    def factory():
        return SqlAlchemyAssetDataUnitOfWork(
            Session(engine, future=True),
            now=lambda: datetime(2027, 1, 1),
            new_id=lambda: "generated-id",
        )

    yield engine, factory
    engine.dispose()


def test_missing_factory_fails_before_creating_a_unit_of_work() -> None:
    with pytest.raises(AssetDataUnavailable):
        list_assets(None, TrustedActor("owner"), "character", "series-a")


def test_denied_series_access_stops_before_asset_table_query(application_database: tuple[Engine, Any]) -> None:
    engine, factory = application_database
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(AssetDataForbidden):
            list_assets(factory, TrustedActor("other"), "character", "series-a")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert any("from series" in statement for statement in statements)
    assert not any("from characters" in statement for statement in statements)


def test_character_update_preserves_ordinary_nulls_but_clears_audio_url(
    application_database: tuple[Engine, Any],
) -> None:
    engine, factory = application_database
    updated = update_asset(
        factory,
        TrustedActor("owner"),
        "character",
        "character-a",
        CharacterUpdate(description=None, audio_url=None, aliases=None),
    )

    assert updated["description"] == "保留说明"
    assert updated["audio_url"] is None
    assert updated["aliases"] == ["旧名"]

    cleared = update_asset(
        factory,
        TrustedActor("owner"),
        "character",
        "character-a",
        CharacterUpdate(aliases=[]),
    )
    assert cleared["aliases"] == []
    with Session(engine, future=True) as session:
        stored = session.execute(
            select(characters).where(characters.c.id == "character-a")
        ).mappings().one()
    assert stored["aliases"] == "[]"
    assert stored["description"] == "保留说明"
    assert stored["audio_url"] is None


def test_storyboard_update_ignores_chapter_and_frame_extras_and_metadata_only_skips_media(
    application_database: tuple[Engine, Any],
) -> None:
    engine, factory = application_database
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        updated = update_storyboard_asset(
            factory,
            TrustedActor("owner"),
            "frame-a",
            StoryboardAssetUpdate(
                name="新镜头名",
                chapter_id="attacker-chapter",
                frame_index=99,
                series_id="attacker-series",
            ),
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert updated["name"] == "新镜头名"
    assert updated["chapter_id"] == "chapter-a"
    assert updated["frame_index"] == 4
    assert updated["series_id"] == "series-a"
    assert not any("storyboard_media_states" in statement for statement in statements)
    assert not any("from chapters" in statement and "for update" in statement for statement in statements)



@pytest.mark.parametrize(
    ("kind", "create_request", "update_request", "column"),
    [
        (
            "character",
            CharacterCreate(name="创建时的角色"),
            CharacterUpdate(description="更新说明"),
            "name",
        ),
        (
            "scene",
            SceneCreate(title="创建时的场景"),
            SceneUpdate(description="更新说明"),
            "title",
        ),
        (
            "prop",
            PropCreate(name="创建时的道具"),
            PropUpdate(description="更新说明"),
            "name",
        ),
    ],
)
def test_asset_create_and_update_responses_use_postcommit_rows(
    application_database: tuple[Engine, Any],
    kind: str,
    create_request: Any,
    update_request: Any,
    column: str,
) -> None:
    engine, _ = application_database
    table = {
        "character": characters,
        "scene": asset_metadata.tables["scenes"],
        "prop": asset_metadata.tables["props"],
    }[kind]
    asset_id = f"created-{kind}"

    def postcommit_factory(value: str):
        def factory():
            uow = SqlAlchemyAssetDataUnitOfWork(
                Session(engine, future=True),
                now=lambda: datetime(2027, 1, 1),
                new_id=lambda: asset_id,
            )
            commit = uow.commit

            def commit_then_mutate() -> None:
                commit()
                with engine.begin() as connection:
                    result = connection.execute(
                        sql_update(table)
                        .where(table.c.id == asset_id)
                        .values({column: value})
                    )
                assert result.rowcount == 1

            uow.commit = commit_then_mutate
            return uow

        return factory

    created = create_asset(
        postcommit_factory("postcommit-create"),
        TrustedActor("owner"),
        kind,
        "series-a",
        create_request,
    )
    assert created["id"] == asset_id
    assert created[column] == "postcommit-create"

    updated = update_asset(
        postcommit_factory("postcommit-update"),
        TrustedActor("owner"),
        kind,
        asset_id,
        update_request,
    )
    assert updated[column] == "postcommit-update"
    assert updated["description"] == "更新说明"


def test_storyboard_create_and_update_responses_use_postcommit_rows(
    application_database: tuple[Engine, Any],
) -> None:
    engine, _ = application_database
    asset_id = "created-storyboard-frame"

    def postcommit_factory(field: str, value: str):
        def factory():
            uow = SqlAlchemyAssetDataUnitOfWork(
                Session(engine, future=True),
                now=lambda: datetime(2027, 1, 1),
                new_id=lambda: asset_id,
            )
            commit = uow.commit

            def commit_then_mutate() -> None:
                commit()
                with engine.begin() as connection:
                    result = connection.execute(
                        sql_update(storyboard_assets)
                        .where(storyboard_assets.c.id == asset_id)
                        .values({field: value})
                    )
                assert result.rowcount == 1

            uow.commit = commit_then_mutate
            return uow

        return factory

    created = create_storyboard_asset(
        postcommit_factory("name", "postcommit-create"),
        TrustedActor("owner"),
        StoryboardAssetCreate(
            chapter_id="chapter-a",
            frame_index=6,
            name="request-create",
        ),
    )
    assert created["id"] == asset_id
    assert created["name"] == "postcommit-create"

    updated = update_storyboard_asset(
        postcommit_factory("description", "postcommit-update"),
        TrustedActor("owner"),
        asset_id,
        StoryboardAssetUpdate(name="request-update"),
    )
    assert updated["name"] == "request-update"
    assert updated["description"] == "postcommit-update"
