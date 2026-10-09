"""Small application ports and immutable values for rough-cut use cases."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from .domain import ChapterRecord, SavedDraft, SourceAsset
from haoai_backend.shared.identity import TrustedActor


class RoughCutUnitOfWork(Protocol):
    def load_chapter(self, chapter_id: str, *, lock: bool) -> ChapterRecord | None:
        ...

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        ...

    def list_source_assets(self, chapter_id: str) -> Sequence[SourceAsset]:
        ...

    def load_private_draft(self, chapter_id: str, user_id: str) -> SavedDraft | None:
        ...

    def insert_private_draft(
        self,
        chapter_id: str,
        user_id: str,
        revision: int,
        frames: tuple[dict[str, object], ...],
    ) -> None:
        ...

    def update_private_draft(
        self,
        chapter_id: str,
        user_id: str,
        expected_revision: int,
        next_revision: int,
        frames: tuple[dict[str, object], ...],
    ) -> bool:
        ...

    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...

    def close(self) -> None:
        ...

    def is_private_draft_unique_conflict(self, error: BaseException) -> bool:
        ...


UnitOfWorkFactory = Callable[[], RoughCutUnitOfWork]
