from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.personal_production.rough_cut.tables import metadata as rough_metadata
from haoai_backend.series_data.tables import metadata as series_metadata, series, chapters, storyboard_assets, chapter_locks, system_configs
from haoai_backend.series_data.persistence import SqlAlchemySeriesDataUnitOfWork
from haoai_backend.shared.identity import TrustedActor


@pytest.fixture
def source_database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'series-data.sqlite'}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    auth_metadata.create_all(engine)
    series_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    rough_metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False, future=True)
    with engine.begin() as connection:
        connection.execute(insert(auth_users).values(
            id="user-a", username="作者", email="author@example.test", hashed_password="x",
            is_superuser=False, membership_type="free", created_at=datetime(2026, 1, 1),
            password_updated_at=None,
        ))
    yield engine, sessions
    engine.dispose()


def _app(sessions: sessionmaker[Session]):
    def resolve_actor(request) -> TrustedActor:
        user_id = request.headers.get("x-test-user")
        if user_id != "user-a":
            raise AuthenticationError(401, "登录状态已失效")
        return TrustedActor(user_id=user_id)

    return create_app(session_factory=sessions, resolve_actor=resolve_actor)


def _request(
    app, method: str, path: str, body: dict[str, Any] | None = None, *, raise_app_exceptions: bool = True
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
            base_url="http://series-data.test",
            headers={"x-test-user": "user-a"},
        ) as client:
            return await client.request(method, path, json=body)

    return asyncio.run(send())


def test_all_twelve_routes_share_real_sql_source_data_and_media_phases(source_database) -> None:
    engine, sessions = source_database
    app = _app(sessions)

    created_series = _request(app, "POST", "/api/series", {"name": "短剧", "description": None})
    assert created_series.status_code == 201
    series_id = created_series.json()["id"]
    assert created_series.json()["owner_name"] == "作者"

    assert _request(app, "GET", "/api/series").json()[0]["name"] == "短剧"
    assert _request(app, "GET", f"/api/series/{series_id}").json()["owner_name"] == "作者"
    updated_series = _request(app, "PUT", f"/api/series/{series_id}", {"name": "更新剧集"})
    assert updated_series.status_code == 200
    assert updated_series.json()["name"] == "更新剧集"

    created_chapter = _request(app, "POST", f"/api/series/{series_id}/chapters", {
        "title": "第一集",
        "content": [
            {"text": "镜头一", "storyboard": ["dropped"], "unknown": "ignored"},
            {"text": "镜头二"},
        ],
    })
    assert created_chapter.status_code == 201, created_chapter.text
    chapter_id = created_chapter.json()["id"]
    assert created_chapter.json()["content"][0]["storyboard"]
    asset_rows = _request(app, "GET", f"/api/series/{series_id}/storyboard-assets?chapter_id={chapter_id}")
    assert asset_rows.status_code == 200
    assert [item["frame_index"] for item in asset_rows.json()] == [0, 1]
    with Session(engine, future=True) as session:
        states = session.execute(select(storyboard_media_states)).mappings().all()
        assert len(states) == 2
        assert {row["media_revision"] for row in states} == {1}

    listed = _request(app, "GET", f"/api/series/{series_id}/chapters")
    assert listed.status_code == 200 and listed.json()[0]["lock"] is None
    reorder = _request(app, "PUT", f"/api/series/{series_id}/chapters/reorder", {"chapters": [{"id": chapter_id, "order": -2}]})
    assert reorder.status_code == 200
    edited = _request(app, "PUT", f"/api/chapters/{chapter_id}", {
        "title": "第一集修改",
        "content": [{"text": "镜头二", "storyboard": created_chapter.json()["content"][1]["storyboard"]}],
    })
    assert edited.status_code == 200
    assert edited.json()["title"] == "第一集修改"
    with Session(engine, future=True) as session:
        chapter = session.execute(select(chapters).where(chapters.c.id == chapter_id)).mappings().one()
        assert json.loads(chapter["content"])[0]["storyboard"] == created_chapter.json()["content"][1]["storyboard"]

    # Recreate two frames so the legacy delete-frame guard remains observable.
    current_content = json.loads(sessionless_content(sessions, chapter_id))
    current_content.append({"text": "镜头三"})
    _request(app, "PUT", f"/api/chapters/{chapter_id}", {"content": current_content})
    removed = _request(app, "PUT", f"/api/chapters/{chapter_id}/delete-frame", {"frame_index": 0})
    assert removed.status_code == 200
    assert len(removed.json()["content"]) == 1

    deleted_chapter = _request(app, "DELETE", f"/api/chapters/{chapter_id}")
    assert deleted_chapter.status_code == 204
    deleted_series = _request(app, "DELETE", f"/api/series/{series_id}")
    assert deleted_series.status_code == 204
    with Session(engine, future=True) as session:
        assert session.execute(select(series.c.id).where(series.c.id == series_id)).first() is None
        assert session.execute(select(storyboard_assets.c.id).where(storyboard_assets.c.chapter_id == chapter_id)).first() is None


def sessionless_content(sessions: sessionmaker[Session], chapter_id: str) -> str:
    with Session(sessions.kw["bind"], future=True) as session:
        return session.execute(select(chapters.c.content).where(chapters.c.id == chapter_id)).scalar_one()


def test_new_series_methods_fail_closed_without_session_or_identity() -> None:
    app = create_app()

    async def send() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x") as client:
            series_response = await client.get("/api/series")
            chapter_response = await client.get("/api/series/s/chapters")
            return series_response, chapter_response

    first, second = asyncio.run(send())
    assert first.status_code == second.status_code == 503


def test_reorder_reads_order_only_after_finding_a_chapter(source_database) -> None:
    _, sessions = source_database
    app = _app(sessions)
    created_series = _request(app, "POST", "/api/series", {"name": "排序兼容"})
    series_id = created_series.json()["id"]
    created_chapter = _request(app, "POST", f"/api/series/{series_id}/chapters", {
        "title": "第一集", "content": [{"text": "镜头"}],
    })
    chapter_id = created_chapter.json()["id"]

    missing_chapter = _request(app, "PUT", f"/api/series/{series_id}/chapters/reorder", {
        "chapters": [{"id": "not-in-this-series"}],
    })
    assert missing_chapter.status_code == 200

    matched_missing_order = _request(
        app,
        "PUT",
        f"/api/series/{series_id}/chapters/reorder",
        {"chapters": [{"id": chapter_id, "order": 9}, {"id": chapter_id}]},
        raise_app_exceptions=False,
    )
    assert matched_missing_order.status_code == 500
    listed = _request(app, "GET", f"/api/series/{series_id}/chapters")
    assert listed.status_code == 200
    assert listed.json()[0]["order"] == created_chapter.json()["order"]


def test_source_sync_skips_non_dict_frames_without_compacting_asset_indices(source_database) -> None:
    engine, sessions = source_database
    app = _app(sessions)
    created_series = _request(app, "POST", "/api/series", {"name": "混合帧"})
    series_id = created_series.json()["id"]
    created_chapter = _request(app, "POST", f"/api/series/{series_id}/chapters", {
        "title": "第一集", "content": [{"text": "A"}, {"text": "B"}],
    })
    chapter_id = created_chapter.json()["id"]
    asset_ids = [frame["storyboard"][0] for frame in created_chapter.json()["content"]]
    mixed_content = [
        {"text": "A", "storyboard": [asset_ids[0]]},
        17,
        {"text": "B", "storyboard": [asset_ids[1]]},
    ]
    with Session(engine, future=True) as session:
        session.execute(
            chapters.update().where(chapters.c.id == chapter_id).values(content=json.dumps(mixed_content))
        )
        session.commit()

    with sessions() as session:
        unit_of_work = SqlAlchemySeriesDataUnitOfWork(session)
        chapter = unit_of_work.sync_storyboard_assets(chapter_id, series_id)
        session.commit()
        assert chapter is not None
        assert json.loads(chapter.content) == mixed_content

    with Session(engine, future=True) as session:
        rows = session.execute(
            select(storyboard_assets.c.id, storyboard_assets.c.frame_index)
            .where(storyboard_assets.c.chapter_id == chapter_id)
        ).all()
    assert {asset_id: frame_index for asset_id, frame_index in rows} == {
        asset_ids[0]: 0,
        asset_ids[1]: 2,
    }


def test_source_sync_keeps_initial_asset_lookup_fixed_when_generated_id_is_referenced(source_database) -> None:
    engine, sessions = source_database
    app = _app(sessions)
    created_series = _request(app, "POST", "/api/series", {"name": "初始素材映射"})
    series_id = created_series.json()["id"]
    created_chapter = _request(app, "POST", f"/api/series/{series_id}/chapters", {
        "title": "第一集", "content": [{"text": "A"}, {"text": "B"}],
    })
    chapter_id = created_chapter.json()["id"]
    dangling_content = [
        {"text": "新素材"},
        {"text": "再次引用", "storyboard": ["created-during-scan"]},
    ]
    with Session(engine, future=True) as session:
        session.execute(
            chapters.update().where(chapters.c.id == chapter_id).values(content=json.dumps(dangling_content))
        )
        session.commit()

    generated_ids = iter(("created-during-scan", "created-after-scan"))
    with sessions() as session:
        unit_of_work = SqlAlchemySeriesDataUnitOfWork(session, new_id=lambda: next(generated_ids))
        chapter = unit_of_work.sync_storyboard_assets(chapter_id, series_id)
        session.commit()
        assert chapter is not None

    with Session(engine, future=True) as session:
        stored = session.execute(select(chapters.c.content).where(chapters.c.id == chapter_id)).scalar_one()
        rows = session.execute(
            select(storyboard_assets.c.id, storyboard_assets.c.frame_index)
            .where(storyboard_assets.c.chapter_id == chapter_id)
        ).all()
    stored_frames = json.loads(stored)
    assert stored_frames[0]["storyboard"] == ["created-during-scan"]
    assert stored_frames[1]["storyboard"] == ["created-after-scan"]
    assert {asset_id: frame_index for asset_id, frame_index in rows} == {
        "created-during-scan": 0,
        "created-after-scan": 1,
    }


@pytest.mark.parametrize(("idle_minutes", "expected_minutes"), [(None, 15), (0, 15), (-3, -3)])
def test_owned_expired_chapter_lock_is_refreshed_with_legacy_idle_fallback(
    source_database, idle_minutes: int | None, expected_minutes: int
) -> None:
    engine, sessions = source_database
    app = _app(sessions)
    created_series = _request(app, "POST", "/api/series", {"name": "锁续期"})
    series_id = created_series.json()["id"]
    created_chapter = _request(app, "POST", f"/api/series/{series_id}/chapters", {
        "title": "第一集", "content": [{"text": "镜头"}],
    })
    chapter_id = created_chapter.json()["id"]
    now = datetime(2020, 1, 1)
    with Session(engine, future=True) as session:
        session.execute(insert(chapter_locks).values(
            id="lock-a", chapter_id=chapter_id, user_id="user-a", username="作者",
            acquired_at=now - timedelta(hours=2), last_active_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        ))
        if idle_minutes is not None:
            session.execute(insert(system_configs).values(
                id="config-a", chapter_lock_idle_minutes=idle_minutes,
            ))
        session.commit()

    updated = _request(app, "PUT", f"/api/chapters/{chapter_id}", {"title": "已编辑"})
    assert updated.status_code == 200, updated.text
    with Session(engine, future=True) as session:
        lock = session.execute(select(chapter_locks).where(chapter_locks.c.chapter_id == chapter_id)).mappings().one()
    assert lock["last_active_at"] > now
    assert lock["expires_at"] - lock["last_active_at"] == timedelta(minutes=expected_minutes)


def test_reordering_to_the_same_order_does_not_refresh_chapter_timestamp(source_database) -> None:
    engine, sessions = source_database
    app = _app(sessions)
    created_series = _request(app, "POST", "/api/series", {"name": "同值排序"})
    series_id = created_series.json()["id"]
    created_chapter = _request(app, "POST", f"/api/series/{series_id}/chapters", {
        "title": "第一集", "content": [{"text": "镜头"}],
    })
    chapter_id = created_chapter.json()["id"]
    with Session(engine, future=True) as session:
        before = session.execute(select(chapters.c.updated_at).where(chapters.c.id == chapter_id)).scalar_one()

    response = _request(app, "PUT", f"/api/series/{series_id}/chapters/reorder", {
        "chapters": [{"id": chapter_id, "order": created_chapter.json()["order"]}],
    })
    assert response.status_code == 200
    with Session(engine, future=True) as session:
        after = session.execute(select(chapters.c.updated_at).where(chapters.c.id == chapter_id)).scalar_one()
    assert after == before


def test_chapter_responses_parse_only_nonempty_string_content(source_database) -> None:
    engine, sessions = source_database
    app = _app(sessions)
    created_series = _request(app, "POST", "/api/series", {"name": "响应内容"})
    series_id = created_series.json()["id"]
    created_chapter = _request(app, "POST", f"/api/series/{series_id}/chapters", {
        "title": "第一集", "content": [],
    })
    chapter_id = created_chapter.json()["id"]

    with Session(engine, future=True) as session:
        session.execute(chapters.update().where(chapters.c.id == chapter_id).values(content=None))
        session.commit()
    sql_null = _request(app, "GET", f"/api/series/{series_id}/chapters")
    assert sql_null.status_code == 200
    assert sql_null.json()[0]["content"] == []

    with Session(engine, future=True) as session:
        session.execute(chapters.update().where(chapters.c.id == chapter_id).values(content="null"))
        session.commit()
    json_null = _request(app, "GET", f"/api/series/{series_id}/chapters")
    assert json_null.status_code == 200
    assert json_null.json()[0]["content"] is None

    with Session(engine, future=True) as session:
        session.execute(chapters.update().where(chapters.c.id == chapter_id).values(content="17"))
        session.commit()
    invalid_response = _request(
        app,
        "GET",
        f"/api/series/{series_id}/chapters",
        raise_app_exceptions=False,
    )
    assert invalid_response.status_code == 500



def test_denied_chapter_write_stops_before_asset_and_private_reads(source_database) -> None:
    engine, sessions = source_database
    now = datetime(2026, 1, 1)
    content = [{"text": "仅所有者可改", "storyboard": ["private-asset"]}]
    with engine.begin() as connection:
        connection.execute(insert(auth_users).values(
            id="user-b",
            username="另一位作者",
            email="other@example.test",
            hashed_password="x",
            is_superuser=False,
            membership_type="free",
            created_at=now,
            password_updated_at=None,
        ))
        connection.execute(insert(series).values(
            id="series-b",
            user_id="user-b",
            name="私人剧集",
            description=None,
            image_url=None,
            style_prompt_id=None,
            team_id=None,
            claimed_by=None,
            claimed_at=None,
            created_at=now,
            updated_at=now,
        ))
        connection.execute(insert(chapters).values(
            id="chapter-b",
            series_id="series-b",
            title="私人章节",
            content=json.dumps(content, ensure_ascii=False),
            order=1,
            created_at=now,
            updated_at=now,
        ))
        connection.execute(insert(storyboard_assets).values(
            id="private-asset",
            series_id="series-b",
            chapter_id="chapter-b",
            frame_index=0,
            name="私人素材",
            description="不可读取",
            image_url="https://media.example/private.jpg",
            created_at=now,
            updated_at=now,
        ))
        connection.execute(insert(storyboard_media_states).values(
            id="private-media",
            chapter_id="chapter-b",
            storyboard_asset_id="private-asset",
            media_revision=1,
            asset_image_digest="asset-digest",
            preview_digest="preview-digest",
            source_valid=True,
            created_at=now,
            updated_at=now,
        ))
        connection.execute(insert(personal_production_notes).values(
            id="private-notes",
            chapter_id="chapter-b",
            user_id="user-b",
            revision=1,
            frame_notes={"private-asset": {"status": "approved", "note": "private"}},
            resume_frame_id="private-asset",
            created_at=now,
            updated_at=now,
        ))

    protected_tables = (
        chapters,
        storyboard_assets,
        storyboard_media_states,
        personal_production_notes,
    )

    def snapshot() -> dict[str, list[dict[str, Any]]]:
        with Session(engine, future=True) as session:
            return {
                table.name: [
                    dict(row)
                    for row in session.execute(
                        select(table).order_by(*table.primary_key.columns)
                    ).mappings().all()
                ]
                for table in protected_tables
            }

    before = snapshot()
    statements: list[str] = []

    def capture_query(connection, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_query)
    try:
        response = _request(
            _app(sessions),
            "PUT",
            "/api/chapters/chapter-b",
            {"title": "越权修改"},
            raise_app_exceptions=False,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_query)

    assert response.status_code == 403
    lowered = [statement.lower().replace(chr(34), "").replace(chr(96), "") for statement in statements]
    assert any("from chapters" in statement for statement in lowered), statements
    assert any("from series" in statement for statement in lowered), statements
    assert not any("storyboard_assets" in statement for statement in lowered), statements
    assert not any("storyboard_media_states" in statement for statement in lowered), statements
    assert not any("personal_production_notes" in statement for statement in lowered), statements
    assert snapshot() == before
