from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from haoai_backend.chapter_asset_replacement.domain import ASSET_TYPES
from haoai_backend.chapter_asset_replacement.persistence import (
    SqlAlchemyChapterAssetReplacementUnitOfWork,
)
from haoai_backend.chapter_asset_replacement.tables import (
    ASSET_DISPLAY_COLUMNS,
    ASSET_TABLES,
)
from haoai_backend.shared.identity import TrustedActor
from chapter_asset_replacement_support import (
    OWNER_METADATA,
    chapters,
    create_owner_database,
)


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "replacement-persistence.sqlite")
    try:
        yield database
    finally:
        database.close()


def test_adapter_projects_each_asset_kind_through_real_sqlite_row_mappings(owner_database) -> None:
    seen_policy_calls: list[tuple[str, str, str]] = []

    def policy(session, actor: TrustedActor, series_id: str) -> None:
        seen_policy_calls.append((actor.user_id, series_id, str(session.bind.url)))

    session = owner_database.session_factory()
    uow = SqlAlchemyChapterAssetReplacementUnitOfWork(session, policy)
    try:
        uow.ensure_clean()
        uow.require_series_access(TrustedActor("user-a"), "series-a")

        expected_names = {
            "character": "新角色",
            "scene": "新场景",
            "prop": "新道具",
        }
        expected_ids = {
            "character": ("old-character", "new-character"),
            "scene": ("old-scene", "new-scene"),
            "prop": ("old-prop", "new-prop"),
        }
        assert tuple(ASSET_TABLES) == ASSET_TYPES
        assert {kind: column.name for kind, column in ASSET_DISPLAY_COLUMNS.items()} == {
            "character": "name",
            "scene": "title",
            "prop": "name",
        }

        for asset_type, (old_id, new_id) in expected_ids.items():
            old_asset = uow.load_asset_for_replacement(asset_type, old_id, "series-a")
            new_asset = uow.load_asset_for_replacement(asset_type, new_id, "series-a")
            assert old_asset is not None and new_asset is not None
            assert old_asset.display_name.startswith("旧")
            assert new_asset.display_name == expected_names[asset_type]
            assert uow.load_asset_for_replacement(asset_type, new_id, "series-b") is None

            by_primary_key = uow.load_asset_by_id(asset_type, new_id)
            assert by_primary_key == new_asset

        assert seen_policy_calls == [
            ("user-a", "series-a", str(owner_database.engine.url))
        ]
    finally:
        uow.rollback()
        uow.close()


def test_chapter_read_and_content_update_use_only_the_chapter_primary_key(owner_database) -> None:
    session = owner_database.session_factory()
    uow = SqlAlchemyChapterAssetReplacementUnitOfWork(session, lambda *_args: None)
    try:
        before = uow.load_chapter("chapter-a")
        assert before is not None
        assert before.series_id == "series-a"
        assert before.title == "待替换章节"
        assert before.order == 1
        assert isinstance(before.updated_at, datetime)

        changed_content = '[{"character": ["new-character"]}]'
        assert uow.update_chapter_content("chapter-a", changed_content) == 1
        assert uow.update_chapter_content("missing-chapter", "[]") == 0
        uow.commit()

        after = uow.load_chapter("chapter-a")
        assert after is not None
        assert after.content == changed_content
        assert after.series_id == before.series_id
        assert after.title == before.title
        assert after.order == before.order
        assert after.updated_at != before.updated_at
    finally:
        uow.rollback()
        uow.close()


def test_orphan_candidate_lookup_is_series_scoped_and_delete_is_primary_key_only(
    owner_database,
) -> None:
    session = owner_database.session_factory()
    uow = SqlAlchemyChapterAssetReplacementUnitOfWork(session, lambda *_args: None)
    try:
        assert uow.list_orphan_candidates("series-a", "character", "old-character") == [
            "old-character"
        ]
        assert uow.list_orphan_candidates("series-b", "character", "old-character") == []
        assert uow.delete_asset_by_id("character", "old-character") == 1
        assert uow.delete_asset_by_id("character", "old-character") == 0
        uow.commit()

        assert uow.load_asset_by_id("character", "old-character") is None
        assert uow.load_asset_by_id("character", "new-character") is not None
    finally:
        uow.rollback()
        uow.close()


def test_adapter_keeps_full_owner_metadata_separate_from_projection_tables(
    owner_database,
) -> None:
    assert len(OWNER_METADATA.tables) >= 24
    assert "chat_messages" in OWNER_METADATA.tables
    assert "personal_production_notes" in OWNER_METADATA.tables
    assert "billing_units" in OWNER_METADATA.tables
    assert "result_evidence" in OWNER_METADATA.tables
    assert chapters in OWNER_METADATA.tables.values()
    assert "chapters" in ASSET_TABLES["character"].metadata.tables
    assert ASSET_TABLES["character"].metadata is not OWNER_METADATA

    with owner_database.engine.connect() as connection:
        assert connection.execute(select(chapters.c.id)).scalars().all() == [
            "chapter-a",
            "chapter-b",
            "neighbor-a",
        ]
