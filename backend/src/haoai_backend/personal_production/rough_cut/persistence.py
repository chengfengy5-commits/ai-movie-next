"""SQLAlchemy Core adapter for the rough-cut unit-of-work port."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .domain import ChapterRecord, SavedDraft, SourceAsset
from .errors import Unavailable
from .ports import TrustedActor
from .tables import chapters, rough_cut_drafts, storyboard_assets


SeriesAccessCheck = Callable[[Session, TrustedActor, str], None]


class SqlAlchemyRoughCutUnitOfWork:
    """One short-lived Session per request, with app-owned commit semantics."""

    def __init__(
        self,
        session: Session,
        series_access_check: SeriesAccessCheck | None,
    ) -> None:
        self._session = session
        self._series_access_check = series_access_check

    def load_chapter(self, chapter_id: str, *, lock: bool) -> ChapterRecord | None:
        statement = select(chapters.c.id, chapters.c.series_id, chapters.c.content).where(
            chapters.c.id == chapter_id
        )
        if lock:
            statement = statement.with_for_update()
        row = self._session.execute(statement).mappings().first()
        if row is None:
            return None
        return ChapterRecord(id=row["id"], series_id=row["series_id"], content=row["content"])

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        if self._series_access_check is None:
            raise Unavailable()
        self._series_access_check(self._session, actor, series_id)

    def list_source_assets(self, chapter_id: str) -> list[SourceAsset]:
        rows = self._session.execute(
            select(storyboard_assets.c.id).where(storyboard_assets.c.chapter_id == chapter_id)
        ).mappings()
        return [SourceAsset(id=row["id"]) for row in rows]

    def load_private_draft(self, chapter_id: str, user_id: str) -> SavedDraft | None:
        row = self._session.execute(
            select(rough_cut_drafts.c.revision, rough_cut_drafts.c.frames).where(
                rough_cut_drafts.c.chapter_id == chapter_id,
                rough_cut_drafts.c.user_id == user_id,
            )
        ).mappings().first()
        if row is None:
            return None
        return SavedDraft(revision=row["revision"], frames=row["frames"])

    def insert_private_draft(
        self,
        chapter_id: str,
        user_id: str,
        revision: int,
        frames: tuple[dict[str, object], ...],
    ) -> None:
        self._session.execute(
            insert(rough_cut_drafts).values(
                id=str(uuid4()),
                chapter_id=chapter_id,
                user_id=user_id,
                revision=revision,
                frames=list(frames),
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )

    def update_private_draft(
        self,
        chapter_id: str,
        user_id: str,
        expected_revision: int,
        next_revision: int,
        frames: tuple[dict[str, object], ...],
    ) -> bool:
        result = self._session.execute(
            update(rough_cut_drafts)
            .where(
                rough_cut_drafts.c.chapter_id == chapter_id,
                rough_cut_drafts.c.user_id == user_id,
                rough_cut_drafts.c.revision == expected_revision,
            )
            .values(
                revision=next_revision,
                frames=list(frames),
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        return result.rowcount == 1

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()

    def is_private_draft_unique_conflict(self, error: BaseException) -> bool:
        if not isinstance(error, IntegrityError):
            return False
        original: Any = error.orig
        constraint_name = getattr(getattr(original, "diag", None), "constraint_name", None)
        if constraint_name == "uq_rough_cut_drafts_chapter_user":
            return True
        message = str(original)
        return (
            "UNIQUE constraint failed: rough_cut_drafts.chapter_id, rough_cut_drafts.user_id"
            in message
        )
