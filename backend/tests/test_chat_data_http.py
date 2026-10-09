from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import event

from haoai_backend.app import create_app
from haoai_backend.chat_data.persistence import SqlAlchemyChatDataUnitOfWork
from haoai_backend.shared.identity import TrustedActor
from test_chat_data_persistence import ChatDatabase, create_database


@pytest.fixture
def database(tmp_path: Path):
    database = create_database(tmp_path / "chat-http.sqlite")
    yield database
    database.engine.dispose()


def build_app(database: ChatDatabase, *, session_counter: dict[str, int] | None = None):
    def make_session():
        if session_counter is not None:
            session_counter["count"] += 1
        return database.session_factory()

    return create_app(
        session_factory=make_session,
        resolve_actor=lambda request: TrustedActor(
            request.headers.get("x-test-user", "user-a")
        ),
    )


def request(app, method: str, path: str, *, user: str = "user-a", body: Any = None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://chat-data.test",
        ) as client:
            return await client.request(
                method,
                path,
                headers={"x-test-user": user},
                json=body,
            )
    return asyncio.run(send())


def test_factory_registers_exact_ten_chat_methods_and_fifty_four_total() -> None:
    app = create_app(
        session_factory=lambda: None,
        resolve_actor=lambda request: TrustedActor("user-a"),
    )
    paths = {
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }
    expected = {
        ("/api/chapters/{chapter_id}/chat-messages", "GET"),
        ("/api/chapters/{chapter_id}/chat-messages", "POST"),
        ("/api/chapters/{chapter_id}/chat-messages/{message_id}", "PUT"),
        ("/api/chapters/{chapter_id}/asset-chat-messages/{message_id}", "PUT"),
        ("/api/chapters/{chapter_id}/asset-chat-messages", "GET"),
        ("/api/chapters/{chapter_id}/asset-chat-messages", "POST"),
        ("/api/chapters/{chapter_id}/chat-messages", "DELETE"),
        ("/api/chapters/{chapter_id}/chat-messages/single/{message_id}", "DELETE"),
        ("/api/chapters/{chapter_id}/asset-chat-messages/single/{message_id}", "DELETE"),
        ("/api/chapters/{chapter_id}/ai-stats", "GET"),
    }

    expected_canvas = {
        ("/api/chapters/{chapter_id}/canvas", "GET"),
        ("/api/chapters/{chapter_id}/canvas", "PUT"),
    }
    expected_download_links = {
        ("/api/download", "GET"),
        ("/api/sign-download-urls", "POST"),
    }

    assert expected <= paths
    assert expected_canvas <= paths
    assert expected_download_links <= paths
    assert len(paths) == 56


def test_http_status_filters_body_chapter_and_no_asset_discriminator(database: ChatDatabase) -> None:
    database.seed_message(
        "asset-message",
        asset_type="",
        asset_id="asset-a",
        content="old",
    )
    app = build_app(database)

    listed = request(app, "GET", "/api/chapters/chapter-a/chat-messages")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == ["asset-message"]

    updated = request(
        app,
        "PUT",
        "/api/chapters/chapter-a/chat-messages/asset-message",
        body={"content": "new"},
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "new"
    assert updated.json()["asset_type"] == ""

    created = request(
        app,
        "POST",
        "/api/chapters/chapter-a/chat-messages",
        body={
            "chapter_id": "chapter-b",
            "frame_index": -1,
            "role": "assistant",
            "content": "body target",
        },
    )
    assert created.status_code == 201
    assert created.json()["chapter_id"] == "chapter-b"
    assert created.json()["frame_index"] == -1

    asset_created = request(
        app,
        "POST",
        "/api/chapters/chapter-a/asset-chat-messages",
        body={
            "chapter_id": "chapter-b",
            "asset_type": "character",
            "asset_id": "asset-b",
            "role": "assistant",
            "content": "asset body target",
            "frame_index": 9,
        },
    )
    assert asset_created.status_code == 201
    assert asset_created.json()["frame_index"] is None

    asset_list = request(
        app,
        "GET",
        "/api/chapters/chapter-b/asset-chat-messages?asset_type=character&asset_id=asset-b",
        user="user-b",
    )
    assert asset_list.status_code == 200
    assert len(asset_list.json()) == 1

    stats = request(app, "GET", "/api/chapters/chapter-a/ai-stats")
    assert stats.status_code == 200
    assert stats.json() == {"chapter": [], "series": []}

    asset_updated = request(
        app,
        "PUT",
        f"/api/chapters/chapter-b/asset-chat-messages/{asset_created.json()['id']}",
        user="user-b",
        body={"content": "asset changed"},
    )
    assert asset_updated.status_code == 200
    assert asset_updated.json()["content"] == "asset changed"

    wrong_kind_delete = request(
        app,
        "DELETE",
        "/api/chapters/chapter-a/chat-messages/single/asset-message",
    )
    assert wrong_kind_delete.status_code == 404

    ordinary_deleted = request(
        app,
        "DELETE",
        f"/api/chapters/chapter-b/chat-messages/single/{created.json()['id']}",
        user="user-b",
    )
    assert ordinary_deleted.status_code == 204
    assert ordinary_deleted.content == b""

    asset_deleted = request(
        app,
        "DELETE",
        f"/api/chapters/chapter-b/asset-chat-messages/single/{asset_created.json()['id']}",
        user="user-b",
    )
    assert asset_deleted.status_code == 204
    assert asset_deleted.content == b""

    bulk_deleted = request(
        app,
        "DELETE",
        "/api/chapters/chapter-a/chat-messages?frame_index=-1",
    )
    assert bulk_deleted.status_code == 204
    assert bulk_deleted.content == b""


def test_http_access_validation_and_unconfigured_factory(database: ChatDatabase) -> None:
    statements: list[str] = []

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(database.engine, "before_cursor_execute", observe_sql)
    try:
        denied = request(
            build_app(database),
            "GET",
            "/api/chapters/chapter-a/chat-messages",
            user="user-b",
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", observe_sql)

    assert denied.status_code == 403
    assert any("from chapters" in statement for statement in statements)
    assert any("from series" in statement for statement in statements)
    assert not any("chat_messages" in statement for statement in statements)
    assert not any(
        statement.lstrip().startswith(("insert", "update", "delete"))
        for statement in statements
    )

    counter = {"count": 0}

    def unused_factory():
        counter["count"] += 1
        return database.session_factory()

    incomplete_app = create_app(session_factory=unused_factory)
    missing_factory = request(
        incomplete_app,
        "GET",
        "/api/chapters/chapter-a/chat-messages",
    )
    assert missing_factory.status_code == 503
    assert counter["count"] == 0

    invalid = request(
        build_app(database),
        "POST",
        "/api/chapters/chapter-a/chat-messages",
        body={
            "chapter_id": "chapter-a",
            "frame_index": None,
            "role": "assistant",
            "content": "invalid frame",
        },
    )
    assert invalid.status_code == 422



def test_missing_chapter_remains_404_for_ordinary_list_but_asset_list_is_conditional(
    database: ChatDatabase,
) -> None:
    app = build_app(database)

    ordinary = request(app, "GET", "/api/chapters/missing/chat-messages")
    conditional_asset = request(
        app,
        "GET",
        "/api/chapters/missing/asset-chat-messages?asset_type=scene&asset_id=missing",
    )

    assert ordinary.status_code == 404
    assert conditional_asset.status_code == 200
    assert conditional_asset.json() == []



def test_conditional_orphan_asset_read_requires_explicit_fk_off_fixture(tmp_path: Path) -> None:
    database = create_database(tmp_path / "orphan-chat.sqlite", foreign_keys=False)
    try:
        database.seed_message(
            "orphan-asset-message",
            "missing-chapter",
            asset_type="scene",
            asset_id="scene-orphan",
            content="orphan fixture",
        )
        response = request(
            build_app(database),
            "GET",
            "/api/chapters/missing-chapter/asset-chat-messages?asset_type=scene&asset_id=scene-orphan",
            user="user-b",
        )

        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == ["orphan-asset-message"]
    finally:
        database.engine.dispose()
