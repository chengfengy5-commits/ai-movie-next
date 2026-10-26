"""Composition-level checks for the series-exchange routes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import Any, Callable

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users as authentication_users,
)
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.series_data.tables import (
    chapters,
    characters,
    metadata as series_metadata,
    props,
    scenes,
    series,
    storyboard_assets,
)
from haoai_backend.series_exchange.tables import (
    fused_assets,
    metadata as exchange_metadata,
)
from haoai_backend.teams.tables import (
    metadata as teams_metadata,
    team_members,
    teams,
)


SECRET = "series-exchange-app-integration-secret"


class TrackedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.close_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        super().commit()

    def close(self) -> None:
        self.close_count += 1
        kind = self.info["kind"]
        self.info["events"].append((f"{kind}-close", threading.get_ident()))
        super().close()


@dataclass
class AppSystem:
    engine: Any
    app: FastAPI
    now: datetime
    events: list[tuple[str, int]]
    auth_sessions: list[TrackedSession]
    business_sessions: list[TrackedSession]
    token_for: Callable[..., str]


def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: Any = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://series-exchange-app.test",
        ) as client:
            kwargs: dict[str, Any] = {"headers": headers}
            if body is not None:
                kwargs["json"] = body
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def _build_system(database_path: Path) -> AppSystem:
    engine = create_engine(f"sqlite:///{database_path}", future=True)
    authentication_metadata.create_all(engine)
    teams_metadata.create_all(engine, tables=[teams, team_members])
    series_metadata.create_all(
        engine,
        tables=[series, chapters, characters, scenes, props, storyboard_assets],
    )
    exchange_metadata.create_all(engine)
    notes_metadata.create_all(
        engine,
        tables=[storyboard_media_states, personal_production_notes],
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    events: list[tuple[str, int]] = []
    auth_sessions: list[TrackedSession] = []
    business_sessions: list[TrackedSession] = []

    def make_factory(
        kind: str,
        opened_sessions: list[TrackedSession],
    ) -> Callable[[], TrackedSession]:
        maker = sessionmaker(
            engine,
            class_=TrackedSession,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def open_session() -> TrackedSession:
            events.append((f"{kind}-open", threading.get_ident()))
            session = maker()
            opened_sessions.append(session)
            return session

        return open_session

    auth_factory = make_factory("auth", auth_sessions)
    business_factory = make_factory("business", business_sessions)

    user_rows = []
    for user_id, membership_type, membership_expires_at in (
        ("owner", "premium", None),
        ("peer", "premium", None),
        ("free-user", "free", None),
        ("expired-user", "premium", now - timedelta(days=1)),
    ):
        user_rows.append(
            {
                "id": user_id,
                "username": user_id,
                "email": f"{user_id}@example.test",
                "hashed_password": "unused-local-test-value",
                "is_superuser": False,
                "membership_type": membership_type,
                "membership_expires_at": membership_expires_at,
                "avatar_url": None,
                "bio": None,
                "created_at": now - timedelta(days=10),
                "password_updated_at": now - timedelta(days=10),
            }
        )

    with engine.begin() as connection:
        connection.execute(insert(authentication_users), user_rows)
        connection.execute(
            insert(user_sessions),
            [
                {
                    "id": f"session-{user['id']}",
                    "user_id": user["id"],
                    "jti": f"jti-{user['id']}",
                    "user_agent": "local integration test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": True,
                    "created_at": now - timedelta(hours=1),
                    "last_seen_at": now - timedelta(minutes=30),
                    "revoked_at": None,
                }
                for user in user_rows
            ]
            + [
                {
                    "id": "session-revoked",
                    "user_id": "owner",
                    "jti": "jti-revoked",
                    "user_agent": "local integration test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": False,
                    "created_at": now - timedelta(hours=1),
                    "last_seen_at": now - timedelta(minutes=30),
                    "revoked_at": now - timedelta(minutes=20),
                }
            ],
        )
        connection.execute(
            insert(teams).values(
                id="team-a",
                name="Team A",
                owner_id="owner",
                created_at=now - timedelta(days=1),
            )
        )
        connection.execute(
            insert(team_members).values(
                id="member-owner",
                team_id="team-a",
                user_id="owner",
                role="owner",
                permissions=None,
                joined_at=now - timedelta(days=1),
            )
        )
        connection.execute(
            insert(series),
            [
                {
                    "id": "series-owned",
                    "user_id": "owner",
                    "name": "Owned series",
                    "description": "Existing series",
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": None,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": now - timedelta(days=2),
                    "updated_at": now - timedelta(days=2),
                },
                {
                    "id": "series-peer",
                    "user_id": "peer",
                    "name": "Peer series",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": None,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": now - timedelta(days=2),
                    "updated_at": now - timedelta(days=2),
                },
            ],
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-owned",
                series_id="series-owned",
                title="Existing chapter",
                content=json.dumps([{"text": "keep", "fused": ["fused-old"]}]),
                order=1,
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            )
        )
        connection.execute(
            insert(storyboard_assets).values(
                id="storyboard-owned",
                series_id="series-owned",
                chapter_id="chapter-owned",
                frame_index=0,
                name="Existing storyboard",
                description="keep frame data",
                image_url="https://assets.example.test/existing.png",
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            )
        )
        connection.execute(
            insert(fused_assets),
            [
                {
                    "id": "fused-old",
                    "series_id": "series-owned",
                    "member_type": "character",
                    "members": '[{"kind":"asset","id":"character-old"}]',
                    "name": "Older fused asset",
                    "image_url": "https://assets.example.test/fused.png",
                    "size": "small",
                    "status": "completed",
                    "error_message": None,
                    "created_at": now - timedelta(days=1),
                    "updated_at": now - timedelta(days=1),
                },
                {
                    "id": "fused-new",
                    "series_id": "series-owned",
                    "member_type": "scene",
                    "members": "[]",
                    "name": "Newer fused asset",
                    "image_url": None,
                    "size": None,
                    "status": "failed",
                    "error_message": "provider error",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: now,
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
    )
    codec = JoseTokenCodec(SECRET)

    def token_for(
        user_id: str,
        *,
        jti: str | None = None,
        expires_at: datetime | None = None,
    ) -> str:
        return codec.encode(
            {
                "sub": user_id,
                "jti": jti or f"jti-{user_id}",
                "exp": expires_at
                or datetime.now(timezone.utc) + timedelta(minutes=5),
            }
        )

    events.clear()
    return AppSystem(
        engine,
        app,
        now,
        events,
        auth_sessions,
        business_sessions,
        token_for,
    )


@pytest.fixture
def app_system(tmp_path: Path):
    system = _build_system(tmp_path / "series-exchange-app.sqlite3")
    try:
        yield system
    finally:
        system.engine.dispose()


def _assert_worker_session_order(
    system: AppSystem,
    events: list[tuple[str, int]],
    *,
    loop_thread: int,
) -> None:
    auth_close = next(
        index for index, (name, _) in enumerate(events) if name == "auth-close"
    )
    business_open = next(
        index for index, (name, _) in enumerate(events) if name == "business-open"
    )
    business_close = next(
        index for index, (name, _) in enumerate(events) if name == "business-close"
    )
    assert auth_close < business_open < business_close
    assert events[auth_close][1] != loop_thread
    assert events[business_open][1] != loop_thread
    assert events[business_open][1] == events[business_close][1]
    assert system.auth_sessions[-1].close_count == 1
    assert system.business_sessions[-1].close_count == 1


def test_cold_factory_registers_exact_exchange_routes_without_session_io() -> None:
    session_calls: list[str] = []

    def unexpected_session() -> None:
        session_calls.append("open")
        raise AssertionError("cold app composition must not open a Session")

    app = create_app(session_factory=unexpected_session)
    routes = {
        (method, route.path, route.name)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    expected = {
        ("GET", "/api/series/{series_id}/fused-assets", "get_fused_assets"),
        ("PUT", "/api/fused-assets/{asset_id}", "put_fused_asset"),
        ("DELETE", "/api/fused-assets/{asset_id}", "remove_fused_asset"),
        ("GET", "/api/series/{series_id}/export", "export_series"),
        ("POST", "/api/series/import", "import_series"),
        ("POST", "/api/fused-assets/generate", "generate_fused_asset"),
        ("POST", "/api/storyboard/generate", "generate_storyboard"),
        ("POST", "/api/storyboard/generate-stream", "generate_storyboard_stream"),
        ("POST", "/api/storyboard/create-chapter-with-storyboard", "create_chapter_with_storyboard"),
        ("POST", "/api/storyboard/two-pass", "two_pass_storyboard"),
        ("POST", "/api/storyboard/extract-characters-stream", "extract_characters_stream"),
        ("POST", "/api/storyboard/judge-character-appearance-stream", "judge_character_appearance_stream"),
        ("POST", "/api/storyboard/extract-scenes-stream", "extract_scenes_stream"),
        ("POST", "/api/storyboard/judge-scene-appearance-stream", "judge_scene_appearance_stream"),
        ("POST", "/api/storyboard/extract-props-stream", "extract_props_stream"),
        ("POST", "/api/storyboard/judge-prop-appearance-stream", "judge_prop_appearance_stream"),
        ("POST", "/api/storyboard/filter-and-save-characters", "filter_and_save_characters"),
        ("POST", "/api/storyboard/filter-and-save-characters-stream", "save_stream"),
        ("POST", "/api/storyboard/filter-and-save-scenes-stream", "save_stream"),
        ("POST", "/api/storyboard/filter-and-save-props-stream", "save_stream"),
        ("POST", "/api/chat/image", "chat_image"),
        ("POST", "/api/chat/video", "chat_video"),
        ("POST", "/api/chat/batch-generate-images", "batch_generate_images"),
    }
    assert len(routes) == 252
    assert expected <= routes

    response = _request(
        app,
        "GET",
        "/api/series/series-owned/fused-assets",
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "剧集交换服务尚未接线"}
    assert session_calls == []


def test_app_authentication_precedes_invalid_import_body_and_closes_sessions(
    app_system: AppSystem,
) -> None:
    system = app_system
    invalid_body = {"data": []}

    missing = _request(
        system.app,
        "POST",
        "/api/series/import",
        body=invalid_body,
    )
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"

    free = _request(
        system.app,
        "POST",
        "/api/series/import",
        token=system.token_for("free-user"),
        body=invalid_body,
    )
    assert free.status_code == 403

    expired_membership = _request(
        system.app,
        "POST",
        "/api/series/import",
        token=system.token_for("expired-user"),
        body=invalid_body,
    )
    assert expired_membership.status_code == 403

    revoked = _request(
        system.app,
        "POST",
        "/api/series/import",
        token=system.token_for("owner", jti="jti-revoked"),
        body=invalid_body,
    )
    assert revoked.status_code == 401
    assert revoked.headers["www-authenticate"] == "Bearer"

    expired_token = _request(
        system.app,
        "POST",
        "/api/series/import",
        token=system.token_for(
            "owner",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ),
        body=invalid_body,
    )
    assert expired_token.status_code == 401
    assert expired_token.headers["www-authenticate"] == "Bearer"

    valid_identity_invalid_body = _request(
        system.app,
        "POST",
        "/api/series/import",
        token=system.token_for("owner"),
        body=invalid_body,
    )
    assert valid_identity_invalid_body.status_code == 422
    assert system.business_sessions == []
    assert system.auth_sessions
    assert all(session.close_count == 1 for session in system.auth_sessions)


def test_app_fused_and_import_export_roundtrip_use_one_worker_session(
    app_system: AppSystem,
) -> None:
    system = app_system
    token = system.token_for("owner")
    loop_thread = threading.get_ident()

    start = len(system.events)
    listed = _request(
        system.app,
        "GET",
        "/api/series/series-owned/fused-assets",
        token=token,
    )
    assert listed.status_code == 200
    fused_rows = listed.json()
    assert [row["id"] for row in fused_rows] == ["fused-new", "fused-old"]
    assert set(fused_rows[0]) == {
        "id",
        "series_id",
        "member_type",
        "members",
        "name",
        "image_url",
        "size",
        "status",
        "error_message",
        "created_at",
        "updated_at",
    }
    assert fused_rows[0]["status"] == "failed"
    assert fused_rows[0]["error_message"] == "provider error"
    assert fused_rows[0]["created_at"] == system.now.isoformat()
    _assert_worker_session_order(
        system,
        system.events[start:],
        loop_thread=loop_thread,
    )
    assert system.business_sessions[-1].commit_count == 0

    start = len(system.events)
    updated = _request(
        system.app,
        "PUT",
        "/api/fused-assets/fused-old",
        token=token,
        body={
            "name": "Renamed fused asset",
            "members": [{"id": "character-old"}],
            "image_url": None,
            "size": "large",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed fused asset"
    assert updated.json()["image_url"] == "https://assets.example.test/fused.png"
    assert updated.json()["members"] == [
        {"kind": "asset", "id": "character-old", "url": None, "name": None}
    ]
    _assert_worker_session_order(
        system,
        system.events[start:],
        loop_thread=loop_thread,
    )
    assert system.business_sessions[-1].commit_count == 1

    start = len(system.events)
    deleted = _request(
        system.app,
        "DELETE",
        "/api/fused-assets/fused-old",
        token=token,
    )
    assert deleted.status_code == 204
    assert deleted.content == b""
    _assert_worker_session_order(
        system,
        system.events[start:],
        loop_thread=loop_thread,
    )
    assert system.business_sessions[-1].commit_count == 1
    with Session(system.engine, future=True) as session:
        assert session.execute(
            select(fused_assets.c.id).where(fused_assets.c.id == "fused-old")
        ).scalar_one_or_none() is None
        assert session.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-owned")
        ).scalar_one() == json.dumps(
            [{"text": "keep", "fused": []}]
        )
        assert session.execute(
            select(storyboard_assets.c.id).where(
                storyboard_assets.c.id == "storyboard-owned"
            )
        ).scalar_one() == "storyboard-owned"

    denied_series = _request(
        system.app,
        "GET",
        "/api/series/series-peer/export",
        token=token,
    )
    assert denied_series.status_code == 403

    export_source = {
        "version": 1,
        "series": {"name": "Source series", "description": "Source details"},
        "chapters": [
            {
                "frames": [
                    {
                        "text": "A frame",
                        "character": ["character-old", "unknown-character"],
                        "scene": ["scene-old"],
                        "prop": ["prop-old"],
                        "custom_payload": {"preserve": True, "count": 7},
                    }
                ]
            },
            {"title": "Empty chapter", "frames": []},
        ],
        "characters": [
            {"id": "character-old", "name": "Hero", "gender": ""}
        ],
        "scenes": [{"id": "scene-old", "title": "Room"}],
        "props": [{"id": "prop-old", "name": "Key"}],
        "storyboard_assets": [
            {
                "chapter_title": "",
                "frame_index": 0,
                "name": "Original board",
                "description": "Original board description",
                "image_url": "https://assets.example.test/original-board.png",
            }
        ],
    }
    start = len(system.events)
    denied_import = _request(
        system.app,
        "POST",
        "/api/series/import",
        token=system.token_for("peer"),
        body={"data": export_source, "team_id": "team-a", "claim": True},
    )
    assert denied_import.status_code == 403
    _assert_worker_session_order(
        system,
        system.events[start:],
        loop_thread=loop_thread,
    )
    assert system.business_sessions[-1].commit_count == 0

    start = len(system.events)
    imported = _request(
        system.app,
        "POST",
        "/api/series/import",
        token=token,
        body={
            "data": export_source,
            "name": "Imported series",
            "description": "Imported description",
            "team_id": "team-a",
            "claim": True,
        },
    )
    assert imported.status_code == 200
    imported_result = imported.json()
    assert imported_result["name"] == "Imported series"
    imported_id = imported_result["id"]
    assert imported_id != "series-owned"
    _assert_worker_session_order(
        system,
        system.events[start:],
        loop_thread=loop_thread,
    )
    assert system.business_sessions[-1].commit_count == 1

    with Session(system.engine, future=True) as session:
        stored_series = session.execute(
            select(series).where(series.c.id == imported_id)
        ).mappings().one()
        imported_chapters = session.execute(
            select(chapters)
            .where(chapters.c.series_id == imported_id)
            .order_by(chapters.c.order.desc())
        ).mappings().all()
        imported_character_id = session.execute(
            select(characters.c.id).where(characters.c.series_id == imported_id)
        ).scalar_one()
        imported_scene_id = session.execute(
            select(scenes.c.id).where(scenes.c.series_id == imported_id)
        ).scalar_one()
        imported_prop_id = session.execute(
            select(props.c.id).where(props.c.series_id == imported_id)
        ).scalar_one()
        imported_storyboard = session.execute(
            select(storyboard_assets)
            .where(storyboard_assets.c.series_id == imported_id)
        ).mappings().one()

    assert stored_series["team_id"] == "team-a"
    assert stored_series["claimed_by"] == "owner"
    assert [chapter["title"] for chapter in imported_chapters] == [
        "章节 1",
        "Empty chapter",
    ]
    imported_frame = json.loads(imported_chapters[0]["content"])[0]
    assert imported_frame["character"] == [imported_character_id, "unknown-character"]
    assert imported_frame["scene"] == [imported_scene_id]
    assert imported_frame["prop"] == [imported_prop_id]
    assert imported_frame["custom_payload"] == {"preserve": True, "count": 7}
    assert imported_frame["storyboard"] == [imported_storyboard["id"]]
    assert imported_storyboard["name"] == "Original board"
    assert imported_storyboard["description"] == "Original board description"
    assert imported_storyboard["image_url"] == (
        "https://assets.example.test/original-board.png"
    )

    start = len(system.events)
    exported = _request(
        system.app,
        "GET",
        f"/api/series/{imported_id}/export",
        token=token,
    )
    assert exported.status_code == 200
    exported_data = exported.json()
    assert exported_data["version"] == 1
    assert set(exported_data) == {
        "version",
        "exported_at",
        "series",
        "chapters",
        "characters",
        "scenes",
        "props",
        "storyboard_assets",
    }
    assert exported_data["series"] == {
        "name": "Imported series",
        "description": "Imported description",
    }
    assert [chapter["title"] for chapter in exported_data["chapters"]] == [
        "章节 1",
        "Empty chapter",
    ]
    assert exported_data["chapters"][0]["frames"][0]["custom_payload"] == {
        "preserve": True,
        "count": 7,
    }
    assert exported_data["storyboard_assets"][0]["name"] == "Original board"
    assert exported_data["storyboard_assets"][0]["chapter_index"] == 0
    assert exported_data["storyboard_assets"][0]["chapter_title"] == "章节 1"
    _assert_worker_session_order(
        system,
        system.events[start:],
        loop_thread=loop_thread,
    )
    assert system.business_sessions[-1].commit_count == 0

    start = len(system.events)
    roundtripped = _request(
        system.app,
        "POST",
        "/api/series/import",
        token=token,
        body={"data": exported_data},
    )
    assert roundtripped.status_code == 200
    roundtripped_id = roundtripped.json()["id"]
    assert roundtripped_id != imported_id
    assert system.business_sessions[-1].commit_count == 1
    _assert_worker_session_order(
        system,
        system.events[start:],
        loop_thread=loop_thread,
    )

    with Session(system.engine, future=True) as session:
        roundtrip_character_id = session.execute(
            select(characters.c.id).where(characters.c.series_id == roundtripped_id)
        ).scalar_one()
        roundtrip_scene_id = session.execute(
            select(scenes.c.id).where(scenes.c.series_id == roundtripped_id)
        ).scalar_one()
        roundtrip_prop_id = session.execute(
            select(props.c.id).where(props.c.series_id == roundtripped_id)
        ).scalar_one()
        roundtrip_chapters = session.execute(
            select(chapters)
            .where(chapters.c.series_id == roundtripped_id)
            .order_by(chapters.c.order.desc())
        ).mappings().all()
        roundtrip_storyboard = session.execute(
            select(storyboard_assets.c.id, storyboard_assets.c.name)
            .where(storyboard_assets.c.series_id == roundtripped_id)
        ).mappings().one()

    roundtrip_frame = json.loads(roundtrip_chapters[0]["content"])[0]
    assert [chapter["title"] for chapter in roundtrip_chapters] == [
        "章节 1",
        "Empty chapter",
    ]
    assert roundtrip_character_id != imported_character_id
    assert roundtrip_scene_id != imported_scene_id
    assert roundtrip_prop_id != imported_prop_id
    assert roundtrip_frame["character"] == [roundtrip_character_id, "unknown-character"]
    assert roundtrip_frame["scene"] == [roundtrip_scene_id]
    assert roundtrip_frame["prop"] == [roundtrip_prop_id]
    assert roundtrip_frame["custom_payload"] == {"preserve": True, "count": 7}
    assert roundtrip_frame["storyboard"] == [roundtrip_storyboard["id"]]
    assert roundtrip_storyboard["id"] != imported_storyboard["id"]
    assert roundtrip_storyboard["name"] == "Original board"
