"""SQL persistence for stable chapter generation-history snapshots and writes."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.personal_production.notes import stage_media_reconciliation
from haoai_backend.personal_production.notes.errors import ChapterMediaStateError, NotesNotFound
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_data.tables import (
    chapter_locks,
    chapters,
    chat_messages,
    series,
    storyboard_assets,
    system_configs,
)
from haoai_backend.shared.identity import TrustedActor

from .domain import (
    canonical_digest,
    first_storyboard_id,
    message_digest_payload,
    message_wire,
    parse_chapter_frames,
    safe_generation_media_url,
    storyboard_references,
    timestamp_wire,
)
from .errors import (
    HistoryConflict,
    HistoryForbidden,
    HistoryInvalid,
    HistoryMediaUrlTooLong,
    HistoryNotFound,
)
from .schemas import GenerationHistoryAdoptRequest

Clock = Callable[[], datetime]


def read_generation_history(
    session: Session,
    actor: TrustedActor,
    chapter_id: str,
    *,
    target_kind: str,
    frame_index: int,
    storyboard_asset_id: str | None,
    chat_mode: str,
) -> dict[str, Any]:
    series_row, chapter_row = _load_scope(session, actor, chapter_id, lock_rows=False)
    assets = _load_chapter_assets(session, chapter_id, lock_rows=False)
    target = _resolve_target(
        series_row,
        chapter_row,
        assets,
        target_kind=target_kind,
        frame_index=frame_index,
        storyboard_asset_id=storyboard_asset_id,
    )
    messages = _read_messages(
        session,
        chapter_id,
        target_kind=target_kind,
        frame_index=frame_index,
        storyboard_asset_id=target["storyboard_asset_id"],
        chat_mode=chat_mode,
    )
    return {
        "protocol_version": 1,
        "series_id": str(series_row["id"]),
        "chapter_id": chapter_id,
        "target_kind": target_kind,
        "storyboard_asset_id": target["storyboard_asset_id"],
        "frame_index": frame_index,
        "chat_mode": chat_mode,
        "writable": target["writable"],
        "read_only_reason": target["read_only_reason"],
        "target_proof": target["target_proof"],
        "messages": messages,
    }


def adopt_generation_history(
    session: Session,
    actor: TrustedActor,
    chapter_id: str,
    request: GenerationHistoryAdoptRequest,
    *,
    now: Clock | None = None,
) -> dict[str, Any]:
    series_row, chapter_row = _load_scope(session, actor, chapter_id, lock_rows=True)
    if request.expected_series_id != series_row["id"]:
        raise HistoryConflict()

    assets = _load_chapter_assets(session, chapter_id, lock_rows=True)
    target = _resolve_target(
        series_row,
        chapter_row,
        assets,
        target_kind=request.target_kind,
        frame_index=request.expected_frame_index,
        storyboard_asset_id=request.storyboard_asset_id,
    )
    if not target["writable"] or target["target_proof"] is None:
        raise HistoryConflict()
    proof = target["target_proof"]
    if (
        target["storyboard_asset_id"] != request.storyboard_asset_id
        or proof["mapping_digest"] != request.expected_mapping_digest
        or proof["target_digest"] != request.expected_target_digest
        or proof["media_url"] != request.expected_media_url
    ):
        raise HistoryConflict()

    lock_row = _lock_and_verify_chapter_lease(
        session,
        actor,
        chapter_id,
        series_row,
        request.expected_lock_identity.model_dump()
        if request.expected_lock_identity is not None
        else None,
        now,
    )

    message = verify_source_message(
        session,
        chapter_id,
        request,
        for_update=False,
    )
    replacement_url = message["content"]

    if proof["media_url"] == replacement_url:
        verify_source_message(session, chapter_id, request, for_update=True)
        _refresh_chapter_lock(session, actor, chapter_id, lock_row, _utcnow(now))
        return _adopt_response(
            "already_current",
            series_row,
            chapter_id,
            request,
            proof,
        )
    if request.target_kind == "storyboard-image" and len(replacement_url) > 500:
        raise HistoryMediaUrlTooLong()

    try:
        updated_content: str | None = None
        if request.target_kind == "frame-preview":
            frames = parse_chapter_frames(chapter_row["content"])
            if frames is None or request.expected_frame_index >= len(frames):
                raise HistoryConflict()
            selected_frame = frames[request.expected_frame_index]
            if not isinstance(selected_frame, dict):
                raise HistoryConflict()
            updated_frame = dict(selected_frame)
            updated_frame["preview"] = replacement_url
            frames[request.expected_frame_index] = updated_frame
            updated_content = json.dumps(frames, ensure_ascii=False, allow_nan=False)
            stage_media_reconciliation(
                session,
                chapter_id,
                chapter_content_override=updated_content,
            )
        else:
            stage_media_reconciliation(
                session,
                chapter_id,
                asset_image_overrides={request.storyboard_asset_id: replacement_url},
                focus_storyboard_asset_id=request.storyboard_asset_id,
            )

        verify_source_message(session, chapter_id, request, for_update=True)
        changed_at = _utcnow(now)
        if request.target_kind == "frame-preview":
            assert updated_content is not None
            original_content = chapter_row["content"]
            content_predicate = (
                chapters.c.content.is_(None)
                if original_content is None
                else chapters.c.content == original_content
            )
            result = session.execute(
                update(chapters)
                .where(
                    chapters.c.id == chapter_id,
                    chapters.c.series_id == series_row["id"],
                    chapters.c.order == chapter_row["order"],
                    content_predicate,
                )
                .values(content=updated_content, updated_at=changed_at)
            )
        else:
            asset = target["asset_row"]
            image_predicate = (
                storyboard_assets.c.image_url.is_(None)
                if asset["image_url"] is None
                else storyboard_assets.c.image_url == asset["image_url"]
            )
            result = session.execute(
                update(storyboard_assets)
                .where(
                    storyboard_assets.c.id == request.storyboard_asset_id,
                    storyboard_assets.c.series_id == series_row["id"],
                    storyboard_assets.c.chapter_id == chapter_id,
                    storyboard_assets.c.frame_index == request.expected_frame_index,
                    image_predicate,
                )
                .values(image_url=replacement_url, updated_at=changed_at)
            )
        if result.rowcount != 1:
            raise HistoryConflict()

        _refresh_chapter_lock(session, actor, chapter_id, lock_row, changed_at)
        updated_chapter = dict(chapter_row)
        if updated_content is not None:
            updated_chapter["content"] = updated_content
        updated_assets = [dict(asset) for asset in assets]
        if request.target_kind == "storyboard-image":
            for asset in updated_assets:
                if asset["id"] == request.storyboard_asset_id:
                    asset["image_url"] = replacement_url
                    asset["updated_at"] = changed_at
                    break
        updated_target = _resolve_target(
            series_row,
            updated_chapter,
            updated_assets,
            target_kind=request.target_kind,
            frame_index=request.expected_frame_index,
            storyboard_asset_id=request.storyboard_asset_id,
        )
        if updated_target["target_proof"] is None or not updated_target["writable"]:
            raise HistoryConflict()
        return _adopt_response(
            "applied",
            series_row,
            chapter_id,
            request,
            updated_target["target_proof"],
        )
    except (NotesNotFound, ChapterMediaStateError) as exc:
        raise HistoryConflict() from exc


def verify_source_message(
    session: Session,
    chapter_id: str,
    request: GenerationHistoryAdoptRequest,
    *,
    for_update: bool,
) -> dict[str, Any]:
    statement = select(chat_messages).where(
        chat_messages.c.id == request.message_id,
        chat_messages.c.chapter_id == chapter_id,
    )
    if for_update:
        statement = statement.with_for_update()
    row = session.execute(statement).mappings().first()
    if row is None:
        raise HistoryConflict()
    current = dict(row)
    if (
        current["chat_mode"] != request.chat_mode
        or current["role"] != "assistant"
        or not _matches_source_tuple(current, request)
        or canonical_digest(message_digest_payload(current)) != request.expected_message_digest
        or not safe_generation_media_url(current["content"])
    ):
        raise HistoryConflict()
    return current


def _matches_source_tuple(message: dict[str, Any], request: GenerationHistoryAdoptRequest) -> bool:
    if request.target_kind == "frame-preview":
        matches = (
            message["frame_index"] == request.expected_frame_index
            and message["asset_type"] is None
            and message["asset_id"] is None
        )
    else:
        matches = (
            message["frame_index"] is None
            and message["asset_type"] == "storyboard"
            and message["asset_id"] == request.storyboard_asset_id
        )
    return matches


def _load_scope(
    session: Session,
    actor: TrustedActor,
    chapter_id: str,
    *,
    lock_rows: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    series_id = session.execute(
        select(chapters.c.series_id).where(chapters.c.id == chapter_id)
    ).scalar_one_or_none()
    if series_id is None:
        raise HistoryNotFound()

    series_query = select(series).where(series.c.id == series_id)
    chapter_query = select(chapters).where(chapters.c.id == chapter_id)
    if lock_rows:
        series_query = series_query.with_for_update()
        chapter_query = chapter_query.with_for_update()
    else:
        series_query = series_query.with_for_update(read=True)
        chapter_query = chapter_query.with_for_update(read=True)
    series_row = session.execute(series_query).mappings().first()
    if series_row is None:
        raise HistoryNotFound()

    try:
        require_series_access(
            SqlAlchemySeriesAccessReader(session),
            ActorIdentity(actor.user_id),
            str(series_row["id"]),
        )
    except SeriesNotFound as exc:
        raise HistoryNotFound() from exc
    except SeriesAccessDenied as exc:
        raise HistoryForbidden() from exc

    chapter_row = session.execute(chapter_query).mappings().first()
    if chapter_row is None:
        raise HistoryNotFound()
    if chapter_row["series_id"] != series_row["id"]:
        raise HistoryConflict()
    return dict(series_row), dict(chapter_row)


def _load_chapter_assets(
    session: Session,
    chapter_id: str,
    *,
    lock_rows: bool,
) -> list[dict[str, Any]]:
    statement = (
        select(storyboard_assets)
        .where(storyboard_assets.c.chapter_id == chapter_id)
        .order_by(storyboard_assets.c.id)
    )
    statement = statement.with_for_update() if lock_rows else statement.with_for_update(read=True)
    return [dict(row) for row in session.execute(statement).mappings().all()]


def _resolve_target(
    series_row: dict[str, Any],
    chapter_row: dict[str, Any],
    assets: list[dict[str, Any]],
    *,
    target_kind: str,
    frame_index: int,
    storyboard_asset_id: str | None,
) -> dict[str, Any]:
    frames = parse_chapter_frames(chapter_row["content"])
    if frames is None:
        return _unwritable_target(storyboard_asset_id, "unreadable_chapter")
    if frame_index >= len(frames):
        raise HistoryConflict()

    mapping_digest = _mapping_digest(series_row, chapter_row, frames, assets)
    first_ids = [first_storyboard_id(frame) for frame in frames]
    id_counts = Counter(value for value in first_ids if value is not None)
    if target_kind == "frame-preview":
        frame = frames[frame_index]
        current_id = first_ids[frame_index]
        if storyboard_asset_id is not None and storyboard_asset_id != current_id:
            raise HistoryConflict()
        if not isinstance(frame, dict):
            return _unwritable_target(current_id, "unreadable_chapter")
        if current_id is None:
            reason = "missing_storyboard_id"
        elif id_counts[current_id] != 1:
            reason = "duplicate_storyboard_id"
        else:
            matching = [asset for asset in assets if asset["id"] == current_id]
            if not matching:
                reason = "asset_not_in_chapter"
            elif len(matching) != 1:
                reason = "ambiguous_storyboard_asset"
            else:
                asset = matching[0]
                reason = None if (
                    asset["series_id"] == series_row["id"]
                    and asset["chapter_id"] == chapter_row["id"]
                    and asset["frame_index"] == frame_index
                ) else "storyboard_index_mismatch"
        media_url = frame.get("preview")
        if media_url is not None and not isinstance(media_url, str):
            return _unwritable_target(current_id, "invalid_media_value")
        proof = {
            "mapping_digest": mapping_digest,
            "target_digest": canonical_digest({
                "target_kind": target_kind,
                "series_id": series_row["id"],
                "chapter_id": chapter_row["id"],
                "frame_index": frame_index,
                "frame": frame,
            }),
            "media_url": media_url,
        }
        return {
            "storyboard_asset_id": current_id,
            "writable": reason is None,
            "read_only_reason": reason,
            "target_proof": proof,
            "asset_row": None,
        }

    if storyboard_asset_id is None or not storyboard_asset_id.strip():
        raise HistoryInvalid()
    matching = [asset for asset in assets if asset["id"] == storyboard_asset_id]
    if not matching:
        return _unwritable_target(storyboard_asset_id, "asset_not_in_chapter")
    if len(matching) != 1:
        return _unwritable_target(storyboard_asset_id, "ambiguous_storyboard_asset")
    asset = matching[0]
    if asset["series_id"] != series_row["id"] or asset["chapter_id"] != chapter_row["id"]:
        return _unwritable_target(storyboard_asset_id, "asset_not_in_chapter")
    current_index = asset["frame_index"]
    if current_index != frame_index:
        raise HistoryConflict()
    frame = frames[frame_index]
    if not isinstance(frame, dict):
        return _unwritable_target(storyboard_asset_id, "unreadable_chapter")
    reason: str | None = None
    if id_counts[storyboard_asset_id] != 1:
        reason = "duplicate_storyboard_id"
    elif first_ids[frame_index] != storyboard_asset_id:
        reason = "asset_not_in_chapter"
    media_url = asset["image_url"]
    if media_url is not None and not isinstance(media_url, str):
        return _unwritable_target(storyboard_asset_id, "invalid_media_value")
    proof = {
        "mapping_digest": mapping_digest,
        "target_digest": canonical_digest({
            "target_kind": target_kind,
            "series_id": series_row["id"],
            "chapter_id": chapter_row["id"],
            "frame_index": frame_index,
            "asset": _asset_digest_facts(asset),
            "linked_frame": {
                "frame_index": frame_index,
                "storyboard_references": storyboard_references(frame),
                "stable_storyboard_id": storyboard_asset_id,
            },
        }),
        "media_url": media_url,
    }
    return {
        "storyboard_asset_id": storyboard_asset_id,
        "writable": reason is None,
        "read_only_reason": reason,
        "target_proof": proof,
        "asset_row": asset,
    }


def _mapping_digest(
    series_row: dict[str, Any],
    chapter_row: dict[str, Any],
    frames: list[Any],
    assets: list[dict[str, Any]],
) -> str:
    return canonical_digest({
        "series_id": series_row["id"],
        "chapter_id": chapter_row["id"],
        "chapter_order": chapter_row["order"],
        "frames": [
            {
                "frame_index": index,
                "stable_storyboard_id": first_storyboard_id(frame),
                "storyboard_references": storyboard_references(frame),
            }
            for index, frame in enumerate(frames)
        ],
        "storyboard_assets": [
            {
                "id": asset["id"],
                "series_id": asset["series_id"],
                "chapter_id": asset["chapter_id"],
                "frame_index": asset["frame_index"],
            }
            for asset in assets
        ],
    })


def _asset_digest_facts(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": asset["id"],
        "series_id": asset["series_id"],
        "chapter_id": asset["chapter_id"],
        "frame_index": asset["frame_index"],
        "name": asset["name"],
        "description": asset["description"],
        "image_url": asset["image_url"],
        "created_at": timestamp_wire(asset["created_at"]),
        "updated_at": timestamp_wire(asset["updated_at"]),
    }


def _read_messages(
    session: Session,
    chapter_id: str,
    *,
    target_kind: str,
    frame_index: int,
    storyboard_asset_id: str | None,
    chat_mode: str,
) -> list[dict[str, Any]]:
    statement = select(chat_messages).where(
        chat_messages.c.chapter_id == chapter_id,
        chat_messages.c.chat_mode == chat_mode,
    )
    if target_kind == "frame-preview":
        statement = statement.where(
            chat_messages.c.frame_index == frame_index,
            chat_messages.c.asset_type.is_(None),
            chat_messages.c.asset_id.is_(None),
        )
    else:
        if storyboard_asset_id is None:
            return []
        statement = statement.where(
            chat_messages.c.frame_index.is_(None),
            chat_messages.c.asset_type == "storyboard",
            chat_messages.c.asset_id == storyboard_asset_id,
        )
    rows = session.execute(
        statement.order_by(chat_messages.c.created_at.asc(), chat_messages.c.id.asc())
    ).mappings().all()
    return [message_wire(dict(row)) for row in rows]


def _lock_and_verify_chapter_lease(
    session: Session,
    actor: TrustedActor,
    chapter_id: str,
    series_row: dict[str, Any],
    expected: dict[str, Any] | None,
    clock: Clock | None,
) -> dict[str, Any] | None:
    if not series_row["team_id"]:
        if expected is not None:
            raise HistoryInvalid()
        return None
    if expected is None or expected.get("user_id") != actor.user_id:
        raise HistoryConflict()
    acquired_at = _parse_lock_timestamp(expected.get("acquired_at"))
    locks = session.execute(
        select(chapter_locks)
        .where(chapter_locks.c.chapter_id == chapter_id)
        .order_by(chapter_locks.c.id)
        .with_for_update()
    ).mappings().all()
    now = _utcnow(clock)
    if len(locks) != 1:
        raise HistoryConflict()
    lock = dict(locks[0])
    if (
        lock["user_id"] != actor.user_id
        or _as_utc_naive(lock["acquired_at"]) != acquired_at
        or _as_utc_naive(lock["expires_at"]) <= now
    ):
        raise HistoryConflict()
    return lock


def _refresh_chapter_lock(
    session: Session,
    actor: TrustedActor,
    chapter_id: str,
    lock: dict[str, Any] | None,
    now: datetime,
) -> None:
    if lock is None:
        return
    if _as_utc_naive(lock["expires_at"]) <= now:
        raise HistoryConflict()
    idle = session.execute(
        select(system_configs.c.chapter_lock_idle_minutes).limit(1)
    ).scalar_one_or_none()
    minutes = idle if idle and idle > 0 else 15
    result = session.execute(
        update(chapter_locks)
        .where(
            chapter_locks.c.id == lock["id"],
            chapter_locks.c.chapter_id == chapter_id,
            chapter_locks.c.user_id == actor.user_id,
            chapter_locks.c.acquired_at == lock["acquired_at"],
            chapter_locks.c.expires_at > now,
        )
        .values(last_active_at=now, expires_at=now + timedelta(minutes=minutes))
    )
    if result.rowcount != 1:
        raise HistoryConflict()


def _adopt_response(
    outcome: str,
    series_row: dict[str, Any],
    chapter_id: str,
    request: GenerationHistoryAdoptRequest,
    target_proof: dict[str, Any],
) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "outcome": outcome,
        "series_id": str(series_row["id"]),
        "chapter_id": chapter_id,
        "target_kind": request.target_kind,
        "storyboard_asset_id": request.storyboard_asset_id,
        "frame_index": request.expected_frame_index,
        "target_proof": target_proof,
    }


def _unwritable_target(storyboard_asset_id: str | None, reason: str) -> dict[str, Any]:
    return {
        "storyboard_asset_id": storyboard_asset_id,
        "writable": False,
        "read_only_reason": reason,
        "target_proof": None,
        "asset_row": None,
    }


def _parse_lock_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise HistoryInvalid()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoryInvalid() from exc
    if parsed.tzinfo is None:
        raise HistoryInvalid()
    return _as_utc_naive(parsed)


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utcnow(clock: Clock | None = None) -> datetime:
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    return _as_utc_naive(value)
