"""Application ports for chat data and its request-scoped transaction."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from haoai_backend.shared.identity import TrustedActor

from .domain import ChatMessageRecord, NewChatMessage


class ChatDataUnitOfWork(Protocol):
    def ensure_clean(self) -> None: ...

    def load_chapter_series_id(self, chapter_id: str) -> str | None: ...

    def require_access(self, actor: TrustedActor, series_id: str) -> None: ...

    def list_chat_messages(
        self,
        chapter_id: str,
        chat_mode: str,
        frame_index: int | None,
    ) -> list[ChatMessageRecord]: ...

    def list_asset_chat_messages(
        self,
        chapter_id: str,
        asset_type: str,
        asset_id: str,
        chat_mode: str,
    ) -> list[ChatMessageRecord]: ...

    def load_chat_message(
        self,
        message_id: str,
        chapter_id: str,
    ) -> ChatMessageRecord | None: ...

    def create_chat_message(self, request: NewChatMessage) -> str: ...

    def refresh_chapter_lock(self, chapter_id: str, user_id: str) -> None: ...

    def update_chat_message_content(
        self,
        message_id: str,
        chapter_id: str,
        content: str,
    ) -> None: ...

    def delete_chat_messages(
        self,
        chapter_id: str,
        frame_index: int | None,
    ) -> None: ...

    def delete_single_chat_message(self, message_id: str, chapter_id: str) -> None: ...

    def delete_single_asset_chat_message(self, message_id: str, chapter_id: str) -> None: ...

    def chapter_statistics(self, chapter_id: str) -> Sequence[Mapping[str, Any]]: ...

    def series_statistics(self, series_id: str) -> Sequence[Mapping[str, Any]]: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], ChatDataUnitOfWork]
