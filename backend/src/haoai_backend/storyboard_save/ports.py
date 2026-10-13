"""Persistence boundary for storyboard asset filtering and saving."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from haoai_backend.shared.identity import TrustedActor

from .domain import AssetKind, AssetRecord, ChapterRecord


class StoryboardSaveStore(Protocol):
    def load_chapter(self, chapter_id: str) -> ChapterRecord | None: ...

    def require_access(self, actor: TrustedActor, series_id: str) -> None: ...

    def list_assets(self, kind: AssetKind, series_id: str) -> list[AssetRecord]: ...

    def asset_series_id(self, kind: AssetKind, asset_id: str) -> str | None: ...

    def find_asset(
        self,
        kind: AssetKind,
        asset_id: str,
        series_id: str,
    ) -> AssetRecord | None: ...

    def insert_asset(
        self,
        kind: AssetKind,
        asset_id: str,
        series_id: str,
        candidate: Mapping[str, Any],
    ) -> None: ...

    def update_aliases(
        self, kind: AssetKind, asset_id: str, series_id: str, aliases: str
    ) -> None: ...

    def update_chapter_content(
        self,
        chapter: ChapterRecord,
        actor: TrustedActor,
        content: str,
    ) -> None: ...

    def list_chapters(self, series_id: str) -> list[ChapterRecord]: ...

    def delete_assets(self, kind: AssetKind, series_id: str, asset_ids: Sequence[str]) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
