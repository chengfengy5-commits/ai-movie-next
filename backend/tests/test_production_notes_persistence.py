"""Real SQLite tests for notes isolation and lazy media maintenance."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from conftest import TestDatabase, chapter_frames, make_series_access_policy
from haoai_backend.personal_production.rough_cut.tables import (
    chapters as rough_chapters,
    storyboard_assets as rough_storyboard_assets,
)
from haoai_backend.personal_production.notes.application import (
    get_personal_production_notes,
    save_personal_production_notes,
)
from haoai_backend.personal_production.notes.domain import FrameNoteChange, PersonalNotesUpdate
from haoai_backend.personal_production.notes.errors import NotesConflict
from haoai_backend.personal_production.notes.persistence import SqlAlchemyNotesUnitOfWork
from haoai_backend.personal_production.notes.domain import ChapterRecord
from haoai_backend.personal_production.notes.reconciliation import reconcile_chapter_media_state
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.shared.identity import TrustedActor


@pytest.fixture
def notes_database(database: TestDatabase) -> TestDatabase:
    notes_metadata.create_all(database.engine)
    return database


def unit_of_work_factory(database: TestDatabase):
    return lambda: SqlAlchemyNotesUnitOfWork(
        database.session_factory(), make_series_access_policy()
    )


def save_update(
    factory,
    user_id: str,
    chapter_id: str,
    *,
    expected_revision: int,
    frames: tuple[FrameNoteChange, ...] = (),
    has_resume_patch: bool = False,
    resume_frame_id: str | None = None,
):
    return save_personal_production_notes(
        factory,
        TrustedActor(user_id),
        chapter_id,
        PersonalNotesUpdate(
            expected_revision,
            frames,
            has_resume_patch,
            resume_frame_id,
        ),
    )


def read_note(database: TestDatabase, chapter_id: str, user_id: str) -> dict[str, Any] | None:
    with database.session_factory() as session:
        row = session.execute(
            select(personal_production_notes).where(
                personal_production_notes.c.chapter_id == chapter_id,
                personal_production_notes.c.user_id == user_id,
            )
        ).mappings().first()
        return dict(row) if row is not None else None


def test_r0_get_creates_media_identity_but_never_a_private_row(notes_database: TestDatabase) -> None:
    notes_database.seed_chapter("notes-a", "series-a", chapter_frames("a", "b"), ("a", "b"))
    factory = unit_of_work_factory(notes_database)

    response = get_personal_production_notes(factory, TrustedActor("user-a"), "notes-a")

    assert response["chapter_id"] == "notes-a"
    assert response["revision"] == 0
    assert response["media_state"] == "ready"
    assert response["frame_notes"] == {}
    assert response["resume_frame_id"] is None
    assert [frame["storyboard_asset_id"] for frame in response["frames"]] == ["a", "b"]
    assert [frame["media_revision"] for frame in response["frames"]] == [1, 1]
    assert read_note(notes_database, "notes-a", "user-a") is None
    with notes_database.session_factory() as session:
        assert session.execute(
            select(storyboard_media_states.c.storyboard_asset_id)
            .where(storyboard_media_states.c.chapter_id == "notes-a")
        ).scalars().all() == ["a", "b"]


def test_media_change_revokes_every_users_approval_and_a_b_a_keeps_tombstone_revision(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("notes-a", "series-a", chapter_frames("a"), ("a",))
    factory = unit_of_work_factory(notes_database)
    for user_id in ("user-a", "user-b"):
        saved = save_update(
            factory,
            user_id,
            "notes-a",
            expected_revision=0,
            frames=(FrameNoteChange("a", 1, "approved", f"{user_id} note"),),
        )
        assert saved["revision"] == 1
        assert saved["frame_notes"]["a"]["status"] == "approved"

    with notes_database.engine.begin() as connection:
        connection.execute(
            update(rough_storyboard_assets)
            .where(rough_storyboard_assets.c.id == "a")
            .values(image_url="https://media.invalid/a.png")
        )

    changed = get_personal_production_notes(factory, TrustedActor("user-a"), "notes-a")
    assert changed["revision"] == 2
    assert changed["frames"][0]["media_revision"] == 2
    assert changed["frame_notes"]["a"]["status"] == "unmarked"
    assert changed["frame_notes"]["a"]["note"] == "user-a note"
    assert changed["frame_notes"]["a"]["needs_reconfirmation"] is True
    other_user = get_personal_production_notes(factory, TrustedActor("user-b"), "notes-a")
    assert other_user["revision"] == 2
    assert other_user["frame_notes"]["a"]["note"] == "user-b note"

    with notes_database.engine.begin() as connection:
        connection.execute(
            delete(rough_storyboard_assets)
            .where(rough_storyboard_assets.c.id == "a")
        )
    deleted = get_personal_production_notes(factory, TrustedActor("user-a"), "notes-a")
    assert deleted["frames"][0]["storyboard_asset_id"] is None
    with notes_database.session_factory() as session:
        assert session.execute(
            select(storyboard_media_states.c.media_revision)
            .where(
                storyboard_media_states.c.chapter_id == "notes-a",
                storyboard_media_states.c.storyboard_asset_id == "a",
            )
        ).scalar_one() == 3

    with notes_database.engine.begin() as connection:
        connection.execute(
            insert(rough_storyboard_assets).values(
                id="a",
                series_id="series-a",
                chapter_id="notes-a",
                frame_index=0,
                image_url="https://media.invalid/a.png",
            )
        )
    restored = get_personal_production_notes(factory, TrustedActor("user-a"), "notes-a")
    assert restored["frames"][0]["storyboard_asset_id"] == "a"
    assert restored["frames"][0]["media_revision"] == 4
    assert get_personal_production_notes(factory, TrustedActor("user-a"), "notes-a")["frames"][0]["media_revision"] == 4


def test_stale_private_revision_rolls_back_pending_media_reconciliation(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("stale-media", "series-a", chapter_frames("a"), ("a",))
    factory = unit_of_work_factory(notes_database)
    initial = get_personal_production_notes(factory, TrustedActor("user-a"), "stale-media")
    save_update(
        factory,
        "user-a",
        "stale-media",
        expected_revision=initial["revision"],
        frames=(FrameNoteChange("a", 1, "approved", "仍然有效"),),
    )
    with notes_database.engine.begin() as connection:
        connection.execute(
            update(rough_storyboard_assets)
            .where(rough_storyboard_assets.c.id == "a")
            .values(image_url="https://media.invalid/changed.png")
        )

    with pytest.raises(NotesConflict, match="个人记录已变化"):
        save_update(
            factory,
            "user-a",
            "stale-media",
            expected_revision=0,
            frames=(FrameNoteChange("a", 1, None, "不能覆盖"),),
        )

    stored = read_note(notes_database, "stale-media", "user-a")
    assert stored is not None
    assert stored["revision"] == 1
    assert stored["frame_notes"]["a"]["status"] == "approved"
    with notes_database.session_factory() as session:
        assert session.execute(
            select(storyboard_media_states.c.media_revision).where(
                storyboard_media_states.c.chapter_id == "stale-media",
                storyboard_media_states.c.storyboard_asset_id == "a",
            )
        ).scalar_one() == 1

    reconciled = get_personal_production_notes(factory, TrustedActor("user-a"), "stale-media")
    assert reconciled["frames"][0]["media_revision"] == 2
    assert reconciled["frame_notes"]["a"]["status"] == "unmarked"
    assert reconciled["frame_notes"]["a"]["needs_reconfirmation"] is True


def test_dual_empty_legacy_approval_commits_revision_then_rejects_prefilled_put(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("empty-media", "series-a", [{"storyboard": ["a"]}], ("a",))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with notes_database.engine.begin() as connection:
        connection.execute(
            insert(personal_production_notes).values(
                id="notes-row",
                chapter_id="empty-media",
                user_id="user-a",
                revision=7,
                frame_notes={
                    "a": {
                        "status": "approved",
                        "note": "原备注",
                        "approved_media_revision": 1,
                        "unknown": {"keep": True},
                    }
                },
                resume_frame_id="a",
                created_at=now,
                updated_at=now,
            )
        )

    with pytest.raises(NotesConflict, match="历史认可已撤销"):
        save_update(
            unit_of_work_factory(notes_database),
            "user-a",
            "empty-media",
            expected_revision=8,
            frames=(FrameNoteChange("a", 1, None, "不应写入"),),
            has_resume_patch=True,
            resume_frame_id=None,
        )

    stored = read_note(notes_database, "empty-media", "user-a")
    assert stored is not None
    assert stored["revision"] == 8
    assert stored["frame_notes"]["a"] == {
        "status": "unmarked",
        "note": "原备注",
        "approved_media_revision": None,
        "needs_reconfirmation": True,
        "unknown": {"keep": True},
    }
    assert stored["resume_frame_id"] == "a"


def test_empty_unreadable_chapter_allows_first_explicit_null_resume_save(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("empty-chapter", "series-a", [])
    response = save_update(
        unit_of_work_factory(notes_database),
        "user-a",
        "empty-chapter",
        expected_revision=0,
        has_resume_patch=True,
        resume_frame_id=None,
    )
    assert response["revision"] == 1
    assert response["media_state"] == "empty"
    assert response["frames"] == []
    assert response["resume_frame_id"] is None
    stored = read_note(notes_database, "empty-chapter", "user-a")
    assert stored is not None and stored["revision"] == 1


def test_get_commits_double_empty_legacy_approval_maintenance_once(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("get-empty", "series-a", [{"storyboard": ["a"]}], ("a",))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with notes_database.engine.begin() as connection:
        connection.execute(
            insert(personal_production_notes).values(
                id="get-empty-note",
                chapter_id="get-empty",
                user_id="user-a",
                revision=7,
                frame_notes={
                    "a": {
                        "status": "approved",
                        "note": "保留原备注",
                        "approved_media_revision": 1,
                        "legacy": {"keep": True},
                    }
                },
                resume_frame_id="a",
                created_at=now,
                updated_at=now,
            )
        )

    factory = unit_of_work_factory(notes_database)
    repaired = get_personal_production_notes(factory, TrustedActor("user-a"), "get-empty")

    assert repaired["revision"] == 8
    assert repaired["frame_notes"]["a"] == {
        "status": "unmarked",
        "note": "保留原备注",
        "approved_media_revision": None,
        "needs_reconfirmation": True,
        "legacy": {"keep": True},
    }
    assert repaired["resume_frame_id"] == "a"
    assert read_note(notes_database, "get-empty", "user-a")["revision"] == 8

    unchanged = get_personal_production_notes(factory, TrustedActor("user-a"), "get-empty")
    assert unchanged["revision"] == 8
    assert unchanged["frame_notes"] == repaired["frame_notes"]


def test_source_writer_can_reconcile_in_its_transaction_and_rollback_all_changes(
    notes_database: TestDatabase,
) -> None:
    original_frames = [{"storyboard": ["a"], "preview": "https://media.invalid/a-v1.png"}]
    notes_database.seed_chapter("source-write", "series-a", original_frames, ("a",))
    factory = unit_of_work_factory(notes_database)
    initial = get_personal_production_notes(factory, TrustedActor("user-a"), "source-write")
    save_update(
        factory,
        "user-a",
        "source-write",
        expected_revision=initial["revision"],
        frames=(FrameNoteChange("a", 1, "approved", "保留到回滚之后"),),
    )

    session = notes_database.session_factory()
    uow = SqlAlchemyNotesUnitOfWork(session, make_series_access_policy())
    chapter = uow.load_chapter("source-write", lock=True)
    assert chapter is not None
    next_frames = [{"storyboard": ["a"], "preview": "https://media.invalid/a-v2.png"}]
    session.execute(
        update(rough_chapters)
        .where(rough_chapters.c.id == "source-write")
        .values(content=json.dumps(next_frames))
    )
    session.execute(
        update(rough_storyboard_assets)
        .where(rough_storyboard_assets.c.id == "a")
        .values(image_url="https://media.invalid/a-v2.png")
    )
    changed_chapter = uow.load_chapter("source-write", lock=False)
    assert changed_chapter is not None

    snapshot = reconcile_chapter_media_state(uow, changed_chapter)
    affected = uow.load_personal_notes("source-write", "user-a", lock=True)

    assert snapshot.frames[0].media_revision == 2
    assert affected is not None
    assert affected.revision == 2
    assert affected.frame_notes["a"]["status"] == "unmarked"
    assert affected.frame_notes["a"]["note"] == "保留到回滚之后"
    assert session.in_transaction()

    uow.rollback()
    uow.close()

    after_rollback = get_personal_production_notes(factory, TrustedActor("user-a"), "source-write")
    assert after_rollback["frames"][0]["media_revision"] == 1
    assert after_rollback["frame_notes"]["a"]["status"] == "approved"
    assert after_rollback["frame_notes"]["a"]["note"] == "保留到回滚之后"


def test_private_unique_key_collision_is_mapped_and_rolled_back_by_application(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("unique-race", "series-a", [])

    class CompetingInsertUnitOfWork(SqlAlchemyNotesUnitOfWork):
        def insert_personal_notes(
            self,
            chapter_id: str,
            user_id: str,
            revision: int,
            frame_notes: dict[str, object],
            resume_frame_id: str | None,
        ):
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            self._connection().execute(
                insert(personal_production_notes).values(
                    id="competing-insert",
                    chapter_id=chapter_id,
                    user_id=user_id,
                    revision=1,
                    frame_notes={},
                    resume_frame_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            return super().insert_personal_notes(
                chapter_id,
                user_id,
                revision,
                frame_notes,
                resume_frame_id,
            )

    factory = lambda: CompetingInsertUnitOfWork(
        notes_database.session_factory(), make_series_access_policy()
    )

    with pytest.raises(NotesConflict, match="个人记录已由另一请求创建或更新"):
        save_update(
            factory,
            "user-a",
            "unique-race",
            expected_revision=0,
            has_resume_patch=True,
            resume_frame_id=None,
        )

    assert read_note(notes_database, "unique-race", "user-a") is None


def test_two_physical_sessions_compete_for_r0_unique_key_without_overwriting_winner(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("r0-race", "series-a", chapter_frames("a"), ("a",))
    with notes_database.engine.connect() as first_connection, notes_database.engine.connect() as second_connection:
        first_driver_connection = first_connection.connection.driver_connection
        second_driver_connection = second_connection.connection.driver_connection
        assert first_driver_connection is not second_driver_connection
        connection_ids = (id(first_driver_connection), id(second_driver_connection))
        assert connection_ids[0] != connection_ids[1]

        first_session = Session(bind=first_connection, expire_on_commit=False)
        second_session = Session(bind=second_connection, expire_on_commit=False)
        first_uow = SqlAlchemyNotesUnitOfWork(first_session, make_series_access_policy())
        second_uow = SqlAlchemyNotesUnitOfWork(second_session, make_series_access_policy())
        try:
            first_seen = first_uow.load_personal_notes("r0-race", "user-a", lock=False)
            second_seen = second_uow.load_personal_notes("r0-race", "user-a", lock=False)
            assert first_seen is None
            assert second_seen is None
            first_session.commit()
            second_session.commit()

            winner_notes = {"a": {"status": "needs_revision", "note": "first writer"}}
            first_uow.insert_personal_notes(
                "r0-race", "user-a", 1, winner_notes, "a"
            )
            first_uow.commit()
            winner_row = read_note(notes_database, "r0-race", "user-a")
            assert winner_row is not None

            with pytest.raises(IntegrityError) as conflict:
                second_uow.insert_personal_notes(
                    "r0-race",
                    "user-a",
                    1,
                    {"a": {"status": "unmarked", "note": "loser must not replace this"}},
                    None,
                )
            assert second_uow.is_integrity_error(conflict.value)
            second_uow.rollback()

            assert read_note(notes_database, "r0-race", "user-a") == winner_row
            assert winner_row["revision"] == 1
            assert winner_row["frame_notes"] == winner_notes
            assert winner_row["resume_frame_id"] == "a"
        finally:
            first_session.close()
            second_session.close()


def test_two_physical_sessions_cas_race_keeps_winner_row_and_rejects_stale_revision(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("cas-race", "series-a", chapter_frames("a", "b"), ("a", "b"))
    factory = unit_of_work_factory(notes_database)
    save_update(
        factory,
        "user-a",
        "cas-race",
        expected_revision=0,
        frames=(FrameNoteChange("a", 1, "needs_revision", "initial"),),
        has_resume_patch=True,
        resume_frame_id="a",
    )
    save_update(
        factory,
        "user-a",
        "cas-race",
        expected_revision=1,
        frames=(FrameNoteChange("a", 1, None, "revision two"),),
    )
    before = read_note(notes_database, "cas-race", "user-a")
    assert before is not None and before["revision"] == 2

    with notes_database.engine.connect() as first_connection, notes_database.engine.connect() as second_connection:
        first_driver_connection = first_connection.connection.driver_connection
        second_driver_connection = second_connection.connection.driver_connection
        assert first_driver_connection is not second_driver_connection
        connection_ids = (id(first_driver_connection), id(second_driver_connection))
        assert connection_ids[0] != connection_ids[1]

        first_session = Session(bind=first_connection, expire_on_commit=False)
        second_session = Session(bind=second_connection, expire_on_commit=False)
        first_uow = SqlAlchemyNotesUnitOfWork(first_session, make_series_access_policy())
        second_uow = SqlAlchemyNotesUnitOfWork(second_session, make_series_access_policy())
        try:
            first_seen = first_uow.load_personal_notes("cas-race", "user-a", lock=False)
            second_seen = second_uow.load_personal_notes("cas-race", "user-a", lock=False)
            assert first_seen is not None and first_seen.revision == 2
            assert second_seen is not None and second_seen.revision == 2
            first_session.commit()
            second_session.commit()

            winner_notes = {
                "a": {
                    **first_seen.frame_notes["a"],
                    "note": "winner revision three",
                    "winner_extension": {"kept": True},
                }
            }
            assert first_uow.update_personal_notes(
                first_seen.id, 2, 3, winner_notes, "b"
            )
            first_uow.commit()
            winner_row = read_note(notes_database, "cas-race", "user-a")
            assert winner_row is not None

            loser_updated = second_uow.update_personal_notes(
                second_seen.id,
                2,
                3,
                {"a": {"status": "unmarked", "note": "stale loser"}},
                None,
            )
            assert loser_updated is False
            with pytest.raises(NotesConflict, match="个人记录已变化"):
                if not loser_updated:
                    raise NotesConflict("个人记录已变化，请刷新后重试")
            second_uow.rollback()

            assert read_note(notes_database, "cas-race", "user-a") == winner_row
            assert winner_row["id"] == before["id"]
            assert winner_row["created_at"] == before["created_at"]
            assert winner_row["revision"] == 3
            assert winner_row["frame_notes"] == winner_notes
            assert winner_row["resume_frame_id"] == "b"
        finally:
            first_session.close()
            second_session.close()


def test_real_sqlite_commit_failure_rolls_back_media_and_private_maintenance(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("commit-before-failure", "series-a", chapter_frames("a"), ("a",))
    factory = unit_of_work_factory(notes_database)
    get_personal_production_notes(factory, TrustedActor("user-a"), "commit-before-failure")
    save_update(
        factory,
        "user-a",
        "commit-before-failure",
        expected_revision=0,
        frames=(FrameNoteChange("a", 1, "approved", "original private note"),),
    )
    original_note = read_note(notes_database, "commit-before-failure", "user-a")
    assert original_note is not None
    with notes_database.engine.begin() as connection:
        connection.execute(
            update(rough_storyboard_assets)
            .where(rough_storyboard_assets.c.id == "a")
            .values(image_url="https://media.invalid/a-v2.png")
        )
    with notes_database.session_factory() as session:
        original_media_state = dict(
            session.execute(
                select(storyboard_media_states).where(
                    storyboard_media_states.c.chapter_id == "commit-before-failure",
                    storyboard_media_states.c.storyboard_asset_id == "a",
                )
            ).mappings().one()
        )

    created_units: list[SqlAlchemyNotesUnitOfWork] = []

    class FailBeforeCommitUnitOfWork(SqlAlchemyNotesUnitOfWork):
        def __init__(self, session, policy):
            super().__init__(session, policy)
            self.commit_attempts = 0
            self.rollback_calls = 0

        def commit(self) -> None:
            self.commit_attempts += 1
            raise RuntimeError("injected failure before SQLite commit")

        def rollback(self) -> None:
            self.rollback_calls += 1
            super().rollback()

    def failing_factory():
        uow = FailBeforeCommitUnitOfWork(
            notes_database.session_factory(), make_series_access_policy()
        )
        created_units.append(uow)
        return uow

    with pytest.raises(RuntimeError, match="before SQLite commit"):
        save_update(
            failing_factory,
            "user-a",
            "commit-before-failure",
            expected_revision=2,
            frames=(FrameNoteChange("a", 2, "needs_revision", "must roll back"),),
        )

    assert len(created_units) == 1
    assert created_units[0].commit_attempts == 1
    assert created_units[0].rollback_calls == 1
    assert read_note(notes_database, "commit-before-failure", "user-a") == original_note
    with notes_database.session_factory() as session:
        after_media_state = dict(
            session.execute(
                select(storyboard_media_states).where(
                    storyboard_media_states.c.chapter_id == "commit-before-failure",
                    storyboard_media_states.c.storyboard_asset_id == "a",
                )
            ).mappings().one()
        )
        assert after_media_state == original_media_state
        assert session.execute(
            select(rough_storyboard_assets.c.image_url).where(
                rough_storyboard_assets.c.id == "a"
            )
        ).scalar_one() == "https://media.invalid/a-v2.png"


def test_real_sqlite_commit_ack_failure_is_not_retried_and_explicit_get_confirms_truth(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("commit-ack-lost", "series-a", chapter_frames("a"), ("a",))
    get_personal_production_notes(
        unit_of_work_factory(notes_database), TrustedActor("user-a"), "commit-ack-lost"
    )
    created_units: list[SqlAlchemyNotesUnitOfWork] = []

    class CommitThenLoseAcknowledgementUnitOfWork(SqlAlchemyNotesUnitOfWork):
        def __init__(self, session, policy):
            super().__init__(session, policy)
            self.commit_attempts = 0
            self.rollback_calls = 0

        def commit(self) -> None:
            self.commit_attempts += 1
            super().commit()
            raise RuntimeError("injected lost acknowledgement after SQLite commit")

        def rollback(self) -> None:
            self.rollback_calls += 1
            super().rollback()

    def ambiguous_factory():
        uow = CommitThenLoseAcknowledgementUnitOfWork(
            notes_database.session_factory(), make_series_access_policy()
        )
        created_units.append(uow)
        return uow

    with pytest.raises(RuntimeError, match="after SQLite commit"):
        save_update(
            ambiguous_factory,
            "user-a",
            "commit-ack-lost",
            expected_revision=0,
            frames=(FrameNoteChange("a", 1, "needs_revision", "durable exactly once"),),
            has_resume_patch=True,
            resume_frame_id="a",
        )

    assert len(created_units) == 1
    assert created_units[0].commit_attempts == 1
    assert created_units[0].rollback_calls == 1
    durable_row = read_note(notes_database, "commit-ack-lost", "user-a")
    assert durable_row is not None
    assert durable_row["revision"] == 1
    assert durable_row["frame_notes"] == {
        "a": {
            "status": "needs_revision",
            "note": "durable exactly once",
            "approved_media_revision": None,
        }
    }
    assert durable_row["resume_frame_id"] == "a"
    with notes_database.session_factory() as session:
        assert session.execute(
            select(personal_production_notes.c.id).where(
                personal_production_notes.c.chapter_id == "commit-ack-lost",
                personal_production_notes.c.user_id == "user-a",
            )
        ).scalars().all() == [durable_row["id"]]

    confirmed = get_personal_production_notes(
        unit_of_work_factory(notes_database), TrustedActor("user-a"), "commit-ack-lost"
    )
    assert confirmed["revision"] == 1
    assert confirmed["frame_notes"] == durable_row["frame_notes"]
    assert confirmed["resume_frame_id"] == "a"
    assert read_note(notes_database, "commit-ack-lost", "user-a") == durable_row


def test_notes_adapter_compiles_the_legacy_lock_order_for_postgresql() -> None:
    statements: list[str] = []

    class Result:
        def mappings(self):
            return self

        def first(self):
            if statements[-1].startswith("SELECT chapters."):
                return {"id": "chapter-a", "series_id": "series-a", "content": "[]"}
            return None

        def all(self):
            return []

    class Connection:
        def execute(self, statement):
            statements.append(str(statement.compile(dialect=postgresql.dialect())))
            return Result()

    class Session:
        def connection(self):
            return Connection()

    uow = SqlAlchemyNotesUnitOfWork(Session(), None)
    assert uow.load_user("user-a", lock=True) is False
    assert uow.load_chapter("chapter-a", lock=True) is not None
    assert uow.list_source_assets("chapter-a", lock=True) == []
    assert uow.list_media_states("chapter-a", lock=True) == []
    assert uow.load_personal_notes("chapter-a", "user-a", lock=True) is None
    assert uow.list_personal_notes("chapter-a", lock=True) == []

    assert len(statements) == 6
    assert statements[0].endswith("FOR NO KEY UPDATE")
    assert all(statement.endswith("FOR UPDATE") for statement in statements[1:])
