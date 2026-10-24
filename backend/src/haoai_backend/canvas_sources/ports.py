"""Application-facing ports for guarded Canvas source commands."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from haoai_backend.asset_data.domain import AssetKind
from haoai_backend.shared.identity import TrustedActor

from .domain import SourceRows
from .schemas import SourceSnapshot


class CanvasSourceUnitOfWork(Protocol):
    now: Callable[[], datetime]

    def new_id(self) -> str: ...

    def load_source(
        self,
        chapter_id: str,
        actor: TrustedActor,
        *,
        expected_series_id: str | None = None,
        write: bool = False,
    ) -> SourceRows: ...

    def snapshot(self, rows: SourceRows, actor: TrustedActor) -> SourceSnapshot: ...

    def require_write_lock(self, rows: SourceRows, actor: TrustedActor) -> None: ...

    def refresh_write_lock(self, chapter_id: str, actor: TrustedActor) -> None: ...

    def compare_version(self, rows: SourceRows, expected_version: str) -> None: ...

    def update_chapter_content(
        self,
        rows: SourceRows,
        content: str | None,
        *,
        updated_at: datetime | None = None,
    ) -> None: ...

    def create_asset(self, kind: AssetKind, values: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def update_asset(
        self,
        kind: AssetKind,
        asset_id: str,
        values: Mapping[str, Any],
        assigned_fields: frozenset[str],
    ) -> Mapping[str, Any] | None: ...

    def create_storyboard_asset(self, values: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def update_storyboard_description(
        self,
        asset: Mapping[str, Any],
        *,
        index: int,
        frame: Mapping[str, Any],
    ) -> None: ...

    def shift_storyboard_indexes(
        self,
        chapter_id: str,
        *,
        asset_ids: Sequence[str],
        delta: int,
    ) -> None: ...

    def delete_storyboard_asset(self, asset: Mapping[str, Any]) -> None: ...

    def reconcile_media(
        self,
        chapter_id: str,
        *,
        content: object,
        new_assets: Sequence[Mapping[str, Any]] = (),
        deleted_asset_ids: set[str] | None = None,
        preserve_unreferenced_state_ids: Sequence[str] = (),
    ) -> None: ...

    def shift_chat_indexes(self, chapter_id: str, *, start_index: int, delta: int) -> None: ...

    def delete_chat_frame(self, chapter_id: str, index: int) -> None: ...

    def load_series_chapters(self, series_id: str) -> list[dict[str, Any]]: ...

    def load_creation_side_effects(
        self,
        chapter_id: str,
        *,
        lock: bool = False,
    ) -> dict[str, Any]: ...

    def delete_created_asset(
        self,
        kind: AssetKind,
        asset_id: str,
        *,
        series_id: str,
    ) -> None: ...

    def has_chat_asset_reference(self, kind: str, asset_id: str) -> bool: ...

    def delete_created_storyboard_asset(
        self,
        chapter_id: str,
        series_id: str,
        asset_id: str,
    ) -> None: ...

    def delete_created_asset_media_state(self, chapter_id: str, asset_id: str) -> None: ...

    def remove_media_states_not_in(self, chapter_id: str, original_ids: Sequence[str]) -> None: ...

    def fingerprint_side_effects(self, chapter_id: str) -> str: ...

    def list_chapter_messages(self, chapter_id: str) -> list[dict[str, Any]]: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


CanvasSourceUnitOfWorkFactory = Callable[[], CanvasSourceUnitOfWork]
