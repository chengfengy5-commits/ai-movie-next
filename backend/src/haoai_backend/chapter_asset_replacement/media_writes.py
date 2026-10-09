"""Adapter to the shared personal-media coordinator."""

from __future__ import annotations

from sqlalchemy.orm import Session

from haoai_backend.personal_production.notes.domain import ChapterRecord as NotesChapterRecord
from haoai_backend.personal_production.notes.persistence import SqlAlchemyNotesUnitOfWork
from haoai_backend.personal_production.notes.reconciliation import reconcile_chapter_media_state
from haoai_backend.shared.identity import TrustedActor

from .domain import ChapterRecord
from .errors import ChapterAssetReplacementFailed


def reconcile_replacement_media(
    session: Session,
    chapter: ChapterRecord,
    actor: TrustedActor,
    content_override: str,
) -> None:
    """Reconcile media and private notes in the same caller-owned Session."""
    del actor  # The existing coordinator only uses the locked chapter and UoW.

    notes_uow = SqlAlchemyNotesUnitOfWork(session, series_access_check=None)
    locked_chapter = notes_uow.load_chapter(chapter.id, lock=True)
    if locked_chapter is None:
        raise ChapterAssetReplacementFailed()

    notes_record = NotesChapterRecord(
        id=locked_chapter.id,
        series_id=locked_chapter.series_id,
        content=locked_chapter.content,
    )
    reconcile_chapter_media_state(
        notes_uow,
        notes_record,
        chapter_content_override=content_override,
    )
