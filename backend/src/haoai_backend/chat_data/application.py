"""Use cases for the ten existing chat and AI statistics methods."""

from __future__ import annotations

from typing import Any

from haoai_backend.shared.identity import TrustedActor

from .domain import NewChatMessage
from .errors import ChatDataNotFound, ChatDataUnavailable
from .ports import ChatDataUnitOfWork, UnitOfWorkFactory
from .presentation import chat_message_payload
from .statistics import merge_model_statistics


def get_chat_messages(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    *,
    frame_index: int | None = None,
    chat_mode: str = "chat",
) -> list[dict[str, Any]]:
    uow = _open(factory)
    try:
        series_id = uow.load_chapter_series_id(chapter_id)
        if series_id is None:
            raise ChatDataNotFound("章节不存在")
        uow.require_access(actor, series_id)
        return [chat_message_payload(row) for row in uow.list_chat_messages(chapter_id, chat_mode, frame_index)]
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def create_chat_message(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    path_chapter_id: str,
    request: NewChatMessage,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        series_id = uow.load_chapter_series_id(path_chapter_id)
        if series_id is None:
            raise ChatDataNotFound("章节不存在")
        uow.require_access(actor, series_id)
        message_id = uow.create_chat_message(request)
        uow.refresh_chapter_lock(path_chapter_id, actor.user_id)
        uow.commit()
        return _readback(uow, message_id, request.chapter_id)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def update_chat_message(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    path_chapter_id: str,
    message_id: str,
    content: str,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        message = uow.load_chat_message(message_id, path_chapter_id)
        if message is None:
            raise ChatDataNotFound("消息不存在")
        series_id = uow.load_chapter_series_id(path_chapter_id)
        if series_id is not None:
            uow.require_access(actor, series_id)
        if message.content != content:
            uow.update_chat_message_content(message_id, path_chapter_id, content)
        uow.commit()
        return _readback(uow, message_id, path_chapter_id)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def get_asset_chat_messages(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    asset_type: str,
    asset_id: str,
    *,
    chat_mode: str = "chat",
) -> list[dict[str, Any]]:
    uow = _open(factory)
    try:
        series_id = uow.load_chapter_series_id(chapter_id)
        if series_id is not None:
            uow.require_access(actor, series_id)
        return [
            chat_message_payload(row)
            for row in uow.list_asset_chat_messages(chapter_id, asset_type, asset_id, chat_mode)
        ]
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def create_asset_chat_message(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    path_chapter_id: str,
    request: NewChatMessage,
) -> dict[str, Any]:
    return create_chat_message(factory, actor, path_chapter_id, request)


def delete_chat_messages(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    *,
    frame_index: int | None = None,
) -> None:
    uow = _open(factory)
    try:
        series_id = uow.load_chapter_series_id(chapter_id)
        if series_id is None:
            raise ChatDataNotFound("章节不存在")
        uow.require_access(actor, series_id)
        uow.delete_chat_messages(chapter_id, frame_index)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def delete_single_chat_message(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    message_id: str,
) -> None:
    uow = _open(factory)
    try:
        series_id = uow.load_chapter_series_id(chapter_id)
        if series_id is None:
            raise ChatDataNotFound("章节不存在")
        uow.require_access(actor, series_id)
        message = uow.load_chat_message(message_id, chapter_id)
        if message is None or message.asset_type is not None:
            raise ChatDataNotFound("消息不存在")
        uow.delete_single_chat_message(message_id, chapter_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def delete_single_asset_chat_message(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    message_id: str,
) -> None:
    uow = _open(factory)
    try:
        series_id = uow.load_chapter_series_id(chapter_id)
        if series_id is None:
            raise ChatDataNotFound("章节不存在")
        uow.require_access(actor, series_id)
        message = uow.load_chat_message(message_id, chapter_id)
        if message is None or message.asset_type is None:
            raise ChatDataNotFound("消息不存在")
        uow.delete_single_asset_chat_message(message_id, chapter_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def get_ai_stats(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
) -> dict[str, list[dict[str, Any]]]:
    uow = _open(factory)
    try:
        series_id = uow.load_chapter_series_id(chapter_id)
        if series_id is None:
            raise ChatDataNotFound("章节不存在")
        uow.require_access(actor, series_id)
        return {
            "chapter": merge_model_statistics(uow.chapter_statistics(chapter_id)),
            "series": merge_model_statistics(uow.series_statistics(series_id)),
        }
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def _readback(
    uow: ChatDataUnitOfWork,
    message_id: str,
    chapter_id: str,
) -> dict[str, Any]:
    record = uow.load_chat_message(message_id, chapter_id)
    if record is None:
        raise RuntimeError("chat message was not visible after commit")
    return chat_message_payload(record)


def _open(factory: UnitOfWorkFactory | None) -> ChatDataUnitOfWork:
    if factory is None:
        raise ChatDataUnavailable()
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
