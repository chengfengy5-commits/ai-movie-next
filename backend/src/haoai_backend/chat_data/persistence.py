"""SQLAlchemy Core adapter for chat messages and read-only task statistics."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.shared.identity import TrustedActor

from .domain import ChatMessageRecord, NewChatMessage
from .errors import ChatDataForbidden, ChatDataNotFound
from .ports import ChatDataUnitOfWork
from .tables import ai_tasks, chapter_locks, chapters, chat_messages, system_configs


class SqlAlchemyChatDataUnitOfWork(ChatDataUnitOfWork):
    """One request-scoped business Session shared by access and chat queries."""

    def __init__(
        self,
        session: Session,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._session = session
        self._now = now or _utcnow
        self._new_id = new_id or (lambda: str(uuid4()))

    def ensure_clean(self) -> None:
        if self._session.new or self._session.dirty or self._session.deleted:
            raise RuntimeError("chat-data requires a clean request-scoped Session")

    def load_chapter_series_id(self, chapter_id: str) -> str | None:
        return self._session.execute(
            select(chapters.c.series_id).where(chapters.c.id == chapter_id)
        ).scalar_one_or_none()

    def require_access(self, actor: TrustedActor, series_id: str) -> None:
        try:
            require_series_access(
                SqlAlchemySeriesAccessReader(self._session),
                ActorIdentity(actor.user_id),
                series_id,
            )
        except SeriesNotFound as exc:
            raise ChatDataNotFound("剧集不存在") from exc
        except SeriesAccessDenied as exc:
            raise ChatDataForbidden(exc.detail) from exc

    def list_chat_messages(
        self,
        chapter_id: str,
        chat_mode: str,
        frame_index: int | None,
    ) -> list[ChatMessageRecord]:
        statement = select(chat_messages).where(
            chat_messages.c.chapter_id == chapter_id,
            chat_messages.c.chat_mode == chat_mode,
        )
        if frame_index is not None:
            statement = statement.where(chat_messages.c.frame_index == frame_index)
        rows = self._session.execute(
            statement.order_by(chat_messages.c.created_at.asc())
        ).mappings().all()
        return [_message_record(row) for row in rows]

    def list_asset_chat_messages(
        self,
        chapter_id: str,
        asset_type: str,
        asset_id: str,
        chat_mode: str,
    ) -> list[ChatMessageRecord]:
        rows = self._session.execute(
            select(chat_messages)
            .where(
                chat_messages.c.chapter_id == chapter_id,
                chat_messages.c.asset_type == asset_type,
                chat_messages.c.asset_id == asset_id,
                chat_messages.c.chat_mode == chat_mode,
            )
            .order_by(chat_messages.c.created_at.asc())
        ).mappings().all()
        return [_message_record(row) for row in rows]

    def load_chat_message(
        self,
        message_id: str,
        chapter_id: str,
    ) -> ChatMessageRecord | None:
        row = self._session.execute(
            select(chat_messages).where(
                chat_messages.c.id == message_id,
                chat_messages.c.chapter_id == chapter_id,
            )
        ).mappings().first()
        return _message_record(row) if row is not None else None

    def create_chat_message(self, request: NewChatMessage) -> str:
        message_id = self._new_id()
        self._session.execute(
            insert(chat_messages).values(
                id=message_id,
                chapter_id=request.chapter_id,
                frame_index=request.frame_index,
                asset_type=request.asset_type,
                asset_id=request.asset_id,
                chat_mode=request.chat_mode,
                role=request.role,
                content=request.content,
                model_name=request.model_name,
                created_at=self._now(),
            )
        )
        return message_id

    def refresh_chapter_lock(self, chapter_id: str, user_id: str) -> None:
        lock = self._session.execute(
            select(chapter_locks).where(chapter_locks.c.chapter_id == chapter_id)
        ).mappings().first()
        if lock is None or lock["user_id"] != user_id:
            return

        configured_minutes = self._session.execute(
            select(system_configs.c.chapter_lock_idle_minutes).limit(1)
        ).scalar_one_or_none()
        idle_minutes = configured_minutes or 15
        now = self._now()
        result = self._session.execute(
            update(chapter_locks)
            .where(chapter_locks.c.id == lock["id"])
            .values(
                last_active_at=now,
                expires_at=_add_minutes(now, idle_minutes),
            )
        )
        if result.rowcount != 1:
            raise StaleDataError("chapter lock disappeared during chat creation")

    def update_chat_message_content(
        self,
        message_id: str,
        chapter_id: str,
        content: str,
    ) -> None:
        result = self._session.execute(
            update(chat_messages)
            .where(
                chat_messages.c.id == message_id,
                chat_messages.c.chapter_id == chapter_id,
            )
            .values(content=content)
        )
        if result.rowcount != 1:
            raise StaleDataError("chat message disappeared during update")

    def delete_chat_messages(
        self,
        chapter_id: str,
        frame_index: int | None,
    ) -> None:
        statement = delete(chat_messages).where(chat_messages.c.chapter_id == chapter_id)
        if frame_index is not None:
            statement = statement.where(chat_messages.c.frame_index == frame_index)
        self._session.execute(statement)

    def delete_single_chat_message(self, message_id: str, chapter_id: str) -> None:
        self._session.execute(
            delete(chat_messages).where(
                chat_messages.c.id == message_id,
                chat_messages.c.chapter_id == chapter_id,
                chat_messages.c.asset_type.is_(None),
            )
        )

    def delete_single_asset_chat_message(self, message_id: str, chapter_id: str) -> None:
        self._session.execute(
            delete(chat_messages).where(
                chat_messages.c.id == message_id,
                chat_messages.c.chapter_id == chapter_id,
                chat_messages.c.asset_type.is_not(None),
            )
        )

    def chapter_statistics(self, chapter_id: str) -> Sequence[dict[str, Any]]:
        statement = (
            select(
                ai_tasks.c.model_name,
                ai_tasks.c.status,
                func.count(ai_tasks.c.id).label("calls"),
                func.coalesce(func.sum(ai_tasks.c.credit_cost), 0).label("credits"),
            )
            .select_from(ai_tasks.join(chat_messages, ai_tasks.c.message_id == chat_messages.c.id))
            .where(
                chat_messages.c.chapter_id == chapter_id,
                ai_tasks.c.status.in_(("completed", "failed")),
            )
            .group_by(ai_tasks.c.model_name, ai_tasks.c.status)
            .order_by(func.count(ai_tasks.c.id).desc())
        )
        return self._session.execute(statement).mappings().all()

    def series_statistics(self, series_id: str) -> Sequence[dict[str, Any]]:
        statement = (
            select(
                ai_tasks.c.model_name,
                ai_tasks.c.status,
                func.count(ai_tasks.c.id).label("calls"),
                func.coalesce(func.sum(ai_tasks.c.credit_cost), 0).label("credits"),
            )
            .select_from(
                ai_tasks.join(chat_messages, ai_tasks.c.message_id == chat_messages.c.id)
                .join(chapters, chat_messages.c.chapter_id == chapters.c.id)
            )
            .where(
                chapters.c.series_id == series_id,
                ai_tasks.c.status.in_(("completed", "failed")),
            )
            .group_by(ai_tasks.c.model_name, ai_tasks.c.status)
            .order_by(func.count(ai_tasks.c.id).desc())
        )
        return self._session.execute(statement).mappings().all()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def _message_record(row: Any) -> ChatMessageRecord:
    return ChatMessageRecord(
        id=row["id"],
        chapter_id=row["chapter_id"],
        frame_index=row["frame_index"],
        asset_type=row["asset_type"],
        asset_id=row["asset_id"],
        chat_mode=row["chat_mode"],
        role=row["role"],
        content=row["content"],
        model_name=row["model_name"],
        created_at=row["created_at"],
    )


def _add_minutes(value: datetime, minutes: int) -> datetime:
    return value + timedelta(minutes=minutes)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
