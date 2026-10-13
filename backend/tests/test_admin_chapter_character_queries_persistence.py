from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.admin_chapter_character_queries.persistence import (
    SqlAlchemyAdminChapterCharacterUnitOfWork,
)
from haoai_backend.admin_chapter_character_queries.tables import (
    Base,
    ChapterRow,
    CharacterRow,
    ChatMessageRow,
    SeriesRow,
    metadata,
)


def test_private_mapping_preserves_metadata_registry_columns_and_relationships() -> None:
    assert Base.metadata is metadata
    assert Base.registry is not None
    assert SeriesRow.__tablename__ == "series"
    assert ChapterRow.__tablename__ == "chapters"
    assert CharacterRow.__tablename__ == "characters"
    assert ChatMessageRow.__tablename__ == "chat_messages"
    assert set(ChapterRow.__table__.columns.keys()) == {
        "id", "series_id", "title", "content", "order", "created_at", "updated_at"
    }
    assert set(CharacterRow.__table__.columns.keys()) == {
        "id", "series_id", "name", "gender", "age", "role", "appearance",
        "description", "image_url", "audio_url", "voice_ref", "created_at",
        "updated_at", "aliases", "canonical_key",
    }
    assert ChapterRow.series.property.back_populates == "chapters"
    assert ChapterRow.chat_messages.property.back_populates == "chapter"
    assert CharacterRow.series.property.back_populates == "characters"
    assert ChapterRow.series.property.mapper.class_ is SeriesRow
    assert ChapterRow.chat_messages.property.mapper.class_ is ChatMessageRow


def test_list_queries_count_before_paging_keep_cross_series_rows_and_exact_dtos() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    now = datetime(2026, 10, 13, 12, 0)
    with Session(engine) as session:
        session.execute(insert(SeriesRow.__table__), [{"id": "s1"}, {"id": "s2"}])
        session.execute(
            insert(ChapterRow.__table__),
            [
                {
                    "id": f"c{index:02}",
                    "series_id": "s1" if index % 2 else "s2",
                    "title": f"Chapter {index}",
                    "content": "not exposed",
                    "order": index,
                    "created_at": now - timedelta(minutes=index),
                    "updated_at": now,
                }
                for index in range(11)
            ],
        )
        session.execute(
            insert(CharacterRow.__table__),
            [
                {
                    "id": "p1",
                    "series_id": "s2",
                    "name": "Hero",
                    "gender": None,
                    "age": None,
                    "role": None,
                    "appearance": "full appearance",
                    "description": "full description",
                    "image_url": "image",
                    "audio_url": "audio",
                    "voice_ref": "voice",
                    "aliases": "[]",
                    "canonical_key": "hero",
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        )
        session.commit()

    with Session(engine) as session:
        uow = SqlAlchemyAdminChapterCharacterUnitOfWork(session)
        chapters, total = uow.list_chapters(
            sort_field="created_at", sort_order="desc", skip=0, limit=10
        )
        assert total == 11
        assert len(chapters) == 10
        assert chapters[0].id == "c00"
        assert {item.series_id for item in chapters} == {"s1", "s2"}
        assert chapters[0].created_at == now
        assert set(chapters[0].__dataclass_fields__) == {
            "id", "series_id", "title", "order", "created_at"
        }

        characters, character_total = uow.list_characters(
            sort_field="created_at", sort_order="desc", skip=0, limit=10
        )
        assert character_total == 1
        assert characters[0].appearance == "full appearance"
        assert characters[0].description == "full description"
        assert characters[0].image_url == "image"
        assert set(characters[0].__dataclass_fields__) == {
            "id", "series_id", "name", "gender", "age", "role",
            "appearance", "description", "image_url", "created_at",
        }

        by_id, _ = uow.list_chapters(
            sort_field="not_a_column", sort_order="asc", skip=0, limit=20
        )
        assert [row.id for row in by_id] == sorted(row.id for row in by_id)
        reverse_id, _ = uow.list_chapters(
            sort_field="id", sort_order="DESC", skip=0, limit=20
        )
        assert [row.id for row in reverse_id] == sorted(row.id for row in reverse_id)


def test_raw_sort_attribute_errors_and_missing_detail_remain_visible() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with Session(engine) as session:
        uow = SqlAlchemyAdminChapterCharacterUnitOfWork(session)
        for sort_field in ("metadata", "registry", "series"):
            with pytest.raises(SQLAlchemyError):
                uow.list_chapters(
                    sort_field=sort_field,
                    sort_order="desc",
                    skip=0,
                    limit=10,
                )
        assert uow.get_chapter("missing") is None
        assert uow.get_character("missing") is None


def test_detail_reads_query_even_when_identity_map_contains_the_row() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    now = datetime(2026, 10, 13, 12, 0)
    with Session(engine) as seed:
        seed.execute(insert(SeriesRow.__table__), {"id": "s1"})
        seed.execute(
            insert(ChapterRow.__table__),
            {
                "id": "c1",
                "series_id": "s1",
                "title": "Chapter",
                "content": None,
                "order": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        seed.execute(
            insert(CharacterRow.__table__),
            {
                "id": "p1",
                "series_id": "s1",
                "name": "Hero",
                "gender": None,
                "age": None,
                "role": None,
                "appearance": None,
                "description": None,
                "image_url": None,
                "audio_url": None,
                "voice_ref": None,
                "aliases": None,
                "canonical_key": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        seed.commit()

    with Session(engine) as session:
        chapter = session.get(ChapterRow, "c1")
        character = session.get(CharacterRow, "p1")
        assert chapter is not None and character is not None
        assert chapter in session.identity_map.values()
        assert character in session.identity_map.values()
        statements: list[str] = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            uow = SqlAlchemyAdminChapterCharacterUnitOfWork(session)
            assert uow.get_chapter("c1").id == "c1"
            assert uow.get_character("p1").id == "p1"
        finally:
            event.remove(engine, "before_cursor_execute", capture)

    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 2
