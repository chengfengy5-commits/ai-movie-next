from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_scene_prop_queries.http import (
    build_admin_scene_prop_queries_router,
)
from haoai_backend.admin_scene_prop_queries.tables import Base, PropRow, SceneRow, SeriesRow
from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import user_sessions, users

NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "admin-scene-prop-http-test-secret"


class ObservedSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.closed = False
        self.info["events"].append(
            (self.info["kind"], "open", id(self), threading.get_ident())
        )

    def execute(self, *args, **kwargs):
        self.info["events"].append(
            (self.info["kind"], "execute", id(self), threading.get_ident())
        )
        return super().execute(*args, **kwargs)

    def commit(self) -> None:
        self.commit_count += 1
        self.info["events"].append(
            (self.info["kind"], "commit", id(self), threading.get_ident())
        )
        super().commit()

    def close(self) -> None:
        self.closed = True
        self.info["events"].append(
            (self.info["kind"], "close", id(self), threading.get_ident())
        )
        super().close()


def request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://scene-prop-admin.test",
        ) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(send())


def build_system(path: Path, *, integrated: bool = False):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    Base.metadata.create_all(engine)
    events: list[tuple[str, str, int, int]] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def factory_for(kind: str, opened: list[ObservedSession]):
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def factory() -> ObservedSession:
            events.append((kind, "factory", 0, threading.get_ident()))
            session = maker()
            opened.append(session)
            return session

        return factory

    auth_session_factory = factory_for("auth", auth_sessions)
    business_session_factory = factory_for("business", business_sessions)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {
                    "id": "admin",
                    "username": "admin",
                    "email": "admin@example.test",
                    "hashed_password": "unused",
                    "is_superuser": True,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW - timedelta(days=1),
                    "password_updated_at": None,
                },
                {
                    "id": "member",
                    "username": "member",
                    "email": "member@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "premium",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW - timedelta(days=1),
                    "password_updated_at": None,
                },
                {
                    "id": "expired-admin",
                    "username": "expired-admin",
                    "email": "expired-admin@example.test",
                    "hashed_password": "unused",
                    "is_superuser": True,
                    "membership_type": "premium",
                    "membership_expires_at": NOW - timedelta(seconds=1),
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW - timedelta(days=1),
                    "password_updated_at": None,
                },
            ],
        )
        connection.execute(
            insert(user_sessions),
            [
                {
                    "id": "admin-session",
                    "user_id": "admin",
                    "jti": "admin-jti",
                    "user_agent": "test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": True,
                    "created_at": NOW - timedelta(hours=1),
                    "last_seen_at": NOW - timedelta(minutes=1),
                    "revoked_at": None,
                },
                {
                    "id": "member-session",
                    "user_id": "member",
                    "jti": "member-jti",
                    "user_agent": "test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": True,
                    "created_at": NOW - timedelta(hours=1),
                    "last_seen_at": NOW - timedelta(minutes=1),
                    "revoked_at": None,
                },
                {
                    "id": "expired-admin-session",
                    "user_id": "expired-admin",
                    "jti": "expired-admin-jti",
                    "user_agent": "test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": True,
                    "created_at": NOW - timedelta(hours=1),
                    "last_seen_at": NOW - timedelta(minutes=1),
                    "revoked_at": None,
                },
            ],
        )
        connection.execute(
            insert(SeriesRow.__table__),
            [{"id": "series-a"}, {"id": "series-b"}],
        )
        connection.execute(
            insert(SceneRow.__table__),
            [
                {
                    "id": f"scene-{number:02}",
                    "series_id": "series-a" if number % 2 == 0 else "series-b",
                    "title": f"Scene {number}",
                    "description": None if number % 2 else f"Description {number}",
                    "image_url": None if number % 2 else f"/scene-{number}.png",
                    "aliases": f"alias-{(number * 7) % 11:02}",
                    "canonical_key": f"key-{(number * 3) % 11:02}",
                    "created_at": NOW + timedelta(minutes=number),
                    "updated_at": NOW + timedelta(minutes=10 - number),
                }
                for number in range(11)
            ],
        )
        connection.execute(
            insert(PropRow.__table__),
            [
                {
                    "id": "prop-a",
                    "series_id": "series-a",
                    "name": "Lantern",
                    "description": None,
                    "image_url": "/lantern.png",
                    "aliases": "z-lamp",
                    "canonical_key": "prop|lantern",
                    "created_at": NOW,
                    "updated_at": NOW + timedelta(minutes=2),
                },
                {
                    "id": "prop-b",
                    "series_id": "series-b",
                    "name": "Key",
                    "description": "Brass key",
                    "image_url": None,
                    "aliases": "a-key",
                    "canonical_key": "prop|key",
                    "created_at": NOW + timedelta(minutes=1),
                    "updated_at": NOW + timedelta(minutes=1),
                },
            ],
        )

    runtime = AuthenticationRuntime(
        session_factory=auth_session_factory,
        secret_key=SECRET,
        clock=lambda: NOW,
    )
    authentication_service = AuthenticationService(
        runtime,
        lambda: SqlAlchemyAuthenticationUnitOfWork(auth_session_factory()),
    )
    if integrated:
        from haoai_backend.app import create_app

        app = create_app(
            session_factory=business_session_factory,
            authentication_runtime=runtime,
        )
    else:
        app = FastAPI()
        app.include_router(
            build_admin_scene_prop_queries_router(
                authentication_service=authentication_service,
                session_factory=business_session_factory,
            )
        )

    def token_for(user_id: str, *, jti: str) -> str:
        return jwt.encode(
            {
                "sub": user_id,
                "jti": jti,
                "exp": NOW + timedelta(minutes=5),
            },
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token_for


def test_real_asgi_default_pages_are_unfiltered_and_keep_legacy_dtos(tmp_path: Path) -> None:
    engine, app, _events, _auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-scene-prop.sqlite"
    )
    headers = {"Authorization": f"Bearer {token_for('admin', jti='admin-jti')}"}
    try:
        scene_response = request(
            app,
            "GET",
            "/api/admin/scenes?series_id=series-a",
            headers=headers,
        )
        assert scene_response.status_code == 200, scene_response.text
        scene_page = scene_response.json()
        assert scene_page["total"] == 11
        assert len(scene_page["data"]) == 10
        assert {row["series_id"] for row in scene_page["data"]} == {
            "series-a",
            "series-b",
        }
        assert scene_page["data"][0]["id"] == "scene-10"
        assert set(scene_page["data"][0]) == {
            "id", "series_id", "title", "description", "image_url", "created_at"
        }
        assert "aliases" not in scene_page["data"][0]
        assert "canonical_key" not in scene_page["data"][0]
        assert "updated_at" not in scene_page["data"][0]

        prop_response = request(
            app,
            "GET",
            "/api/admin/props?series_id=series-a",
            headers=headers,
        )
        assert prop_response.status_code == 200, prop_response.text
        prop_page = prop_response.json()
        assert prop_page["total"] == 2
        assert {row["series_id"] for row in prop_page["data"]} == {
            "series-a",
            "series-b",
        }
        assert set(prop_page["data"][0]) == {
            "id", "series_id", "name", "description", "image_url", "created_at"
        }

        scene_detail = request(
            app,
            "GET",
            "/api/admin/scenes/scene-01",
            headers=headers,
        )
        assert scene_detail.status_code == 200
        assert scene_detail.json()["series_id"] == "series-b"
        assert scene_detail.json()["description"] is None

        prop_detail = request(
            app,
            "GET",
            "/api/admin/props/prop-b",
            headers=headers,
        )
        assert prop_detail.status_code == 200
        assert prop_detail.json()["image_url"] is None
        assert prop_detail.json()["description"] == "Brass key"

        for session in business_sessions:
            assert session.commit_count == 0
            assert session.closed
    finally:
        engine.dispose()



def test_create_app_integrates_scene_prop_routes_with_public_auth(tmp_path: Path) -> None:
    from fastapi.routing import APIRoute

    from haoai_backend.app import create_app

    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-scene-prop-integrated.sqlite",
        integrated=True,
    )
    own_routes = {
        ("GET", "/api/admin/scenes"),
        ("GET", "/api/admin/scenes/{scene_id}"),
        ("GET", "/api/admin/props"),
        ("GET", "/api/admin/props/{prop_id}"),
    }
    registered = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert own_routes <= registered

    headers = {"Authorization": f"Bearer {token_for('admin', jti='admin-jti')}"}
    try:
        unauthenticated = request(app, "GET", "/api/admin/scenes?skip=bad")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions
        for path in (
            "/api/admin/scenes",
            "/api/admin/scenes/scene-01",
            "/api/admin/props",
            "/api/admin/props/prop-a",
        ):
            start = len(events)
            response = request(app, "GET", path, headers=headers)
            assert response.status_code == 200, response.text
            request_events = events[start:]
            auth_session = auth_sessions[-1]
            business_session = business_sessions[-1]
            auth_close = next(
                index for index, event in enumerate(request_events)
                if event[0] == "auth" and event[1] == "close" and event[2] == id(auth_session)
            )
            business_open = next(
                index
                for index, event in enumerate(request_events)
                if event[0] == "business"
                and event[1] == "open"
                and event[2] == id(business_session)
            )
            assert auth_close < business_open
            session_events = [event for event in request_events if event[2] == id(business_session)]
            assert {event[3] for event in session_events} == {session_events[0][3]}
            assert session_events[-1][1] == "close"
            assert business_session.closed
            assert business_session.commit_count == 0

        before_denied = len(business_sessions)
        denied = request(
            app,
            "GET",
            "/api/admin/scenes?skip=bad",
            headers={"Authorization": f"Bearer {token_for('member', jti='member-jti')}"},
        )
        assert denied.status_code == 403
        assert len(business_sessions) == before_denied
    finally:
        engine.dispose()

    cold_calls: list[str] = []

    def forbidden_factory():
        cold_calls.append("called")
        raise AssertionError("cold app must not open a session")

    cold_app = create_app(session_factory=forbidden_factory)
    assert cold_calls == []
    for path in (
        "/api/admin/scenes",
        "/api/admin/scenes/scene-01",
        "/api/admin/props",
        "/api/admin/props/prop-a",
    ):
        response = request(cold_app, "GET", path)
        assert response.status_code == 503, response.text
    assert cold_calls == []


def test_auth_context_closes_before_business_worker_and_read_never_commits(tmp_path: Path) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-scene-prop-lifecycle.sqlite"
    )
    headers = {
        "Authorization": f"Bearer {token_for('admin', jti='admin-jti')}"
    }
    request_thread = threading.get_ident()
    try:
        response = request(app, "GET", "/api/admin/scenes", headers=headers)
        assert response.status_code == 200, response.text
        assert auth_sessions and business_sessions
        auth_close = max(
            index
            for index, event in enumerate(events)
            if event[0] == "auth" and event[1] == "close"
        )
        business_open = min(
            index
            for index, event in enumerate(events)
            if event[0] == "business" and event[1] == "open"
        )
        assert auth_close < business_open

        for session in [*auth_sessions, *business_sessions]:
            session_events = [
                event for event in events if event[2] == id(session)
            ]
            assert {event[3] for event in session_events} == {
                session_events[0][3]
            }
            assert session.closed
        assert business_sessions[0].commit_count == 0
        assert events[business_open][3] != request_thread
    finally:
        engine.dispose()


def test_auth_precedes_bad_query_validation_and_requires_admin_only(tmp_path: Path) -> None:
    engine, app, _events, _auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-scene-prop-auth.sqlite"
    )
    try:
        missing = request(app, "GET", "/api/admin/scenes?skip=bad")
        assert missing.status_code == 401
        assert missing.json()["detail"] == "Not authenticated"
        assert missing.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        member_header = {
            "Authorization": f"Bearer {token_for('member', jti='member-jti')}"
        }
        denied = request(
            app,
            "GET",
            "/api/admin/scenes",
            headers=member_header,
        )
        assert denied.status_code == 403
        assert denied.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert not business_sessions

        expired_membership_admin = {
            "Authorization": f"Bearer {token_for('expired-admin', jti='expired-admin-jti')}"
        }
        allowed = request(
            app,
            "GET",
            "/api/admin/props",
            headers=expired_membership_admin,
        )
        assert allowed.status_code == 200
        assert len(business_sessions) == 1
    finally:
        engine.dispose()


def test_missing_rows_and_cold_router_responses(tmp_path: Path) -> None:
    engine, app, _events, _auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-scene-prop-not-found.sqlite"
    )
    admin_header = {
        "Authorization": f"Bearer {token_for('admin', jti='admin-jti')}"
    }
    try:
        missing_scene = request(
            app,
            "GET",
            "/api/admin/scenes/missing",
            headers=admin_header,
        )
        assert missing_scene.status_code == 404
        assert missing_scene.json()["detail"] == "场景不存在"

        missing_prop = request(
            app,
            "GET",
            "/api/admin/props/missing",
            headers=admin_header,
        )
        assert missing_prop.status_code == 404
        assert missing_prop.json()["detail"] == "道具不存在"

        cold_app = FastAPI()
        cold_app.include_router(
            build_admin_scene_prop_queries_router(
                authentication_service=None,
                session_factory=None,
            )
        )
        cold = request(cold_app, "GET", "/api/admin/scenes")
        assert cold.status_code == 503
        assert cold.json()["detail"] == "场景与道具查询服务尚未接线"
    finally:
        engine.dispose()
