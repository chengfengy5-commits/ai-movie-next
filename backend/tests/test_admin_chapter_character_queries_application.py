from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from haoai_backend.admin_chapter_character_queries import application
from haoai_backend.admin_chapter_character_queries.domain import (
    AdminChapterCharacterActor,
    ChapterRecord,
    CharacterRecord,
)
from haoai_backend.admin_chapter_character_queries.errors import (
    AdminChapterCharacterForbidden,
    AdminChapterCharacterQueriesUnavailable,
    ChapterNotFound,
    CharacterNotFound,
)


NOW = datetime(2026, 10, 13, 12, 0, 0)
ADMIN = AdminChapterCharacterActor("admin", True)
MEMBER = AdminChapterCharacterActor("member", False)


class FakeUnitOfWork:
    def __init__(self, *, primary_error: BaseException | None = None) -> None:
        self.events: list[str] = []
        self.primary_error = primary_error
        self.rollback_error: BaseException | None = None
        self.close_error: BaseException | None = None

    def list_chapters(self, **kwargs: Any):
        self.events.append("list_chapters")
        self.list_args = kwargs
        return (
            (ChapterRecord("c1", "s1", "Chapter", 1, NOW),),
            11,
        )

    def get_chapter(self, chapter_id: str):
        self.events.append("get_chapter")
        return None

    def list_characters(self, **kwargs: Any):
        self.events.append("list_characters")
        self.list_args = kwargs
        return (
            (
                CharacterRecord(
                    "p1", "s1", "Hero", None, None, None, None, None, None, NOW
                ),
            ),
            1,
        )

    def get_character(self, character_id: str):
        self.events.append("get_character")
        return None

    def rollback(self) -> None:
        self.events.append("rollback")
        if self.rollback_error:
            raise self.rollback_error

    def close(self) -> None:
        self.events.append("close")
        if self.close_error:
            raise self.close_error


def test_list_chapters_preserves_legacy_defaults_projection_and_read_only_lifecycle() -> None:
    unit = FakeUnitOfWork()
    result = application.list_chapters(lambda: unit, ADMIN)

    assert unit.list_args == {
        "sort_field": "created_at",
        "sort_order": "desc",
        "skip": 0,
        "limit": 10,
    }
    assert result == {
        "data": [
            {
                "id": "c1",
                "series_id": "s1",
                "title": "Chapter",
                "order": 1,
                "created_at": NOW,
            }
        ],
        "total": 11,
    }
    assert unit.events == ["list_chapters", "close"]
    assert "commit" not in unit.events


@pytest.mark.parametrize(
    ("call", "factory"),
    [
        (lambda factory: application.list_chapters(factory, MEMBER), lambda: pytest.fail("opened")),
        (lambda factory: application.list_characters(factory, MEMBER), lambda: pytest.fail("opened")),
    ],
)
def test_non_admin_is_rejected_before_opening_business_unit(call, factory) -> None:
    with pytest.raises(AdminChapterCharacterForbidden):
        call(factory)


def test_missing_factory_and_missing_records_have_expected_errors() -> None:
    with pytest.raises(AdminChapterCharacterQueriesUnavailable):
        application.list_chapters(None, ADMIN)
    with pytest.raises(ChapterNotFound):
        application.get_chapter(lambda: FakeUnitOfWork(), ADMIN, "missing")
    with pytest.raises(CharacterNotFound):
        application.get_character(lambda: FakeUnitOfWork(), ADMIN, "missing")


def test_primary_read_error_survives_rollback_and_close_errors() -> None:
    primary = RuntimeError("read failed")
    unit = FakeUnitOfWork(primary_error=primary)

    def fail_read(**kwargs: Any):
        unit.events.append("list_chapters")
        raise primary

    unit.list_chapters = fail_read
    unit.rollback_error = RuntimeError("rollback failed")
    unit.close_error = RuntimeError("close failed")

    with pytest.raises(RuntimeError, match="read failed") as caught:
        application.list_chapters(lambda: unit, ADMIN)
    assert caught.value is primary
    assert unit.events == ["list_chapters", "rollback", "close"]


def test_successful_close_error_is_not_suppressed() -> None:
    unit = FakeUnitOfWork()
    unit.close_error = RuntimeError("close failed")

    with pytest.raises(RuntimeError, match="close failed"):
        application.list_characters(lambda: unit, ADMIN)
    assert unit.events == ["list_characters", "close"]
