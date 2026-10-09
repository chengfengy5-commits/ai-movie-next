"""Use cases for private personal rough-cut reads and saves."""

from __future__ import annotations

from .domain import (
    RoughCutUpdate,
    SavedDraft,
    parse_chapter_frames,
    project_snapshot,
    project_source_frames,
    validate_complete_update,
)
from .errors import Conflict, NotFound
from .ports import RoughCutUnitOfWork, TrustedActor, UnitOfWorkFactory


def _load_current_source(
    uow: RoughCutUnitOfWork,
    actor: TrustedActor,
    chapter_id: str,
    *,
    lock: bool,
) -> tuple[list[dict[str, object]], SavedDraft | None]:
    chapter = uow.load_chapter(chapter_id, lock=lock)
    if chapter is None:
        raise NotFound()

    uow.require_series_access(actor, chapter.series_id)
    content_frames = parse_chapter_frames(chapter.content)
    assets = uow.list_source_assets(chapter.id)
    source_frames = project_source_frames(content_frames, (asset.id for asset in assets))
    draft = uow.load_private_draft(chapter.id, actor.user_id)
    return source_frames, draft


def get_rough_cut(
    uow_factory: UnitOfWorkFactory,
    actor: TrustedActor,
    chapter_id: str,
) -> dict[str, object]:
    uow = uow_factory()
    try:
        source_frames, draft = _load_current_source(uow, actor, chapter_id, lock=False)
        return project_snapshot(chapter_id, source_frames, draft)
    except Exception:
        try:
            uow.rollback()
        except Exception:
            pass
        raise
    finally:
        uow.close()


def save_rough_cut(
    uow_factory: UnitOfWorkFactory,
    actor: TrustedActor,
    chapter_id: str,
    update: RoughCutUpdate,
) -> dict[str, object]:
    uow = uow_factory()
    try:
        source_frames, draft = _load_current_source(uow, actor, chapter_id, lock=True)
        current_revision = draft.revision if draft is not None else 0
        if update.expected_revision != current_revision:
            raise Conflict()

        persisted_frames = validate_complete_update(update, source_frames)
        next_revision = current_revision + 1

        try:
            if draft is None:
                uow.insert_private_draft(chapter_id, actor.user_id, next_revision, persisted_frames)
            elif not uow.update_private_draft(
                chapter_id,
                actor.user_id,
                update.expected_revision,
                next_revision,
                persisted_frames,
            ):
                raise Conflict()
            uow.commit()
        except Exception as exc:
            if uow.is_private_draft_unique_conflict(exc):
                raise Conflict() from exc
            raise

        return project_snapshot(
            chapter_id,
            source_frames,
            SavedDraft(revision=next_revision, frames=list(persisted_frames)),
        )
    except Exception:
        try:
            uow.rollback()
        except Exception:
            pass
        raise
    finally:
        uow.close()
