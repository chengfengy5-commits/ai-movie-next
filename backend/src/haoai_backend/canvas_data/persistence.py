"""Request-scoped SQLAlchemy Core unit of work for chapter canvases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.shared.identity import TrustedActor

from .domain import CanvasDocumentRecord, ChapterRecord
from .errors import CanvasDataForbidden, CanvasDataNotFound
from .tables import canvas_documents, chapter_locks, chapters, system_configs


class SqlAlchemyCanvasDataUnitOfWork:
    """Use the caller's business Session; the application owns its lifecycle."""

    def __init__(
        self,
        session: Session,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._session = session
        self._now = now or datetime.utcnow
        self._new_id = new_id or (lambda: str(uuid4()))
        self._access_reader = SqlAlchemySeriesAccessReader(session)

    def ensure_clean(self) -> None:
        if self._session.new or self._session.dirty or self._session.deleted:
            raise RuntimeError("canvas-data requires a clean request-scoped Session")

    def load_chapter(self, chapter_id: str) -> ChapterRecord | None:
        row = self._session.execute(
            select(chapters.c.id, chapters.c.series_id).where(chapters.c.id == chapter_id)
        ).mappings().first()
        if row is None:
            return None
        return ChapterRecord(id=row["id"], series_id=row["series_id"])

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        try:
            require_series_access(
                self._access_reader,
                ActorIdentity(actor.user_id),
                series_id,
            )
        except SeriesNotFound as exc:
            raise CanvasDataNotFound("剧集不存在") from exc
        except SeriesAccessDenied as exc:
            raise CanvasDataForbidden(exc.detail) from exc

    def refresh_chapter_lock(self, chapter_id: str, user_id: str) -> None:
        lock = self._session.execute(
            select(chapter_locks.c.id, chapter_locks.c.user_id).where(
                chapter_locks.c.chapter_id == chapter_id
            )
        ).mappings().first()
        if lock is None or lock["user_id"] != user_id:
            return

        idle_minutes = self._session.execute(
            select(system_configs.c.chapter_lock_idle_minutes).limit(1)
        ).scalar_one_or_none()
        idle_minutes = idle_minutes or 15
        now = self._now()
        result = self._session.execute(
            update(chapter_locks)
            .where(chapter_locks.c.id == lock["id"])
            .values(last_active_at=now, expires_at=now + timedelta(minutes=idle_minutes))
        )
        if result.rowcount != 1:
            raise RuntimeError("chapter lock refresh did not update exactly one row")

    def load_canvas_document(self, chapter_id: str) -> CanvasDocumentRecord | None:
        row = self._session.execute(
            select(canvas_documents).where(canvas_documents.c.chapter_id == chapter_id)
        ).mappings().first()
        return self._canvas_document(row) if row is not None else None

    def insert_canvas_document(
        self,
        *,
        series_id: str,
        chapter_id: str,
        user_id: str,
        document_json: str,
    ) -> str:
        document_id = self._new_id()
        now = self._now()
        self._session.execute(
            insert(canvas_documents).values(
                id=document_id,
                series_id=series_id,
                chapter_id=chapter_id,
                version=1,
                document_json=document_json,
                created_by=user_id,
                updated_by=user_id,
                created_at=now,
                updated_at=now,
            )
        )
        return document_id

    def update_canvas_document(
        self,
        document_id: str,
        *,
        version: int,
        user_id: str,
        document_json: str,
        update_document_json: bool,
        update_updated_by: bool,
    ) -> None:
        values: dict[str, Any] = {
            "version": version,
            "updated_at": self._now(),
        }
        if update_document_json:
            values["document_json"] = document_json
        if update_updated_by:
            values["updated_by"] = user_id

        result = self._session.execute(
            update(canvas_documents)
            .where(canvas_documents.c.id == document_id)
            .values(**values)
        )
        if result.rowcount != 1:
            raise RuntimeError("canvas document update did not match exactly one row")

    def refresh_canvas_document(self, document_id: str) -> CanvasDocumentRecord | None:
        row = self._session.execute(
            select(canvas_documents).where(canvas_documents.c.id == document_id)
        ).mappings().first()
        return self._canvas_document(row) if row is not None else None

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def _canvas_document(row: Any) -> CanvasDocumentRecord:
        return CanvasDocumentRecord(
            id=row["id"],
            series_id=row["series_id"],
            chapter_id=row["chapter_id"],
            version=row["version"],
            document_json=row["document_json"],
            created_by=row["created_by"],
            updated_by=row["updated_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
