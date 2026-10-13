"""SQLAlchemy adapter for legacy storyboard_assets and chat_messages reads."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .domain import ChatMessageRecord, StoryboardAssetRecord
from .ports import AdminStoryboardChatQueriesUnitOfWork
from .tables import ChatMessageRow, StoryboardAssetRow


class SqlAlchemyAdminStoryboardChatQueriesUnitOfWork(
    AdminStoryboardChatQueriesUnitOfWork
):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_storyboard_assets(
        self,
        *,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[StoryboardAssetRecord], int]:
        query = self._session.query(StoryboardAssetRow)
        total = query.count()
        sort_column = getattr(StoryboardAssetRow, sort_field, StoryboardAssetRow.id)
        ordering = desc(sort_column) if sort_order == "desc" else asc(sort_column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        return tuple(_storyboard_asset_record(row) for row in rows), total

    def get_storyboard_asset(self, asset_id: str) -> StoryboardAssetRecord | None:
        row = (
            self._session.query(StoryboardAssetRow)
            .filter(StoryboardAssetRow.id == asset_id)
            .first()
        )
        return _storyboard_asset_record(row) if row is not None else None

    def list_chat_messages(
        self,
        *,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[ChatMessageRecord], int]:
        query = self._session.query(ChatMessageRow)
        total = query.count()
        sort_column = getattr(ChatMessageRow, sort_field, ChatMessageRow.id)
        ordering = desc(sort_column) if sort_order == "desc" else asc(sort_column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        return tuple(_chat_message_record(row) for row in rows), total

    def get_chat_message(self, message_id: str) -> ChatMessageRecord | None:
        row = (
            self._session.query(ChatMessageRow)
            .filter(ChatMessageRow.id == message_id)
            .first()
        )
        return _chat_message_record(row) if row is not None else None

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def _storyboard_asset_record(row: Any) -> StoryboardAssetRecord:
    return StoryboardAssetRecord(
        id=row.id,
        series_id=row.series_id,
        chapter_id=row.chapter_id,
        frame_index=row.frame_index,
        name=row.name,
        description=row.description,
        image_url=row.image_url,
        created_at=row.created_at,
    )


def _chat_message_record(row: Any) -> ChatMessageRecord:
    return ChatMessageRecord(
        id=row.id,
        chapter_id=row.chapter_id,
        role=row.role,
        chat_mode=row.chat_mode,
        content=row.content,
        created_at=row.created_at,
    )
