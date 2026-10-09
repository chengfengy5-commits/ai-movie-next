from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, event, insert, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.asset_data.application import (
    create_storyboard_asset,
    delete_asset,
    delete_storyboard_asset,
    update_asset,
    update_storyboard_asset,
)
from haoai_backend.asset_data.errors import AssetDataWriteConflict
from haoai_backend.asset_data.naming import make_key
from haoai_backend.asset_data.persistence import SqlAlchemyAssetDataUnitOfWork
from haoai_backend.asset_data.schemas import (
    CharacterCreate,
    SceneUpdate,
    StoryboardAssetCreate,
    StoryboardAssetUpdate,
)
from haoai_backend.asset_data.tables import (
    ASSET_TABLES,
    metadata as asset_metadata,
    storyboard_assets,
)
from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.media import digest_media_identity, normalize_media_identity
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.series_data.tables import (
    chapters,
    metadata as series_metadata,
    series,
)
from haoai_backend.shared.identity import TrustedActor


NOW = datetime(2026, 1, 1, 12, 0, 0)
LATER = datetime(2027, 2, 3, 4, 5, 6)
ACTOR = TrustedActor("owner")


@pytest.fixture
def source_database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'asset-data-source-phases.sqlite'}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    auth_metadata.create_all(engine)
    series_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    asset_metadata.create_all(engine)
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
            insert(series),
            [
                {
                    "id": series_id,
                    "user_id": owner,
                    "name": series_id,
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": None,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for series_id, owner in (("series-a", "owner"), ("series-b", "other"))
            ],
        )

    def factory(new_ids: tuple[str, ...] = ()):
        identifiers = iter(new_ids)
        return SqlAlchemyAssetDataUnitOfWork(
            Session(engine, future=True),
            now=lambda: LATER,
            new_id=lambda: next(identifiers),
        )

    yield engine, factory
    engine.dispose()


def seed_chapter(
    engine: Engine,
    chapter_id: str,
    content: object,
    *,
    series_id: str = "series-a",
    order: int = 1,
) -> None:
    serialized = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    with engine.begin() as connection:
        connection.execute(
            insert(chapters).values(
                id=chapter_id,
                series_id=series_id,
                title=chapter_id,
                content=serialized,
                order=order,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def seed_storyboard_asset(
    engine: Engine,
    asset_id: str,
    chapter_id: str,
    frame_index: int,
    image_url: str | None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(storyboard_assets).values(
                id=asset_id,
                series_id="series-a",
                chapter_id=chapter_id,
                frame_index=frame_index,
                name=asset_id,
                description="说明",
                image_url=image_url,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def seed_media_state(
    engine: Engine,
    asset_id: str,
    chapter_id: str,
    image_url: str | None,
    preview_url: str | None,
    *,
    revision: int = 1,
    source_valid: bool = True,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(storyboard_media_states).values(
                id=f"media-{asset_id}",
                chapter_id=chapter_id,
                storyboard_asset_id=asset_id,
                media_revision=revision,
                asset_image_digest=digest_media_identity(normalize_media_identity(image_url)),
                preview_digest=digest_media_identity(normalize_media_identity(preview_url)),
                source_valid=source_valid,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def seed_approved_note(engine: Engine, user_id: str, chapter_id: str, asset_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(personal_production_notes).values(
                id=f"notes-{user_id}-{chapter_id}",
                chapter_id=chapter_id,
                user_id=user_id,
                revision=1,
                frame_notes={
                    asset_id: {
                        "status": "approved",
                        "note": f"{user_id} 的保留文字",
                        "approved_media_revision": 1,
                        "needs_reconfirmation": False,
                        "future_field": {"keep": True},
                    },
                    "unrelated-frame": {"opaque": "keep"},
                },
                resume_frame_id=asset_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def snapshot(engine: Engine, tables: tuple[Any, ...]) -> dict[str, list[dict[str, Any]]]:
    with Session(engine, future=True) as session:
        return {
            table.name: [
                dict(row)
                for row in session.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings().all()
            ]
            for table in tables
        }


def test_storyboard_image_change_revises_media_and_revokes_each_approved_owner(source_database) -> None:
    engine, factory = source_database
    content = [{"text": "镜头", "storyboard": ["frame-a"], "preview": "preview://v1"}]
    seed_chapter(engine, "chapter-a", content)
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, "image://v1")
    seed_media_state(engine, "frame-a", "chapter-a", "image://v1", "preview://v1")
    seed_approved_note(engine, "owner", "chapter-a", "frame-a")
    seed_approved_note(engine, "other", "chapter-a", "frame-a")

    updated = update_storyboard_asset(
        factory,
        ACTOR,
        "frame-a",
        StoryboardAssetUpdate(image_url="image://v2"),
    )

    assert updated["image_url"] == "image://v2"
    with Session(engine, future=True) as session:
        media = session.execute(select(storyboard_media_states)).mappings().one()
        notes = {
            row["user_id"]: row
            for row in session.execute(select(personal_production_notes)).mappings().all()
        }
    assert media["media_revision"] == 2
    assert media["source_valid"] is True
    assert media["asset_image_digest"] == digest_media_identity(normalize_media_identity("image://v2"))
    assert {user_id: row["revision"] for user_id, row in notes.items()} == {"owner": 2, "other": 2}
    for user_id, row in notes.items():
        assert row["frame_notes"]["frame-a"] == {
            "status": "unmarked",
            "note": f"{user_id} 的保留文字",
            "approved_media_revision": None,
            "needs_reconfirmation": True,
            "future_field": {"keep": True},
        }
        assert row["frame_notes"]["unrelated-frame"] == {"opaque": "keep"}
        assert row["resume_frame_id"] == "frame-a"


def test_storyboard_image_a_to_b_to_a_keeps_media_revision_monotonic_and_other_approval(source_database) -> None:
    engine, factory = source_database
    content = [
        {"text": "A", "storyboard": ["frame-a"]},
        {"text": "B", "storyboard": ["frame-b"]},
    ]
    seed_chapter(engine, "chapter-a", content)
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, "image://a1")
    seed_storyboard_asset(engine, "frame-b", "chapter-a", 1, "image://b1")
    seed_media_state(engine, "frame-a", "chapter-a", "image://a1", None)
    seed_media_state(engine, "frame-b", "chapter-a", "image://b1", None)
    with engine.begin() as connection:
        connection.execute(
            insert(personal_production_notes),
            [
                {
                    "id": f"notes-{user_id}-chapter-a",
                    "chapter_id": "chapter-a",
                    "user_id": user_id,
                    "revision": 1,
                    "frame_notes": {
                        "frame-a": {
                            "status": "approved",
                            "note": f"{user_id} 的 A 文本",
                            "approved_media_revision": 1,
                            "needs_reconfirmation": False,
                            "extra": {"retain": "a"},
                        },
                        "frame-b": {
                            "status": "approved",
                            "note": f"{user_id} 的 B 文本",
                            "approved_media_revision": 1,
                            "needs_reconfirmation": False,
                            "extra": {"retain": "b"},
                        },
                        "opaque-frame": {"future": [1, 2]},
                    },
                    "resume_frame_id": "frame-a",
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for user_id in ("owner", "other")
            ],
        )

    update_storyboard_asset(
        factory,
        ACTOR,
        "frame-a",
        StoryboardAssetUpdate(image_url="image://a2"),
    )
    with Session(engine, future=True) as session:
        after_first_change = {
            row["user_id"]: row
            for row in session.execute(select(personal_production_notes)).mappings().all()
        }
    for row in after_first_change.values():
        assert row["revision"] == 2
        assert row["frame_notes"]["frame-a"] == {
            "status": "unmarked",
            "note": row["frame_notes"]["frame-a"]["note"],
            "approved_media_revision": None,
            "needs_reconfirmation": True,
            "extra": {"retain": "a"},
        }
        assert row["frame_notes"]["frame-b"]["status"] == "approved"
        assert row["frame_notes"]["frame-b"]["approved_media_revision"] == 1

    update_storyboard_asset(
        factory,
        ACTOR,
        "frame-a",
        StoryboardAssetUpdate(image_url="image://a1"),
    )

    with Session(engine, future=True) as session:
        media = {
            row["storyboard_asset_id"]: row
            for row in session.execute(select(storyboard_media_states)).mappings().all()
        }
        notes = {
            row["user_id"]: row
            for row in session.execute(select(personal_production_notes)).mappings().all()
        }
    assert media["frame-a"]["media_revision"] == 3
    assert media["frame-a"]["asset_image_digest"] == digest_media_identity(
        normalize_media_identity("image://a1")
    )
    assert media["frame-b"]["media_revision"] == 1
    assert media["frame-b"]["asset_image_digest"] == digest_media_identity(
        normalize_media_identity("image://b1")
    )
    for user_id, row in notes.items():
        assert row["revision"] == 2
        assert row["frame_notes"]["frame-a"]["status"] == "unmarked"
        assert row["frame_notes"]["frame-a"]["note"] == f"{user_id} 的 A 文本"
        assert row["frame_notes"]["frame-a"]["extra"] == {"retain": "a"}
        assert row["frame_notes"]["frame-b"] == {
            "status": "approved",
            "note": f"{user_id} 的 B 文本",
            "approved_media_revision": 1,
            "needs_reconfirmation": False,
            "extra": {"retain": "b"},
        }
        assert row["frame_notes"]["opaque-frame"] == {"future": [1, 2]}
        assert row["resume_frame_id"] == "frame-a"


def test_metadata_only_storyboard_update_does_not_initialize_media_state(source_database) -> None:
    engine, factory = source_database
    seed_chapter(engine, "chapter-a", [{"text": "未登记媒体", "storyboard": ["frame-a"]}])
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, None)

    updated = update_storyboard_asset(
        factory,
        ACTOR,
        "frame-a",
        StoryboardAssetUpdate(description="仅改说明"),
    )

    assert updated["description"] == "仅改说明"
    with Session(engine, future=True) as session:
        assert session.execute(select(storyboard_media_states)).mappings().all() == []
        assert session.execute(select(personal_production_notes)).mappings().all() == []


def test_storyboard_insert_seeds_media_before_source_insert_without_changing_chapter_references(
    source_database,
) -> None:
    engine, factory = source_database
    content = '[{"text":"预先存在的源引用","storyboard":["frame-created"],"preview":"preview://created"}]'
    seed_chapter(engine, "chapter-a", content)
    seed_chapter(engine, "chapter-b", [])
    with engine.begin() as connection:
        connection.execute(
            insert(asset_metadata.tables["chapter_locks"]).values(
                id="lock-owner",
                chapter_id="chapter-a",
                user_id="owner",
                username="owner",
                acquired_at=NOW,
                last_active_at=NOW,
                expires_at=NOW,
            )
        )
        connection.execute(
            insert(asset_metadata.tables["chapter_locks"]).values(
                id="lock-other",
                chapter_id="chapter-b",
                user_id="other",
                username="other",
                acquired_at=NOW,
                last_active_at=NOW,
                expires_at=NOW,
            )
        )
    media_visible_before_insert: list[tuple[int, bool, int]] = []

    def observe_source_insert(connection, cursor, statement, parameters, context, executemany) -> None:
        if not statement.lstrip().lower().startswith("insert into storyboard_assets"):
            return
        result = connection.exec_driver_sql(
            "SELECT media_revision, source_valid FROM storyboard_media_states "
            "WHERE storyboard_asset_id = ?",
            ("frame-created",),
        ).one_or_none()
        asset_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM storyboard_assets WHERE id = ?",
            ("frame-created",),
        ).scalar_one()
        if result is not None:
            media_visible_before_insert.append((result[0], bool(result[1]), asset_count))

    event.listen(engine, "before_cursor_execute", observe_source_insert)
    try:
        created = create_storyboard_asset(
            lambda: factory(("frame-created",)),
            ACTOR,
            StoryboardAssetCreate(
                chapter_id="chapter-a",
                frame_index=0,
                name="新镜头",
                image_url="image://created",
            ),
        )
    finally:
        event.remove(engine, "before_cursor_execute", observe_source_insert)

    assert created["id"] == "frame-created"
    assert media_visible_before_insert == [(2, True, 0)]
    foreign_lock_asset = create_storyboard_asset(
        lambda: factory(("frame-foreign-lock",)),
        ACTOR,
        StoryboardAssetCreate(
            chapter_id="chapter-b",
            frame_index=0,
            name="外部锁仍可新增",
            image_url=None,
        ),
    )
    assert foreign_lock_asset["id"] == "frame-foreign-lock"
    with Session(engine, future=True) as session:
        chapter = session.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one()
        media = session.execute(
            select(storyboard_media_states).where(
                storyboard_media_states.c.storyboard_asset_id == "frame-created"
            )
        ).mappings().one()
        locks = {
            row["id"]: row
            for row in session.execute(select(asset_metadata.tables["chapter_locks"])).mappings().all()
        }
    assert chapter == content
    assert media["media_revision"] == 2
    assert media["source_valid"] is True
    assert locks["lock-owner"]["last_active_at"] == LATER
    assert locks["lock-owner"]["expires_at"] > LATER
    assert locks["lock-other"]["last_active_at"] == NOW
    assert locks["lock-other"]["expires_at"] == NOW


def test_storyboard_delete_keeps_chapter_reference_and_tombstones_media_without_lock_refresh(
    source_database,
) -> None:
    engine, factory = source_database
    content = '[{"text":"原帧","storyboard":["frame-a"],"future":true}]'
    seed_chapter(engine, "chapter-a", content)
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, "image://v1")
    seed_media_state(engine, "frame-a", "chapter-a", "image://v1", None)
    seed_approved_note(engine, "owner", "chapter-a", "frame-a")
    with engine.begin() as connection:
        connection.execute(
            insert(asset_metadata.tables["chapter_locks"]).values(
                id="lock-a",
                chapter_id="chapter-a",
                user_id="owner",
                username="owner",
                acquired_at=NOW,
                last_active_at=NOW,
                expires_at=NOW,
            )
        )

    delete_storyboard_asset(factory, ACTOR, "frame-a")

    with Session(engine, future=True) as session:
        stored_content = session.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one()
        media = session.execute(select(storyboard_media_states)).mappings().one()
        note = session.execute(select(personal_production_notes)).mappings().one()
        lock = session.execute(select(asset_metadata.tables["chapter_locks"])).mappings().one()
        remaining_assets = session.execute(select(storyboard_assets)).mappings().all()
    assert stored_content == content
    assert media["media_revision"] == 2
    assert media["asset_image_digest"] is None and media["preview_digest"] is None
    assert media["source_valid"] is False
    assert note["revision"] == 2
    assert note["resume_frame_id"] == "frame-a"
    assert note["frame_notes"]["frame-a"]["status"] == "unmarked"
    assert note["frame_notes"]["frame-a"]["future_field"] == {"keep": True}
    assert lock["last_active_at"] == NOW and lock["expires_at"] == NOW
    assert remaining_assets == []


def test_source_failure_after_media_and_note_updates_rolls_back_all_rows(source_database) -> None:
    engine, factory = source_database
    content = '[{"text":"原帧","storyboard":["frame-a"],"preview":"preview://v1"}]'
    seed_chapter(engine, "chapter-a", content)
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, "image://v1")
    seed_media_state(engine, "frame-a", "chapter-a", "image://v1", "preview://v1")
    seed_approved_note(engine, "owner", "chapter-a", "frame-a")
    seed_approved_note(engine, "other", "chapter-a", "frame-a")
    tables = (chapters, storyboard_assets, storyboard_media_states, personal_production_notes)
    before = snapshot(engine, tables)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_storyboard_image_update "
                "BEFORE UPDATE OF image_url ON storyboard_assets "
                "WHEN OLD.id = 'frame-a' BEGIN SELECT RAISE(ABORT, 'forced source failure'); END"
            )
        )

    with pytest.raises(IntegrityError, match="forced source failure"):
        update_storyboard_asset(
            factory,
            ACTOR,
            "frame-a",
            StoryboardAssetUpdate(image_url="image://v2"),
        )

    assert snapshot(engine, tables) == before


def test_category_delete_removes_exact_references_across_chapters_in_one_commit(source_database) -> None:
    engine, factory = source_database
    first = '[{"text":"A","character":["char-a","keep"],"future":{"x":1}},17,{"character":["char-a"]}]'
    second = '[{"character":["char-a"]},{"character":["keep"]}]'
    untouched = '[{"text":"没有目标引用","future":true}]'
    seed_chapter(engine, "chapter-a", first, order=1)
    seed_chapter(engine, "chapter-b", second, order=2)
    seed_chapter(engine, "chapter-c", untouched, order=3)
    with engine.begin() as connection:
        connection.execute(
            insert(ASSET_TABLES["character"]).values(
                id="char-a",
                series_id="series-a",
                name="角色A",
                gender=None,
                age=None,
                role=None,
                appearance=None,
                description=None,
                image_url=None,
                audio_url=None,
                voice_ref=None,
                aliases="[]",
                canonical_key="角色A",
                created_at=NOW,
                updated_at=NOW,
            )
        )

    session = Session(engine, future=True)
    commit_count = 0
    sql_events: list[tuple[str, str | None]] = []
    phase_events: list[tuple[str, str]] = []

    def observed_factory():
        uow = SqlAlchemyAssetDataUnitOfWork(session, now=lambda: LATER)
        reconcile = uow.reconcile_chapter_media
        update_chapter = uow.update_chapter_content

        def reconcile_with_event(chapter, actor, **kwargs):
            chapter_id = str(chapter["id"])
            phase_events.append(("coordination-complete-start", chapter_id))
            result = reconcile(chapter, actor, **kwargs)
            phase_events.append(("coordination-complete", chapter_id))
            return result

        def update_with_event(chapter_id, *args, **kwargs):
            phase_events.append(("chapter-source-update", chapter_id))
            return update_chapter(chapter_id, *args, **kwargs)

        uow.reconcile_chapter_media = reconcile_with_event
        uow.update_chapter_content = update_with_event
        return uow

    def after_commit(_session: Session) -> None:
        nonlocal commit_count
        commit_count += 1

    def observe_sql(connection, cursor, statement, parameters, context, executemany) -> None:
        compiled = getattr(context, "compiled", None)
        expression = getattr(compiled, "statement", None)
        if expression is None:
            return
        if getattr(expression, "_for_update_arg", None) is not None:
            froms = expression.get_final_froms()
            if any(getattr(from_clause, "name", None) == "chapters" for from_clause in froms):
                if isinstance(parameters, dict):
                    chapter_id = parameters.get("id_1")
                elif isinstance(parameters, (tuple, list)) and parameters:
                    chapter_id = parameters[0]
                else:
                    chapter_id = getattr(compiled, "params", {}).get("id_1")
                sql_events.append(("chapter-lock", chapter_id))
        if statement.lstrip().lower().startswith("update chapters"):
            sql_events.append(("chapter-update", None))

    event.listen(session, "after_commit", after_commit)
    event.listen(engine, "before_cursor_execute", observe_sql)
    try:
        delete_asset(
            observed_factory,
            ACTOR,
            "character",
            "char-a",
        )
    finally:
        event.remove(engine, "before_cursor_execute", observe_sql)
        event.remove(session, "after_commit", after_commit)

    assert commit_count == 1
    last_coordination = max(
        index for index, item in enumerate(phase_events) if item[0] == "coordination-complete"
    )
    first_chapter_source_update = min(
        index for index, item in enumerate(phase_events) if item[0] == "chapter-source-update"
    )
    assert last_coordination < first_chapter_source_update, phase_events
    first_chapter_update = next(
        index for index, item in enumerate(sql_events) if item[0] == "chapter-update"
    )
    chapter_locks_before_update = {
        chapter_id
        for kind, chapter_id in sql_events[:first_chapter_update]
        if kind == "chapter-lock"
    }
    assert {"chapter-a", "chapter-b"} <= chapter_locks_before_update, sql_events

    with Session(engine, future=True) as session:
        contents = {
            row["id"]: row["content"]
            for row in session.execute(select(chapters)).mappings().all()
        }
        assets = session.execute(select(ASSET_TABLES["character"])).mappings().all()
    assert contents["chapter-a"] == '[{"text": "A", "character": ["keep"], "future": {"x": 1}}, 17, {"character": []}]'
    assert contents["chapter-b"] == '[{"character": []}, {"character": ["keep"]}]'
    assert contents["chapter-c"] == untouched
    assert assets == []


def test_category_delete_later_chapter_failure_rolls_back_asset_and_all_earlier_updates(source_database) -> None:
    engine, factory = source_database
    first = '[{"character":["char-a"],"storyboard":["frame-a"],"preview":"preview://a-current"}]'
    second = '[{"character":["char-a"],"storyboard":["frame-b"],"preview":"preview://b-current"}]'
    seed_chapter(engine, "chapter-a", first, order=1)
    seed_chapter(engine, "chapter-b", second, order=2)
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, "image://a")
    seed_storyboard_asset(engine, "frame-b", "chapter-b", 0, "image://b")
    seed_media_state(engine, "frame-a", "chapter-a", "image://a", "preview://a-old")
    seed_media_state(engine, "frame-b", "chapter-b", "image://b", "preview://b-old")
    seed_approved_note(engine, "owner", "chapter-a", "frame-a")
    seed_approved_note(engine, "other", "chapter-a", "frame-a")
    seed_approved_note(engine, "owner", "chapter-b", "frame-b")
    seed_approved_note(engine, "other", "chapter-b", "frame-b")
    with engine.begin() as connection:
        connection.execute(
            insert(ASSET_TABLES["character"]).values(
                id="char-a",
                series_id="series-a",
                name="角色A",
                gender=None,
                age=None,
                role=None,
                appearance=None,
                description=None,
                image_url=None,
                audio_url=None,
                voice_ref=None,
                aliases="[]",
                canonical_key="角色A",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER reject_second_chapter_reconciliation "
                "BEFORE UPDATE OF media_revision ON storyboard_media_states "
                "WHEN OLD.storyboard_asset_id = 'frame-b' "
                "BEGIN SELECT RAISE(ABORT, 'forced later chapter reconciliation failure'); END"
            )
        )
    tables = (
        chapters,
        ASSET_TABLES["character"],
        storyboard_assets,
        storyboard_media_states,
        personal_production_notes,
    )
    before = snapshot(engine, tables)
    assert len(before["storyboard_media_states"]) == 2
    assert len(before["personal_production_notes"]) == 4

    statements: list[str] = []

    def observe_sql(connection, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", observe_sql)
    try:
        with pytest.raises(
            IntegrityError,
            match="forced later chapter reconciliation failure",
        ):
            delete_asset(factory, ACTOR, "character", "char-a")
    finally:
        event.remove(engine, "before_cursor_execute", observe_sql)

    assert any(statement.startswith("delete from characters") for statement in statements)
    assert sum(
        statement.startswith("update storyboard_media_states")
        for statement in statements
    ) == 2
    assert any(
        statement.startswith("update personal_production_notes")
        for statement in statements
    )
    assert snapshot(engine, tables) == before



def test_category_delete_rolls_back_when_a_candidate_chapter_disappears_before_lock(
    source_database,
) -> None:
    engine, factory = source_database
    seed_chapter(
        engine,
        "chapter-a",
        '[{"text":"A","character":["char-a"],"storyboard":["frame-a"]}]',
        order=1,
    )
    seed_chapter(
        engine,
        "chapter-b",
        '[{"text":"B","character":["char-a"],"storyboard":["frame-b"]}]',
        order=2,
    )
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, "image://a")
    seed_storyboard_asset(engine, "frame-b", "chapter-b", 0, "image://b")
    seed_media_state(engine, "frame-a", "chapter-a", "image://a", "preview://a")
    seed_media_state(engine, "frame-b", "chapter-b", "image://b", "preview://b")
    seed_approved_note(engine, "owner", "chapter-a", "frame-a")
    seed_approved_note(engine, "other", "chapter-a", "frame-a")
    seed_approved_note(engine, "owner", "chapter-b", "frame-b")
    seed_approved_note(engine, "other", "chapter-b", "frame-b")
    with engine.begin() as connection:
        connection.execute(
            insert(ASSET_TABLES["character"]).values(
                id="char-a",
                series_id="series-a",
                name="角色A",
                gender=None,
                age=None,
                role=None,
                appearance=None,
                description=None,
                image_url=None,
                audio_url=None,
                voice_ref=None,
                aliases="[]",
                canonical_key="角色A",
                created_at=NOW,
                updated_at=NOW,
            )
        )

    tables = (
        ASSET_TABLES["character"],
        chapters,
        storyboard_assets,
        storyboard_media_states,
        personal_production_notes,
    )
    before = snapshot(engine, tables)
    assert len(before["storyboard_media_states"]) == 2
    assert len(before["personal_production_notes"]) == 4

    def factory_with_missing_locked_chapter():
        uow = factory()
        original_lock_chapters = uow.lock_chapters

        def delete_candidate_then_lock(chapter_ids):
            assert set(chapter_ids) == {"chapter-a", "chapter-b"}
            uow._session.execute(
                delete(personal_production_notes).where(
                    personal_production_notes.c.chapter_id == "chapter-b"
                )
            )
            uow._session.execute(
                delete(storyboard_media_states).where(
                    storyboard_media_states.c.chapter_id == "chapter-b"
                )
            )
            uow._session.execute(
                delete(storyboard_assets).where(
                    storyboard_assets.c.chapter_id == "chapter-b"
                )
            )
            uow._session.execute(
                delete(chapters).where(chapters.c.id == "chapter-b")
            )
            return original_lock_chapters(chapter_ids)

        uow.lock_chapters = delete_candidate_then_lock
        return uow

    with pytest.raises(
        RuntimeError,
        match="a chapter changed while media reconciliation was acquiring locks",
    ):
        delete_asset(factory_with_missing_locked_chapter, ACTOR, "character", "char-a")

    assert snapshot(engine, tables) == before



def _asset_update_factory_with_interleaved_writer(
    engine: Engine,
    factory,
    events: list[tuple[object, ...]],
    *,
    interleave_after_read: int,
    external_image_url: str,
):
    def make_uow():
        uow = SqlAlchemyAssetDataUnitOfWork(Session(engine, future=True), now=lambda: LATER)
        original_load_asset = uow.load_asset
        original_load_chapter = uow.load_chapter
        original_reconcile = uow.reconcile_chapter_media
        original_update = uow.update_asset
        read_count = 0
        interleaved = False

        def load_asset(kind, asset_id, *, lock: bool = False):
            nonlocal read_count, interleaved
            row = original_load_asset(kind, asset_id, lock=lock)
            if kind == "storyboard" and asset_id == "frame-a":
                if lock:
                    events.append(("asset-lock-read", None if row is None else row["image_url"]))
                else:
                    read_count += 1
                    events.append(("asset-read", read_count, None if row is None else row["image_url"]))
                    if read_count == interleave_after_read and not interleaved:
                        interleaved = True
                        events.append(("external-update-start", external_image_url))
                        update_storyboard_asset(
                            factory,
                            ACTOR,
                            "frame-a",
                            StoryboardAssetUpdate(image_url=external_image_url),
                        )
                        events.append(("external-update-committed", external_image_url))
            return row

        def load_chapter(chapter_id, *, lock: bool = False):
            if lock:
                events.append(("chapter-lock", chapter_id))
            return original_load_chapter(chapter_id, lock=lock)

        def reconcile_chapter_media(chapter, actor, **kwargs):
            events.append(("reconcile", str(chapter["id"]), dict(kwargs)))
            return original_reconcile(chapter, actor, **kwargs)

        def update_asset(kind, asset_id, values, *, assigned_fields, expected_storyboard_facts=None):
            events.append(("source-update", kind, dict(values), expected_storyboard_facts))
            return original_update(
                kind,
                asset_id,
                values,
                assigned_fields=assigned_fields,
                expected_storyboard_facts=expected_storyboard_facts,
            )

        uow.load_asset = load_asset
        uow.load_chapter = load_chapter
        uow.reconcile_chapter_media = reconcile_chapter_media
        uow.update_asset = update_asset
        return uow

    return make_uow


def _seed_image_update_case(engine: Engine, *, image_url: str = "image://a") -> None:
    content = '[{"text":"镜头","storyboard":["frame-a"]}]'
    seed_chapter(engine, "chapter-a", content)
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, image_url)
    seed_media_state(engine, "frame-a", "chapter-a", image_url, None)
    seed_approved_note(engine, "owner", "chapter-a", "frame-a")
    seed_approved_note(engine, "other", "chapter-a", "frame-a")


def test_same_image_assignment_does_not_overwrite_a_concurrent_image_change(source_database) -> None:
    engine, factory = source_database
    _seed_image_update_case(engine)
    events: list[tuple[object, ...]] = []
    outer_factory = _asset_update_factory_with_interleaved_writer(
        engine,
        factory,
        events,
        interleave_after_read=1,
        external_image_url="image://b",
    )

    updated = update_storyboard_asset(
        outer_factory,
        ACTOR,
        "frame-a",
        StoryboardAssetUpdate(image_url="image://a", description="外层说明"),
    )

    assert updated["image_url"] == "image://b"
    assert updated["description"] == "外层说明"
    assert not any(event[0] == "reconcile" for event in events)
    outer_source_updates = [event for event in events if event[0] == "source-update"]
    assert len(outer_source_updates) == 1
    assert "image_url" not in outer_source_updates[0][2]
    with Session(engine, future=True) as session:
        asset = session.execute(
            select(storyboard_assets).where(storyboard_assets.c.id == "frame-a")
        ).mappings().one()
        media = session.execute(select(storyboard_media_states)).mappings().one()
        notes = {
            row["user_id"]: row
            for row in session.execute(select(personal_production_notes)).mappings().all()
        }
    assert asset["image_url"] == "image://b"
    assert media["media_revision"] == 2
    assert media["asset_image_digest"] == digest_media_identity(normalize_media_identity("image://b"))
    assert {user_id: row["revision"] for user_id, row in notes.items()} == {"owner": 2, "other": 2}


def test_changed_image_reconciles_from_persisted_base_before_source_update(source_database) -> None:
    engine, factory = source_database
    _seed_image_update_case(engine)
    events: list[tuple[object, ...]] = []
    outer_factory = _asset_update_factory_with_interleaved_writer(
        engine,
        factory,
        events,
        interleave_after_read=1,
        external_image_url="image://b",
    )

    updated = update_storyboard_asset(
        outer_factory,
        ACTOR,
        "frame-a",
        StoryboardAssetUpdate(image_url="image://c"),
    )

    assert updated["image_url"] == "image://c"
    event_names = [item[0] for item in events]
    chapter_lock = event_names.index("chapter-lock")
    locked_asset_read = event_names.index("asset-lock-read")
    reconcile = event_names.index("reconcile")
    source_update = event_names.index("source-update")
    assert chapter_lock < locked_asset_read < reconcile < source_update
    assert events[locked_asset_read] == ("asset-lock-read", "image://b")
    assert events[source_update][3] == ("chapter-a", "image://b")
    with Session(engine, future=True) as session:
        asset = session.execute(
            select(storyboard_assets).where(storyboard_assets.c.id == "frame-a")
        ).mappings().one()
        media = session.execute(select(storyboard_media_states)).mappings().one()
        notes = {
            row["user_id"]: row
            for row in session.execute(select(personal_production_notes)).mappings().all()
        }
    assert asset["image_url"] == "image://c"
    assert media["media_revision"] == 3
    assert media["asset_image_digest"] == digest_media_identity(normalize_media_identity("image://c"))
    assert {user_id: row["revision"] for user_id, row in notes.items()} == {"owner": 2, "other": 2}


def test_asset_facts_changed_between_prefetch_and_lock_fail_without_outer_write(source_database) -> None:
    engine, factory = source_database
    _seed_image_update_case(engine)
    events: list[tuple[object, ...]] = []
    outer_factory = _asset_update_factory_with_interleaved_writer(
        engine,
        factory,
        events,
        interleave_after_read=2,
        external_image_url="image://b",
    )

    with pytest.raises(RuntimeError, match="a storyboard asset changed while media locks were acquired"):
        update_storyboard_asset(
            outer_factory,
            ACTOR,
            "frame-a",
            StoryboardAssetUpdate(image_url="image://c"),
        )

    assert [item[0] for item in events].count("external-update-committed") == 1
    assert not any(item[0] == "reconcile" for item in events)
    assert not any(item[0] == "source-update" for item in events)
    with Session(engine, future=True) as session:
        asset = session.execute(
            select(storyboard_assets).where(storyboard_assets.c.id == "frame-a")
        ).mappings().one()
        media = session.execute(select(storyboard_media_states)).mappings().one()
        notes = {
            row["user_id"]: row
            for row in session.execute(select(personal_production_notes)).mappings().all()
        }
    assert asset["image_url"] == "image://b"
    assert media["media_revision"] == 2
    assert media["asset_image_digest"] == digest_media_identity(normalize_media_identity("image://b"))
    assert {user_id: row["revision"] for user_id, row in notes.items()} == {"owner": 2, "other": 2}



def test_postcommit_asset_refresh_failure_leaves_one_durable_write(source_database) -> None:
    engine, base_factory = source_database
    content = '[{"text":"镜头","storyboard":["frame-a"],"preview":"preview://v1"}]'
    seed_chapter(engine, "chapter-a", content)
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, "image://v1")
    seed_media_state(engine, "frame-a", "chapter-a", "image://v1", "preview://v1")
    seed_approved_note(engine, "owner", "chapter-a", "frame-a")
    seed_approved_note(engine, "other", "chapter-a", "frame-a")
    tables = (
        chapters,
        storyboard_assets,
        storyboard_media_states,
        personal_production_notes,
    )
    factory_calls = 0
    commit_calls = 0
    refresh_attempts = 0

    def failing_refresh_factory():
        nonlocal factory_calls, commit_calls, refresh_attempts
        factory_calls += 1
        uow = base_factory()
        original_commit = uow.commit
        original_load_asset = uow.load_asset
        committed = False

        def commit_once() -> None:
            nonlocal committed, commit_calls
            commit_calls += 1
            original_commit()
            committed = True

        def fail_postcommit_refresh(kind, asset_id, *, lock: bool = False):
            nonlocal refresh_attempts
            if committed and kind == "storyboard" and asset_id == "frame-a":
                refresh_attempts += 1
                raise RuntimeError("postcommit asset refresh failed")
            return original_load_asset(kind, asset_id, lock=lock)

        uow.commit = commit_once
        uow.load_asset = fail_postcommit_refresh
        return uow

    with pytest.raises(RuntimeError, match="postcommit asset refresh failed"):
        update_storyboard_asset(
            failing_refresh_factory,
            ACTOR,
            "frame-a",
            StoryboardAssetUpdate(image_url="image://v2"),
        )

    assert factory_calls == 1
    assert commit_calls == 1
    assert refresh_attempts == 1
    durable = snapshot(engine, tables)
    assert durable["storyboard_assets"][0]["image_url"] == "image://v2"
    media = durable["storyboard_media_states"][0]
    assert media["media_revision"] == 2
    assert media["asset_image_digest"] == digest_media_identity(
        normalize_media_identity("image://v2")
    )
    assert {
        row["user_id"]: row["revision"]
        for row in durable["personal_production_notes"]
    } == {"owner": 2, "other": 2}
    assert {
        row["user_id"]: row["frame_notes"]["frame-a"]["status"]
        for row in durable["personal_production_notes"]
    } == {"owner": "unmarked", "other": "unmarked"}



def test_zero_row_storyboard_update_rolls_back_media_and_private_changes(source_database) -> None:
    engine, factory = source_database
    content = '[{"text":"镜头","storyboard":["frame-a"],"preview":"preview://v1"}]'
    seed_chapter(engine, "chapter-a", content)
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, "image://v1")
    seed_media_state(engine, "frame-a", "chapter-a", "image://v1", "preview://v1")
    seed_approved_note(engine, "owner", "chapter-a", "frame-a")
    seed_approved_note(engine, "other", "chapter-a", "frame-a")
    tables = (
        chapters,
        storyboard_assets,
        storyboard_media_states,
        personal_production_notes,
    )
    before = snapshot(engine, tables)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER ignore_storyboard_image_update "
                "BEFORE UPDATE OF image_url ON storyboard_assets "
                "WHEN OLD.id = 'frame-a' BEGIN SELECT RAISE(IGNORE); END"
            )
        )

    with pytest.raises(AssetDataWriteConflict):
        update_storyboard_asset(
            factory,
            ACTOR,
            "frame-a",
            StoryboardAssetUpdate(image_url="image://v2"),
        )

    assert snapshot(engine, tables) == before


def test_failure_before_commit_rolls_back_real_media_source_and_private_dml(source_database) -> None:
    engine, base_factory = source_database
    content = '[{"text":"镜头","storyboard":["frame-a"],"preview":"preview://v1"}]'
    seed_chapter(engine, "chapter-a", content)
    seed_storyboard_asset(engine, "frame-a", "chapter-a", 0, "image://v1")
    seed_media_state(engine, "frame-a", "chapter-a", "image://v1", "preview://v1")
    seed_approved_note(engine, "owner", "chapter-a", "frame-a")
    seed_approved_note(engine, "other", "chapter-a", "frame-a")
    tables = (
        chapters,
        storyboard_assets,
        storyboard_media_states,
        personal_production_notes,
    )
    before = snapshot(engine, tables)
    statements: list[str] = []
    factory_calls = 0
    commit_attempts = 0

    def observe_sql(connection, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().lower().startswith(("update ", "insert ", "delete ")):
            statements.append(statement.lower())

    def fail_before_commit_factory():
        nonlocal factory_calls, commit_attempts
        factory_calls += 1
        uow = base_factory()

        def fail_before_commit() -> None:
            nonlocal commit_attempts
            commit_attempts += 1
            raise RuntimeError("injected failure before commit")

        uow.commit = fail_before_commit
        return uow

    event.listen(engine, "before_cursor_execute", observe_sql)
    try:
        with pytest.raises(RuntimeError, match="injected failure before commit"):
            update_storyboard_asset(
                fail_before_commit_factory,
                ACTOR,
                "frame-a",
                StoryboardAssetUpdate(image_url="image://v2"),
            )
    finally:
        event.remove(engine, "before_cursor_execute", observe_sql)

    assert factory_calls == 1
    assert commit_attempts == 1
    assert any("update storyboard_media_states" in sql for sql in statements)
    assert any("update personal_production_notes" in sql for sql in statements)
    assert any("update storyboard_assets" in sql for sql in statements)
    assert snapshot(engine, tables) == before



def test_zero_row_category_delete_rolls_back_asset_dml(source_database) -> None:
    engine, factory = source_database
    seed_chapter(engine, "chapter-a", "[]")
    with engine.begin() as connection:
        connection.execute(
            insert(ASSET_TABLES["character"]).values(
                id="char-a",
                series_id="series-a",
                name="角色A",
                gender=None,
                age=None,
                role=None,
                appearance=None,
                description=None,
                image_url=None,
                audio_url=None,
                voice_ref=None,
                aliases="[]",
                canonical_key="角色A",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER ignore_character_delete "
                "BEFORE DELETE ON characters WHEN OLD.id = 'char-a' "
                "BEGIN SELECT RAISE(IGNORE); END"
            )
        )
    tables = (chapters, ASSET_TABLES["character"])
    before = snapshot(engine, tables)

    with pytest.raises(AssetDataWriteConflict):
        delete_asset(factory, ACTOR, "character", "char-a")

    assert snapshot(engine, tables) == before


def _interleaved_scene_update_factory(
    engine: Engine,
    events: list[tuple[object, ...]],
    *,
    scene_id: str,
    concurrent_title: str,
):
    def make_uow():
        uow = SqlAlchemyAssetDataUnitOfWork(Session(engine, future=True), now=lambda: LATER)
        original_load = uow.load_asset
        original_update = uow.update_asset
        interleaved = False

        def load_asset(kind, asset_id, *, lock: bool = False):
            nonlocal interleaved
            row = original_load(kind, asset_id, lock=lock)
            if kind == "scene" and asset_id == scene_id and not lock and not interleaved:
                interleaved = True
                events.append(("first-read", dict(row or {})))
                with engine.begin() as connection:
                    connection.execute(
                        update(ASSET_TABLES["scene"])
                        .where(ASSET_TABLES["scene"].c.id == scene_id)
                        .values(title=concurrent_title, updated_at=NOW)
                    )
                events.append(("concurrent-commit", concurrent_title))
            return row

        def update_asset(kind, asset_id, values, *, assigned_fields, expected_storyboard_facts=None):
            events.append(("planned-write", dict(values), frozenset(assigned_fields)))
            return original_update(
                kind,
                asset_id,
                values,
                assigned_fields=assigned_fields,
                expected_storyboard_facts=expected_storyboard_facts,
            )

        uow.load_asset = load_asset
        uow.update_asset = update_asset
        return uow

    return make_uow


def test_category_assignment_intent_does_not_rebase_on_the_existence_read(source_database) -> None:
    engine, _ = source_database
    scene_table = ASSET_TABLES["scene"]
    with engine.begin() as connection:
        connection.execute(insert(scene_table), [
            {
                "id": scene_id,
                "series_id": "series-a",
                "title": initial_title,
                "description": None,
                "image_url": None,
                "aliases": "[]",
                "canonical_key": make_key("scene", initial_title),
                "created_at": NOW,
                "updated_at": NOW,
            }
            for scene_id, initial_title in (
                ("scene-same", "same-before"),
                ("scene-empty", "empty-before"),
                ("scene-changed", "changed-before"),
            )
        ])

    updates = (
        ("scene-same", SceneUpdate(title="same-before"), "same-after", "same-after", False),
        ("scene-empty", SceneUpdate(), "empty-after", "empty-after", False),
        ("scene-changed", SceneUpdate(title="changed-after"), "changed-after", "changed-after", True),
    )
    for scene_id, request, concurrent_title, expected_title, outer_write_expected in updates:
        statements: list[tuple[str, Any]] = []
        events: list[tuple[object, ...]] = []

        def capture(connection, cursor, statement, parameters, context, executemany) -> None:
            if statement.lstrip().lower().startswith("update scenes"):
                statements.append((statement, parameters))

        event.listen(engine, "before_cursor_execute", capture)
        try:
            result = update_asset(
                _interleaved_scene_update_factory(
                    engine,
                    events,
                    scene_id=scene_id,
                    concurrent_title=concurrent_title,
                ),
                ACTOR,
                "scene",
                scene_id,
                request,
            )
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        assert result["title"] == expected_title
        assert result["updated_at"] == (LATER if outer_write_expected else NOW)
        assert [event[0] for event in events].count("concurrent-commit") == 1
        planned = next(event for event in events if event[0] == "planned-write")
        assert bool(planned[2]) is outer_write_expected
        assert len(statements) == 1 + int(outer_write_expected)
        if scene_id == "scene-changed":
            outer_statement, outer_parameters = statements[-1]
            outer_assignments = outer_statement.lower().split("where", 1)[0]
            assert "title" in outer_assignments
            assert "changed-after" in repr(outer_parameters)

    with Session(engine, future=True) as session:
        rows = {
            row["id"]: row
            for row in session.execute(
                select(scene_table).where(scene_table.c.id.in_([item[0] for item in updates]))
            ).mappings().all()
        }
    assert {key: row["title"] for key, row in rows.items()} == {
        "scene-same": "same-after",
        "scene-empty": "empty-after",
        "scene-changed": "changed-after",
    }
    assert rows["scene-changed"]["updated_at"] == LATER


def test_storyboard_same_value_assignment_preserves_concurrent_name_and_description(source_database) -> None:
    engine, factory = source_database
    _seed_image_update_case(engine)
    with engine.begin() as connection:
        connection.execute(
            update(storyboard_assets)
            .where(storyboard_assets.c.id == "frame-a")
            .values(name="name-before", description="description-before", updated_at=NOW)
        )

    for field, initial, concurrent in (
        ("name", "name-before", "name-after"),
        ("description", "description-before", "description-after"),
    ):
        if field == "description":
            with engine.begin() as connection:
                connection.execute(
                    update(storyboard_assets)
                    .where(storyboard_assets.c.id == "frame-a")
                    .values(name="name-after", description=initial, updated_at=NOW)
                )
        events: list[tuple[object, ...]] = []
        statements: list[tuple[str, Any]] = []

        def make_uow():
            uow = SqlAlchemyAssetDataUnitOfWork(Session(engine, future=True), now=lambda: LATER)
            original_load = uow.load_asset
            original_update = uow.update_asset
            interleaved = False

            def load_asset(kind, asset_id, *, lock: bool = False):
                nonlocal interleaved
                row = original_load(kind, asset_id, lock=lock)
                if kind == "storyboard" and asset_id == "frame-a" and not lock and not interleaved:
                    interleaved = True
                    with engine.begin() as connection:
                        connection.execute(
                            update(storyboard_assets)
                            .where(storyboard_assets.c.id == "frame-a")
                            .values({field: concurrent, "updated_at": NOW})
                        )
                    events.append(("concurrent-commit", field, concurrent))
                return row

            def update_asset(kind, asset_id, values, *, assigned_fields, expected_storyboard_facts=None):
                events.append(("planned-write", dict(values), frozenset(assigned_fields)))
                return original_update(
                    kind,
                    asset_id,
                    values,
                    assigned_fields=assigned_fields,
                    expected_storyboard_facts=expected_storyboard_facts,
                )

            uow.load_asset = load_asset
            uow.update_asset = update_asset
            return uow

        def capture(connection, cursor, statement, parameters, context, executemany) -> None:
            if statement.lstrip().lower().startswith("update storyboard_assets"):
                statements.append((statement, parameters))

        event.listen(engine, "before_cursor_execute", capture)
        try:
            request = StoryboardAssetUpdate(**{field: initial})
            result = update_storyboard_asset(make_uow, ACTOR, "frame-a", request)
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        assert result[field] == concurrent
        assert result["image_url"] == "image://a"
        assert result["updated_at"] == NOW
        assert [event[0] for event in events].count("concurrent-commit") == 1
        planned = next(event for event in events if event[0] == "planned-write")
        assert planned[2] == frozenset()
        assert len(statements) == 1

    with Session(engine, future=True) as session:
        stored = session.execute(
            select(storyboard_assets).where(storyboard_assets.c.id == "frame-a")
        ).mappings().one()
        media = session.execute(select(storyboard_media_states)).mappings().one()
        notes = session.execute(select(personal_production_notes)).mappings().all()
    assert stored["name"] == "name-after"
    assert stored["description"] == "description-after"
    assert media["media_revision"] == 1
    assert {row["user_id"]: row["revision"] for row in notes} == {"owner": 1, "other": 1}
