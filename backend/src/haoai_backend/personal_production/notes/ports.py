"""Framework-free ports for transactional personal-note use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from haoai_backend.shared.identity import TrustedActor
from .domain import ChapterRecord, MediaStateRecord, PersonalNotesRecord, SourceAsset


class NotesUnitOfWork(Protocol):
    def ensure_clean(self) -> None: ...

    def load_user(self, user_id: str, *, lock: bool) -> bool: ...

    def load_chapter(self, chapter_id: str, *, lock: bool) -> ChapterRecord | None: ...

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None: ...

    def list_source_assets(self, chapter_id: str, *, lock: bool) -> Sequence[SourceAsset]: ...

    def list_media_states(self, chapter_id: str, *, lock: bool) -> Sequence[MediaStateRecord]: ...

    def insert_media_state(
        self,
        chapter_id: str,
        storyboard_asset_id: str,
        *,
        media_revision: int,
        asset_image_digest: str | None,
        preview_digest: str | None,
        source_valid: bool,
    ) -> MediaStateRecord: ...

    def update_media_state(
        self,
        state_id: str,
        expected_revision: int,
        next_revision: int,
        *,
        asset_image_digest: str | None,
        preview_digest: str | None,
        source_valid: bool,
    ) -> bool: ...

    def list_personal_notes(self, chapter_id: str, *, lock: bool) -> Sequence[PersonalNotesRecord]: ...

    def load_personal_notes(
        self,
        chapter_id: str,
        user_id: str,
        *,
        lock: bool,
    ) -> PersonalNotesRecord | None: ...

    def insert_personal_notes(
        self,
        chapter_id: str,
        user_id: str,
        revision: int,
        frame_notes: Mapping[str, object],
        resume_frame_id: str | None,
    ) -> PersonalNotesRecord: ...

    def update_personal_notes(
        self,
        record_id: str,
        expected_revision: int,
        next_revision: int,
        frame_notes: Mapping[str, object],
        resume_frame_id: str | None,
    ) -> bool: ...

    def is_integrity_error(self, error: BaseException) -> bool: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


NotesUnitOfWorkFactory = Callable[[], NotesUnitOfWork]
SeriesAccessCheck = Callable[[object, TrustedActor, str], None]
