"""Transactional use cases for personal production notes."""

from __future__ import annotations

from typing import Mapping

from haoai_backend.shared.identity import TrustedActor

from .domain import (
    ChapterMediaSnapshot,
    PersonalNotesRecord,
    PersonalNotesUpdate,
    PersonalProductionSnapshot,
    apply_notes_update,
    revoke_empty_media_approvals,
    snapshot_response,
)
from .errors import (
    ChapterMediaStateError,
    InvalidNotesUpdate,
    NotesConflict,
    NotesNotFound,
    NotesUnauthorized,
    NotesUnavailable,
)
from .ports import NotesUnitOfWork, NotesUnitOfWorkFactory
from .reconciliation import reconcile_chapter_media_state


INVALID_FRAME_DETAIL = "镜头身份缺失、重复或已失效，不能保存该镜头"
STALE_MEDIA_DETAIL = "镜头素材已变化，请刷新后重新确认"
EMPTY_MEDIA_APPROVAL_DETAIL = "原图和预览均为空，不能将该镜头标记为已认可"
INVALID_RESUME_DETAIL = "续作目标缺少唯一有效的本章分镜身份"


def get_personal_production_notes(
    uow_factory: NotesUnitOfWorkFactory,
    actor: TrustedActor,
    chapter_id: str,
) -> dict[str, object]:
    uow = uow_factory()
    try:
        _ensure_clean(uow)
        chapter = uow.load_chapter(chapter_id, lock=True)
        if chapter is None:
            raise NotesNotFound()
        uow.require_series_access(actor, chapter.series_id)
        media = _reconcile_or_unavailable(uow, chapter)
        record = uow.load_personal_notes(chapter_id, actor.user_id, lock=True)
        revision = record.revision if record is not None else 0
        notes = record.frame_notes if record is not None else {}
        resume_frame_id = record.resume_frame_id if record is not None else None
        revoked = False
        if record is not None:
            repaired, revoked = revoke_empty_media_approvals(notes, media)
            if revoked:
                if not uow.update_personal_notes(
                    record.id,
                    record.revision,
                    record.revision + 1,
                    repaired,
                    record.resume_frame_id,
                ):
                    raise NotesUnavailable()
                revision += 1
                notes = repaired
        uow.commit()
        return snapshot_response(_snapshot(media, revision, notes, resume_frame_id, revoked))
    except Exception:
        _rollback_safely(uow)
        raise
    finally:
        uow.close()


def save_personal_production_notes(
    uow_factory: NotesUnitOfWorkFactory,
    actor: TrustedActor,
    chapter_id: str,
    update: PersonalNotesUpdate,
) -> dict[str, object]:
    uow = uow_factory()
    try:
        _ensure_clean(uow)
        if not uow.load_user(actor.user_id, lock=True):
            raise NotesUnauthorized()
        chapter = uow.load_chapter(chapter_id, lock=True)
        if chapter is None:
            raise NotesNotFound()
        uow.require_series_access(actor, chapter.series_id)
        media = _reconcile_or_unavailable(uow, chapter)
        record = uow.load_personal_notes(chapter_id, actor.user_id, lock=True)
        revision = record.revision if record is not None else 0
        frame_notes = record.frame_notes if record is not None else {}
        resume_frame_id = record.resume_frame_id if record is not None else None

        if record is not None:
            repaired, revoked = revoke_empty_media_approvals(frame_notes, media)
            if revoked:
                if not uow.update_personal_notes(
                    record.id,
                    record.revision,
                    record.revision + 1,
                    repaired,
                    record.resume_frame_id,
                ):
                    raise NotesUnavailable()
                uow.commit()
                raise NotesConflict("历史认可已撤销，请刷新后重试")

        if update.expected_revision != revision:
            raise NotesConflict("个人记录已变化，请刷新后重试")

        _validate_patch(update, media)
        snapshot = _snapshot(media, revision, frame_notes, resume_frame_id)
        next_notes, next_resume = apply_notes_update(snapshot, update)
        next_revision = revision + 1
        try:
            if record is None:
                uow.insert_personal_notes(
                    chapter_id,
                    actor.user_id,
                    next_revision,
                    next_notes,
                    next_resume,
                )
            elif not uow.update_personal_notes(
                record.id,
                revision,
                next_revision,
                next_notes,
                next_resume,
            ):
                raise NotesConflict("个人记录已变化，请刷新后重试")
            uow.commit()
        except Exception as exc:
            if uow.is_integrity_error(exc):
                raise NotesConflict("个人记录已由另一请求创建或更新，请刷新后重试") from exc
            raise

        return snapshot_response(_snapshot(media, next_revision, next_notes, next_resume))
    except Exception:
        _rollback_safely(uow)
        raise
    finally:
        uow.close()


def _validate_patch(update: PersonalNotesUpdate, media: ChapterMediaSnapshot) -> None:
    by_id = {
        frame.storyboard_asset_id: frame
        for frame in media.frames
        if frame.storyboard_asset_id is not None
    }
    for change in update.frames:
        frame = by_id.get(change.storyboard_asset_id)
        if frame is None or not frame.source_valid or frame.media_revision is None:
            raise InvalidNotesUpdate(INVALID_FRAME_DETAIL)
        if change.expected_media_revision != frame.media_revision:
            raise NotesConflict(STALE_MEDIA_DETAIL)
        if (
            change.status == "approved"
            and frame.asset_image_digest is None
            and frame.preview_digest is None
        ):
            raise InvalidNotesUpdate(EMPTY_MEDIA_APPROVAL_DETAIL)

    if update.has_resume_patch and update.resume_frame_id is not None:
        target = by_id.get(update.resume_frame_id)
        if target is None or not target.source_valid:
            raise InvalidNotesUpdate(INVALID_RESUME_DETAIL)


def _ensure_clean(uow: NotesUnitOfWork) -> None:
    try:
        uow.ensure_clean()
    except ChapterMediaStateError as exc:
        raise NotesUnavailable() from exc


def _reconcile_or_unavailable(uow: NotesUnitOfWork, chapter) -> ChapterMediaSnapshot:
    try:
        return reconcile_chapter_media_state(uow, chapter)
    except ChapterMediaStateError as exc:
        raise NotesUnavailable() from exc


def _snapshot(
    media: ChapterMediaSnapshot,
    revision: int,
    frame_notes: object,
    resume_frame_id: str | None,
    legacy_approval_revoked: bool = False,
) -> PersonalProductionSnapshot:
    normalized: Mapping[str, object] = frame_notes if isinstance(frame_notes, dict) else {}
    return PersonalProductionSnapshot(
        media=media,
        revision=revision,
        frame_notes=tuple((key, value) for key, value in normalized.items()),
        resume_frame_id=resume_frame_id,
        legacy_approval_revoked=legacy_approval_revoked,
    )


def _rollback_safely(uow: NotesUnitOfWork) -> None:
    try:
        uow.rollback()
    except Exception:
        pass
