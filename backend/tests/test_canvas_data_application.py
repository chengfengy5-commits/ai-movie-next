from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from haoai_backend.canvas_data import application
from haoai_backend.canvas_data.domain import CanvasDocumentRecord, ChapterRecord
from haoai_backend.canvas_data.errors import CanvasDataBadRequest, CanvasDataConflict, CanvasDataForbidden, CanvasDataNotFound
from haoai_backend.shared.identity import TrustedActor


class FakeUnitOfWork:
    def __init__(self, *, chapter=ChapterRecord("chapter-a", "series-a"), document=None) -> None:
        self.chapter = chapter
        self.document = document
        self.events: list[tuple[Any, ...]] = []
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: CanvasDocumentRecord | None = None

    def ensure_clean(self) -> None:
        self.events.append(("clean",))

    def load_chapter(self, chapter_id: str):
        self.events.append(("chapter", chapter_id))
        return self.chapter

    def require_series_access(self, actor, series_id: str) -> None:
        self.events.append(("access", actor.user_id, series_id))

    def refresh_chapter_lock(self, chapter_id: str, user_id: str) -> None:
        self.events.append(("lock", chapter_id, user_id))

    def load_canvas_document(self, chapter_id: str):
        self.events.append(("load-document", chapter_id))
        return self.document

    def insert_canvas_document(self, *, series_id, chapter_id, user_id, document_json):
        self.events.append(("insert", series_id, chapter_id, user_id, document_json))
        return "created-id"

    def update_canvas_document(
        self,
        document_id,
        *,
        version,
        user_id,
        document_json,
        update_document_json,
        update_updated_by,
    ):
        self.events.append((
            "update",
            document_id,
            version,
            user_id,
            document_json,
            update_document_json,
            update_updated_by,
        ))

    def refresh_canvas_document(self, document_id):
        self.events.append(("refresh", document_id))
        return self.refreshed

    def commit(self) -> None:
        self.commits += 1
        self.events.append(("commit",))

    def rollback(self) -> None:
        self.rollbacks += 1
        self.events.append(("rollback",))

    def close(self) -> None:
        self.closed = True
        self.events.append(("close",))


def record(version: int = 1, document_json: object = "{}") -> CanvasDocumentRecord:
    now = datetime(2026, 1, 1)
    return CanvasDocumentRecord("doc-a", "series-a", "chapter-a", version, document_json, "user-a", "user-a", now, now)


def test_get_checks_chapter_then_access_and_does_not_commit() -> None:
    uow = FakeUnitOfWork()
    result = application.get_chapter_canvas(lambda: uow, TrustedActor("user-a"), "chapter-a")

    assert result["document_json"]["viewport"] == {"x": 0, "y": 0, "zoom": 1}
    assert [event[0] for event in uow.events] == ["clean", "chapter", "access", "load-document", "close"]
    assert uow.commits == 0
    assert uow.closed


def test_missing_chapter_precedes_access_and_returns_not_found() -> None:
    uow = FakeUnitOfWork(chapter=None)

    with pytest.raises(CanvasDataNotFound):
        application.get_chapter_canvas(lambda: uow, TrustedActor("user-a"), "missing")

    assert [event[0] for event in uow.events] == ["clean", "chapter", "rollback", "close"]


def test_put_lock_and_size_checks_precede_version_conflict() -> None:
    uow = FakeUnitOfWork(document=record(version=9))
    oversized = {"text": "界" * 1_000_001}

    with pytest.raises(CanvasDataBadRequest):
        application.save_chapter_canvas(lambda: uow, TrustedActor("user-a"), "chapter-a", oversized, version=1)

    names = [event[0] for event in uow.events]
    assert names.index("access") < names.index("lock") < names.index("rollback")
    assert "load-document" not in names
    assert uow.rollbacks == 1


def test_put_version_conflict_rolls_back_lock_refresh() -> None:
    uow = FakeUnitOfWork(document=record(version=3))

    with pytest.raises(CanvasDataConflict):
        application.save_chapter_canvas(lambda: uow, TrustedActor("user-a"), "chapter-a", {"nodes": []}, version=2)

    assert [event[0] for event in uow.events] == ["clean", "chapter", "access", "lock", "load-document", "rollback", "close"]
    assert uow.commits == 0


def test_first_save_ignores_version_and_returns_refreshed_metadata_with_request_echo() -> None:
    uow = FakeUnitOfWork(document=None)
    now = datetime(2026, 2, 3)
    uow.refreshed = CanvasDocumentRecord("created-id", "series-a", "chapter-a", 1, '{"stored":true}', "user-a", "user-b", now, now)
    payload = {"version": 1, "title": "请求内容"}

    result = application.save_chapter_canvas(lambda: uow, TrustedActor("user-a"), "chapter-a", payload, version=999)

    insert_event = next(event for event in uow.events if event[0] == "insert")
    assert insert_event[1:] == ("series-a", "chapter-a", "user-a", '{"version": 1, "title": "请求内容"}')
    assert result["version"] == 1
    assert result["updated_by"] == "user-b"
    assert result["document_json"] is payload
    assert uow.commits == 1
    assert [event[0] for event in uow.events][-3:] == ["commit", "refresh", "close"]


def test_same_document_existing_save_still_increments_version_once() -> None:
    uow = FakeUnitOfWork(document=record(version=4, document_json='{"nodes": []}'))
    now = datetime(2026, 3, 4)
    uow.refreshed = CanvasDocumentRecord("doc-a", "series-a", "chapter-a", 5, '{"nodes": []}', "user-a", "user-a", now, now)
    payload = {"nodes": []}

    result = application.save_chapter_canvas(lambda: uow, TrustedActor("user-a"), "chapter-a", payload, version=4)

    update_event = next(event for event in uow.events if event[0] == "update")
    assert update_event[2] == 5
    assert update_event[5:] == (False, False)
    assert result["version"] == 5
    assert result["document_json"] is payload
    assert uow.commits == 1


def test_access_denial_rolls_back_without_document_lookup() -> None:
    class Denied(FakeUnitOfWork):
        def require_series_access(self, actor, series_id):
            self.events.append(("access", actor.user_id, series_id))
            raise CanvasDataForbidden("无权访问该剧集")

    uow = Denied()
    with pytest.raises(CanvasDataForbidden):
        application.get_chapter_canvas(lambda: uow, TrustedActor("outsider"), "chapter-a")
    assert "load-document" not in [event[0] for event in uow.events]
    assert uow.rollbacks == 1
