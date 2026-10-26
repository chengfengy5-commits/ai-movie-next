"""Framework-independent chapter canvas use cases."""

from __future__ import annotations

from typing import Any

from haoai_backend.shared.identity import TrustedActor

from .domain import (
    CanvasSaveGuard,
    empty_canvas_document,
    exceeds_document_limit,
    serialize_document,
)
from .errors import (
    CanvasDataBadRequest,
    CanvasDataConflict,
    CanvasDataNotFound,
    CanvasDataUnavailable,
)
from .ports import UnitOfWorkFactory
from .presentation import canvas_document_payload, guarded_canvas_document_payload


DOCUMENT_TOO_LARGE = "画布文档过大，请精简节点后重试"
VERSION_CONFLICT = "画布已被其他成员更新，请刷新后重试"
CHAPTER_NOT_FOUND = "章节不存在"


def _open(factory: UnitOfWorkFactory | None):
    if factory is None:
        raise CanvasDataUnavailable()
    uow = factory()
    try:
        uow.ensure_clean()
    except BaseException:
        try:
            uow.close()
        except BaseException:
            pass
        raise
    return uow


def _rollback(uow: Any) -> None:
    try:
        uow.rollback()
    except BaseException:
        pass


def get_chapter_canvas(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    *,
    guarded: bool = False,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        chapter = uow.load_chapter(chapter_id)
        if chapter is None:
            raise CanvasDataNotFound(CHAPTER_NOT_FOUND)
        uow.require_series_access(actor, chapter.series_id)
        document = uow.load_canvas_document(chapter_id)
        if document is None:
            payload = empty_canvas_document(chapter_id)
        else:
            payload = canvas_document_payload(document)
        if guarded:
            payload["save_capability"] = "guarded-v1"
        return payload
    except BaseException:
        _rollback(uow)
        raise
    finally:
        uow.close()


def save_chapter_canvas(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    document_json: dict[str, Any],
    version: int | None = None,
    save_guard: CanvasSaveGuard | None = None,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        chapter = uow.load_chapter(chapter_id)
        if chapter is None:
            raise CanvasDataNotFound(CHAPTER_NOT_FOUND)
        uow.require_series_access(actor, chapter.series_id)
        uow.refresh_chapter_lock(chapter_id, actor.user_id)

        serialized = serialize_document(document_json)
        if exceeds_document_limit(serialized):
            raise CanvasDataBadRequest(DOCUMENT_TOO_LARGE)

        if save_guard is not None:
            if save_guard.mode != "guarded-v1":
                raise CanvasDataBadRequest("画布保存模式无效")
            if version is not None and version != save_guard.version:
                raise CanvasDataBadRequest("画布版本参数不一致")
            record = uow.save_guarded_canvas_document(
                series_id=chapter.series_id,
                chapter_id=chapter_id,
                user_id=actor.user_id,
                document_json=serialized,
                guard=save_guard,
            )
            if record is None:
                raise CanvasDataConflict(VERSION_CONFLICT)
            uow.commit()
            return guarded_canvas_document_payload(record)

        current = uow.load_canvas_document(chapter_id)
        if current is not None and version is not None and version != current.version:
            raise CanvasDataConflict(VERSION_CONFLICT)

        if current is None:
            document_id = uow.insert_canvas_document(
                series_id=chapter.series_id,
                chapter_id=chapter_id,
                user_id=actor.user_id,
                document_json=serialized,
            )
        else:
            document_id = current.id
            stored_document = current.document_json
            uow.update_canvas_document(
                document_id,
                user_id=actor.user_id,
                document_json=serialized,
                update_document_json=stored_document != serialized,
                update_updated_by=current.updated_by != actor.user_id,
            )

        uow.commit()
        refreshed = uow.refresh_canvas_document(document_id)
        if refreshed is None:
            raise RuntimeError("committed canvas document could not be read back")
        return canvas_document_payload(
            refreshed,
            document_json=document_json,
            echo_request=True,
        )
    except BaseException:
        _rollback(uow)
        raise
    finally:
        uow.close()
