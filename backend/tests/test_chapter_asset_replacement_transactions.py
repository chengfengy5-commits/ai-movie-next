from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from haoai_backend.chapter_asset_replacement.application import replace_chapter_asset
from haoai_backend.chapter_asset_replacement.domain import ReplaceAssetCommand
from haoai_backend.chapter_asset_replacement.errors import ChapterAssetReplacementFailed
from haoai_backend.chapter_asset_replacement.persistence import (
    SqlAlchemyChapterAssetReplacementUnitOfWork,
)
from haoai_backend.shared.identity import TrustedActor
from chapter_asset_replacement_support import (
    CONSERVATION_TABLES,
    OWNER_METADATA,
    TrackedSession,
    chapters,
    characters,
    create_owner_database,
    personal_production_notes,
    storyboard_media_states,
)


COMMAND = ReplaceAssetCommand("old-character", "new-character", "character")
ACTOR = TrustedActor("user-a")


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "replacement-transactions.sqlite")
    try:
        yield database
    finally:
        database.close()


def allow_series(_session, actor: TrustedActor, series_id: str) -> None:
    if actor.user_id != "user-a" or series_id != "series-a":
        raise AssertionError("unexpected series access policy call")


def make_factory(database, unit_type=SqlAlchemyChapterAssetReplacementUnitOfWork):
    units: list[SqlAlchemyChapterAssetReplacementUnitOfWork] = []

    def factory():
        unit = unit_type(database.session_factory(), allow_series)
        units.append(unit)
        return unit

    return factory, units


def assert_only_source_write_and_character_cleanup(before, after) -> None:
    for table_name in CONSERVATION_TABLES:
        old_rows = before[table_name]
        new_rows = after[table_name]
        if table_name == "chapters":
            assert [row["id"] for row in old_rows] == [row["id"] for row in new_rows]
            old_by_id = {row["id"]: row for row in old_rows}
            new_by_id = {row["id"]: row for row in new_rows}
            for chapter_id in old_by_id:
                if chapter_id == "chapter-a":
                    changed = {
                        key
                        for key in old_by_id[chapter_id]
                        if old_by_id[chapter_id][key] != new_by_id[chapter_id][key]
                    }
                    assert changed == {"content", "updated_at"}
                else:
                    assert new_by_id[chapter_id] == old_by_id[chapter_id]
            continue
        if table_name == "characters":
            assert new_rows == [row for row in old_rows if row["id"] != "old-character"]
            continue
        assert new_rows == old_rows, table_name


def read_chapter_and_characters(database) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    with database.engine.connect() as connection:
        chapter_row = connection.execute(
            select(chapters).where(chapters.c.id == "chapter-a")
        ).mappings().first()
        character_rows = connection.execute(
            select(characters).order_by(characters.c.id)
        ).mappings().all()
    return (
        dict(chapter_row) if chapter_row is not None else None,
        [dict(row) for row in character_rows],
    )


def test_full_owner_database_workflow_preserves_every_unrelated_row(owner_database) -> None:
    before = owner_database.snapshot()
    factory, units = make_factory(owner_database)

    result = replace_chapter_asset(factory, ACTOR, "chapter-a", COMMAND)

    after = owner_database.snapshot()
    assert result == {"message": "已替换 1 处分镜，使用 新角色"}
    assert_only_source_write_and_character_cleanup(before, after)
    content = json.loads(
        next(row for row in after["chapters"] if row["id"] == "chapter-a")["content"]
    )
    assert content[0]["character"] == ["new-character", "new-character"]
    assert content[0]["scene"] == ["old-scene"]
    assert content[0]["prop"] == ["old-prop"]
    assert content[0]["storyboard"] == ["storyboard-a", "ignored-secondary-id"]
    assert content[0]["preview"] == "https://media.invalid/preview-a.png"
    assert content[0]["unknown_field"] == {"preserve": True, "label": "保留"}
    assert any(row["asset_id"] == "old-character" for row in after["chat_messages"])
    assert any(row["chapter_id"] == "chapter-a" for row in after["personal_production_notes"])
    assert any(row["chapter_id"] == "chapter-a" for row in after["rough_cut_drafts"])
    assert after["credit_logs"] == before["credit_logs"]
    assert after["task_submissions"] == before["task_submissions"]
    assert after["billing_units"] == before["billing_units"]
    assert after["execution_steps"] == before["execution_steps"]
    assert after["external_submissions"] == before["external_submissions"]
    assert after["result_evidence"] == before["result_evidence"]
    assert len(units) == 1
    assert units[0]._session.close_calls == 1


def test_phase_one_database_failure_rolls_back_media_and_private_note_catchup(
    owner_database,
) -> None:
    with owner_database.engine.begin() as connection:
        connection.execute(
            update(storyboard_media_states)
            .where(storyboard_media_states.c.id == "media-state-a")
            .values(asset_image_digest="stale-media-digest")
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER fail_replacement_chapter_update
            BEFORE UPDATE OF content ON chapters
            WHEN OLD.id = 'chapter-a'
            BEGIN
                SELECT RAISE(ABORT, 'injected source update failure');
            END
            """
        )
    before = owner_database.snapshot()

    class InspectBeforeRejectedUpdate(SqlAlchemyChapterAssetReplacementUnitOfWork):
        saw_uncommitted_media_catchup = False

        def update_chapter_content(self, chapter_id: str, content: str) -> int:
            media = self._session.execute(
                select(storyboard_media_states).where(
                    storyboard_media_states.c.id == "media-state-a"
                )
            ).mappings().one()
            notes = self._session.execute(
                select(personal_production_notes).where(
                    personal_production_notes.c.id == "notes-a"
                )
            ).mappings().one()
            self.saw_uncommitted_media_catchup = (
                media["media_revision"] == 2
                and notes["revision"] == 8
                and notes["frame_notes"]["storyboard-a"]["status"] == "unmarked"
                and notes["frame_notes"]["storyboard-a"]["needs_reconfirmation"] is True
            )
            return super().update_chapter_content(chapter_id, content)

    factory, units = make_factory(owner_database, InspectBeforeRejectedUpdate)
    with pytest.raises(IntegrityError, match="injected source update failure"):
        replace_chapter_asset(factory, ACTOR, "chapter-a", COMMAND)

    assert len(units) == 1
    assert units[0].saw_uncommitted_media_catchup
    assert units[0]._session.close_calls == 1
    assert owner_database.snapshot() == before


def test_real_sqlite_update_rowcount_zero_rolls_back_coordinator_writes_and_returns_500(
    owner_database,
) -> None:
    """A SQLite trigger controls rowcount; this is not a PG race or a new CAS gate."""
    with owner_database.engine.begin() as connection:
        connection.execute(
            update(storyboard_media_states)
            .where(storyboard_media_states.c.id == "media-state-a")
            .values(asset_image_digest="stale-media-digest")
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER ignore_replacement_chapter_update
            BEFORE UPDATE OF content ON chapters
            WHEN OLD.id = 'chapter-a'
            BEGIN
                SELECT RAISE(IGNORE);
            END
            """
        )
    before = owner_database.snapshot()

    class ObserveZeroUpdate(SqlAlchemyChapterAssetReplacementUnitOfWork):
        commit_attempts = 0
        rollback_calls = 0
        update_rowcount: int | None = None
        observed_coordinator_writes = False

        def update_chapter_content(self, chapter_id: str, content: str) -> int:
            media = self._session.execute(
                select(storyboard_media_states).where(
                    storyboard_media_states.c.id == "media-state-a"
                )
            ).mappings().one()
            notes = self._session.execute(
                select(personal_production_notes).where(
                    personal_production_notes.c.id == "notes-a"
                )
            ).mappings().one()
            self.observed_coordinator_writes = (
                media["media_revision"] == 2
                and media["asset_image_digest"] != "stale-media-digest"
                and notes["revision"] == 8
                and notes["frame_notes"]["storyboard-a"]["status"] == "unmarked"
                and notes["frame_notes"]["storyboard-a"]["needs_reconfirmation"] is True
            )
            self.update_rowcount = super().update_chapter_content(chapter_id, content)
            return self.update_rowcount

        def commit(self) -> None:
            self.commit_attempts += 1
            super().commit()

        def rollback(self) -> None:
            self.rollback_calls += 1
            super().rollback()

    factory, units = make_factory(owner_database, ObserveZeroUpdate)

    with pytest.raises(ChapterAssetReplacementFailed) as failure:
        replace_chapter_asset(factory, ACTOR, "chapter-a", COMMAND)

    unit = units[0]
    assert failure.value.status_code == 500
    assert unit.observed_coordinator_writes
    assert unit.update_rowcount == 0
    assert unit.commit_attempts == 0
    assert unit.rollback_calls == 1
    assert unit._session.close_calls == 1
    assert owner_database.snapshot() == before


def test_phase_two_delete_failure_rolls_back_cleanup_but_keeps_phase_one_durable(
    owner_database,
) -> None:
    class DeleteThenFail(SqlAlchemyChapterAssetReplacementUnitOfWork):
        commit_attempts = 0
        rollback_calls = 0

        def commit(self) -> None:
            self.commit_attempts += 1
            super().commit()

        def delete_asset_by_id(self, asset_type: str, asset_id: str) -> int:
            super().delete_asset_by_id(asset_type, asset_id)
            raise RuntimeError("phase-two failure after selected-row delete")

        def rollback(self) -> None:
            self.rollback_calls += 1
            super().rollback()

    factory, units = make_factory(owner_database, DeleteThenFail)

    with pytest.raises(RuntimeError, match="phase-two failure"):
        replace_chapter_asset(factory, ACTOR, "chapter-a", COMMAND)

    assert len(units) == 1
    unit = units[0]
    assert unit.commit_attempts == 1
    assert unit.rollback_calls == 1
    assert unit._session.close_calls == 1
    chapter_row, asset_rows = read_chapter_and_characters(owner_database)
    assert chapter_row is not None
    assert json.loads(chapter_row["content"])[0]["character"] == [
        "new-character",
        "new-character",
    ]
    assert {row["id"] for row in asset_rows} >= {"old-character", "new-character"}


@pytest.mark.parametrize(
    ("lost_commit_number", "old_asset_survives"),
    [(1, True), (2, False)],
)
def test_real_commit_acknowledgement_loss_is_not_replayed_and_fresh_read_confirms_truth(
    owner_database, lost_commit_number: int, old_asset_survives: bool
) -> None:
    class CommitThenLoseAcknowledgement(SqlAlchemyChapterAssetReplacementUnitOfWork):
        commit_attempts = 0
        rollback_calls = 0

        def commit(self) -> None:
            self.commit_attempts += 1
            super().commit()
            if self.commit_attempts == lost_commit_number:
                raise RuntimeError("commit acknowledgement lost after durable commit")

        def rollback(self) -> None:
            self.rollback_calls += 1
            super().rollback()

    factory, units = make_factory(owner_database, CommitThenLoseAcknowledgement)
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        replace_chapter_asset(factory, ACTOR, "chapter-a", COMMAND)

    assert len(units) == 1
    unit = units[0]
    assert unit.commit_attempts == lost_commit_number
    assert unit.rollback_calls == 1
    assert unit._session.close_calls == 1

    # A fresh, independent read is the only confirmation after an ambiguous response.
    chapter_row, asset_rows = read_chapter_and_characters(owner_database)
    assert chapter_row is not None
    assert json.loads(chapter_row["content"])[0]["character"] == [
        "new-character",
        "new-character",
    ]
    ids = {row["id"] for row in asset_rows}
    assert ("old-character" in ids) is old_asset_survives
    assert "new-character" in ids


def test_actual_zero_row_delete_after_candidate_selection_still_commits_phase_two(
    owner_database,
) -> None:
    class DeleteBetweenSelectAndApplicationDelete(SqlAlchemyChapterAssetReplacementUnitOfWork):
        commit_attempts = 0
        delete_rowcounts: list[int] = []

        def commit(self) -> None:
            self.commit_attempts += 1
            super().commit()

        def list_orphan_candidates(self, series_id, asset_type, asset_id):
            candidates = super().list_orphan_candidates(series_id, asset_type, asset_id)
            if candidates:
                self.delete_rowcounts.append(super().delete_asset_by_id(asset_type, candidates[0]))
            return candidates

        def delete_asset_by_id(self, asset_type, asset_id):
            rowcount = super().delete_asset_by_id(asset_type, asset_id)
            self.delete_rowcounts.append(rowcount)
            return rowcount

    factory, units = make_factory(owner_database, DeleteBetweenSelectAndApplicationDelete)
    before_ids = {row["id"] for row in owner_database.snapshot()["characters"]}

    result = replace_chapter_asset(factory, ACTOR, "chapter-a", COMMAND)

    assert result == {"message": "已替换 1 处分镜，使用 新角色"}
    assert units[0].commit_attempts == 2
    assert units[0].delete_rowcounts == [1, 0]
    assert units[0]._session.close_calls == 1
    after_ids = {row["id"] for row in owner_database.snapshot()["characters"]}
    assert before_ids - after_ids == {"old-character"}


def test_phase_two_uses_fresh_series_after_interleaved_external_migration_and_live_asset_name(
    owner_database,
) -> None:
    with owner_database.physical_connection_pair() as (request_connection, external_connection):
        request_dbapi = request_connection.connection.dbapi_connection
        external_dbapi = external_connection.connection.dbapi_connection
        assert request_dbapi is not external_dbapi

        class MoveChapterAfterPhaseOne(SqlAlchemyChapterAssetReplacementUnitOfWork):
            commit_attempts = 0

            def commit(self) -> None:
                self.commit_attempts += 1
                super().commit()
                if self.commit_attempts == 1:
                    with external_connection.begin():
                        external_connection.execute(
                            update(chapters)
                            .where(chapters.c.id == "chapter-a")
                            .values(series_id="series-b")
                        )
                        external_connection.execute(
                            update(chapters)
                            .where(chapters.c.id == "chapter-b")
                            .values(content="[]")
                        )
                        external_connection.execute(
                            update(characters)
                            .where(characters.c.id == "old-character")
                            .values(series_id="series-b")
                        )
                        external_connection.execute(
                            update(characters)
                            .where(characters.c.id == "new-character")
                            .values(name="并发改名")
                        )

        units: list[MoveChapterAfterPhaseOne] = []

        def factory():
            unit = MoveChapterAfterPhaseOne(
                TrackedSession(
                    bind=request_connection,
                    autoflush=False,
                    expire_on_commit=True,
                    future=True,
                ),
                allow_series,
            )
            units.append(unit)
            return unit

        result = replace_chapter_asset(factory, ACTOR, "chapter-a", COMMAND)

        assert result == {"message": "已替换 1 处分镜，使用 并发改名"}
        assert units[0].commit_attempts == 2
        assert units[0]._session.close_calls == 1

    chapter_row, asset_rows = read_chapter_and_characters(owner_database)
    assert chapter_row is not None and chapter_row["series_id"] == "series-b"
    assert json.loads(chapter_row["content"])[0]["character"] == [
        "new-character",
        "new-character",
    ]
    assert {row["id"] for row in asset_rows} == {
        "new-character",
        "other-character",
    }
    assert (
        next(row for row in asset_rows if row["id"] == "new-character")["series_id"]
        == "series-a"
    )


def test_missing_chapter_after_first_durable_commit_returns_500_without_asset_readback(
    owner_database,
) -> None:
    with owner_database.physical_connection_pair() as (request_connection, external_connection):
        class RemoveChapterAfterPhaseOne(SqlAlchemyChapterAssetReplacementUnitOfWork):
            commit_attempts = 0
            durable_content: str | None = None
            asset_pk_queries: list[tuple[str, str]] = []

            def commit(self) -> None:
                self.commit_attempts += 1
                super().commit()
                if self.commit_attempts != 1:
                    return
                with external_connection.begin():
                    self.durable_content = external_connection.execute(
                        select(chapters.c.content).where(chapters.c.id == "chapter-a")
                    ).scalar_one()
                    child_tables = [
                        table
                        for table in OWNER_METADATA.sorted_tables
                        if any(
                            foreign_key.column is chapters.c.id
                            for foreign_key in table.foreign_keys
                        )
                    ]
                    for table in child_tables:
                        external_connection.execute(
                            delete(table).where(table.c.chapter_id == "chapter-a")
                        )
                    external_connection.execute(
                        delete(chapters).where(chapters.c.id == "chapter-a")
                    )

            def load_asset_by_id(self, asset_type, asset_id):
                self.asset_pk_queries.append((asset_type, asset_id))
                return super().load_asset_by_id(asset_type, asset_id)

        units: list[RemoveChapterAfterPhaseOne] = []

        def factory():
            unit = RemoveChapterAfterPhaseOne(
                TrackedSession(
                    bind=request_connection,
                    autoflush=False,
                    expire_on_commit=True,
                    future=True,
                ),
                allow_series,
            )
            units.append(unit)
            return unit

        with pytest.raises(ChapterAssetReplacementFailed) as failure:
            replace_chapter_asset(factory, ACTOR, "chapter-a", COMMAND)

        assert failure.value.status_code == 500
        assert units[0].commit_attempts == 1
        assert units[0].durable_content is not None
        assert json.loads(units[0].durable_content)[0]["character"] == [
            "new-character",
            "new-character",
        ]
        assert units[0].asset_pk_queries == []
        assert units[0]._session.close_calls == 1

    with owner_database.engine.connect() as connection:
        assert connection.execute(
            select(chapters.c.id).where(chapters.c.id == "chapter-a")
        ).first() is None
        assert connection.execute(
            select(characters.c.id).where(characters.c.id == "old-character")
        ).scalar_one() == "old-character"
