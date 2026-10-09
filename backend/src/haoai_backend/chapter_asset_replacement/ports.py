"""Application ports for chapter asset replacement."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from haoai_backend.shared.identity import TrustedActor

from .domain import ChapterAsset, ChapterRecord


class ChapterAssetReplacementUnitOfWork(Protocol):
    def ensure_clean(self) -> None: ...

    def load_chapter(self, chapter_id: str, *, lock: bool = False) -> ChapterRecord | None: ...

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None: ...

    def load_asset_for_replacement(
        self,
        asset_type: str,
        asset_id: str,
        series_id: str,
    ) -> ChapterAsset | None: ...

    def reconcile_chapter_media(
        self,
        chapter: ChapterRecord,
        actor: TrustedActor,
        content_override: str,
    ) -> None: ...

    def update_chapter_content(self, chapter_id: str, content: str) -> int: ...

    def list_chapters_for_series(self, series_id: str) -> Sequence[ChapterRecord]: ...

    def list_orphan_candidates(
        self,
        series_id: str,
        asset_type: str,
        asset_id: str,
    ) -> Sequence[str]: ...

    def delete_asset_by_id(self, asset_type: str, asset_id: str) -> int: ...

    def load_asset_by_id(self, asset_type: str, asset_id: str) -> ChapterAsset | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], ChapterAssetReplacementUnitOfWork]
SeriesAccessCheck = Callable[[object, TrustedActor, str], None]
