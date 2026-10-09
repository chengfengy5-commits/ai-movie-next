"""Pure personal-note snapshot and partial-update rules."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Mapping


MAX_NOTE_LENGTH = 2000
MAX_FRAME_UPDATES = 500


@dataclass(frozen=True, slots=True)
class ChapterRecord:
    id: str
    series_id: str
    content: object


@dataclass(frozen=True, slots=True)
class SourceAsset:
    id: str
    chapter_id: str
    image_url: str | None


@dataclass(frozen=True, slots=True)
class MediaStateRecord:
    id: str
    chapter_id: str
    storyboard_asset_id: str
    media_revision: int
    asset_image_digest: str | None
    preview_digest: str | None
    source_valid: bool


@dataclass(frozen=True, slots=True)
class FrameMediaSnapshot:
    frame_index: int
    storyboard_asset_id: str | None
    media_revision: int | None
    source_valid: bool
    asset_image_digest: str | None
    preview_digest: str | None
    invalid_reason: str | None


@dataclass(frozen=True, slots=True)
class ChapterMediaSnapshot:
    chapter_id: str
    state: str
    frames: tuple[FrameMediaSnapshot, ...]


@dataclass(frozen=True, slots=True)
class PersonalNotesRecord:
    id: str
    chapter_id: str
    user_id: str
    revision: int
    frame_notes: object
    resume_frame_id: str | None


@dataclass(frozen=True, slots=True)
class FrameNoteChange:
    storyboard_asset_id: str
    expected_media_revision: int
    status: str | None
    note: str | None


@dataclass(frozen=True, slots=True)
class PersonalNotesUpdate:
    expected_revision: int
    frames: tuple[FrameNoteChange, ...]
    has_resume_patch: bool
    resume_frame_id: str | None


@dataclass(frozen=True, slots=True)
class PersonalProductionSnapshot:
    media: ChapterMediaSnapshot
    revision: int
    frame_notes: tuple[tuple[str, object], ...]
    resume_frame_id: str | None
    legacy_approval_revoked: bool = False




def revoke_empty_media_approvals(
    frame_notes: object,
    media: ChapterMediaSnapshot,
) -> tuple[dict[str, object], bool]:
    normalized = deepcopy(frame_notes) if isinstance(frame_notes, dict) else {}
    changed = False
    for frame in media.frames:
        if (
            frame.storyboard_asset_id is None
            or not frame.source_valid
            or frame.asset_image_digest is not None
            or frame.preview_digest is not None
        ):
            continue
        entry = normalized.get(frame.storyboard_asset_id)
        if not isinstance(entry, dict) or entry.get("status") != "approved":
            continue
        normalized[frame.storyboard_asset_id] = {
            **entry,
            "status": "unmarked",
            "approved_media_revision": None,
            "needs_reconfirmation": True,
        }
        changed = True
    return normalized, changed


def apply_frame_note_changes(
    frame_notes: object,
    changes: tuple[FrameNoteChange, ...],
    media_by_id: Mapping[str, FrameMediaSnapshot],
) -> dict[str, object]:
    updated = deepcopy(frame_notes) if isinstance(frame_notes, dict) else {}
    for change in changes:
        media = media_by_id[change.storyboard_asset_id]
        existing = updated.get(change.storyboard_asset_id)
        entry = deepcopy(existing) if isinstance(existing, dict) else {}
        next_status = change.status if change.status is not None else entry.get("status", "unmarked")
        next_note = change.note if change.note is not None else entry.get("note", "")
        entry["status"] = next_status
        entry["note"] = next_note
        if next_status == "approved":
            entry["approved_media_revision"] = media.media_revision
            entry["needs_reconfirmation"] = False
        else:
            entry["approved_media_revision"] = None
            if change.status is not None:
                entry.pop("needs_reconfirmation", None)
        updated[change.storyboard_asset_id] = entry
    return updated


def apply_notes_update(
    snapshot: PersonalProductionSnapshot,
    update: PersonalNotesUpdate,
) -> tuple[dict[str, object], str | None]:
    frame_notes = dict(snapshot.frame_notes)
    media_by_id = {
        frame.storyboard_asset_id: frame
        for frame in snapshot.media.frames
        if frame.storyboard_asset_id is not None
    }
    frame_notes = apply_frame_note_changes(frame_notes, update.frames, media_by_id)
    resume = update.resume_frame_id if update.has_resume_patch else snapshot.resume_frame_id
    return frame_notes, resume


def snapshot_response(snapshot: PersonalProductionSnapshot) -> dict[str, object]:
    return {
        "chapter_id": snapshot.media.chapter_id,
        "revision": snapshot.revision,
        "media_state": snapshot.media.state,
        "frames": [
            {
                "frame_index": frame.frame_index,
                "storyboard_asset_id": frame.storyboard_asset_id,
                "media_revision": frame.media_revision,
                "source_valid": frame.source_valid,
                "asset_image_digest": frame.asset_image_digest,
                "preview_digest": frame.preview_digest,
                "invalid_reason": frame.invalid_reason,
            }
            for frame in snapshot.media.frames
        ],
        "frame_notes": deepcopy(dict(snapshot.frame_notes)),
        "resume_frame_id": snapshot.resume_frame_id,
    }
