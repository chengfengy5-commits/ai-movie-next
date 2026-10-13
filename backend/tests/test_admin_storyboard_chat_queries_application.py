from __future__ import annotations

from datetime import datetime

import pytest

from haoai_backend.admin_storyboard_chat_queries import application
from haoai_backend.admin_storyboard_chat_queries.domain import (
    AdminStoryboardChatQueriesActor,
    ChatMessageRecord,
    StoryboardAssetRecord,
)


NOW = datetime(2026, 10, 13, 12, 0, 0)
ACTOR = AdminStoryboardChatQueriesActor(user_id="admin", is_superuser=True)


class FakeUnitOfWork:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.events: list[str] = []

    def list_storyboard_assets(self, **_kwargs):
        self.events.append("list_storyboard_assets")
        if self.failure is not None:
            raise self.failure
        return (
            StoryboardAssetRecord(
                id="asset-1",
                series_id="series-1",
                chapter_id="chapter-1",
                frame_index=3,
                name="关键帧",
                description=None,
                image_url="https://example.test/frame.png",
                created_at=NOW,
            ),
        ), 1

    def get_storyboard_asset(self, asset_id: str):
        self.events.append(f"get_storyboard_asset:{asset_id}")
        return None

    def list_chat_messages(self, **_kwargs):
        self.events.append("list_chat_messages")
        # A direct record fixture covers defensive NULL behavior without changing
        # the legacy database column, whose schema is NOT NULL.
        return (
            ChatMessageRecord(
                id="message-1",
                chapter_id="chapter-1",
                role="assistant",
                chat_mode="chat",
                content="汉" * 201,
                created_at=NOW,
            ),
            ChatMessageRecord(
                id="message-null",
                chapter_id="chapter-2",
                role="user",
                chat_mode="chat",
                content=None,
                created_at=NOW,
            ),
        ), 2

    def get_chat_message(self, message_id: str):
        self.events.append(f"get_chat_message:{message_id}")
        return ChatMessageRecord(
            id=message_id,
            chapter_id="chapter-1",
            role="assistant",
            chat_mode="chat",
            content=None,
            created_at=NOW,
        )

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.events.append("close")


def test_use_cases_keep_legacy_projection_and_unicode_preview() -> None:
    unit = FakeUnitOfWork()
    factory_calls = 0

    def factory() -> FakeUnitOfWork:
        nonlocal factory_calls
        factory_calls += 1
        return unit

    assets = application.list_storyboard_assets(factory, ACTOR)
    assert assets == {
        "data": [
            {
                "id": "asset-1",
                "series_id": "series-1",
                "chapter_id": "chapter-1",
                "frame_index": 3,
                "name": "关键帧",
                "description": None,
                "image_url": "https://example.test/frame.png",
                "created_at": NOW,
            }
        ],
        "total": 1,
    }

    messages = application.list_chat_messages(factory, ACTOR)
    assert messages["total"] == 2
    assert messages["data"][0]["content"] == "汉" * 200
    assert messages["data"][1]["content"] == ""

    detail = application.get_chat_message(factory, ACTOR, "message-full")
    assert detail["content"] is None
    assert factory_calls == 3
    assert unit.events.count("close") == 3


def test_query_failure_rolls_back_and_closes_best_effort_preserving_exception() -> None:
    primary = RuntimeError("query failed")

    class FailingCleanupUnit(FakeUnitOfWork):
        def rollback(self) -> None:
            self.events.append("rollback")
            raise RuntimeError("rollback failed")

        def close(self) -> None:
            self.events.append("close")
            raise RuntimeError("close failed")

    unit = FailingCleanupUnit(failure=primary)
    with pytest.raises(RuntimeError) as caught:
        application.list_storyboard_assets(lambda: unit, ACTOR)

    assert caught.value is primary
    assert unit.events == ["list_storyboard_assets", "rollback", "close"]


def test_successful_query_propagates_close_failure() -> None:
    close_failure = RuntimeError("close failed")

    class CloseFailingUnit(FakeUnitOfWork):
        def close(self) -> None:
            self.events.append("close")
            raise close_failure

    unit = CloseFailingUnit()
    with pytest.raises(RuntimeError) as caught:
        application.list_storyboard_assets(lambda: unit, ACTOR)

    assert caught.value is close_failure
    assert unit.events == ["list_storyboard_assets", "close"]
