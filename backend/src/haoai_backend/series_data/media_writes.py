"""Same-session adapter for the established personal-media coordinator."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from haoai_backend.personal_production.notes.persistence import SqlAlchemyNotesUnitOfWork
from haoai_backend.personal_production.notes.reconciliation import reconcile_chapter_media_state
from haoai_backend.shared.identity import TrustedActor

from .domain import ChapterRecord
from .errors import SeriesDataNotFound


UNSET_CONTENT = object()


def reconcile_source_media_state(
    session: Session,
    chapter: ChapterRecord,
    actor: TrustedActor,
    *,
    content_override: object = UNSET_CONTENT,
    new_assets: tuple[dict[str, Any], ...] = (),
    deleted_asset_ids: set[object] | None = None,
) -> None:
    """Reconcile through the caller's Session without owning its transaction."""
    notes_uow = SqlAlchemyNotesUnitOfWork(session, series_access_check=None)
    notes_chapter = notes_uow.load_chapter(chapter.id, lock=True)
    if notes_chapter is None:
        raise SeriesDataNotFound("章节不存在")
    kwargs: dict[str, Any] = {}
    if content_override is not UNSET_CONTENT:
        kwargs["chapter_content_override"] = content_override
    if new_assets:
        kwargs["new_assets"] = new_assets
    if deleted_asset_ids:
        kwargs["deleted_asset_ids"] = set(deleted_asset_ids)
    reconcile_chapter_media_state(notes_uow, notes_chapter, **kwargs)
