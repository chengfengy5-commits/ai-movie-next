"""SQLAlchemy Core adapter for same-Session notes transactions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.shared.identity import TrustedActor

from .domain import ChapterRecord, MediaStateRecord, PersonalNotesRecord, SourceAsset
from .errors import ChapterMediaStateError, NotesUnavailable
from .ports import NotesUnitOfWork
from .tables import (
    chapters,
    personal_production_notes,
    storyboard_assets,
    storyboard_media_states,
    users,
)

SeriesAccessCheck = Callable[[Session, TrustedActor, str], None]


class SqlAlchemyNotesUnitOfWork:
    """A request-scoped Core adapter; transaction ownership stays in the use case."""

    def __init__(self, session: Session, series_access_check: SeriesAccessCheck | None) -> None:
        self._session = session
        self._series_access_check = series_access_check

    def _connection(self):
        return self._session.connection()

    def ensure_clean(self) -> None:
        if self._session.new or self._session.dirty or self._session.deleted:
            raise ChapterMediaStateError("notes require a clean request-scoped Session")

    def load_user(self, user_id: str, *, lock: bool) -> bool:
        statement = select(users.c.id).where(users.c.id == user_id)
        if lock:
            statement = statement.with_for_update(key_share=True)
        return self._connection().execute(statement).first() is not None

    def load_chapter(self, chapter_id: str, *, lock: bool) -> ChapterRecord | None:
        statement = select(chapters.c.id, chapters.c.series_id, chapters.c.content).where(
            chapters.c.id == chapter_id
        )
        if lock:
            statement = statement.with_for_update()
        row = self._connection().execute(statement).mappings().first()
        if row is None:
            return None
        return ChapterRecord(row["id"], row["series_id"], row["content"])

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        if self._series_access_check is None:
            raise NotesUnavailable()
        self._series_access_check(self._session, actor, series_id)

    def list_source_assets(self, chapter_id: str, *, lock: bool = False) -> list[SourceAsset]:
        statement = select(
            storyboard_assets.c.id,
            storyboard_assets.c.chapter_id,
            storyboard_assets.c.image_url,
        ).where(storyboard_assets.c.chapter_id == chapter_id).order_by(storyboard_assets.c.id)
        if lock:
            statement = statement.with_for_update()
        rows = self._connection().execute(statement).mappings().all()
        return [SourceAsset(row["id"], row["chapter_id"], row["image_url"]) for row in rows]

    def list_media_states(self, chapter_id: str, *, lock: bool = False) -> list[MediaStateRecord]:
        statement = select(storyboard_media_states).where(
            storyboard_media_states.c.chapter_id == chapter_id
        ).order_by(storyboard_media_states.c.storyboard_asset_id)
        if lock:
            statement = statement.with_for_update()
        return [_media_state(row) for row in self._connection().execute(statement).mappings().all()]

    def insert_media_state(
        self,
        chapter_id: str,
        storyboard_asset_id: str,
        *,
        media_revision: int,
        asset_image_digest: str | None,
        preview_digest: str | None,
        source_valid: bool,
    ) -> MediaStateRecord:
        now = _now()
        row = {
            "id": str(uuid4()),
            "chapter_id": chapter_id,
            "storyboard_asset_id": storyboard_asset_id,
            "media_revision": media_revision,
            "asset_image_digest": asset_image_digest,
            "preview_digest": preview_digest,
            "source_valid": source_valid,
            "created_at": now,
            "updated_at": now,
        }
        try:
            self._connection().execute(insert(storyboard_media_states).values(**row))
        except IntegrityError as exc:
            if _is_media_state_unique_conflict(exc):
                raise ChapterMediaStateError("chapter media state was initialized concurrently") from exc
            raise
        return _media_state(row)

    def update_media_state(
        self,
        state_id: str,
        expected_revision: int,
        next_revision: int,
        *,
        asset_image_digest: str | None,
        preview_digest: str | None,
        source_valid: bool,
    ) -> bool:
        result = self._connection().execute(
            update(storyboard_media_states)
            .where(
                storyboard_media_states.c.id == state_id,
                storyboard_media_states.c.media_revision == expected_revision,
            )
            .values(
                media_revision=next_revision,
                asset_image_digest=asset_image_digest,
                preview_digest=preview_digest,
                source_valid=source_valid,
                updated_at=_now(),
            )
        )
        return result.rowcount == 1

    def list_personal_notes(self, chapter_id: str, *, lock: bool = False) -> list[PersonalNotesRecord]:
        statement = select(personal_production_notes).where(
            personal_production_notes.c.chapter_id == chapter_id
        ).order_by(personal_production_notes.c.user_id, personal_production_notes.c.id)
        if lock:
            statement = statement.with_for_update()
        return [_notes_record(row) for row in self._connection().execute(statement).mappings().all()]

    def load_personal_notes(
        self,
        chapter_id: str,
        user_id: str,
        *,
        lock: bool = False,
    ) -> PersonalNotesRecord | None:
        statement = select(personal_production_notes).where(
            personal_production_notes.c.chapter_id == chapter_id,
            personal_production_notes.c.user_id == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = self._connection().execute(statement).mappings().first()
        return _notes_record(row) if row is not None else None

    def insert_personal_notes(
        self,
        chapter_id: str,
        user_id: str,
        revision: int,
        frame_notes: dict[str, object],
        resume_frame_id: str | None,
    ) -> PersonalNotesRecord:
        now = _now()
        row = {
            "id": str(uuid4()),
            "chapter_id": chapter_id,
            "user_id": user_id,
            "revision": revision,
            "frame_notes": frame_notes,
            "resume_frame_id": resume_frame_id,
            "created_at": now,
            "updated_at": now,
        }
        self._connection().execute(insert(personal_production_notes).values(**row))
        return _notes_record(row)

    def update_personal_notes(
        self,
        record_id: str,
        expected_revision: int,
        next_revision: int,
        frame_notes: dict[str, object],
        resume_frame_id: str | None,
    ) -> bool:
        result = self._connection().execute(
            update(personal_production_notes)
            .where(
                personal_production_notes.c.id == record_id,
                personal_production_notes.c.revision == expected_revision,
            )
            .values(
                revision=next_revision,
                frame_notes=frame_notes,
                resume_frame_id=resume_frame_id,
                updated_at=_now(),
            )
        )
        return result.rowcount == 1

    def is_integrity_error(self, error: BaseException) -> bool:
        return isinstance(error, IntegrityError)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def _media_state(row) -> MediaStateRecord:
    return MediaStateRecord(
        row["id"],
        row["chapter_id"],
        row["storyboard_asset_id"],
        int(row["media_revision"]),
        row["asset_image_digest"],
        row["preview_digest"],
        bool(row["source_valid"]),
    )


def _notes_record(row) -> PersonalNotesRecord:
    return PersonalNotesRecord(
        row["id"],
        row["chapter_id"],
        row["user_id"],
        int(row["revision"]),
        row["frame_notes"],
        row["resume_frame_id"],
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_media_state_unique_conflict(error: IntegrityError) -> bool:
    original = error.orig
    constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
    if constraint == "uq_storyboard_media_states_chapter_asset":
        return True
    return "storyboard_media_states.chapter_id, storyboard_media_states.storyboard_asset_id" in str(original)
