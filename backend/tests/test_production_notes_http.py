"""ASGI tests for the existing notes path and strict old-compatible DTO."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import event, select

from conftest import TestDatabase, chapter_frames, make_actor_resolver, make_series_access_policy
from haoai_backend.app import create_app
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.personal_production.rough_cut.ports import TrustedActor


@pytest.fixture
def notes_database(database: TestDatabase) -> TestDatabase:
    notes_metadata.create_all(database.engine)
    return database


def request(
    app,
    method: str,
    path: str,
    *,
    user: str | None = "user-a",
    body: Any = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers = {"x-test-user": user} if user is not None else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://notes.test",
        ) as client:
            return await client.request(method, path, headers=headers, json=body)

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


NOTES_PATH = "/api/chapters/{chapter_id}/personal-production-notes"


def test_factory_adds_chat_and_asset_methods_without_changing_notes_and_rough_cut(notes_database: TestDatabase) -> None:
    app = build_app(notes_database)
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
        ("/api/site-config", frozenset({"GET"})),
        ("/api/purchase-packages", frozenset({"GET"})),
        ("/api/system-models", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/rough-cut", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/rough-cut", frozenset({"PUT"})),
        (NOTES_PATH, frozenset({"GET"})),
        (NOTES_PATH, frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/canvas", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/canvas", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/replace-asset", frozenset({"POST"})),
        ("/api/chapters/{chapter_id}/lock", frozenset({"POST"})),
        ("/api/chapters/{chapter_id}/lock", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/lock", frozenset({"DELETE"})),
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
        ("/api/storyboard/filter-and-save-characters", frozenset({"POST"})),
        ("/api/storyboard/filter-and-save-characters-stream", frozenset({"POST"})),
        ("/api/storyboard/filter-and-save-scenes-stream", frozenset({"POST"})),
        ("/api/storyboard/filter-and-save-props-stream", frozenset({"POST"})),
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
        ("/api/download", frozenset({"GET"})),
        ("/api/sign-download-urls", frozenset({"POST"})),
        ("/api/chat/image", frozenset({"POST"})),
        ("/api/chat/video", frozenset({"POST"})),
        ("/api/chat/batch-generate-images", frozenset({"POST"})),
        ("/api/teams", frozenset({"POST"})),
        ("/api/teams/my", frozenset({"GET"})),
        ("/api/teams/{team_id}", frozenset({"GET"})),
        ("/api/teams/{team_id}", frozenset({"PUT"})),
        ("/api/teams/{team_id}", frozenset({"DELETE"})),
        ("/api/teams/{team_id}/leave", frozenset({"POST"})),
        ("/api/teams/{team_id}/members/{user_id}", frozenset({"DELETE"})),
        ("/api/teams/{team_id}/members/{user_id}/role", frozenset({"PUT"})),
        ("/api/teams/{team_id}/members/{user_id}/permissions", frozenset({"PUT"})),
        ("/api/teams/{team_id}/invites", frozenset({"POST"})),
        ("/api/teams/{team_id}/invites", frozenset({"GET"})),
        ("/api/teams/{team_id}/invites/{invite_id}", frozenset({"DELETE"})),
        ("/api/teams/join", frozenset({"POST"})),
        ("/api/teams/{team_id}/series", frozenset({"GET"})),
        ("/api/teams/{team_id}/series", frozenset({"POST"})),
        ("/api/series/{series_id}/share", frozenset({"POST"})),
        ("/api/series/{series_id}/share", frozenset({"DELETE"})),
        ("/api/teams/{team_id}/series/{series_id}/claim", frozenset({"POST"})),
        ("/api/teams/{team_id}/series/{series_id}/claim", frozenset({"DELETE"})),
        ("/api/teams/{team_id}/series/{series_id}/transfer", frozenset({"POST"})),
        ("/api/teams/{team_id}/members/{user_id}/tasks", frozenset({"GET"})),
        ("/api/teams/{team_id}/usage", frozenset({"GET"})),
        ("/api/teams/{team_id}/series/{series_id}/usage", frozenset({"GET"})),
        ("/api/teams/{team_id}/usage/model", frozenset({"GET"})),
        ("/api/teams/{team_id}/usage/export", frozenset({"GET"})),
        ("/api/chat/tasks", frozenset({"GET"})),
        ("/api/chat/submissions/{operation}", frozenset({"GET"})),
        ("/api/chat/tasks/list", frozenset({"GET"})),
        ("/api/chat/tasks/{task_id}/request", frozenset({"GET"})),
        ("/api/chat/ai-review/counts", frozenset({"GET"})),
        ("/api/chat/ai-review/{task_id}", frozenset({"GET"})),
        ("/api/chat/batch-optimize/running", frozenset({"GET"})),
        ("/api/chat/batch-optimize/{task_id}/status", frozenset({"GET"})),
        ("/api/chat/batch-optimize/{task_id}/cancel", frozenset({"POST"})),
        ("/api/tasks", frozenset({"POST"})),
        ("/api/tasks/{task_id}", frozenset({"PUT"})),
        ("/api/tasks/{task_id}/progress", frozenset({"PUT"})),
        ("/api/tasks/{task_id}/external-status", frozenset({"GET"})),
        ("/api/admin/tasks", frozenset({"GET"})),
        ("/api/admin/tasks/{task_id}", frozenset({"GET"})),
        ("/api/user/preferences", frozenset({"GET"})),
        ("/api/user/preferences", frozenset({"PUT"})),
        ("/api/prompts", frozenset({"GET"})),
        ("/api/prompts/{prompt_id}", frozenset({"GET"})),
        ("/api/prompts", frozenset({"POST"})),
        ("/api/prompts/{prompt_id}", frozenset({"PUT"})),
        ("/api/prompts/{prompt_id}", frozenset({"DELETE"})),
        ("/api/prompts/{prompt_id}/promote-to-system", frozenset({"POST"})),
        ("/api/prompts/{prompt_id}/demote-to-personal", frozenset({"POST"})),
        ("/api/prompts/reorder", frozenset({"POST"})),
        ("/api/users/{user_id}/card", frozenset({"GET"})),
        ("/api/users/me/profile", frozenset({"PUT"})),
        ("/api/admin/purchase-packages", frozenset({"GET"})),
        ("/api/admin/purchase-packages/{pkg_id}", frozenset({"GET"})),
        ("/api/admin/purchase-packages", frozenset({"POST"})),
        ("/api/admin/purchase-packages/{pkg_id}", frozenset({"PUT"})),
        ("/api/admin/purchase-packages/{pkg_id}", frozenset({"DELETE"})),
        ("/api/admin/system-prompts", frozenset({"GET"})),
        ("/api/admin/system-prompts/{prompt_id}", frozenset({"GET"})),
        ("/api/admin/system-prompts", frozenset({"POST"})),
        ("/api/admin/system-prompts/{prompt_id}", frozenset({"PUT"})),
        ("/api/admin/system-prompts/{prompt_id}", frozenset({"DELETE"})),
        ("/api/admin/system-config", frozenset({"GET"})),
        ("/api/admin/system-config/{config_id}", frozenset({"GET"})),
        ("/api/admin/system-config/{config_id}", frozenset({"PUT"})),
        ("/api/admin/user-credits", frozenset({"GET"})),
        ("/api/admin/user-credits/{credit_id}", frozenset({"GET"})),
        ("/api/admin/user-credits/{credit_id}", frozenset({"PUT"})),
        ("/api/admin/credit-logs", frozenset({"GET"})),
        ("/api/admin/redemption-codes", frozenset({"GET"})),
        ("/api/admin/redemption-codes/{code_id}", frozenset({"GET"})),
        ("/api/admin/redemption-codes", frozenset({"POST"})),
        ("/api/admin/redemption-codes/{code_id}", frozenset({"DELETE"})),
        ("/api/admin/chapters", frozenset({"GET"})),
        ("/api/admin/chapters/{chapter_id}", frozenset({"GET"})),
        ("/api/admin/characters", frozenset({"GET"})),
        ("/api/admin/characters/{character_id}", frozenset({"GET"})),
        ("/api/admin/scenes", frozenset({"GET"})),
        ("/api/admin/scenes/{scene_id}", frozenset({"GET"})),
        ("/api/admin/props", frozenset({"GET"})),
        ("/api/admin/props/{prop_id}", frozenset({"GET"})),
        ("/api/admin/storyboard-assets", frozenset({"GET"})),
        ("/api/admin/storyboard-assets/{asset_id}", frozenset({"GET"})),
        ("/api/admin/chat-messages", frozenset({"GET"})),
        ("/api/admin/chat-messages/{message_id}", frozenset({"GET"})),
        ("/api/admin/system-models", frozenset({"GET"})),
        ("/api/admin/system-models", frozenset({"POST"})),
        ("/api/admin/system-models/{model_id}", frozenset({"GET"})),
        ("/api/admin/system-models/{model_id}", frozenset({"PUT"})),
        ("/api/admin/system-models/{model_id}", frozenset({"DELETE"})),
        ("/api/admin/model-configs", frozenset({"GET"})),
        ("/api/admin/model-configs", frozenset({"POST"})),
        ("/api/admin/model-configs/{model_id}", frozenset({"GET"})),
        ("/api/admin/model-configs/{model_id}", frozenset({"PUT"})),
        ("/api/admin/model-configs/{model_id}", frozenset({"DELETE"})),
        ("/api/admin/prompt-configs", frozenset({"GET"})),
        ("/api/admin/prompt-configs", frozenset({"POST"})),
        ("/api/admin/prompt-configs/{prompt_id}", frozenset({"GET"})),
        ("/api/admin/prompt-configs/{prompt_id}", frozenset({"PUT"})),
        ("/api/admin/prompt-configs/{prompt_id}", frozenset({"DELETE"})),
        ("/api/admin/users", frozenset({"GET"})),
        ("/api/admin/users", frozenset({"POST"})),
        ("/api/admin/users/{user_id}", frozenset({"PUT"})),
        ("/api/admin/users/{user_id}", frozenset({"GET"})),
        ("/api/admin/users/{user_id}", frozenset({"DELETE"})),
        ("/api/admin/series", frozenset({"GET"})),
        ("/api/admin/series/{series_id}", frozenset({"GET"})),
        ("/api/admin/series/{series_id}", frozenset({"PUT"})),
        ("/api/admin/series", frozenset({"POST"})),
        ("/api/admin/smtp-accounts", frozenset({"GET"})),
        ("/api/admin/smtp-accounts", frozenset({"POST"})),
        ("/api/admin/smtp-accounts/{account_id}", frozenset({"GET"})),
        ("/api/admin/smtp-accounts/{account_id}", frozenset({"PUT"})),
        ("/api/admin/smtp-accounts/{account_id}", frozenset({"DELETE"})),
        ("/api/admin/smtp-accounts/{account_id}/reset-count", frozenset({"POST"})),
        ("/api/admin/stats", frozenset({"GET"})),
        ("/api/models", frozenset({"GET"})),
        ("/api/models/{model_id}", frozenset({"GET"})),
        ("/api/models", frozenset({"POST"})),
        ("/api/models/{model_id}", frozenset({"PUT"})),
        ("/api/models/{model_id}", frozenset({"DELETE"})),
        ("/api/models/{model_id}/promote-to-system", frozenset({"POST"})),
        ("/api/models/{model_id}/demote-to-personal", frozenset({"POST"})),
        ("/api/models/reorder", frozenset({"POST"})),
        ("/api/models/test-connection", frozenset({"POST"})),
        ("/api/redemption-codes/generate", frozenset({"POST"})),
        ("/api/redemption-codes/redeem", frozenset({"POST"})),
        ("/uploads/{file_path:path}", frozenset({"GET"})),
        ("/api/provider-quota/accounts", frozenset({"GET"})),
        ("/api/provider-quota/accounts", frozenset({"POST"})),
        ("/api/provider-quota/accounts/{account_id}", frozenset({"PUT"})),
        ("/api/provider-quota/accounts/{account_id}", frozenset({"DELETE"})),
        ("/api/provider-quota/accounts/{account_id}/balance", frozenset({"GET"})),
        ("/api/provider-quota/probe", frozenset({"POST"})),
        ("/api/zhangyuge/balance", frozenset({"GET"})),
        ("/api/zhangyuge/pat", frozenset({"GET"})),
        ("/api/zhangyuge/pat", frozenset({"POST"})),
        ("/api/aistarslab/config", frozenset({"GET"})),
        ("/api/aistarslab/credits", frozenset({"GET"})),
        ("/api/aistarslab/add-default", frozenset({"POST"})),
        ("/api/aistarslab/build-image-params", frozenset({"POST"})),
        ("/api/aistarslab/add-image-default", frozenset({"POST"})),
        ("/api/aistarslab/estimate-cost", frozenset({"POST"})),
        ("/api/aistarslab/build-params", frozenset({"POST"})),
        ("/api/aistarslab/sync-all", frozenset({"POST"})),
        ("/api/chat/proxy-image", frozenset({"GET"})),
        ("/api/upload/image", frozenset({"POST"})),
        ("/api/upload/audio", frozenset({"POST"})),
        ("/api/upload/video", frozenset({"POST"})),
        ("/api/upload/retry-oss", frozenset({"POST"})),
        ("/api/chat/proxy-download", frozenset({"GET"})),
        ("/api/chat/stream", frozenset({"POST"})),
        ("/api/chat/ai-review", frozenset({"POST"})),
        ("/api/chat/batch-optimize", frozenset({"POST"})),
        ("/", frozenset({"GET"})),
        ("/health", frozenset({"GET"})),
        ("/ready", frozenset({"GET"})),
        ("/api/series/{series_id}/assets/orphan-check", frozenset({"POST"})),
        ("/api/series/{series_id}/assets/duplicates", frozenset({"GET"})),
        ("/api/series/{series_id}/assets/duplicates/ignore", frozenset({"POST"})),
        ("/api/series/{series_id}/assets/duplicates/ignored", frozenset({"GET"})),
        ("/api/series/{series_id}/assets/merge", frozenset({"POST"})),
        ("/api/series/{series_id}/fused-assets", frozenset({"GET"})),
        ("/api/fused-assets/generate", frozenset({"POST"})),
        ("/api/storyboard/generate", frozenset({"POST"})),
        ("/api/storyboard/generate-stream", frozenset({"POST"})),
        ("/api/storyboard/create-chapter-with-storyboard", frozenset({"POST"})),
        ("/api/storyboard/two-pass", frozenset({"POST"})),
        ("/api/storyboard/two-pass/quote", frozenset({"POST"})),
        ("/api/storyboard/two-pass/stream", frozenset({"POST"})),
        ("/api/storyboard/extract-characters-stream", frozenset({"POST"})),
        ("/api/storyboard/judge-character-appearance-stream", frozenset({"POST"})),
        ("/api/storyboard/extract-scenes-stream", frozenset({"POST"})),
        ("/api/storyboard/judge-scene-appearance-stream", frozenset({"POST"})),
        ("/api/storyboard/extract-props-stream", frozenset({"POST"})),
        ("/api/storyboard/judge-prop-appearance-stream", frozenset({"POST"})),
        ("/api/fused-assets/{asset_id}", frozenset({"PUT"})),
        ("/api/fused-assets/{asset_id}", frozenset({"DELETE"})),
        ("/api/series/{series_id}/export", frozenset({"GET"})),
        ("/api/series/import", frozenset({"POST"})),
    }
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None


def test_r0_multiframe_read_private_patch_resume_and_explicit_null_clear(notes_database: TestDatabase) -> None:
    notes_database.seed_chapter("chapter-a", "series-a", chapter_frames("a", "b"), ("a", "b"))
    app = build_app(notes_database)
    path = "/api/chapters/chapter-a/personal-production-notes"

    initial = request(app, "GET", path)
    assert initial.status_code == 200
    assert initial.json()["revision"] == 0
    assert initial.json()["media_state"] == "ready"
    assert initial.json()["frame_notes"] == {}
    assert initial.json()["resume_frame_id"] is None
    assert [frame["storyboard_asset_id"] for frame in initial.json()["frames"]] == ["a", "b"]
    assert [frame["media_revision"] for frame in initial.json()["frames"]] == [1, 1]

    saved = request(
        app,
        "PUT",
        path,
        body={
            "expected_revision": 0,
            "frames": [
                {"storyboard_asset_id": "a", "expected_media_revision": 1, "status": "approved", "note": "备注 A"},
                {"storyboard_asset_id": "b", "expected_media_revision": 1, "status": "needs_revision", "note": "备注 B"},
            ],
            "resume_frame_id": "b",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["resume_frame_id"] == "b"
    assert saved.json()["frame_notes"]["a"]["approved_media_revision"] == 1
    assert saved.json()["frame_notes"]["b"]["note"] == "备注 B"

    partial = request(
        app,
        "PUT",
        path,
        body={
            "expected_revision": 1,
            "frames": [
                {"storyboard_asset_id": "a", "expected_media_revision": 1, "note": "只更新备注"},
                {"storyboard_asset_id": "b", "expected_media_revision": 1, "status": "unmarked"},
            ],
        },
    )
    assert partial.status_code == 200
    assert partial.json()["revision"] == 2
    assert partial.json()["frame_notes"]["a"]["status"] == "approved"
    assert partial.json()["frame_notes"]["a"]["approved_media_revision"] == 1
    assert partial.json()["frame_notes"]["b"]["note"] == "备注 B"
    assert partial.json()["resume_frame_id"] == "b"

    cleared = request(
        app,
        "PUT",
        path,
        body={"expected_revision": 2, "frames": [], "resume_frame_id": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["revision"] == 3
    assert cleared.json()["resume_frame_id"] is None

    other_user = request(app, "GET", path, user="user-b")
    assert other_user.status_code == 200
    assert other_user.json()["revision"] == 0
    assert other_user.json()["frame_notes"] == {}


@pytest.mark.parametrize(
    ("content", "expected_state"),
    [([], "empty"), (None, "unreadable"), ("not-json", "unreadable")],
)
def test_empty_or_unreadable_chapter_and_explicit_null_resume_can_create_r1(
    notes_database: TestDatabase,
    content,
    expected_state: str,
) -> None:
    notes_database.seed_chapter("empty", "series-a", content)
    app = build_app(notes_database)
    response = request(
        app,
        "PUT",
        "/api/chapters/empty/personal-production-notes",
        body={"expected_revision": 0, "frames": [], "resume_frame_id": None},
    )
    assert response.status_code == 200
    assert response.json()["media_state"] == expected_state
    assert response.json()["revision"] == 1
    assert response.json()["frames"] == []


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_access_denial_precedes_source_and_private_queries(
    notes_database: TestDatabase,
    method: str,
) -> None:
    notes_database.seed_user("outsider")
    notes_database.seed_chapter("chapter-a", "series-a", "not-json", ("asset-a",))
    statements: list[str] = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(notes_database.engine, "before_cursor_execute", observe)
    response = request(
        build_app(notes_database),
        method,
        "/api/chapters/chapter-a/personal-production-notes",
        user="outsider",
        body=(
            {"expected_revision": 0, "frames": [], "resume_frame_id": None}
            if method == "PUT"
            else None
        ),
    )
    event.remove(notes_database.engine, "before_cursor_execute", observe)

    assert response.status_code == 403
    assert any("chapters" in statement for statement in statements)
    assert any("test_series_memberships" in statement for statement in statements)
    assert not any("storyboard_assets" in statement for statement in statements)
    assert not any("storyboard_media_states" in statement for statement in statements)
    assert not any("personal_production_notes" in statement for statement in statements)


def test_put_checks_user_existence_before_chapter_and_source_queries(notes_database: TestDatabase) -> None:
    notes_database.seed_chapter("chapter-a", "series-a", chapter_frames("a"), ("a",))
    statements: list[str] = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(notes_database.engine, "before_cursor_execute", observe)
    try:
        response = request(
            build_app(notes_database),
            "PUT",
            "/api/chapters/chapter-a/personal-production-notes",
            user="missing-user",
            body={"expected_revision": 0, "frames": [], "resume_frame_id": None},
        )
    finally:
        event.remove(notes_database.engine, "before_cursor_execute", observe)

    assert response.status_code == 401
    assert any("select users.id" in statement for statement in statements)
    assert not any("chapters" in statement for statement in statements)
    assert not any("storyboard_assets" in statement for statement in statements)
    assert not any("personal_production_notes" in statement for statement in statements)


def test_async_trusted_resolver_completes_before_session_creation(notes_database: TestDatabase) -> None:
    notes_database.seed_chapter("chapter-a", "series-a", chapter_frames("a"), ("a",))
    events: list[str] = []

    async def resolver(_request) -> TrustedActor:
        events.append("resolver-start")
        await asyncio.sleep(0)
        events.append("resolver-finished")
        return TrustedActor("user-a")

    def session_factory():
        events.append("session-created")
        return notes_database.session_factory()

    app = create_app(
        session_factory=session_factory,
        resolve_actor=resolver,
        series_access_policy=make_series_access_policy(),
    )
    response = request(app, "GET", "/api/chapters/chapter-a/personal-production-notes")
    assert response.status_code == 200
    assert events[:3] == ["resolver-start", "resolver-finished", "session-created"]


def test_access_policy_receives_the_request_unit_of_work_session(notes_database: TestDatabase) -> None:
    notes_database.seed_chapter("same-session", "series-a", chapter_frames("a"), ("a",))
    created_sessions = []
    policy_sessions = []
    connections = []

    def session_factory():
        session = notes_database.session_factory()
        created_sessions.append(session)
        return session

    def policy(session, actor, series_id):
        policy_sessions.append(session)
        make_series_access_policy()(session, actor, series_id)

    def observe(connection, cursor, statement, parameters, context, executemany):
        watched_tables = (
            "chapters",
            "test_series_memberships",
            "storyboard_assets",
            "storyboard_media_states",
            "personal_production_notes",
        )
        if any(table_name in statement.lower() for table_name in watched_tables):
            connections.append(id(connection.connection.driver_connection))

    event.listen(notes_database.engine, "before_cursor_execute", observe)
    try:
        app = create_app(
            session_factory=session_factory,
            resolve_actor=make_actor_resolver(),
            series_access_policy=policy,
        )
        response = request(
            app,
            "GET",
            "/api/chapters/same-session/personal-production-notes",
        )
    finally:
        event.remove(notes_database.engine, "before_cursor_execute", observe)

    assert response.status_code == 200
    assert len(created_sessions) == 1
    assert policy_sessions == created_sessions
    assert connections
    assert len(set(connections)) == 1


def test_strict_dto_rejects_invalid_bodies_before_opening_a_session(notes_database: TestDatabase) -> None:
    calls = {"sessions": 0}
    app = build_app(notes_database, counter=calls)
    path = "/api/chapters/chapter-a/personal-production-notes"
    invalid_bodies = [
        {"expected_revision": True, "frames": []},
        {"expected_revision": -1, "frames": [], "resume_frame_id": None},
        {"expected_revision": 0, "frames": [], "resume_frame_id": None, "owner_id": "user-b"},
        {"expected_revision": 0, "frames": [{"storyboard_asset_id": "a", "expected_media_revision": True, "note": "x"}]},
        {"expected_revision": 0, "frames": [{"storyboard_asset_id": "a", "expected_media_revision": 1, "note": "字" * 2001}]},
        {"expected_revision": 0, "frames": [{"storyboard_asset_id": f"a-{index}", "expected_media_revision": 1, "note": "x"} for index in range(501)]},
        {"expected_revision": 0, "frames": [{"storyboard_asset_id": "a", "expected_media_revision": 1, "note": "x"}, {"storyboard_asset_id": "a", "expected_media_revision": 1, "note": "y"}]},
        {"expected_revision": 0, "frames": []},
    ]
    for body in invalid_bodies:
        assert request(app, "PUT", path, body=body).status_code == 422
    assert calls["sessions"] == 0


def test_stale_personal_revision_precedes_invalid_frame_and_media_revision_conflict(notes_database: TestDatabase) -> None:
    notes_database.seed_chapter("chapter-a", "series-a", chapter_frames("a"), ("a",))
    app = build_app(notes_database)
    path = "/api/chapters/chapter-a/personal-production-notes"
    first = request(app, "PUT", path, body={
        "expected_revision": 0,
        "frames": [{"storyboard_asset_id": "a", "expected_media_revision": 1, "note": "first"}],
    })
    assert first.status_code == 200

    stale_and_invalid = request(app, "PUT", path, body={
        "expected_revision": 0,
        "frames": [{"storyboard_asset_id": "missing", "expected_media_revision": 1, "note": "x"}],
    })
    assert stale_and_invalid.status_code == 409
    assert stale_and_invalid.json()["detail"] == "个人记录已变化，请刷新后重试"

    stale_media = request(app, "PUT", path, body={
        "expected_revision": 1,
        "frames": [{"storyboard_asset_id": "a", "expected_media_revision": 2, "note": "x"}],
    })
    assert stale_media.status_code == 409
    assert stale_media.json()["detail"] == "镜头素材已变化，请刷新后重新确认"


def test_incomplete_factory_disables_notes_before_session_creation(notes_database: TestDatabase) -> None:
    calls = {"sessions": 0}

    def session_factory():
        calls["sessions"] += 1
        return notes_database.session_factory()

    complete_resolver = make_actor_resolver()
    complete_policy = make_series_access_policy()
    apps = [
        create_app(),
        create_app(session_factory=session_factory, series_access_policy=complete_policy),
        create_app(session_factory=session_factory, resolve_actor=complete_resolver),
        create_app(resolve_actor=complete_resolver, series_access_policy=complete_policy),
    ]
    for app in apps:
        response = request(app, "GET", "/api/chapters/chapter-a/personal-production-notes")
        assert response.status_code == 503
    assert calls["sessions"] == 0


def test_more_than_500_source_frames_remain_readable_and_a_small_patch_is_accepted(
    notes_database: TestDatabase,
) -> None:
    frame_ids = tuple(f"large-frame-{index}" for index in range(501))
    content = [{"storyboard": [frame_id], "preview": f"https://media.invalid/{frame_id}.png"} for frame_id in frame_ids]
    notes_database.seed_chapter("large-source", "series-a", content, frame_ids)
    app = build_app(notes_database)
    path = "/api/chapters/large-source/personal-production-notes"

    initial = request(app, "GET", path)
    assert initial.status_code == 200
    assert len(initial.json()["frames"]) == 501
    assert initial.json()["frames"][-1]["storyboard_asset_id"] == frame_ids[-1]

    saved = request(
        app,
        "PUT",
        path,
        body={
            "expected_revision": 0,
            "frames": [
                {
                    "storyboard_asset_id": frame_ids[-1],
                    "expected_media_revision": 1,
                    "note": "只更新一个镜头",
                }
            ],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["frame_notes"][frame_ids[-1]]["note"] == "只更新一个镜头"


def test_invalid_sibling_rejects_the_whole_multi_frame_patch(notes_database: TestDatabase) -> None:
    notes_database.seed_chapter(
        "mixed-source",
        "series-a",
        [
            {"storyboard": ["valid-a"]},
            {"storyboard": ["duplicate-b"]},
            {"storyboard": ["duplicate-b"]},
        ],
        ("valid-a", "duplicate-b"),
    )
    app = build_app(notes_database)
    path = "/api/chapters/mixed-source/personal-production-notes"
    initial = request(app, "GET", path)
    assert initial.status_code == 200
    assert initial.json()["frames"][0]["source_valid"] is True
    assert initial.json()["frames"][1]["source_valid"] is False

    rejected = request(
        app,
        "PUT",
        path,
        body={
            "expected_revision": 0,
            "frames": [
                {"storyboard_asset_id": "valid-a", "expected_media_revision": 1, "note": "不得部分提交"},
                {"storyboard_asset_id": "duplicate-b", "expected_media_revision": 1, "note": "无效"},
            ],
        },
    )

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "镜头身份缺失、重复或已失效，不能保存该镜头"
    assert request(app, "GET", path).json()["frame_notes"] == {}


def test_empty_media_cannot_be_approved_and_invalid_resume_is_rejected_atomically(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter(
        "empty-media", "series-a", [{"storyboard": ["empty-frame"]}], ("empty-frame",)
    )
    app = build_app(notes_database)
    path = "/api/chapters/empty-media/personal-production-notes"
    initial = request(app, "GET", path)
    assert initial.status_code == 200
    assert initial.json()["frames"][0]["asset_image_digest"] is None
    assert initial.json()["frames"][0]["preview_digest"] is None

    approved = request(
        app,
        "PUT",
        path,
        body={
            "expected_revision": 0,
            "frames": [
                {
                    "storyboard_asset_id": "empty-frame",
                    "expected_media_revision": 1,
                    "status": "approved",
                }
            ],
        },
    )
    assert approved.status_code == 422
    assert approved.json()["detail"] == "原图和预览均为空，不能将该镜头标记为已认可"

    invalid_resume = request(
        app,
        "PUT",
        path,
        body={"expected_revision": 0, "frames": [], "resume_frame_id": "missing"},
    )
    assert invalid_resume.status_code == 422
    assert invalid_resume.json()["detail"] == "续作目标缺少唯一有效的本章分镜身份"
    assert request(app, "GET", path).json()["frame_notes"] == {}
    assert request(app, "GET", path).json()["resume_frame_id"] is None


def test_unknown_database_failure_is_not_translated_to_a_successful_read(
    notes_database: TestDatabase,
) -> None:
    notes_database.seed_chapter("unknown-db", "series-a", chapter_frames("a"), ("a",))
    app = build_app(notes_database)

    def fail_private_read(connection, cursor, statement, parameters, context, executemany):
        if "personal_production_notes" in statement.lower():
            raise RuntimeError("database driver failure")

    event.listen(notes_database.engine, "before_cursor_execute", fail_private_read)
    try:
        with pytest.raises(RuntimeError, match="database driver failure"):
            request(app, "GET", "/api/chapters/unknown-db/personal-production-notes")
    finally:
        event.remove(notes_database.engine, "before_cursor_execute", fail_private_read)

    with notes_database.session_factory() as session:
        assert session.execute(
            select(personal_production_notes.c.id).where(
                personal_production_notes.c.chapter_id == "unknown-db"
            )
        ).first() is None
        assert session.execute(
            select(storyboard_media_states.c.id).where(
                storyboard_media_states.c.chapter_id == "unknown-db"
            )
        ).first() is None
