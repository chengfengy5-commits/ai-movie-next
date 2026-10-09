"""ASGI-level HTTP tests using the actual FastAPI and SQLite adapter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from sqlalchemy import insert

from haoai_backend.app import create_app
from haoai_backend.personal_production.rough_cut.ports import TrustedActor
from haoai_backend.personal_production.rough_cut.tables import rough_cut_drafts
from conftest import TestDatabase, chapter_frames, make_actor_resolver, make_series_access_policy
from haoai_backend.personal_production.rough_cut.tables import storyboard_assets


def request(
    app,
    method: str,
    path: str,
    *,
    user: str | None = "user-a",
    body: Any = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers = {"x-test-user": user} if user is not None else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://rough-cut.test",
        ) as client:
            return await client.request(method, path, headers=headers, json=body, params=params)

    return asyncio.run(send())


def build_app(database: TestDatabase, *, resolver=None, policy=None, counter=None):
    def session_factory():
        if counter is not None:
            counter["sessions"] += 1
        return database.session_factory()

    return create_app(
        session_factory=session_factory,
        resolve_actor=resolver or make_actor_resolver(),
        series_access_policy=policy or make_series_access_policy(),
    )


def test_app_factory_registers_eleven_auth_ten_chat_four_personal_twelve_series_and_fifteen_asset_methods(database: TestDatabase) -> None:
    app = build_app(database)
    routes = {
        (route.path, frozenset(getattr(route, "methods", set())))
        for route in app.routes
    }
    assert routes == {
        ("/api/auth/register", frozenset({"POST"})),
        ("/api/auth/login", frozenset({"POST"})),
        ("/api/auth/me", frozenset({"GET"})),
        ("/api/auth/sessions", frozenset({"GET"})),
        ("/api/auth/sessions/{session_id}", frozenset({"DELETE"})),
        ("/api/auth/password", frozenset({"PUT"})),
        ("/api/auth/forgot-password", frozenset({"POST"})),
        ("/api/auth/reset-password", frozenset({"POST"})),
        ("/api/auth/credits/me", frozenset({"GET"})),
        ("/api/auth/send-code", frozenset({"POST"})),
        ("/api/auth/verify-code", frozenset({"POST"})),
        ("/api/chapters/{chapter_id}/rough-cut", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/rough-cut", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/personal-production-notes", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/personal-production-notes", frozenset({"PUT"})),
        ("/api/series", frozenset({"GET"})),
        ("/api/series/{series_id}", frozenset({"GET"})),
        ("/api/series", frozenset({"POST"})),
        ("/api/series/{series_id}", frozenset({"PUT"})),
        ("/api/series/{series_id}", frozenset({"DELETE"})),
        ("/api/series/{series_id}/chapters", frozenset({"GET"})),
        ("/api/series/{series_id}/chapters/reorder", frozenset({"PUT"})),
        ("/api/series/{series_id}/chapters", frozenset({"POST"})),
        ("/api/chapters/{chapter_id}", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/delete-frame", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}", frozenset({"DELETE"})),
        ("/api/series/{series_id}/storyboard-assets", frozenset({"GET"})),
        ("/api/series/{series_id}/characters", frozenset({"GET"})),
        ("/api/series/{series_id}/characters", frozenset({"POST"})),
        ("/api/characters/{character_id}", frozenset({"PUT"})),
        ("/api/characters/{character_id}", frozenset({"DELETE"})),
        ("/api/series/{series_id}/scenes", frozenset({"GET"})),
        ("/api/series/{series_id}/scenes", frozenset({"POST"})),
        ("/api/scenes/{scene_id}", frozenset({"PUT"})),
        ("/api/scenes/{scene_id}", frozenset({"DELETE"})),
        ("/api/series/{series_id}/props", frozenset({"GET"})),
        ("/api/series/{series_id}/props", frozenset({"POST"})),
        ("/api/props/{prop_id}", frozenset({"PUT"})),
        ("/api/props/{prop_id}", frozenset({"DELETE"})),
        ("/api/storyboard-assets", frozenset({"POST"})),
        ("/api/storyboard-assets/{asset_id}", frozenset({"PUT"})),
        ("/api/storyboard-assets/{asset_id}", frozenset({"DELETE"})),
        ("/api/chapters/{chapter_id}/chat-messages", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/chat-messages", frozenset({"POST"})),
        ("/api/chapters/{chapter_id}/chat-messages/{message_id}", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/asset-chat-messages/{message_id}", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/asset-chat-messages", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/asset-chat-messages", frozenset({"POST"})),
        ("/api/chapters/{chapter_id}/chat-messages", frozenset({"DELETE"})),
        ("/api/chapters/{chapter_id}/chat-messages/single/{message_id}", frozenset({"DELETE"})),
        ("/api/chapters/{chapter_id}/asset-chat-messages/single/{message_id}", frozenset({"DELETE"})),
        ("/api/chapters/{chapter_id}/ai-stats", frozenset({"GET"})),
    }


def test_incomplete_composition_returns_503_without_creating_session(database: TestDatabase) -> None:
    calls = {"sessions": 0}

    def session_factory():
        calls["sessions"] += 1
        return database.session_factory()

    complete_resolver = make_actor_resolver()
    complete_policy = make_series_access_policy()
    apps = [
        create_app(),
        create_app(session_factory=session_factory, series_access_policy=complete_policy),
        create_app(session_factory=session_factory, resolve_actor=complete_resolver),
        create_app(resolve_actor=complete_resolver, series_access_policy=complete_policy),
    ]
    for app in apps:
        response = request(app, "GET", "/api/chapters/chapter-a/rough-cut")
        assert response.status_code == 503
    assert calls["sessions"] == 0


def test_authentication_failure_does_not_create_session(database: TestDatabase) -> None:
    calls = {"sessions": 0}

    def session_factory():
        calls["sessions"] += 1
        return database.session_factory()

    app = build_app(database, resolver=make_actor_resolver(), counter=calls)
    response = request(app, "GET", "/api/chapters/chapter-a/rough-cut", user=None)
    assert response.status_code == 401
    assert calls["sessions"] == 0


def test_access_policy_runs_before_source_or_private_reads(database: TestDatabase) -> None:
    database.seed_user("outsider")
    database.seed_chapter("chapter-a", "series-a", "not-json", ("asset-a",))
    statements: list[str] = []

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    from sqlalchemy import event
    event.listen(database.engine, "before_cursor_execute", observe_sql)

    app = build_app(database)
    response = request(app, "GET", "/api/chapters/chapter-a/rough-cut", user="outsider")
    event.remove(database.engine, "before_cursor_execute", observe_sql)

    assert response.status_code == 403
    assert any("chapters" in sql for sql in statements)
    assert any("test_series_memberships" in sql for sql in statements)
    assert not any("storyboard_assets" in sql or "rough_cut_drafts" in sql for sql in statements)


def test_access_policy_and_source_reads_share_the_request_connection(database: TestDatabase) -> None:
    database.seed_chapter("chapter-a", "series-a", chapter_frames("a"), ("a",))
    executed: list[tuple[str, int]] = []

    from sqlalchemy import event

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        lowered = statement.lower()
        if any(table in lowered for table in ("test_series_memberships", "storyboard_assets", "rough_cut_drafts")):
            executed.append((lowered, id(connection.connection)))

    event.listen(database.engine, "before_cursor_execute", observe_sql)
    response = request(build_app(database), "GET", "/api/chapters/chapter-a/rough-cut")
    event.remove(database.engine, "before_cursor_execute", observe_sql)

    assert response.status_code == 200
    assert len(executed) == 3
    assert len({connection_id for _, connection_id in executed}) == 1



def test_async_actor_resolver_finishes_before_sync_endpoint_opens_session(database: TestDatabase) -> None:
    database.seed_chapter("chapter-a", "series-a", chapter_frames("asset-a"), ("asset-a",))
    events: list[str] = []

    async def resolve_actor(request) -> TrustedActor:
        events.append("resolver-start")
        await asyncio.sleep(0)
        events.append("resolver-finished")
        return TrustedActor(user_id="user-a")

    def session_factory():
        events.append("session-created")
        return database.session_factory()

    app = create_app(
        session_factory=session_factory,
        resolve_actor=resolve_actor,
        series_access_policy=make_series_access_policy(),
    )
    response = request(app, "GET", "/api/chapters/chapter-a/rough-cut")

    assert response.status_code == 200
    assert events[:3] == ["resolver-start", "resolver-finished", "session-created"]


def test_http_write_reorders_the_full_set_and_is_private_by_trusted_identity(database: TestDatabase) -> None:
    database.seed_chapter("chapter-a", "series-a", chapter_frames("a", "b"), ("a", "b"))
    app = build_app(database)
    body = {
        "expected_revision": 0,
        "frames": [
            {"asset_id": "b", "included": True},
            {"asset_id": "a", "included": False},
        ],
    }

    saved = request(app, "PUT", "/api/chapters/chapter-a/rough-cut", body=body)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["saved"] is True
    assert [(frame["asset_id"], frame["frame_index"], frame["included"], frame["pending"]) for frame in saved.json()["frames"]] == [
        ("b", 1, True, False),
        ("a", 0, False, False),
    ]
    assert saved.json()["removed_asset_ids"] == []

    read_back = request(app, "GET", "/api/chapters/chapter-a/rough-cut", params={"user_id": "user-b"})
    assert read_back.status_code == 200
    assert read_back.json()["revision"] == 1
    assert [frame["asset_id"] for frame in read_back.json()["frames"]] == ["b", "a"]

    user_b = request(app, "GET", "/api/chapters/chapter-a/rough-cut", user="user-b")
    assert user_b.status_code == 200
    assert user_b.json()["revision"] == 0
    assert user_b.json()["saved"] is False
    assert [frame["asset_id"] for frame in user_b.json()["frames"]] == ["a", "b"]

    forged_owner = {**body, "user_id": "user-b"}
    rejected = request(app, "PUT", "/api/chapters/chapter-a/rough-cut", body=forged_owner)
    assert rejected.status_code == 422


def test_resolver_membership_rejection_precedes_session_creation(database: TestDatabase) -> None:
    calls = {"sessions": 0}

    def session_factory():
        calls["sessions"] += 1
        return database.session_factory()

    def resolver(request):
        from haoai_backend.personal_production.rough_cut.errors import Forbidden
        raise Forbidden("当前没有有效会员")

    app = create_app(
        session_factory=session_factory,
        resolve_actor=resolver,
        series_access_policy=make_series_access_policy(),
    )
    response = request(app, "GET", "/api/chapters/chapter-a/rough-cut")
    assert response.status_code == 403
    assert calls["sessions"] == 0


def test_empty_chapter_can_be_saved_as_revision_one(database: TestDatabase) -> None:
    database.seed_chapter("empty-chapter", "series-a", None)
    app = build_app(database)

    saved = request(
        app,
        "PUT",
        "/api/chapters/empty-chapter/rough-cut",
        body={"expected_revision": 0, "frames": []},
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "chapter_id": "empty-chapter",
        "revision": 1,
        "saved": True,
        "frames": [],
        "removed_asset_ids": [],
    }


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": True, "frames": []},
        {"expected_revision": -1, "frames": []},
        {"expected_revision": 0, "frames": [], "owner_id": "user-b"},
        {"expected_revision": 0, "frames": [{"asset_id": "", "included": True}]},
        {"expected_revision": 0, "frames": [{"asset_id": 42, "included": True}]},
        {"expected_revision": 0, "frames": [{"asset_id": "a", "included": 1}]},
        {"expected_revision": 0, "frames": [{"asset_id": "a", "included": True, "extra": 1}]},
        {"expected_revision": 0, "frames": [{"asset_id": "😀" * 37, "included": True}]},
        {"expected_revision": 0, "frames": [{"asset_id": f"a-{i}", "included": True} for i in range(501)]},
    ],
)
def test_strict_body_contract_rejects_invalid_values_before_session(database: TestDatabase, body: Any) -> None:
    calls = {"sessions": 0}
    app = build_app(database, counter=calls)
    response = request(app, "PUT", "/api/chapters/chapter-a/rough-cut", body=body)
    assert response.status_code == 422
    assert calls["sessions"] == 0


def test_error_priority_preserves_not_found_source_revision_identity_and_size(database: TestDatabase) -> None:
    app = build_app(database)

    missing = request(
        app,
        "PUT",
        "/api/chapters/missing/rough-cut",
        body={"expected_revision": 0, "frames": []},
    )
    assert missing.status_code == 404

    database.seed_chapter("bad-source", "series-a", "broken", ("asset-a",))
    with database.engine.begin() as connection:
        connection.execute(
            insert(rough_cut_drafts).values(
                id="draft-bad-source",
                chapter_id="bad-source",
                user_id="user-a",
                revision=1,
                frames=[{"asset_id": "asset-a", "included": True}],
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
    bad_source = request(
        app,
        "PUT",
        "/api/chapters/bad-source/rough-cut",
        body={"expected_revision": 0, "frames": []},
    )
    assert bad_source.status_code == 422

    database.seed_chapter("invalid-identity", "series-a", [{"text": "no id"}])
    with database.engine.begin() as connection:
        connection.execute(
            insert(rough_cut_drafts).values(
                id="draft-invalid-identity",
                chapter_id="invalid-identity",
                user_id="user-a",
                revision=1,
                frames=[],
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
    conflict_first = request(
        app,
        "PUT",
        "/api/chapters/invalid-identity/rough-cut",
        body={"expected_revision": 0, "frames": []},
    )
    assert conflict_first.status_code == 409

    large_invalid_frames = [{"storyboard": [f"id-{i}"]} for i in range(501)]
    large_invalid_frames[-1] = {"text": "invalid"}
    database.seed_chapter(
        "large-invalid",
        "series-a",
        large_invalid_frames,
        tuple(f"id-{i}" for i in range(500)),
    )
    invalid_before_size = request(
        app,
        "PUT",
        "/api/chapters/large-invalid/rough-cut",
        body={"expected_revision": 0, "frames": []},
    )
    assert invalid_before_size.status_code == 422

    large_valid_frames = [{"storyboard": [f"valid-{i}"]} for i in range(501)]
    database.seed_chapter(
        "large-valid",
        "series-a",
        large_valid_frames,
        tuple(f"valid-{i}" for i in range(501)),
    )
    oversized_source = request(
        app,
        "PUT",
        "/api/chapters/large-valid/rough-cut",
        body={"expected_revision": 0, "frames": []},
    )
    assert oversized_source.status_code == 413

    unlimited_get = request(app, "GET", "/api/chapters/large-valid/rough-cut")
    assert unlimited_get.status_code == 200
    assert len(unlimited_get.json()["frames"]) == 501


def test_wire_does_not_trim_asset_ids_or_add_javascript_safe_integer_limit(database: TestDatabase) -> None:
    database.seed_chapter("space-id", "series-a", [{"storyboard": [" "], "text": "空白 ID"}], (" ",))
    app = build_app(database)
    saved = request(
        app,
        "PUT",
        "/api/chapters/space-id/rough-cut",
        body={"expected_revision": 0, "frames": [{"asset_id": " ", "included": True}]},
    )
    assert saved.status_code == 200
    assert saved.json()["frames"][0]["asset_id"] == " "

    huge_revision = request(
        app,
        "PUT",
        "/api/chapters/space-id/rough-cut",
        body={
            "expected_revision": 9_007_199_254_740_993,
            "frames": [{"asset_id": " ", "included": False}],
        },
    )
    assert huge_revision.status_code == 409


def test_source_asset_resolution_keeps_the_legacy_chapter_only_filter(database: TestDatabase) -> None:
    database.seed_chapter("chapter-a", "series-a", chapter_frames("foreign-series-asset"))
    with database.engine.begin() as connection:
        connection.execute(
            insert(storyboard_assets).values(
                id="foreign-series-asset",
                series_id="another-series",
                chapter_id="chapter-a",
                frame_index=0,
                image_url=None,
            )
        )
    response = request(build_app(database), "GET", "/api/chapters/chapter-a/rough-cut")
    assert response.status_code == 200
    assert response.json()["frames"][0]["asset_id"] == "foreign-series-asset"


def test_duplicate_and_incomplete_sets_are_rejected_after_current_source_read(database: TestDatabase) -> None:
    database.seed_chapter("chapter-a", "series-a", chapter_frames("a", "b"), ("a", "b"))
    app = build_app(database)
    duplicate = request(
        app,
        "PUT",
        "/api/chapters/chapter-a/rough-cut",
        body={
            "expected_revision": 0,
            "frames": [
                {"asset_id": "a", "included": True},
                {"asset_id": "a", "included": False},
            ],
        },
    )
    incomplete = request(
        app,
        "PUT",
        "/api/chapters/chapter-a/rough-cut",
        body={"expected_revision": 0, "frames": [{"asset_id": "a", "included": True}]},
    )
    assert duplicate.status_code == 422
    assert incomplete.status_code == 422
    assert database.read_draft("chapter-a", "user-a") is None
