from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from haoai_backend.chat_data import application
from haoai_backend.chat_data.domain import ChatMessageRecord, NewChatMessage
from haoai_backend.chat_data.errors import ChatDataNotFound
from haoai_backend.shared.identity import TrustedActor


class RecordingUnitOfWork:
    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.messages: dict[tuple[str, str], ChatMessageRecord] = {}

    def ensure_clean(self) -> None:
        self.events.append(("ensure_clean",))

    def load_chapter_series_id(self, chapter_id: str) -> str | None:
        self.events.append(("chapter", chapter_id))
        return "series-a" if chapter_id in {"chapter-a", "chapter-b"} else None

    def require_access(self, actor: TrustedActor, series_id: str) -> None:
        self.events.append(("access", actor.user_id, series_id))

    def list_chat_messages(self, chapter_id: str, chat_mode: str, frame_index: int | None):
        self.events.append(("list", chapter_id, chat_mode, frame_index))
        return []

    def list_asset_chat_messages(self, chapter_id: str, asset_type: str, asset_id: str, chat_mode: str):
        self.events.append(("list_asset", chapter_id, asset_type, asset_id, chat_mode))
        return []

    def load_chat_message(self, message_id: str, chapter_id: str):
        self.events.append(("load_message", message_id, chapter_id))
        return self.messages.get((message_id, chapter_id))

    def create_chat_message(self, request: NewChatMessage) -> str:
        self.events.append(("insert", request.chapter_id))
        record = ChatMessageRecord(
            id="message-new",
            chapter_id=request.chapter_id,
            frame_index=request.frame_index,
            asset_type=request.asset_type,
            asset_id=request.asset_id,
            chat_mode=request.chat_mode,
            role=request.role,
            content=request.content,
            model_name=request.model_name,
            created_at=datetime(2026, 10, 9, 12, 0),
        )
        self.messages[("message-new", request.chapter_id)] = record
        return record.id

    def refresh_chapter_lock(self, chapter_id: str, user_id: str) -> None:
        self.events.append(("refresh_lock", chapter_id, user_id))

    def update_chat_message_content(self, message_id: str, chapter_id: str, content: str) -> None:
        self.events.append(("update", message_id, chapter_id, content))
        current = self.messages[(message_id, chapter_id)]
        self.messages[(message_id, chapter_id)] = replace(current, content=content)

    def delete_chat_messages(self, chapter_id: str, frame_index: int | None) -> None:
        self.events.append(("delete_many", chapter_id, frame_index))

    def delete_single_chat_message(self, message_id: str, chapter_id: str) -> None:
        self.events.append(("delete_single", message_id, chapter_id))

    def delete_single_asset_chat_message(self, message_id: str, chapter_id: str) -> None:
        self.events.append(("delete_single_asset", message_id, chapter_id))

    def chapter_statistics(self, chapter_id: str):
        self.events.append(("chapter_stats", chapter_id))
        return []

    def series_statistics(self, series_id: str):
        self.events.append(("series_stats", series_id))
        return []

    def commit(self) -> None:
        self.events.append(("commit",))

    def rollback(self) -> None:
        self.events.append(("rollback",))

    def close(self) -> None:
        self.events.append(("close",))


def test_create_checks_path_chapter_then_access_inserts_body_chapter_and_reads_back() -> None:
    uow = RecordingUnitOfWork()
    request = NewChatMessage(
        chapter_id="chapter-b",
        frame_index=-2,
        asset_type=None,
        asset_id=None,
        chat_mode="chat",
        role="user",
        content="text",
        model_name=None,
    )

    result = application.create_chat_message(
        lambda: uow,
        TrustedActor("user-a"),
        "chapter-a",
        request,
    )

    assert result["chapter_id"] == "chapter-b"
    assert uow.events[:5] == [
        ("ensure_clean",),
        ("chapter", "chapter-a"),
        ("access", "user-a", "series-a"),
        ("insert", "chapter-b"),
        ("refresh_lock", "chapter-a", "user-a"),
    ]
    assert uow.events[5:] == [
        ("commit",),
        ("load_message", "message-new", "chapter-b"),
        ("close",),
    ]


def test_missing_chapter_precedes_access_and_closes_request_uow() -> None:
    uow = RecordingUnitOfWork()

    with pytest.raises(ChatDataNotFound, match="章节不存在"):
        application.get_chat_messages(lambda: uow, TrustedActor("user-a"), "missing")

    assert ("access", "user-a", "series-a") not in uow.events
    assert ("rollback",) in uow.events
    assert uow.events[-1] == ("close",)


def test_update_missing_message_precedes_conditional_chapter_access() -> None:
    uow = RecordingUnitOfWork()

    with pytest.raises(ChatDataNotFound, match="消息不存在"):
        application.update_chat_message(
            lambda: uow,
            TrustedActor("user-a"),
            "missing-chapter",
            "missing-message",
            "replacement",
        )

    assert uow.events[:2] == [
        ("ensure_clean",),
        ("load_message", "missing-message", "missing-chapter"),
    ]
    assert not any(event[0] == "chapter" for event in uow.events)
    assert ("rollback",) in uow.events
