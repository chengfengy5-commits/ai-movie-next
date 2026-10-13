"""SQLAlchemy read-only unit of work for legacy chapters and characters."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .domain import ChapterRecord, CharacterRecord
from .ports import AdminChapterCharacterUnitOfWork
from .tables import ChapterRow, CharacterRow


class SqlAlchemyAdminChapterCharacterUnitOfWork(
    AdminChapterCharacterUnitOfWork
):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_chapters(
        self, *, sort_field: str, sort_order: str, skip: int, limit: int
    ) -> tuple[Sequence[ChapterRecord], int]:
        query = self._session.query(ChapterRow)
        total = query.count()
        sort_column = getattr(ChapterRow, sort_field, ChapterRow.id)
        if sort_order == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(asc(sort_column))
        rows = query.offset(skip).limit(limit).all()
        return tuple(_chapter_record(row) for row in rows), total

    def get_chapter(self, chapter_id: str) -> ChapterRecord | None:
        row = (
            self._session.query(ChapterRow)
            .filter(ChapterRow.id == chapter_id)
            .first()
        )
        return _chapter_record(row) if row is not None else None

    def list_characters(
        self, *, sort_field: str, sort_order: str, skip: int, limit: int
    ) -> tuple[Sequence[CharacterRecord], int]:
        query = self._session.query(CharacterRow)
        total = query.count()
        sort_column = getattr(CharacterRow, sort_field, CharacterRow.id)
        if sort_order == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(asc(sort_column))
        rows = query.offset(skip).limit(limit).all()
        return tuple(_character_record(row) for row in rows), total

    def get_character(self, character_id: str) -> CharacterRecord | None:
        row = (
            self._session.query(CharacterRow)
            .filter(CharacterRow.id == character_id)
            .first()
        )
        return _character_record(row) if row is not None else None

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def _chapter_record(row: ChapterRow) -> ChapterRecord:
    return ChapterRecord(
        id=row.id,
        series_id=row.series_id,
        title=row.title,
        order=row.order,
        created_at=row.created_at,
    )


def _character_record(row: CharacterRow) -> CharacterRecord:
    return CharacterRecord(
        id=row.id,
        series_id=row.series_id,
        name=row.name,
        gender=row.gender,
        age=row.age,
        role=row.role,
        appearance=row.appearance,
        description=row.description,
        image_url=row.image_url,
        created_at=row.created_at,
    )
