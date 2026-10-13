"""Persistence ports for read-only chapter and character queries."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypeAlias

from .domain import ChapterRecord, CharacterRecord


class AdminChapterCharacterUnitOfWork(Protocol):
    def list_chapters(
        self, *, sort_field: str, sort_order: str, skip: int, limit: int
    ) -> tuple[Sequence[ChapterRecord], int]: ...

    def get_chapter(self, chapter_id: str) -> ChapterRecord | None: ...

    def list_characters(
        self, *, sort_field: str, sort_order: str, skip: int, limit: int
    ) -> tuple[Sequence[CharacterRecord], int]: ...

    def get_character(self, character_id: str) -> CharacterRecord | None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


AdminChapterCharacterUnitOfWorkFactory: TypeAlias = Callable[
    [], AdminChapterCharacterUnitOfWork
]
