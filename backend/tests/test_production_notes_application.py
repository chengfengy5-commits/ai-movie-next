"""Application transaction, CAS, and error-order tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from haoai_backend.personal_production.notes.application import (
    get_personal_production_notes,
    save_personal_production_notes,
)
from haoai_backend.personal_production.notes.domain import (
    ChapterRecord,
    FrameNoteChange,
    MediaStateRecord,
    PersonalNotesRecord,
    PersonalNotesUpdate,
    SourceAsset,
)
from haoai_backend.personal_production.notes.errors import NotesConflict
from haoai_backend.personal_production.notes.media import digest_media_identity
from haoai_backend.shared.identity import TrustedActor


class ScriptedUnitOfWork:
    def __init__(self, shared: dict[str, Any]) -> None:
        self.shared = shared
        self.pending: PersonalNotesRecord | None = None
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def ensure_clean(self) -> None:
        return None

    def load_user(self, user_id: str, *, lock: bool) -> bool:
        return user_id == "user-a"

    def load_chapter(self, chapter_id: str, *, lock: bool) -> ChapterRecord | None:
        return self.shared["chapter"] if chapter_id == "chapter-a" else None

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        assert actor.user_id == "user-a"
        assert series_id == "series-a"

    def list_source_assets(self, chapter_id: str, *, lock: bool) -> list[SourceAsset]:
        return [self.shared["asset"]]

    def list_media_states(self, chapter_id: str, *, lock: bool) -> list[MediaStateRecord]:
        return [self.shared["media_state"]]

    def insert_media_state(self, chapter_id: str, storyboard_asset_id: str, **values: Any) -> MediaStateRecord:
        state = MediaStateRecord(
            id="media-state",
            chapter_id=chapter_id,
            storyboard_asset_id=storyboard_asset_id,
            media_revision=values["media_revision"],
            asset_image_digest=values["asset_image_digest"],
            preview_digest=values["preview_digest"],
            source_valid=values["source_valid"],
        )
        self.shared["media_state"] = state
        return state

    def update_media_state(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def list_personal_notes(self, chapter_id: str, *, lock: bool) -> list[PersonalNotesRecord]:
        record = self.shared.get("notes")
        return [record] if record is not None else []

    def load_personal_notes(self, chapter_id: str, user_id: str, *, lock: bool) -> PersonalNotesRecord | None:
        record = self.shared.get("notes")
        return record if record is not None and record.user_id == user_id else None

    def insert_personal_notes(self, chapter_id: str, user_id: str, revision: int, frame_notes: dict[str, object], resume_frame_id: str | None) -> PersonalNotesRecord:
        record = PersonalNotesRecord("note-row", chapter_id, user_id, revision, frame_notes, resume_frame_id)
        self.pending = record
        return record

    def update_personal_notes(self, record_id: str, expected_revision: int, next_revision: int, frame_notes: dict[str, object], resume_frame_id: str | None) -> bool:
        record = self.shared.get("notes")
        if record is None or record.revision != expected_revision or self.shared.get("force_cas_failure"):
            return False
        self.shared["notes"] = PersonalNotesRecord(record_id, record.chapter_id, record.user_id, next_revision, frame_notes, resume_frame_id)
        return True

    def is_integrity_error(self, error: BaseException) -> bool:
        return False

    def commit(self) -> None:
        self.commits += 1
        if self.pending is not None:
            self.shared["notes"] = self.pending
            self.pending = None
        if self.shared.get("commit_ack_lost"):
            raise RuntimeError("commit acknowledgement was lost after durable write")

    def rollback(self) -> None:
        self.rollbacks += 1
        self.pending = None

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def shared_state() -> dict[str, Any]:
    preview = "https://media.invalid/frame-a.png?tenant=notes-test"
    return {
        "chapter": ChapterRecord("chapter-a", "series-a", [{"storyboard": ["frame-a"], "preview": preview}]),
        "asset": SourceAsset("frame-a", "chapter-a", None),
        "media_state": MediaStateRecord(
            "media-state",
            "chapter-a",
            "frame-a",
            1,
            None,
            digest_media_identity(preview),
            True,
        ),
    }


def test_personal_revision_conflict_precedes_bad_frame_identity(shared_state: dict[str, Any]) -> None:
    shared_state["notes"] = PersonalNotesRecord("note-row", "chapter-a", "user-a", 3, {}, None)
    uow = ScriptedUnitOfWork(shared_state)
    with pytest.raises(NotesConflict, match="个人记录已变化"):
        save_personal_production_notes(
            lambda: uow,
            TrustedActor("user-a"),
            "chapter-a",
            PersonalNotesUpdate(2, (FrameNoteChange("missing", 1, None, "x"),), False, None),
        )
    assert uow.rollbacks == 1
    assert shared_state["notes"].revision == 3


def test_private_row_cas_failure_rolls_back_without_overwriting_current_state(shared_state: dict[str, Any]) -> None:
    shared_state["notes"] = PersonalNotesRecord("note-row", "chapter-a", "user-a", 1, {"frame-a": {"status": "unmarked", "note": "before"}}, None)
    shared_state["force_cas_failure"] = True
    uow = ScriptedUnitOfWork(shared_state)
    with pytest.raises(NotesConflict, match="个人记录已变化"):
        save_personal_production_notes(
            lambda: uow,
            TrustedActor("user-a"),
            "chapter-a",
            PersonalNotesUpdate(1, (FrameNoteChange("frame-a", 1, None, "after"),), False, None),
        )
    assert uow.rollbacks == 1
    assert shared_state["notes"].revision == 1
    assert shared_state["notes"].frame_notes["frame-a"]["note"] == "before"


def test_lost_commit_ack_is_not_retried_and_explicit_get_observes_durable_result(shared_state: dict[str, Any]) -> None:
    shared_state["commit_ack_lost"] = True
    units: list[ScriptedUnitOfWork] = []

    def factory() -> ScriptedUnitOfWork:
        unit = ScriptedUnitOfWork(shared_state)
        units.append(unit)
        return unit

    with pytest.raises(RuntimeError, match="acknowledgement was lost"):
        save_personal_production_notes(
            factory,
            TrustedActor("user-a"),
            "chapter-a",
            PersonalNotesUpdate(0, (FrameNoteChange("frame-a", 1, "needs_revision", "saved once"),), False, None),
        )

    assert len(units) == 1
    assert units[0].commits == 1
    assert units[0].rollbacks == 1
    assert shared_state["notes"].revision == 1
    shared_state["commit_ack_lost"] = False

    response = get_personal_production_notes(factory, TrustedActor("user-a"), "chapter-a")
    assert len(units) == 2
    assert response["revision"] == 1
    assert response["frame_notes"]["frame-a"]["note"] == "saved once"


class IntegrityFailingUnitOfWork(ScriptedUnitOfWork):
    def __init__(self, shared: dict[str, Any], failure_phase: str) -> None:
        super().__init__(shared)
        self.failure_phase = failure_phase

    def insert_personal_notes(
        self,
        chapter_id: str,
        user_id: str,
        revision: int,
        frame_notes: dict[str, object],
        resume_frame_id: str | None,
    ) -> PersonalNotesRecord:
        if self.failure_phase == "insert":
            raise _unique_error()
        return super().insert_personal_notes(
            chapter_id, user_id, revision, frame_notes, resume_frame_id
        )

    def update_personal_notes(
        self,
        record_id: str,
        expected_revision: int,
        next_revision: int,
        frame_notes: dict[str, object],
        resume_frame_id: str | None,
    ) -> bool:
        if self.failure_phase == "update":
            raise _unique_error()
        if self.failure_phase == "commit":
            record = self.shared["notes"]
            self.pending = PersonalNotesRecord(
                record_id,
                record.chapter_id,
                record.user_id,
                next_revision,
                frame_notes,
                resume_frame_id,
            )
            return True
        return super().update_personal_notes(
            record_id,
            expected_revision,
            next_revision,
            frame_notes,
            resume_frame_id,
        )

    def is_integrity_error(self, error: BaseException) -> bool:
        return isinstance(error, IntegrityError)

    def commit(self) -> None:
        if self.failure_phase == "commit":
            self.commits += 1
            raise _unique_error()
        super().commit()


def _unique_error() -> IntegrityError:
    return IntegrityError("private notes unique key", {}, RuntimeError("duplicate key"))


@pytest.mark.parametrize("phase", ["insert", "update", "commit"])
def test_private_notes_integrity_errors_are_all_mapped_to_legacy_conflict(
    shared_state: dict[str, Any],
    phase: str,
) -> None:
    if phase != "insert":
        shared_state["notes"] = PersonalNotesRecord(
            "note-row", "chapter-a", "user-a", 1, {"frame-a": {"note": "原文"}}, None
        )
    uow = IntegrityFailingUnitOfWork(shared_state, phase)
    update = PersonalNotesUpdate(
        0 if phase == "insert" else 1,
        (FrameNoteChange("frame-a", 1, None, "新文本"),),
        False,
        None,
    )

    with pytest.raises(NotesConflict, match="个人记录已由另一请求创建或更新"):
        save_personal_production_notes(
            lambda: uow,
            TrustedActor("user-a"),
            "chapter-a",
            update,
        )

    assert uow.rollbacks == 1
    assert uow.closed
    if phase == "insert":
        assert "notes" not in shared_state
    else:
        assert shared_state["notes"].revision == 1
        assert shared_state["notes"].frame_notes["frame-a"]["note"] == "原文"
