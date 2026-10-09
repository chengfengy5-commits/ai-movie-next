from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.asset_data.http import mount_asset_data_routes
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from haoai_backend.series_data.tables import chapters, metadata as series_metadata, series
from haoai_backend.shared.identity import TrustedActor


NOW = datetime(2026, 1, 1)


@pytest.fixture
def http_database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'asset-data-http.sqlite'}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    auth_metadata.create_all(engine)
    series_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    from haoai_backend.asset_data.tables import metadata as asset_metadata

    asset_metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(auth_users),
            [
                {
                    "id": user_id,
                    "username": user_id,
                    "email": f"{user_id}@example.test",
                    "hashed_password": "test-hash",
                    "is_superuser": False,
                    "membership_type": "free",
                    "created_at": NOW,
                    "password_updated_at": None,
                }
                for user_id in ("owner", "other")
            ],
        )
        connection.execute(
            insert(series).values(
                id="series-a",
                user_id="owner",
                name="剧集",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-a",
                series_id="series-a",
                title="第一集",
                content="[]",
                order=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    sessions = sessionmaker(engine, expire_on_commit=False, future=True)
    yield engine, sessions
    engine.dispose()


def request(
    app,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    user_id: str = "owner",
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://asset-data.test",
            headers={"x-test-user": user_id},
        ) as client:
            return await client.request(method, path, json=body)

    return asyncio.run(send())


def build_app(sessions: sessionmaker[Session]):
    async def resolve_actor(request: httpx.Request) -> TrustedActor:
        await asyncio.sleep(0)
        user_id = request.headers.get("x-test-user")
        if user_id not in {"owner", "other"}:
            raise AuthenticationError(401, "登录状态已失效")
        return TrustedActor(user_id)

    return create_app(
        session_factory=sessions,
        resolve_actor=resolve_actor,
        series_access_policy=lambda session, actor, series_id: None,
    )


def test_all_fifteen_asset_routes_use_real_sql_and_preserve_dto_presence(http_database) -> None:
    engine, sessions = http_database
    app = build_app(sessions)

    character_list = request(app, "GET", "/api/series/series-a/characters")
    assert character_list.status_code == 200 and character_list.json() == []
    character_create = request(
        app,
        "POST",
        "/api/series/series-a/characters",
        {"name": "林岚", "description": "原说明", "audio_url": "voice://one", "aliases": ["ignored"]},
    )
    assert character_create.status_code == 201
    character = character_create.json()
    assert character["aliases"] == []
    assert character["audio_url"] == "voice://one"
    character_update = request(
        app,
        "PUT",
        f"/api/characters/{character['id']}",
        {"description": None, "audio_url": None, "aliases": []},
    )
    assert character_update.status_code == 200
    assert character_update.json()["description"] == "原说明"
    assert character_update.json()["audio_url"] is None
    assert character_update.json()["aliases"] == []
    assert request(app, "DELETE", f"/api/characters/{character['id']}").status_code == 204

    assert request(app, "GET", "/api/series/series-a/scenes").json() == []
    scene_create = request(
        app,
        "POST",
        "/api/series/series-a/scenes",
        {"title": "医院 内景 夜", "image_url": "image://scene"},
    )
    assert scene_create.status_code == 201
    scene_id = scene_create.json()["id"]
    scene_update = request(
        app,
        "PUT",
        f"/api/scenes/{scene_id}",
        {"title": "医院 外景 夜", "aliases": ["医院旧名"]},
    )
    assert scene_update.status_code == 200
    assert scene_update.json()["aliases"] == ["医院旧名"]
    assert request(app, "DELETE", f"/api/scenes/{scene_id}").status_code == 204

    assert request(app, "GET", "/api/series/series-a/props").json() == []
    prop_create = request(
        app,
        "POST",
        "/api/series/series-a/props",
        {"name": "旧手机", "description": "会保留"},
    )
    assert prop_create.status_code == 201
    prop_id = prop_create.json()["id"]
    prop_update = request(app, "PUT", f"/api/props/{prop_id}", {"description": "更新说明"})
    assert prop_update.status_code == 200
    assert prop_update.json()["description"] == "更新说明"
    assert request(app, "DELETE", f"/api/props/{prop_id}").status_code == 204

    storyboard_create = request(
        app,
        "POST",
        "/api/storyboard-assets",
        {
            "chapter_id": "chapter-a",
            "frame_index": -2,
            "name": "",
            "image_url": "image://frame",
            "series_id": "forged-series",
            "unknown": "ignored",
        },
    )
    assert storyboard_create.status_code == 201
    storyboard = storyboard_create.json()
    assert storyboard["series_id"] == "series-a"
    assert storyboard["chapter_id"] == "chapter-a"
    assert storyboard["frame_index"] == -2
    assert storyboard["name"] == ""
    storyboard_update = request(
        app,
        "PUT",
        f"/api/storyboard-assets/{storyboard['id']}",
        {"name": "正式镜头", "chapter_id": "forged-chapter", "frame_index": 10},
    )
    assert storyboard_update.status_code == 200
    assert storyboard_update.json()["name"] == "正式镜头"
    assert storyboard_update.json()["chapter_id"] == "chapter-a"
    assert storyboard_update.json()["frame_index"] == -2
    assert request(app, "DELETE", f"/api/storyboard-assets/{storyboard['id']}").status_code == 204

    with Session(engine, future=True) as session:
        chapter_content = session.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one()
    assert chapter_content == "[]"


def test_asset_route_maps_denied_access_before_private_asset_reads(http_database) -> None:
    engine, sessions = http_database
    app = build_app(sessions)
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = request(app, "GET", "/api/series/series-a/characters", user_id="other")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert response.status_code == 403
    assert response.json()["detail"] == "无权访问该剧集"
    assert any("from series" in statement for statement in statements)
    assert not any("from characters" in statement for statement in statements)


def test_asset_routes_fail_closed_without_session_or_identity_factory() -> None:
    app = FastAPI()
    mount_asset_data_routes(app, uow_factory=None, resolve_actor=None)

    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://asset-data.test",
        ) as client:
            return await client.get("/api/series/series-a/characters")

    response = asyncio.run(send())
    assert response.status_code == 503
