"""Same-transaction media-state reconciliation reusable by future source writers."""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from typing import Mapping, Sequence
from .domain import ChapterMediaSnapshot, ChapterRecord, FrameMediaSnapshot, MediaStateRecord, SourceAsset
from .errors import ChapterMediaStateError
from .media import (
    ChapterMediaProjection,
    digest_media_identity,
    project_chapter_storyboard_media,
)
from .ports import NotesUnitOfWork

UNSET = object()


def reconcile_chapter_media_state(
    uow: NotesUnitOfWork,
    chapter: ChapterRecord,
    *,
    chapter_content_override: object = UNSET,
    asset_image_overrides: Mapping[str, object] | None = None,
    deleted_asset_ids: set[str] | None = None,
    new_assets: tuple[Mapping[str, object], ...] = (),
) -> ChapterMediaSnapshot:
    """Reconcile current source/media facts in the caller's transaction.

    The caller must already hold the chapter lock and must commit or roll back
    the surrounding unit of work. Overrides let future source writers share this
    logic without requiring an ORM flush or a global event hook. Reconcile before
    reading affected note ORM instances, or expire them after this Core update.
    """
    assets = list(uow.list_source_assets(chapter.id, lock=True))
    states = {state.storyboard_asset_id: state for state in uow.list_media_states(chapter.id, lock=True)}
    _base_projection, base_facts = _facts_by_storyboard_id(chapter.id, chapter.content, assets)

    if not states:
        for asset_id, facts in base_facts.items():
            state = uow.insert_media_state(
                chapter.id,
                asset_id,
                media_revision=1,
                asset_image_digest=facts["asset_image_digest"],
                preview_digest=facts["preview_digest"],
                source_valid=bool(facts["source_valid"]),
            )
            states[asset_id] = state

    target_content = chapter.content if chapter_content_override is UNSET else chapter_content_override
    target_assets = _overlay_assets(
        assets,
        asset_image_overrides=asset_image_overrides or {},
        deleted_asset_ids=deleted_asset_ids or set(),
        new_assets=new_assets,
    )
    projection, target_facts = _facts_by_storyboard_id(chapter.id, target_content, target_assets)
    changed_ids: set[str] = set()

    for asset_id, desired in target_facts.items():
        current = states.get(asset_id)
        if current is None:
            states[asset_id] = uow.insert_media_state(
                chapter.id,
                asset_id,
                media_revision=1,
                asset_image_digest=desired["asset_image_digest"],
                preview_digest=desired["preview_digest"],
                source_valid=bool(desired["source_valid"]),
            )
            continue
        if _state_facts(current) == desired:
            continue
        next_revision = current.media_revision + 1
        if not uow.update_media_state(
            current.id,
            current.media_revision,
            next_revision,
            asset_image_digest=desired["asset_image_digest"],
            preview_digest=desired["preview_digest"],
            source_valid=bool(desired["source_valid"]),
        ):
            raise ChapterMediaStateError("chapter media state changed concurrently")
        states[asset_id] = MediaStateRecord(
            id=current.id,
            chapter_id=current.chapter_id,
            storyboard_asset_id=current.storyboard_asset_id,
            media_revision=next_revision,
            asset_image_digest=desired["asset_image_digest"],
            preview_digest=desired["preview_digest"],
            source_valid=bool(desired["source_valid"]),
        )
        changed_ids.add(asset_id)

    for asset_id, current in tuple(states.items()):
        if asset_id in target_facts:
            continue
        invalid_facts: dict[str, object] = {
            "asset_image_digest": None,
            "preview_digest": None,
            "source_valid": False,
        }
        if _state_facts(current) == invalid_facts:
            continue
        next_revision = current.media_revision + 1
        if not uow.update_media_state(
            current.id,
            current.media_revision,
            next_revision,
            asset_image_digest=None,
            preview_digest=None,
            source_valid=False,
        ):
            raise ChapterMediaStateError("chapter media state changed concurrently")
        states[asset_id] = MediaStateRecord(
            id=current.id,
            chapter_id=current.chapter_id,
            storyboard_asset_id=current.storyboard_asset_id,
            media_revision=next_revision,
            asset_image_digest=None,
            preview_digest=None,
            source_valid=False,
        )
        changed_ids.add(asset_id)

    if changed_ids:
        _revoke_approved_notes(uow, chapter.id, changed_ids)

    snapshots = tuple(
        _snapshot_frame(frame, states)
        for frame in projection.frames
    )
    return ChapterMediaSnapshot(chapter.id, projection.state, snapshots)


def _parse_frames(content: object) -> list[object] | None:
    value = content
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return value if isinstance(value, list) else None


def _facts_by_storyboard_id(
    chapter_id: str,
    chapter_content: object,
    assets: Sequence[SourceAsset],
) -> tuple[ChapterMediaProjection, dict[str, dict[str, object]]]:
    asset_dicts = [
        {"id": asset.id, "chapter_id": asset.chapter_id, "image_url": asset.image_url}
        for asset in assets
    ]
    projection = project_chapter_storyboard_media(chapter_id, chapter_content, asset_dicts)
    frames = _parse_frames(chapter_content)
    candidates: list[str | None] = []
    if frames is not None:
        for frame in frames:
            references = frame.get("storyboard") if isinstance(frame, Mapping) else None
            candidate = references[0] if isinstance(references, list) and references else None
            candidates.append(candidate if isinstance(candidate, str) and candidate.strip() else None)
    counts = Counter(candidate for candidate in candidates if candidate is not None)

    facts: dict[str, dict[str, object]] = {}
    for index, candidate in enumerate(candidates):
        if candidate is None:
            continue
        previous = facts.get(candidate)
        if previous is not None:
            previous.update(asset_image_digest=None, preview_digest=None, source_valid=False)
            continue
        projected = projection.frames[index] if index < len(projection.frames) else None
        is_valid = counts[candidate] == 1 and projected is not None and projected.is_valid
        facts[candidate] = {
            "asset_image_digest": digest_media_identity(projected.asset_image_identity) if is_valid and projected else None,
            "preview_digest": digest_media_identity(projected.preview_identity) if is_valid and projected else None,
            "source_valid": is_valid,
        }
    return projection, facts


def _overlay_assets(
    assets: Sequence[SourceAsset],
    *,
    asset_image_overrides: Mapping[str, object],
    deleted_asset_ids: set[str],
    new_assets: tuple[Mapping[str, object], ...],
) -> list[SourceAsset]:
    by_id = {asset.id: asset for asset in assets}
    for asset_id in deleted_asset_ids:
        by_id.pop(asset_id, None)
    for asset_id, image_url in asset_image_overrides.items():
        current = by_id.get(asset_id)
        if current is not None:
            by_id[asset_id] = SourceAsset(current.id, current.chapter_id, image_url if isinstance(image_url, str) else None)
    for asset in new_assets:
        asset_id = asset.get("id")
        if isinstance(asset_id, str) and asset_id not in deleted_asset_ids:
            by_id[asset_id] = SourceAsset(
                asset_id,
                str(asset.get("chapter_id", "")),
                asset.get("image_url") if isinstance(asset.get("image_url"), str) else None,
            )
    return [by_id[key] for key in sorted(by_id)]


def _state_facts(state: MediaStateRecord) -> dict[str, object]:
    return {
        "asset_image_digest": state.asset_image_digest,
        "preview_digest": state.preview_digest,
        "source_valid": state.source_valid,
    }


def _snapshot_frame(
    frame,
    states: Mapping[str, MediaStateRecord],
) -> FrameMediaSnapshot:
    candidate = frame.storyboard_asset_id if frame.is_valid else None
    state = states.get(candidate) if candidate is not None else None
    usable = state is not None and state.source_valid
    return FrameMediaSnapshot(
        frame_index=frame.frame_index,
        storyboard_asset_id=candidate if usable else None,
        media_revision=state.media_revision if usable else None,
        source_valid=bool(usable),
        asset_image_digest=state.asset_image_digest if usable else None,
        preview_digest=state.preview_digest if usable else None,
        invalid_reason=frame.invalid_reason if not usable else None,
    )


def _revoke_approved_notes(
    uow: NotesUnitOfWork,
    chapter_id: str,
    changed_ids: set[str],
) -> None:
    for record in uow.list_personal_notes(chapter_id, lock=True):
        frames = deepcopy(record.frame_notes) if isinstance(record.frame_notes, dict) else {}
        changed = False
        for asset_id in changed_ids:
            entry = frames.get(asset_id)
            if not isinstance(entry, dict) or entry.get("status") != "approved":
                continue
            frames[asset_id] = {
                **entry,
                "status": "unmarked",
                "approved_media_revision": None,
                "needs_reconfirmation": True,
            }
            changed = True
        if not changed:
            continue
        if not uow.update_personal_notes(
            record.id,
            record.revision,
            record.revision + 1,
            frames,
            record.resume_frame_id,
        ):
            raise ChapterMediaStateError("personal notes changed concurrently during media reconciliation")
