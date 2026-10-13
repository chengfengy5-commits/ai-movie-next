from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_chapter_character_queries.http import (
    build_admin_chapter_character_queries_router,
)
from haoai_backend.admin_chapter_character_queries.tables import metadata as query_metadata
from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)


NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "admin-chapter-character-http-test-secret"


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.closed = False
        self.info["events"].append(
            (self.info["kind"], "open", id(self), threading.get_ident())
        )

    def execute(self, *args: Any, **kwargs: Any):
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
            base_url="http://admin-chapter-character.test",
        ) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(send())


def build_system(tmp_path, *, business_factory: bool = True, integrated: bool = False):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'chapter-character.sqlite'}")
    authentication_metadata.create_all(engine)
    query_metadata.create_all(engine)
    events: list[tuple[str, str, int, int]] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def make_factory(kind: str, opened: list[ObservedSession]):
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

    auth_factory = make_factory("auth", auth_sessions)
    business_session_factory = make_factory("business", business_sessions)
    with Session(engine) as session:
        for user_id, is_admin, membership, membership_expiry in (
            ("admin", True, "free", None),
            ("member", False, "free", None),
            ("expired-admin", True, "premium", NOW - timedelta(seconds=1)),
        ):
            session.execute(
                insert(users).values(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.test",
                    hashed_password="unused",
                    is_superuser=is_admin,
                    membership_type=membership,
                    membership_expires_at=membership_expiry,
                    avatar_url=None,
                    bio=None,
                    created_at=NOW - timedelta(days=1),
                    password_updated_at=None,
                )
            )
        session.execute(
            insert(user_sessions),
            [
                {
                    "id": f"{user_id}-session",
                    "user_id": user_id,
                    "jti": f"{user_id}-jti",
                    "user_agent": "test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": True,
                    "created_at": NOW - timedelta(hours=1),
                    "last_seen_at": NOW - timedelta(minutes=1),
                    "revoked_at": None,
                }
                for user_id in ("admin", "member", "expired-admin")
            ],
        )
        session.execute(
            insert(query_metadata.tables["series"]),
            [{"id": "s1"}],
        )
        session.execute(
            insert(query_metadata.tables["chapters"]),
            [
                {
                    "id": "chapter-1",
                    "series_id": "s1",
                    "title": "First",
                    "content": "private body",
                    "order": 1,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
            ],
        )
        session.execute(
            insert(query_metadata.tables["characters"]),
            [
                {
                    "id": "character-1",
                    "series_id": "s1",
                    "name": "Hero",
                    "gender": None,
                    "age": None,
                    "role": None,
                    "appearance": "full",
                    "description": "private",
                    "image_url": "hero.png",
                    "audio_url": "private audio",
                    "voice_ref": "private voice",
                    "aliases": "[]",
                    "canonical_key": "hero",
                    "created_at": NOW,
                    "updated_at": NOW,
                }
            ],
        )
        session.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: NOW.replace(tzinfo=timezone.utc),
    )
    service = AuthenticationService(
        runtime,
        lambda: SqlAlchemyAuthenticationUnitOfWork(auth_factory()),
    )
    if integrated:
        from haoai_backend.app import create_app

        app = create_app(
            session_factory=business_session_factory if business_factory else None,
            authentication_runtime=runtime,
        )
    else:
        app = FastAPI()
        app.include_router(
            build_admin_chapter_character_queries_router(
                authentication_service=service,
                session_factory=business_session_factory if business_factory else None,
            )
        )

    def token(user_id: str) -> str:
        return jwt.encode(
            {
                "sub": user_id,
                "jti": f"{user_id}-jti",
                "exp": NOW.replace(tzinfo=timezone.utc) + timedelta(minutes=5),
            },
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token, service


def test_public_auth_routes_exact_projections_and_worker_lifecycle(tmp_path) -> None:
    engine, app, events, auth_sessions, business_sessions, token, _ = build_system(tmp_path)
    headers = {"Authorization": f"Bearer {token('admin')}"}
    try:
        first_request_start = len(events)
        chapters = request(app, "GET", "/api/admin/chapters", headers=headers)
        assert chapters.status_code == 200, chapters.text
        assert chapters.json()["total"] == 1
        assert set(chapters.json()["data"][0]) == {
            "id", "series_id", "title", "order", "created_at"
        }
        assert len(business_sessions) == 1
        first_request_events = events[first_request_start:]
        auth_close = next(
            i for i, event in enumerate(first_request_events)
            if event[0] == "auth" and event[1] == "close"
        )
        business_open = next(
            i for i, event in enumerate(first_request_events)
            if event[0] == "business" and event[1] == "open"
        )
        assert auth_close < business_open
        first_business_threads = {
            event[3]
            for event in first_request_events
            if event[0] == "business"
        }
        assert len(first_business_threads) == 1
        assert threading.get_ident() not in first_business_threads

        chapter = request(app, "GET", "/api/admin/chapters/chapter-1", headers=headers)
        assert chapter.status_code == 200
        assert set(chapter.json()) == {"id", "series_id", "title", "order", "created_at"}

        characters = request(app, "GET", "/api/admin/characters", headers=headers)
        assert characters.status_code == 200
        assert set(characters.json()["data"][0]) == {
            "id", "series_id", "name", "gender", "age", "role", "created_at"
        }
        detail = request(app, "GET", "/api/admin/characters/character-1", headers=headers)
        assert detail.status_code == 200
        assert set(detail.json()) == {
            "id", "series_id", "name", "gender", "age", "role", "created_at",
            "appearance", "description", "image_url",
        }
        assert "audio_url" not in detail.json()
        assert "voice_ref" not in detail.json()

        business_thread_ids = {
            event[3] for event in events if event[0] == "business"
        }
        assert len(business_thread_ids) == 1
        assert threading.get_ident() not in business_thread_ids
        assert all(
            any(
                event[0] == "business"
                and event[1] == "close"
                and event[3] == thread_id
                for event in events
            )
            for thread_id in business_thread_ids
        )
    finally:
        engine.dispose()



def test_create_app_integrates_chapter_character_routes_with_public_auth(tmp_path) -> None:
    from fastapi.routing import APIRoute

    from haoai_backend.app import create_app

    engine, app, events, auth_sessions, business_sessions, token, _ = build_system(
        tmp_path,
        integrated=True,
    )
    own_routes = {
        ("GET", "/api/admin/chapters"),
        ("GET", "/api/admin/chapters/{chapter_id}"),
        ("GET", "/api/admin/characters"),
        ("GET", "/api/admin/characters/{character_id}"),
    }
    registered = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert own_routes <= registered

    headers = {"Authorization": f"Bearer {token('admin')}"}
    try:
        unauthenticated = request(app, "GET", "/api/admin/chapters?skip=bad")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions
        for path in (
            "/api/admin/chapters",
            "/api/admin/chapters/chapter-1",
            "/api/admin/characters",
            "/api/admin/characters/character-1",
        ):
            event_start = len(events)
            response = request(app, "GET", path, headers=headers)
            assert response.status_code == 200, response.text
            request_events = events[event_start:]
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
            business_threads = {
                event[3] for event in request_events
                if event[0] == "business" and event[2] == id(business_session)
            }
            assert len(business_threads) == 1
            assert threading.get_ident() not in business_threads
            assert business_session.closed
            assert business_session.commit_count == 0

        before_denied = len(business_sessions)
        denied = request(
            app,
            "GET",
            "/api/admin/chapters?skip=bad",
            headers={"Authorization": f"Bearer {token('member')}"},
        )
        assert denied.status_code == 403
        assert len(business_sessions) == before_denied
    finally:
        engine.dispose()

    forbidden_calls: list[str] = []

    def forbidden_factory():
        forbidden_calls.append("called")
        raise AssertionError("cold app must not open a session")

    cold_app = create_app(session_factory=forbidden_factory)
    assert forbidden_calls == []
    for path in (
        "/api/admin/chapters",
        "/api/admin/chapters/chapter-1",
        "/api/admin/characters",
        "/api/admin/characters/character-1",
    ):
        response = request(cold_app, "GET", path)
        assert response.status_code == 503, response.text
    assert forbidden_calls == []


def test_auth_precedes_typed_query_validation_and_handles_admin_failures(tmp_path) -> None:
    engine, app, events, auth_sessions, business_sessions, token, _ = build_system(tmp_path)
    try:
        unauthenticated = request(app, "GET", "/api/admin/chapters?skip=bad")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"
        assert not business_sessions

        member = request(
            app,
            "GET",
            "/api/admin/chapters?skip=bad",
            headers={"Authorization": f"Bearer {token('member')}"},
        )
        assert member.status_code == 403
        assert not business_sessions

        expired_member = request(
            app,
            "GET",
            "/api/admin/chapters",
            headers={"Authorization": f"Bearer {token('expired-admin')}"},
        )
        assert expired_member.status_code == 200

        business_sessions_before_invalid_query = len(business_sessions)
        bad_query = request(
            app,
            "GET",
            "/api/admin/chapters?skip=bad",
            headers={"Authorization": f"Bearer {token('admin')}"},
        )
        assert bad_query.status_code == 422
        assert len(business_sessions) == business_sessions_before_invalid_query
    finally:
        engine.dispose()


def test_cold_router_is_inert_and_authenticates_before_missing_business_factory(tmp_path) -> None:
    created: list[str] = []

    def forbidden_factory():
        created.append("called")
        raise AssertionError("unexpected session construction")

    inert_app = FastAPI()
    inert_app.include_router(
        build_admin_chapter_character_queries_router(
            authentication_service=None,
            session_factory=forbidden_factory,
        )
    )
    assert created == []
    cold = request(inert_app, "GET", "/api/admin/chapters")
    assert cold.status_code == 503
    assert created == []

    engine, app, events, auth_sessions, business_sessions, token, service = build_system(
        tmp_path, business_factory=False
    )
    try:
        response = request(
            app,
            "GET",
            "/api/admin/chapters",
            headers={"Authorization": f"Bearer {token('admin')}"},
        )
        assert response.status_code == 503
        assert len(auth_sessions) == 1
        assert not business_sessions
        assert any(
            event[0] == "auth"
            and event[1] == "close"
            and event[2] == id(auth_sessions[-1])
            for event in events
        )
    finally:
        engine.dispose()
