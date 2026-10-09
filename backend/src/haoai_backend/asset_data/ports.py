"""Framework-free ports for asset-data use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from haoai_backend.series_access.ports import SeriesAccessReader
from haoai_backend.shared.identity import TrustedActor

from .domain import AssetKind


class AssetDataUnitOfWork(SeriesAccessReader, Protocol):
    def ensure_clean(self) -> None: ...

    def current_time(self) -> datetime: ...

    def new_id(self) -> str: ...

    def load_chapter(
        self,
        chapter_id: str,
        *,
        lock: bool = False,
    ) -> Mapping[str, Any] | None: ...

    def list_chapters(self, series_id: str) -> Sequence[Mapping[str, Any]]: ...

    def lock_chapters(self, chapter_ids: Sequence[str]) -> Sequence[Mapping[str, Any]]: ...

    def list_assets(
        self,
        kind: AssetKind,
        series_id: str,
        *,
        chapter_id: str | None = None,
    ) -> Sequence[Mapping[str, Any]]: ...

    def load_asset(
        self,
        kind: str,
        asset_id: str,
        *,
        lock: bool = False,
    ) -> Mapping[str, Any] | None: ...

    def create_asset(self, kind: str, values: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def update_asset(
        self,
        kind: str,
        asset_id: str,
        values: Mapping[str, Any],
        *,
        assigned_fields: frozenset[str],
        expected_storyboard_facts: tuple[str, object] | None = None,
    ) -> Mapping[str, Any] | None: ...

    def delete_asset(self, kind: str, asset_id: str) -> bool: ...

    def update_chapter_content(
        self,
        chapter_id: str,
        expected_content: object,
        content: str,
        *,
        updated_at: datetime,
    ) -> bool: ...

    def reconcile_chapter_media(
        self,
        chapter: Mapping[str, Any],
        actor: TrustedActor,
        *,
        content_override: object | None = None,
        new_assets: Sequence[Mapping[str, Any]] = (),
        asset_image_overrides: Mapping[str, object] | None = None,
        deleted_asset_ids: set[str] | None = None,
    ) -> None: ...

    def refresh_chapter_lock(self, chapter_id: str, user_id: str) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...

    def is_integrity_error(self, error: BaseException) -> bool: ...


AssetDataUnitOfWorkFactory = Callable[[], AssetDataUnitOfWork]
