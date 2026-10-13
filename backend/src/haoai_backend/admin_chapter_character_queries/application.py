"""Read-only administrator chapter and character use cases."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from .domain import (
    AdminChapterCharacterActor,
    ChapterRecord,
    CharacterRecord,
    Page,
    chapter_payload,
    character_detail_payload,
    character_list_payload,
)
from .errors import (
    AdminChapterCharacterForbidden,
    AdminChapterCharacterQueriesUnavailable,
    ChapterNotFound,
    CharacterNotFound,
)
from .ports import (
    AdminChapterCharacterUnitOfWork,
    AdminChapterCharacterUnitOfWorkFactory,
)


@contextmanager
def _open(
    factory: AdminChapterCharacterUnitOfWorkFactory | None,
) -> Iterator[AdminChapterCharacterUnitOfWork]:
    if factory is None:
        raise AdminChapterCharacterQueriesUnavailable()
    unit_of_work = factory()
    try:
        yield unit_of_work
    except BaseException:
        try:
            unit_of_work.rollback()
        except BaseException:
            pass
        try:
            unit_of_work.close()
        except BaseException:
            pass
        raise
    else:
        unit_of_work.close()


def _require_admin(actor: AdminChapterCharacterActor) -> None:
    if not actor.is_superuser:
        raise AdminChapterCharacterForbidden()


def list_chapters(
    factory: AdminChapterCharacterUnitOfWorkFactory | None,
    actor: AdminChapterCharacterActor,
    *,
    skip: int = 0,
    limit: int = 10,
    sort_field: str = "created_at",
    sort_order: str = "desc",
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        records, total = unit_of_work.list_chapters(
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        return {"data": [chapter_payload(item) for item in records], "total": total}


def get_chapter(
    factory: AdminChapterCharacterUnitOfWorkFactory | None,
    actor: AdminChapterCharacterActor,
    chapter_id: str,
) -> dict[str, object]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        record: ChapterRecord | None = unit_of_work.get_chapter(chapter_id)
        if record is None:
            raise ChapterNotFound()
        return chapter_payload(record)


def list_characters(
    factory: AdminChapterCharacterUnitOfWorkFactory | None,
    actor: AdminChapterCharacterActor,
    *,
    skip: int = 0,
    limit: int = 10,
    sort_field: str = "created_at",
    sort_order: str = "desc",
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        records, total = unit_of_work.list_characters(
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        return {
            "data": [character_list_payload(item) for item in records],
            "total": total,
        }


def get_character(
    factory: AdminChapterCharacterUnitOfWorkFactory | None,
    actor: AdminChapterCharacterActor,
    character_id: str,
) -> dict[str, object]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        record: CharacterRecord | None = unit_of_work.get_character(character_id)
        if record is None:
            raise CharacterNotFound()
        return character_detail_payload(record)
