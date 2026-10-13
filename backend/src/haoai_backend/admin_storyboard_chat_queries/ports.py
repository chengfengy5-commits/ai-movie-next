"""Persistence ports for administrator storyboard and chat queries."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from .domain import ChatMessageRecord, StoryboardAssetRecord


class AdminStoryboardChatQueriesUnitOfWork(Protocol):
    def list_storyboard_assets(
        self,
        *,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[StoryboardAssetRecord], int]: ...

    def get_storyboard_asset(self, asset_id: str) -> StoryboardAssetRecord | None: ...

    def list_chat_messages(
        self,
        *,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[ChatMessageRecord], int]: ...

    def get_chat_message(self, message_id: str) -> ChatMessageRecord | None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AdminStoryboardChatQueriesUnitOfWork]
