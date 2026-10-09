"""Use cases for the twelve legacy series, chapter and storyboard methods."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from haoai_backend.shared.identity import TrustedActor

from .domain import chapter_create_content, parse_source_frames
from .errors import SeriesDataBadRequest, SeriesDataNotFound, SeriesDataUnavailable
from .ports import Clock, SeriesDataUnitOfWork, UnitOfWorkFactory
from .presentation import chapter_payload, series_detail, series_list_item, storyboard_asset_payload
from .schemas import ChapterCreate, ChapterUpdate, DeleteFrameRequest, ReorderChaptersRequest, SeriesCreate, SeriesUpdate


def get_series_list(factory: UnitOfWorkFactory | None, actor: TrustedActor) -> list[dict[str, Any]]:
    uow = _open(factory)
    try:
        return [series_list_item(item) for item in uow.list_series_for_actor(actor)]
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def get_series(factory: UnitOfWorkFactory | None, actor: TrustedActor, series_id: str) -> dict[str, Any]:
    uow = _open(factory)
    try:
        uow.require_access(actor, series_id)
        record = uow.load_series(series_id)
        if record is None:
            raise SeriesDataNotFound("剧集不存在")
        team_name, owner_name = uow.load_detail_names(record)
        return series_detail(record, uow.resolve_prompt(record.style_prompt_id), team_name=team_name, owner_name=owner_name)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def create_series(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    request: SeriesCreate,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        record = uow.create_series(actor, request.model_dump())
        uow.commit()
        team_name, owner_name = uow.load_detail_names(record)
        return series_detail(record, uow.resolve_prompt(record.style_prompt_id), team_name=team_name, owner_name=owner_name)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def update_series(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    request: SeriesUpdate,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        uow.require_access(actor, series_id)
        record = uow.update_series(series_id, request.model_dump())
        uow.commit()
        team_name, owner_name = uow.load_detail_names(record)
        return series_detail(record, uow.resolve_prompt(record.style_prompt_id), team_name=team_name, owner_name=owner_name)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def delete_series(factory: UnitOfWorkFactory | None, actor: TrustedActor, series_id: str) -> None:
    uow = _open(factory)
    try:
        uow.delete_series(actor, series_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def get_chapters(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    *,
    now: Clock | None = None,
) -> list[dict[str, Any]]:
    uow = _open(factory)
    try:
        current = (now or _utcnow)()
        return [chapter_payload(item["record"], item["lock"]) for item in uow.list_chapters(series_id, actor, current)]
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def reorder_chapters(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    request: ReorderChaptersRequest,
) -> dict[str, str]:
    uow = _open(factory)
    try:
        uow.require_access(actor, series_id)
        for item in request.chapters:
            uow.reorder_chapter(series_id, item)
        uow.commit()
        return {"message": "排序已更新"}
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def create_chapter(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    request: ChapterCreate,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        uow.require_access(actor, series_id)
        order = request.order
        if order == 0:
            order = (uow.max_chapter_order(series_id) or 0) + 1
        content = chapter_create_content(
            [frame.model_dump() for frame in request.content] if request.content is not None else None,
            request.raw_content,
        )
        chapter = uow.create_chapter(series_id, request.title, content, order)
        uow.commit()
        chapter = uow.sync_storyboard_assets(chapter.id, series_id) or chapter
        uow.commit()
        return chapter_payload(chapter)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def update_chapter(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    request: ChapterUpdate,
    *,
    now: Clock | None = None,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        chapter = uow.load_chapter(chapter_id)
        if chapter is None:
            raise SeriesDataNotFound("章节不存在")
        uow.require_access(actor, chapter.series_id)
        old_frames = parse_source_frames(chapter.content)
        values = request.model_dump()
        chapter = uow.update_chapter_fields(chapter, values, actor, (now or _utcnow)())
        uow.commit()
        if request.content is not None:
            chapter = uow.sync_storyboard_assets(chapter_id, chapter.series_id) or chapter
            uow.commit()
            uow.update_chat_mapping(chapter_id, old_frames, request.content)
            uow.commit()
            chapter = uow.load_chapter(chapter_id) or chapter
        return chapter_payload(chapter)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def delete_frame(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    request: DeleteFrameRequest,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        chapter = uow.load_chapter(chapter_id)
        if chapter is None:
            raise SeriesDataNotFound("章节不存在")
        uow.require_access(actor, chapter.series_id)
        frames = parse_source_frames(chapter.content)
        if len(frames) <= 1:
            raise SeriesDataBadRequest("至少保留一个分镜")
        if request.frame_index < 0 or request.frame_index >= len(frames):
            raise SeriesDataBadRequest("分镜索引无效")
        frames.pop(request.frame_index)
        chapter = uow.delete_frame_first_phase(chapter, actor, request.frame_index, frames)
        uow.commit()
        chapter = uow.sync_storyboard_assets(chapter_id, chapter.series_id) or chapter
        uow.commit()
        return chapter_payload(uow.load_chapter(chapter_id) or chapter)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def delete_chapter(factory: UnitOfWorkFactory | None, actor: TrustedActor, chapter_id: str) -> None:
    uow = _open(factory)
    try:
        chapter = uow.load_chapter(chapter_id)
        if chapter is None:
            raise SeriesDataNotFound("章节不存在")
        uow.require_access(actor, chapter.series_id)
        series_id, refs = uow.delete_chapter(chapter)
        uow.commit()
        if any(refs.values()):
            uow.cleanup_orphans(series_id, refs)
            uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def get_storyboard_assets(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    chapter_id: str | None = None,
) -> list[dict[str, Any]]:
    uow = _open(factory)
    try:
        uow.require_access(actor, series_id)
        return [storyboard_asset_payload(asset) for asset in uow.list_storyboard_assets(series_id, chapter_id)]
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def _open(factory: UnitOfWorkFactory | None) -> SeriesDataUnitOfWork:
    if factory is None:
        raise SeriesDataUnavailable()
    uow = factory()
    try:
        uow.ensure_clean()
    except Exception:
        try:
            uow.close()
        except Exception:
            pass
        raise
    return uow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
