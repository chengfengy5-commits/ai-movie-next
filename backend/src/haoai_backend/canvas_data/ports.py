"""Application ports for chapter canvas persistence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from haoai_backend.shared.identity import TrustedActor

from .domain import CanvasDocumentRecord, ChapterRecord


class CanvasDataUnitOfWork(Protocol):
    def ensure_clean(self) -> None: ...

    def load_chapter(self, chapter_id: str) -> ChapterRecord | None: ...

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None: ...

    def refresh_chapter_lock(self, chapter_id: str, user_id: str) -> None: ...

    def load_canvas_document(self, chapter_id: str) -> CanvasDocumentRecord | None: ...

    def insert_canvas_document(
        self,
        *,
        series_id: str,
        chapter_id: str,
        user_id: str,
        document_json: str,
    ) -> str: ...

    def update_canvas_document(
        self,
        document_id: str,
        *,
        version: int,
        user_id: str,
        document_json: str,
        update_document_json: bool,
        update_updated_by: bool,
    ) -> None: ...

    def refresh_canvas_document(self, document_id: str) -> CanvasDocumentRecord | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], CanvasDataUnitOfWork]
Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
