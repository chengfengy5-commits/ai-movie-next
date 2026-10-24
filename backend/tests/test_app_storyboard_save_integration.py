"""Whole-app integration checks for the storyboard-save streaming routes."""

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
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.asset_data.tables import (
    characters,
    metadata as asset_metadata,
    props,
    scenes,
)
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
    metadata as series_metadata,
    series,
    storyboard_assets,
)
from haoai_backend.storyboard_save.errors import StoryboardSaveSourceConflict
from haoai_backend.storyboard_save.persistence import SqlAlchemyStoryboardSaveStore
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams.tables import (
    metadata as teams_metadata,
    team_members,
    teams,
)

SECRET = "storyboard-save-app-integration-synthetic-secret"
SAVE_REQUEST_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "frontend/src/testing/fixtures/storyboardAssetWire.json")
    .read_text(encoding="utf-8")
)["save_requests"]
SAVE_ROUTES = {
    ("POST", "/api/storyboard/filter-and-save-characters", "filter_and_save_characters"),
    ("POST", "/api/storyboard/filter-and-save-characters-stream", "save_stream"),
    ("POST", "/api/storyboard/filter-and-save-scenes-stream", "save_stream"),
    ("POST", "/api/storyboard/filter-and-save-props-stream", "save_stream"),
}


class TrackedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.info["events"].append((f"{self.info['kind']}-execute", threading.get_ident()))
        return super().execute(*args, **kwargs)

    def commit(self) -> None:
        self.commit_count += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_count += 1
        super().rollback()

    def close(self) -> None:
        self.close_count += 1
        self.info["events"].append((f"{self.info['kind']}-close", threading.get_ident()))
        super().close()


@dataclass
class AppSystem:
    engine: Any
    app: FastAPI
    events: list[tuple[str, int]]
    auth_sessions: list[TrackedSession]
    business_sessions: list[TrackedSession]
    token_for: Callable[[str], str]


def _request(
    app: FastAPI,
    path: str,
    *,
    token: str | None = None,
    body: Any = None,
    raw_body: bytes | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if raw_body is not None:
            headers["Content-Type"] = "application/json"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://storyboard-save-app.test",
        ) as client:
            if raw_body is not None:
                return await client.post(path, headers=headers, content=raw_body)
            return await client.post(path, headers=headers, json=body)

    return asyncio.run(send())


def _sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _build_system(database_path: Path) -> AppSystem:
    engine = create_engine(f"sqlite:///{database_path}", future=True)
    authentication_metadata.create_all(engine)
    teams_metadata.create_all(engine, tables=[teams, team_members])
    series_metadata.create_all(
        engine,
        tables=[series, chapters, storyboard_assets],
    )
    asset_metadata.create_all(engine, tables=[characters, scenes, props])
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
        opened: list[TrackedSession],
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
            opened.append(session)
            return session

        return open_session

    auth_factory = make_factory("auth", auth_sessions)
    business_factory = make_factory("business", business_sessions)

    user_rows = []
    for user_id, membership_type, membership_expires_at in (
        ("owner", "premium", None),
        ("teammate", "premium", None),
        ("peer", "premium", None),
        ("free-user", "free", None),
        ("expired-user", "premium", now - timedelta(days=1)),
    ):
        user_rows.append(
            {
                "id": user_id,
                "username": user_id,
                "email": f"{user_id}@example.test",
                "hashed_password": "synthetic-integration-fixture",
                "is_superuser": False,
                "membership_type": membership_type,
                "membership_expires_at": membership_expires_at,
                "avatar_url": None,
                "bio": None,
                "created_at": now - timedelta(days=10),
                "password_updated_at": now - timedelta(days=10),
            }
        )

    frames = [
        {
            "text": "保持该帧",
            "storyboard": ["story-owned"],
            "extra": {"preserved": True},
        }
    ]
    with engine.begin() as connection:
        connection.execute(insert(authentication_users), user_rows)
        connection.execute(
            insert(user_sessions),
            [
                {
                    "id": f"auth-session-{row['id']}",
                    "user_id": row["id"],
                    "jti": f"jti-{row['id']}",
                    "user_agent": "local integration test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": True,
                    "created_at": now - timedelta(hours=1),
                    "last_seen_at": now - timedelta(minutes=5),
                    "revoked_at": None,
                }
                for row in user_rows
            ],
        )
        connection.execute(
            insert(teams).values(
                id="team-a",
                name="Integration team",
                owner_id="owner",
                created_at=now - timedelta(days=2),
            )
        )
        connection.execute(
            insert(team_members),
            [
                {
                    "id": "team-owner",
                    "team_id": "team-a",
                    "user_id": "owner",
                    "role": "owner",
                    "permissions": None,
                    "joined_at": now - timedelta(days=2),
                },
                {
                    "id": "team-teammate",
                    "team_id": "team-a",
                    "user_id": "teammate",
                    "role": "viewer",
                    "permissions": '["enter_claimed_series"]',
                    "joined_at": now - timedelta(days=2),
                },
            ],
        )
        connection.execute(
            insert(series),
            [
                {
                    "id": "series-owned",
                    "user_id": "owner",
                    "name": "Owned series",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": None,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": now - timedelta(days=3),
                    "updated_at": now - timedelta(days=3),
                },
                {
                    "id": "series-owned-alt",
                    "user_id": "owner",
                    "name": "Second owned series",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": None,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": now - timedelta(days=3),
                    "updated_at": now - timedelta(days=3),
                },
                {
                    "id": "series-claimed",
                    "user_id": "owner",
                    "name": "Claimed series",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": "team-a",
                    "claimed_by": "owner",
                    "claimed_at": now - timedelta(days=1),
                    "created_at": now - timedelta(days=3),
                    "updated_at": now - timedelta(days=1),
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
                    "created_at": now - timedelta(days=3),
                    "updated_at": now - timedelta(days=3),
                },
            ],
        )
        connection.execute(
            insert(chapters),
            [
                {
                    "id": "chapter-owned",
                    "series_id": "series-owned",
                    "title": "Owned chapter",
                    "content": json.dumps(frames, ensure_ascii=False),
                    "order": 1,
                    "created_at": now - timedelta(hours=2),
                    "updated_at": now - timedelta(hours=2),
                },
                {
                    "id": "chapter-claimed",
                    "series_id": "series-claimed",
                    "title": "Claimed chapter",
                    "content": json.dumps([{"text": "team frame"}], ensure_ascii=False),
                    "order": 1,
                    "created_at": now - timedelta(hours=2),
                    "updated_at": now - timedelta(hours=2),
                },
            ],
        )
        connection.execute(
            insert(storyboard_assets).values(
                id="story-owned",
                series_id="series-owned",
                chapter_id="chapter-owned",
                frame_index=0,
                name="Storyboard frame",
                description=None,
                image_url="fixture://storyboard/owned.png",
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            )
        )
        connection.execute(
            insert(props).values(
                id="foreign-prop",
                series_id="series-peer",
                name="Peer's prop",
                description=None,
                image_url=None,
                aliases="[]",
                canonical_key="peer-prop",
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            )
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

    def token_for(user_id: str) -> str:
        return codec.encode(
            {
                "sub": user_id,
                "jti": f"jti-{user_id}",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            }
        )

    events.clear()
    return AppSystem(engine, app, events, auth_sessions, business_sessions, token_for)


@pytest.fixture
def app_system(tmp_path: Path):
    system = _build_system(tmp_path / "storyboard-save-app.sqlite3")
    try:
        yield system
    finally:
        system.engine.dispose()


def _assert_session_handoff(
    system: AppSystem,
    event_slice: list[tuple[str, int]],
    *,
    loop_thread: int,
) -> None:
    auth_close = next(i for i, (name, _) in enumerate(event_slice) if name == "auth-close")
    business_open = next(i for i, (name, _) in enumerate(event_slice) if name == "business-open")
    business_execute = next(
        i for i, (name, _) in enumerate(event_slice) if name == "business-execute"
    )
    business_close = next(i for i, (name, _) in enumerate(event_slice) if name == "business-close")
    assert auth_close < business_open < business_execute < business_close
    assert event_slice[auth_close][1] != loop_thread
    assert event_slice[business_open][1] != loop_thread
    assert event_slice[business_open][1] == event_slice[business_close][1]
    assert event_slice[business_open][1] == event_slice[business_execute][1]
    assert system.auth_sessions[-1].close_count == 1
    assert system.business_sessions[-1].close_count == 1


def test_composed_app_mounts_exact_save_routes_and_fails_closed_without_ports() -> None:
    session_calls: list[str] = []

    def unexpected_session() -> None:
        session_calls.append("open")
        raise AssertionError("missing identity must fail before opening a Session")

    app = create_app(session_factory=unexpected_session)
    routes = {
        (method, route.path, route.name)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert SAVE_ROUTES <= routes

    response = _request(
        app,
        "/api/storyboard/filter-and-save-scenes-stream",
        body={
            "chapter_id": "chapter-owned",
            "scenes": [{"title": "scene"}],
            "appearances": [["scene"]],
        },
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "分镜保存服务尚未接线"}
    assert session_calls == []


def test_non_stream_route_uses_real_auth_before_body_and_persists_on_owned_worker(
    app_system: AppSystem,
) -> None:
    system = app_system
    path = "/api/storyboard/filter-and-save-characters"

    missing = _request(system.app, path, raw_body=b"[")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert system.business_sessions == []

    expired_jwt = JoseTokenCodec(SECRET).encode(
        {
            "sub": "owner",
            "jti": "jti-owner",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        }
    )
    expired = _request(system.app, path, token=expired_jwt, raw_body=b"[")
    assert expired.status_code == 401
    assert expired.headers["www-authenticate"] == "Bearer"

    with system.engine.begin() as connection:
        connection.execute(
            update(user_sessions)
            .where(user_sessions.c.id == "auth-session-owner")
            .values(is_active=False)
        )
    revoked = _request(
        system.app, path, token=system.token_for("owner"), raw_body=b"["
    )
    assert revoked.status_code == 401
    assert revoked.headers["www-authenticate"] == "Bearer"
    with system.engine.begin() as connection:
        connection.execute(
            update(user_sessions)
            .where(user_sessions.c.id == "auth-session-owner")
            .values(is_active=True)
        )

    for user_id in ("free-user", "expired-user"):
        event_start = len(system.events)
        denied = _request(
            system.app, path, token=system.token_for(user_id), raw_body=b"["
        )
        assert denied.status_code == 403
        assert not any(
            name == "business-open" for name, _ in system.events[event_start:]
        )
        assert system.auth_sessions[-1].close_count == 1

    event_start = len(system.events)
    malformed = _request(
        system.app, path, token=system.token_for("owner"), raw_body=b"["
    )
    assert malformed.status_code == 500
    assert malformed.json() == {"detail": "过滤角色失败"}
    assert system.auth_sessions[-1].close_count == 1
    assert not any(
        name == "business-open" for name, _ in system.events[event_start:]
    )

    event_start = len(system.events)
    loop_thread = threading.get_ident()
    response = _request(
        system.app,
        path,
        token=system.token_for("owner"),
        body={
            "chapter_id": "chapter-owned",
            "characters": [
                {
                    "name": "林澈",
                    "gender": "女",
                    "age": "17",
                    "role": "主角",
                    "appearance": "短发",
                }
            ],
            "appearances": [["林澈"]],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["total_count"] == 1
    _assert_session_handoff(
        system, system.events[event_start:], loop_thread=loop_thread
    )
    assert system.business_sessions[-1].commit_count == 2

    with system.engine.connect() as connection:
        character_id = connection.execute(
            select(characters.c.id).where(
                characters.c.series_id == "series-owned",
                characters.c.name == "林澈",
            )
        ).scalar_one()
        content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-owned")
            ).scalar_one()
        )
        assert content[0]["character"] == [character_id]
        assert connection.execute(
            select(storyboard_media_states.c.id).where(
                storyboard_media_states.c.chapter_id == "chapter-owned"
            )
        ).first() is not None


def test_all_save_routes_stream_and_persist_with_shared_auth_and_business_sessions(
    app_system: AppSystem,
) -> None:
    system = app_system
    token = system.token_for("owner")
    loop_thread = threading.get_ident()
    requests = (
        (
            "/api/storyboard/filter-and-save-characters-stream",
            {
                "chapter_id": "chapter-owned",
                "merged_characters": [
                    {
                        "name": "林澈",
                        "appearance": "短发",
                        "original_name": "旧称林同学",
                    }
                ],
                "appearances": [["旧称林同学"]],
            },
        ),
        (
            "/api/storyboard/filter-and-save-scenes-stream",
            {
                "chapter_id": "chapter-owned",
                "scenes": [
                    {"title": "旧书房", "description": "木质书架", "original_name": "书房"}
                ],
                "appearances": [["书房"]],
            },
        ),
        (
            "/api/storyboard/filter-and-save-props-stream",
            {
                "chapter_id": "chapter-owned",
                "props": [{"name": "铜铃", "description": "小铃", "original_name": "小铃"}],
                "appearances": [["小铃"]],
            },
        ),
    )

    for path, body in requests:
        event_start = len(system.events)
        response = _request(system.app, path, token=token, body=body)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _sse_events(response)
        assert events[0]["type"] == "start"
        assert events[-1] == {"type": "done"}
        assert any(
            event.get("type") == "complete" and event.get("success")
            for event in events
        )
        assert not any(event.get("type") == "error" for event in events)
        _assert_session_handoff(
            system,
            system.events[event_start:],
            loop_thread=loop_thread,
        )
        assert system.business_sessions[-1].commit_count >= 3

    with system.engine.connect() as connection:
        character_row = connection.execute(
            select(characters.c.id, characters.c.aliases).where(
                characters.c.series_id == "series-owned",
                characters.c.name == "林澈",
            )
        ).one()
        scene_row = connection.execute(
            select(scenes.c.id, scenes.c.aliases).where(
                scenes.c.series_id == "series-owned",
                scenes.c.title == "旧书房",
            )
        ).one()
        prop_row = connection.execute(
            select(props.c.id, props.c.aliases).where(
                props.c.series_id == "series-owned",
                props.c.name == "铜铃",
            )
        ).one()
        content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-owned")
            ).scalar_one()
        )
        assert "旧称林同学" in character_row.aliases
        assert "书房" in scene_row.aliases
        assert "小铃" in prop_row.aliases
        assert content[0]["character"] == [character_row.id]
        assert content[0]["scene"] == [scene_row.id]
        assert content[0]["prop"] == [prop_row.id]
        assert content[0]["storyboard"] == ["story-owned"]
        assert content[0]["extra"] == {"preserved": True}
        assert connection.execute(
            select(storyboard_media_states.c.id).where(
                storyboard_media_states.c.chapter_id == "chapter-owned"
            )
        ).first() is not None


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
def test_save_stream_rejects_stale_expected_chapter_source_before_writing(
    app_system: AppSystem,
    kind: str,
) -> None:
    system = app_system
    contract = SAVE_REQUEST_FIXTURE[kind]
    with system.engine.begin() as connection:
        expected_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-owned")
            ).scalar_one()
        )
        current_content = [
            {
                **expected_content[0],
                "text": "并发编辑后的新镜头文本",
                "extra": {"preserved": True, "revision": "B"},
            }
        ]
        connection.execute(
            update(chapters)
            .where(chapters.c.id == "chapter-owned")
            .values(content=json.dumps(current_content, ensure_ascii=False))
        )

    response = _request(
        system.app,
        contract["path"],
        token=system.token_for("owner"),
        body={
            **contract["wire"],
            "chapter_id": "chapter-owned",
            "expected_content": expected_content,
        },
    )

    assert response.status_code == 409
    assert not any(
        event.get("type") == "complete" and event.get("success")
        for event in _sse_events(response)
    )
    assert system.business_sessions[-1].commit_count == 0
    with system.engine.connect() as connection:
        assert connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-owned")
        ).scalar_one() == json.dumps(current_content, ensure_ascii=False)
        asset_table = {"character": characters, "scene": scenes, "prop": props}[kind]
        assert connection.execute(
            select(asset_table.c.id).where(asset_table.c.series_id == "series-owned")
        ).all() == []


def test_save_stream_rejects_chapter_moved_to_another_accessible_series_before_writing(
    app_system: AppSystem,
) -> None:
    system = app_system
    contract = SAVE_REQUEST_FIXTURE["scene"]
    expected_content = SAVE_REQUEST_FIXTURE["source_content"]
    with system.engine.begin() as connection:
        connection.execute(
            update(chapters)
            .where(chapters.c.id == "chapter-owned")
            .values(series_id="series-owned-alt")
        )

    response = _request(
        system.app,
        contract["path"],
        token=system.token_for("owner"),
        body={
            **contract["wire"],
            "chapter_id": "chapter-owned",
            "expected_content": expected_content,
            "expected_series_id": "series-owned",
        },
    )

    assert response.status_code == 409
    assert system.business_sessions[-1].commit_count == 0
    assert system.business_sessions[-1].close_count == 1
    with system.engine.connect() as connection:
        assert connection.execute(
            select(chapters.c.series_id).where(chapters.c.id == "chapter-owned")
        ).scalar_one() == "series-owned-alt"
        assert connection.execute(select(scenes.c.id)).all() == []


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
@pytest.mark.parametrize(
    "expected_series_id",
    ["series-owned", "series-peer"],
    ids=["matches-chapter", "does-not-match-chapter"],
)
def test_unauthorized_actor_cannot_probe_chapter_series_with_source_guard(
    app_system: AppSystem,
    kind: str,
    expected_series_id: str,
) -> None:
    system = app_system
    contract = SAVE_REQUEST_FIXTURE[kind]
    response = _request(
        system.app,
        contract["path"],
        token=system.token_for("peer"),
        body={
            **contract["wire"],
            "chapter_id": "chapter-owned",
            "expected_content": SAVE_REQUEST_FIXTURE["source_content"],
            "expected_series_id": expected_series_id,
        },
    )

    assert response.status_code == 403
    assert system.business_sessions[-1].commit_count == 0
    assert system.business_sessions[-1].close_count == 1
    asset_table = {"character": characters, "scene": scenes, "prop": props}[kind]
    with system.engine.connect() as connection:
        assert connection.execute(
            select(asset_table.c.id).where(asset_table.c.series_id == "series-owned")
        ).all() == []


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
def test_shared_save_request_fixture_reaches_real_route_and_dry_run_is_read_only(
    app_system: AppSystem,
    kind: str,
) -> None:
    system = app_system
    contract = SAVE_REQUEST_FIXTURE[kind]
    body = {**contract["wire"], "chapter_id": "chapter-owned"}
    response = _request(
        system.app,
        contract["path"],
        token=system.token_for("owner"),
        body=body,
    )

    assert response.status_code == 200
    events = _sse_events(response)
    assert any(
        event.get("type") == "complete" and event.get("success") is True
        and event.get("dry_run") is True
        for event in events
    )
    assert system.business_sessions[-1].commit_count == 0
    with system.engine.connect() as connection:
        assert connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-owned")
        ).scalar_one() == json.dumps(SAVE_REQUEST_FIXTURE["source_content"], ensure_ascii=False)
        assert connection.execute(
            select(characters.c.id).where(characters.c.series_id == "series-owned")
        ).all() == []
        assert connection.execute(
            select(scenes.c.id).where(scenes.c.series_id == "series-owned")
        ).all() == []


@pytest.mark.parametrize("invalid", [None, "frames", {"frame": 1}, float("nan")])
def test_explicit_invalid_expected_content_is_422_before_business_session(
    app_system: AppSystem,
    invalid: Any,
) -> None:
    system = app_system
    body = {
        **SAVE_REQUEST_FIXTURE["scene"]["wire"],
        "chapter_id": "chapter-owned",
        "expected_content": invalid,
    }

    request_content: dict[str, Any] = {"body": body}
    if isinstance(invalid, float) and invalid != invalid:
        request_content = {
            "raw_body": json.dumps(body, ensure_ascii=False, allow_nan=True).encode("utf-8")
        }
    response = _request(
        system.app,
        SAVE_REQUEST_FIXTURE["scene"]["path"],
        token=system.token_for("owner"),
        **request_content,
    )

    assert response.status_code == 422
    assert system.business_sessions == []


@pytest.mark.parametrize("invalid", [None, "", "   ", True, 1])
def test_explicit_invalid_expected_series_id_is_422_before_business_session(
    app_system: AppSystem,
    invalid: Any,
) -> None:
    system = app_system
    body = {
        **SAVE_REQUEST_FIXTURE["scene"]["wire"],
        "chapter_id": "chapter-owned",
        "expected_series_id": invalid,
    }

    response = _request(
        system.app,
        SAVE_REQUEST_FIXTURE["scene"]["path"],
        token=system.token_for("owner"),
        body=body,
    )

    assert response.status_code == 422
    assert system.business_sessions == []


@pytest.mark.parametrize(
    "mutation",
    ["content", "chapter_series", "same_access_series", "permission"],
)
def test_source_or_access_change_after_asset_phase_keeps_committed_asset_and_stops_binding(
    app_system: AppSystem,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    system = app_system
    contract = SAVE_REQUEST_FIXTURE["scene"]
    body = {**contract["wire"], "chapter_id": "chapter-owned", "dry_run": False}
    original_commit = SqlAlchemyStoryboardSaveStore.commit
    changed = False
    changed_content = [
        {**SAVE_REQUEST_FIXTURE["source_content"][0], "text": "并发提交的版本B"}
    ]

    def commit_with_concurrent_change(store: SqlAlchemyStoryboardSaveStore) -> None:
        nonlocal changed
        original_commit(store)
        if changed:
            return
        changed = True
        with system.engine.begin() as connection:
            if mutation == "content":
                connection.execute(
                    update(chapters)
                    .where(chapters.c.id == "chapter-owned")
                    .values(content=json.dumps(changed_content, ensure_ascii=False))
                )
            elif mutation == "chapter_series":
                connection.execute(
                    update(chapters)
                    .where(chapters.c.id == "chapter-owned")
                    .values(series_id="series-peer")
                )
            elif mutation == "same_access_series":
                connection.execute(
                    update(chapters)
                    .where(chapters.c.id == "chapter-owned")
                    .values(series_id="series-owned-alt")
                )
            else:
                connection.execute(
                    update(series)
                    .where(series.c.id == "series-owned")
                    .values(user_id="peer")
                )

    monkeypatch.setattr(SqlAlchemyStoryboardSaveStore, "commit", commit_with_concurrent_change)
    if mutation == "same_access_series":
        body.pop("expected_series_id", None)
    response = _request(
        system.app,
        contract["path"],
        token=system.token_for("owner"),
        body=body,
    )

    assert changed
    assert response.status_code == 200
    events = _sse_events(response)
    errors = [event for event in events if event.get("type") == "error"]
    assert errors
    assert not any(event.get("type") == "complete" and event.get("success") for event in events)
    assert "已提交的素材阶段仍保留" in errors[0].get("message", "")
    with system.engine.connect() as connection:
        assert connection.execute(
            select(scenes.c.id).where(
                scenes.c.series_id == "series-owned",
                scenes.c.title == "夜色书房",
            )
        ).first() is not None
        chapter = connection.execute(
            select(chapters.c.series_id, chapters.c.content).where(
                chapters.c.id == "chapter-owned"
            )
        ).one()
        if mutation == "content":
            assert chapter.series_id == "series-owned"
            assert chapter.content == json.dumps(changed_content, ensure_ascii=False)
        elif mutation == "chapter_series":
            assert chapter.series_id == "series-peer"
            assert chapter.content == json.dumps(SAVE_REQUEST_FIXTURE["source_content"], ensure_ascii=False)
        elif mutation == "same_access_series":
            assert chapter.series_id == "series-owned-alt"
            assert chapter.content == json.dumps(SAVE_REQUEST_FIXTURE["source_content"], ensure_ascii=False)
        else:
            assert chapter.series_id == "series-owned"
            assert chapter.content == json.dumps(SAVE_REQUEST_FIXTURE["source_content"], ensure_ascii=False)


@pytest.mark.parametrize("no_op", [False, True], ids=["changed-content", "expected-no-op"])
def test_chapter_content_cas_rejects_stale_raw_source_and_rolls_back_reconciliation(
    app_system: AppSystem,
    no_op: bool,
) -> None:
    system = app_system
    session = Session(system.engine, expire_on_commit=False)
    store = SqlAlchemyStoryboardSaveStore(session)
    chapter = store.load_chapter("chapter-owned")
    assert chapter is not None
    session.commit()
    changed_content = [
        {**SAVE_REQUEST_FIXTURE["source_content"][0], "text": "并发提交的版本B"}
    ]
    with system.engine.begin() as connection:
        connection.execute(
            update(chapters)
            .where(chapters.c.id == "chapter-owned")
            .values(content=json.dumps(changed_content, ensure_ascii=False))
        )

    content_to_write = chapter.content if no_op else json.dumps(
        [{"text": "准备覆盖B", "storyboard": [], "extra": {"preserved": True}}],
        ensure_ascii=False,
    )
    with pytest.raises(StoryboardSaveSourceConflict):
        store.update_chapter_content(
            chapter,
            TrustedActor("owner"),
            content_to_write,
            require_current_source=True,
        )
    session.close()

    with system.engine.connect() as connection:
        assert connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-owned")
        ).scalar_one() == json.dumps(changed_content, ensure_ascii=False)
        assert connection.execute(
            select(storyboard_media_states.c.id).where(
                storyboard_media_states.c.chapter_id == "chapter-owned"
            )
        ).all() == []


def test_chapter_content_cas_uses_is_null_for_raw_null_source(app_system: AppSystem) -> None:
    system = app_system
    with system.engine.begin() as connection:
        connection.execute(
            update(chapters)
            .where(chapters.c.id == "chapter-owned")
            .values(content=None)
        )
    session = Session(system.engine, expire_on_commit=False)
    store = SqlAlchemyStoryboardSaveStore(session)
    chapter = store.load_chapter("chapter-owned")
    assert chapter is not None and chapter.content is None
    session.commit()
    changed_content = [{"text": "并发提交的版本B"}]
    with system.engine.begin() as connection:
        connection.execute(
            update(chapters)
            .where(chapters.c.id == "chapter-owned")
            .values(content=json.dumps(changed_content, ensure_ascii=False))
        )

    with pytest.raises(StoryboardSaveSourceConflict):
        store.update_chapter_content(
            chapter,
            TrustedActor("owner"),
            json.dumps([{"text": "不能覆盖B"}], ensure_ascii=False),
            require_current_source=True,
        )
    session.close()

    with system.engine.connect() as connection:
        assert connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-owned")
        ).scalar_one() == json.dumps(changed_content, ensure_ascii=False)
        assert connection.execute(
            select(storyboard_media_states.c.id).where(
                storyboard_media_states.c.chapter_id == "chapter-owned"
            )
        ).all() == []
        assert connection.execute(
            select(props.c.id).where(props.c.series_id == "series-owned")
        ).all() == []


def test_authentication_precedes_malformed_json_and_denials_never_open_business_session(
    app_system: AppSystem,
) -> None:
    system = app_system
    path = "/api/storyboard/filter-and-save-characters-stream"

    missing = _request(system.app, path, raw_body=b"[")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert system.business_sessions == []

    for user_id in ("free-user", "expired-user"):
        event_start = len(system.events)
        response = _request(
            system.app,
            path,
            token=system.token_for(user_id),
            raw_body=b"[",
        )
        assert response.status_code == 403
        assert system.events[event_start:]
        assert not any(name == "business-open" for name, _ in system.events[event_start:])
        assert system.auth_sessions[-1].close_count == 1
        assert system.auth_sessions[-1].info["kind"] == "auth"

    assert system.business_sessions == []


def test_claimed_team_permission_controls_storyboard_save_access(
    app_system: AppSystem,
) -> None:
    system = app_system
    loop_thread = threading.get_ident()
    path = "/api/storyboard/filter-and-save-scenes-stream"
    body = {
        "chapter_id": "chapter-claimed",
        "scenes": [{"title": "团队场景"}],
        "appearances": [["团队场景"]],
    }

    event_start = len(system.events)
    allowed = _request(system.app, path, token=system.token_for("teammate"), body=body)
    assert allowed.status_code == 200
    assert any(
        event.get("type") == "complete" and event.get("success")
        for event in _sse_events(allowed)
    )
    _assert_session_handoff(
        system,
        system.events[event_start:],
        loop_thread=loop_thread,
    )

    event_start = len(system.events)
    denied = _request(system.app, path, token=system.token_for("peer"), body=body)
    assert denied.status_code == 403
    _assert_session_handoff(
        system,
        system.events[event_start:],
        loop_thread=loop_thread,
    )
    with system.engine.connect() as connection:
        saved_claimed_scenes = connection.execute(
            select(scenes.c.id).where(
                scenes.c.series_id == "series-claimed",
                scenes.c.title == "团队场景",
            )
        ).all()
        assert len(saved_claimed_scenes) == 1


def test_foreign_series_asset_reference_is_rejected_before_any_save_commit(
    app_system: AppSystem,
) -> None:
    system = app_system
    response = _request(
        system.app,
        "/api/storyboard/filter-and-save-props-stream",
        token=system.token_for("owner"),
        body={
            "chapter_id": "chapter-owned",
            "props": [
                {
                    "name": "跨剧集引用",
                    "existing_id": "foreign-prop",
                    "is_existing": True,
                }
            ],
            "appearances": [["跨剧集引用"]],
        },
    )
    assert response.status_code == 200
    events = _sse_events(response)
    assert any(
        event.get("type") == "error"
        and event.get("message") == "素材引用不属于当前剧集"
        for event in events
    )
    assert events[-1] == {"type": "done"}
    business = system.business_sessions[-1]
    assert business.commit_count == 0
    assert business.rollback_count >= 1
    assert business.close_count == 1
    with system.engine.connect() as connection:
        assert connection.execute(
            select(props.c.id, props.c.aliases).where(props.c.id == "foreign-prop")
        ).one() == ("foreign-prop", "[]")
        assert connection.execute(
            select(props.c.id).where(
                props.c.series_id == "series-owned",
                props.c.name == "跨剧集引用",
            )
        ).first() is None
