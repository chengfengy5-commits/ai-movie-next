"""ASGI contract tests; these exercise the in-process FastAPI transport, not TCP."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import update

from haoai_backend.app import create_app
from haoai_backend.chapter_asset_replacement.schemas import ReplaceAssetRequest
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor
from chapter_asset_replacement_support import chapters, create_owner_database


PATH = "/api/chapters/{chapter_id}/replace-asset"


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "replacement-http.sqlite")
    try:
        yield database
    finally:
        database.close()


def request(app, method: str, path: str, *, body: Any = None) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://replacement.test",
        ) as client:
            return await client.request(method, path, json=body)

    return asyncio.run(send())


def build_app(database, *, resolver=None, policy=None):
    def allow(_session, actor: TrustedActor, series_id: str) -> None:
        if actor.user_id != "user-a" or series_id != "series-a":
            raise BusinessError(404, "剧集不可用")

    return create_app(
        session_factory=database.session_factory,
        resolve_actor=resolver or (lambda _request: TrustedActor("user-a")),
        series_access_policy=policy or allow,
    )


def test_request_model_is_three_plain_strings_and_ignores_extra_fields() -> None:
    assert tuple(ReplaceAssetRequest.model_fields) == (
        "old_asset_id",
        "new_asset_id",
        "asset_type",
    )
    assert all(field.annotation is str for field in ReplaceAssetRequest.model_fields.values())
    assert ReplaceAssetRequest.model_config.get("extra") != "forbid"


@pytest.mark.parametrize(
    "body",
    [
        {"new_asset_id": "new-character", "asset_type": "character"},
        {"old_asset_id": None, "new_asset_id": "new-character", "asset_type": "character"},
        {"old_asset_id": 1, "new_asset_id": "new-character", "asset_type": "character"},
    ],
)
def test_missing_null_and_non_string_fields_are_http_422_without_opening_uow(
    owner_database, body: dict[str, object]
) -> None:
    app = build_app(owner_database)

    response = request(app, "POST", PATH.format(chapter_id="chapter-a"), body=body)

    assert response.status_code == 422
    assert owner_database.session_factory.created == []


def test_extra_request_fields_are_ignored_and_valid_payload_runs_real_owner_uow(
    owner_database,
) -> None:
    app = build_app(owner_database)
    body = {
        "old_asset_id": "old-character",
        "new_asset_id": "new-character",
        "asset_type": "character",
        "ignored_extension": {"from": "forward-compatible-client"},
    }

    response = request(app, "POST", PATH.format(chapter_id="chapter-a"), body=body)

    assert response.status_code == 200
    assert response.json() == {"message": "已替换 1 处分镜，使用 新角色"}
    assert len(owner_database.session_factory.created) == 1
    assert owner_database.session_factory.created[0].close_calls == 1


@pytest.mark.parametrize(
    ("body", "expected_status", "expected_detail"),
    [
        (
            {"old_asset_id": "same", "new_asset_id": "same", "asset_type": "character"},
            400,
            "新旧资产不能相同",
        ),
        (
            {
                "old_asset_id": "old-character",
                "new_asset_id": "new-character",
                "asset_type": "unknown",
            },
            400,
            "无效的资产类型",
        ),
    ],
)
def test_application_validation_failures_keep_their_chinese_http_details(
    owner_database, body: dict[str, str], expected_status: int, expected_detail: str
) -> None:
    app = build_app(owner_database)

    response = request(app, "POST", PATH.format(chapter_id="chapter-a"), body=body)

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
    assert len(owner_database.session_factory.created) == 1
    assert owner_database.session_factory.created[0].close_calls == 1


def test_http_maps_missing_chapter_access_denial_missing_asset_and_bad_content(
    tmp_path,
) -> None:
    cases = (
        ("missing", "user-a", "old-character", "new-character", "character", None, 404, "章节不存在"),
        ("chapter-a", "user-b", "old-character", "new-character", "character", None, 404, "剧集不可用"),
        ("chapter-a", "user-a", "old-character", "absent", "character", None, 404, "新资产不存在"),
        (
            "chapter-a",
            "user-a",
            "old-character",
            "new-character",
            "character",
            "{}",
            400,
            "章节内容格式异常",
        ),
    )
    for index, (
        chapter_id,
        user_id,
        old_id,
        new_id,
        asset_type,
        content,
        status,
        detail,
    ) in enumerate(cases):
        database = create_owner_database(tmp_path / f"http-error-{index}.sqlite")
        try:
            if content is not None:
                with database.engine.begin() as connection:
                    connection.execute(
                        update(chapters)
                        .where(chapters.c.id == "chapter-a")
                        .values(content=content)
                    )
            app = build_app(
                database,
                resolver=lambda _request, user_id=user_id: TrustedActor(user_id),
            )
            response = request(
                app,
                "POST",
                PATH.format(chapter_id=chapter_id),
                body={
                    "old_asset_id": old_id,
                    "new_asset_id": new_id,
                    "asset_type": asset_type,
                },
            )

            assert response.status_code == status
            assert response.json()["detail"] == detail
            assert len(database.session_factory.created) == 1
            assert database.session_factory.created[0].close_calls == 1
        finally:
            database.close()


def test_unwired_or_untrusted_identity_fails_closed_before_session_creation(owner_database) -> None:
    unwired = create_app()
    response = request(
        unwired,
        "POST",
        PATH.format(chapter_id="chapter-a"),
        body={"old_asset_id": "old", "new_asset_id": "new", "asset_type": "character"},
    )
    assert response.status_code == 503

    invalid_actor_app = build_app(owner_database, resolver=lambda _request: object())
    invalid_actor = request(
        invalid_actor_app,
        "POST",
        PATH.format(chapter_id="chapter-a"),
        body={
            "old_asset_id": "old-character",
            "new_asset_id": "new-character",
            "asset_type": "character",
        },
    )
    assert invalid_actor.status_code == 503
    assert owner_database.session_factory.created == []


def test_factory_registers_only_one_post_for_replacement_path(owner_database) -> None:
    app = build_app(owner_database)
    routes = [
        (route.path, frozenset(getattr(route, "methods", set())))
        for route in app.routes
        if route.path.startswith("/api/chapters/{chapter_id}/replace-asset")
    ]

    assert routes == [(PATH, frozenset({"POST"}))]
