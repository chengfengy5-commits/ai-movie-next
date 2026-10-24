from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.orm import Session

from .domain import ChapterMediaSnapshot, MediaStateRecord, PersonalNotesRecord
from .errors import NotesNotFound
from .persistence import SqlAlchemyNotesUnitOfWork
from .reconciliation import reconcile_chapter_media_state

_UNSET = object()


@dataclass(frozen=True, slots=True)
class MediaReconciliationOutcome:
    """Facts staged by media reconciliation in the caller's transaction."""

    snapshot: ChapterMediaSnapshot
    media_revision_before: int | None
    media_revision_after: int | None
    notes_revision_before: int
    notes_revision_after: int


def stage_media_reconciliation(
    shared_session: Session,
    chapter_id: str,
    *,
    chapter_content_override: object = _UNSET,
    chapter_content_before: object = _UNSET,
    new_assets: Sequence[Mapping[str, object]] = (),
    asset_image_overrides: Mapping[str, object] | None = None,
    deleted_asset_ids: set[str] | None = None,
    preserve_unreferenced_state_ids: set[str] | None = None,
    focus_storyboard_asset_id: str | None = None,
) -> MediaReconciliationOutcome:
    """Stage media-state and approval changes using the caller's Session.

    This facade deliberately leaves commit, rollback, and Session lifetime to
    the source writer that owns the broader generation application transaction.
    """

    if not isinstance(shared_session, Session):
        raise TypeError("shared_session must be the caller-owned SQLAlchemy Session")

    uow = SqlAlchemyNotesUnitOfWork(shared_session, series_access_check=None)
    chapter = uow.load_chapter(chapter_id, lock=True)
    if chapter is None:
        raise NotesNotFound()
    if chapter_content_before is not _UNSET:
        chapter = replace(chapter, content=chapter_content_before)

    before_states = uow.list_media_states(chapter_id, lock=True)
    before_notes = uow.list_personal_notes(chapter_id, lock=True)
    before_revision = _max_notes_revision(before_notes)
    previous_state = next(
        (state for state in before_states if state.storyboard_asset_id == focus_storyboard_asset_id),
        None,
    )

    kwargs: dict[str, Any] = {
        "new_assets": tuple(dict(asset) for asset in new_assets),
        "asset_image_overrides": dict(asset_image_overrides or {}),
        "deleted_asset_ids": set(deleted_asset_ids or ()),
        "preserve_unreferenced_state_ids": set(preserve_unreferenced_state_ids or ()),
    }
    if chapter_content_override is not _UNSET:
        kwargs["chapter_content_override"] = chapter_content_override

    snapshot = reconcile_chapter_media_state(uow, chapter, **kwargs)

    after_states = uow.list_media_states(chapter_id, lock=True)
    after_notes = uow.list_personal_notes(chapter_id, lock=True)
    next_state = next(
        (state for state in after_states if state.storyboard_asset_id == focus_storyboard_asset_id),
        None,
    )
    return MediaReconciliationOutcome(
        snapshot=snapshot,
        media_revision_before=_revision(previous_state),
        media_revision_after=_revision(next_state),
        notes_revision_before=before_revision,
        notes_revision_after=_max_notes_revision(after_notes),
    )


def _revision(state: MediaStateRecord | None) -> int | None:
    return None if state is None else state.media_revision


def _max_notes_revision(records: Sequence[PersonalNotesRecord]) -> int:
    return max((record.revision for record in records), default=0)


__all__ = ("MediaReconciliationOutcome", "stage_media_reconciliation")
