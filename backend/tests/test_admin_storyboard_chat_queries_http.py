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

from haoai_backend.admin_storyboard_chat_queries.http import (
    build_admin_storyboard_chat_queries_router,
)
from haoai_backend.admin_storyboard_chat_queries.tables import (
    ChatMessageRow,
    StoryboardAssetRow,
    metadata as query_metadata,
)
from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import users


NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "storyboard-chat-queries-http-test-secret"


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

    def rollback(self) -> None:
        self.info["events"].append(
            (self.info["kind"], "rollback", id(self), threading.get_ident())
        )
        super().rollback()

    def close(self) -> None:
        self.closed = True
        self.info["events"].append(
            (self.info["kind"], "close", id(self), threading.get_ident())
        )
        super().close()


def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://storyboard-chat-queries.test",
        ) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(send())


def _build_system(path: Path, *, integrated: bool = False):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    query_metadata.create_all(engine)
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
    with Session(engine, future=True) as session:
        session.execute(
            insert(users),
            [
                {
                    "id": user_id,
                    "username": user_id,
                    "email": f"{user_id}@example.test",
                    "hashed_password": "unused",
                    "is_superuser": is_admin,
                    "membership_type": membership,
                    "membership_expires_at": expires_at,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW - timedelta(days=1),
                    "password_updated_at": None,
                }
                for user_id, is_admin, membership, expires_at in (
                    ("admin", True, "free", None),
                    (
                        "expired-admin",
                        True,
                        "premium",
                        NOW - timedelta(seconds=1),
                    ),
                    ("member", False, "premium", None),
                )
            ],
        )
        session.execute(
            query_metadata.tables["series"].insert(),
            [{"id": "series-a"}, {"id": "series-b"}],
        )
        session.execute(
            query_metadata.tables["chapters"].insert(),
            [{"id": "chapter-a"}, {"id": "chapter-b"}],
        )
        session.add_all(
            [
                StoryboardAssetRow(
                    id=f"asset-{index:02d}",
                    series_id="series-a" if index % 2 else "series-b",
                    chapter_id="chapter-a" if index % 3 else "chapter-b",
                    frame_index=index,
                    name=f"分镜-{index:02d}",
                    description=None,
                    image_url=None,
                    created_at=NOW + timedelta(seconds=index),
                    updated_at=NOW + timedelta(seconds=index),
                )
                for index in range(11)
            ]
        )
        full_content = "汉" * 201
        session.add(
            ChatMessageRow(
                id="message-long",
                chapter_id="chapter-a",
                frame_index=2,
                asset_type=None,
                asset_id=None,
                chat_mode="chat",
                role="assistant",
                content=full_content,
                model_name="test-model",
                created_at=NOW,
            )
        )
        session.commit()

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
            build_admin_storyboard_chat_queries_router(
                authentication_service=authentication_service,
                session_factory=business_session_factory,
            )
        )

    def token_for(user_id: str) -> str:
        return jwt.encode(
            {"sub": user_id, "exp": NOW.replace(tzinfo=timezone.utc) + timedelta(minutes=5)},
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token_for, full_content


def test_public_auth_precedes_query_validation_and_reads_stay_on_worker(
    tmp_path: Path,
) -> None:
    (engine, app, events, auth_sessions, business_sessions, token_for, full_content) = (
        _build_system(tmp_path / "admin-storyboard-chat-http.sqlite")
    )
    try:
        invalid_query = _request(
            app, "GET", "/api/admin/storyboard-assets?skip=not-an-int"
        )
        assert invalid_query.status_code == 401
        assert invalid_query.headers.get("www-authenticate") == "Bearer"
        assert business_sessions == []

        admin_header = {"Authorization": f"Bearer {token_for('admin')}"}
        asset_response = _request(
            app, "GET", "/api/admin/storyboard-assets", headers=admin_header
        )
        assert asset_response.status_code == 200, asset_response.text
        assets = asset_response.json()
        assert assets["total"] == 11
        assert len(assets["data"]) == 10
        assert {asset["series_id"] for asset in assets["data"]} == {
            "series-a",
            "series-b",
        }
        assert {asset["chapter_id"] for asset in assets["data"]} == {
            "chapter-a",
            "chapter-b",
        }
        assert "updated_at" not in assets["data"][0]

        asset_detail = _request(
            app, "GET", "/api/admin/storyboard-assets/asset-10", headers=admin_header
        )
        assert asset_detail.status_code == 200
        assert asset_detail.json()["id"] == "asset-10"
        assert "updated_at" not in asset_detail.json()

        message_response = _request(
            app, "GET", "/api/admin/chat-messages", headers=admin_header
        )
        assert message_response.status_code == 200
        assert message_response.json()["data"][0]["content"] == "汉" * 200
        detail_response = _request(
            app, "GET", "/api/admin/chat-messages/message-long", headers=admin_header
        )
        assert detail_response.status_code == 200
        assert detail_response.json()["content"] == full_content

        missing_asset = _request(
            app, "GET", "/api/admin/storyboard-assets/missing", headers=admin_header
        )
        missing_message = _request(
            app, "GET", "/api/admin/chat-messages/missing", headers=admin_header
        )
        assert (missing_asset.status_code, missing_asset.json()["detail"]) == (
            404,
            "分镜资产不存在",
        )
        assert (missing_message.status_code, missing_message.json()["detail"]) == (
            404,
            "消息不存在",
        )

        assert len(business_sessions) == 6
        assert all(session.closed for session in business_sessions)
        assert all(session.commit_count == 0 for session in business_sessions)
        for session in [*auth_sessions, *business_sessions]:
            session_events = [event for event in events if event[2] == id(session)]
            assert session_events
            assert {event[3] for event in session_events} == {session_events[0][3]}

        first_business_factory = next(
            i for i, event in enumerate(events)
            if event[0] == "business" and event[1] == "factory"
        )
        last_prior_auth_close = max(
            i for i, event in enumerate(events[:first_business_factory])
            if event[0] == "auth" and event[1] == "close"
        )
        assert last_prior_auth_close < first_business_factory
    finally:
        engine.dispose()



def test_create_app_integrates_storyboard_chat_routes_with_public_auth(tmp_path: Path) -> None:
    from fastapi.routing import APIRoute

    from haoai_backend.app import create_app

    engine, app, events, auth_sessions, business_sessions, token_for, _content = (
        _build_system(tmp_path / "admin-storyboard-chat-integrated.sqlite", integrated=True)
    )
    own_routes = {
        ("GET", "/api/admin/storyboard-assets"),
        ("GET", "/api/admin/storyboard-assets/{asset_id}"),
        ("GET", "/api/admin/chat-messages"),
        ("GET", "/api/admin/chat-messages/{message_id}"),
    }
    registered = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert own_routes <= registered
    headers = {"Authorization": f"Bearer {token_for('admin')}"}
    try:
        unauthenticated = _request(
            app,
            "GET",
            "/api/admin/storyboard-assets?skip=not-an-int",
        )
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions
        for path in (
            "/api/admin/storyboard-assets",
            "/api/admin/storyboard-assets/asset-10",
            "/api/admin/chat-messages",
            "/api/admin/chat-messages/message-long",
        ):
            start = len(events)
            response = _request(app, "GET", path, headers=headers)
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
        denied = _request(
            app,
            "GET",
            "/api/admin/storyboard-assets?skip=not-an-int",
            headers={"Authorization": f"Bearer {token_for('member')}"},
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
        "/api/admin/storyboard-assets",
        "/api/admin/storyboard-assets/asset-10",
        "/api/admin/chat-messages",
        "/api/admin/chat-messages/message-long",
    ):
        response = _request(cold_app, "GET", path)
        assert response.status_code == 503, response.text
    assert cold_calls == []


def test_free_and_expired_admins_are_allowed_but_regular_member_is_forbidden(
    tmp_path: Path,
) -> None:
    (engine, app, _events, _auth_sessions, business_sessions, token_for, _content) = (
        _build_system(tmp_path / "admin-storyboard-chat-auth.sqlite")
    )
    try:
        for user_id in ("admin", "expired-admin"):
            response = _request(
                app,
                "GET",
                "/api/admin/storyboard-assets?limit=1",
                headers={"Authorization": f"Bearer {token_for(user_id)}"},
            )
            assert response.status_code == 200, response.text

        before_member_request = len(business_sessions)
        forbidden = _request(
            app,
            "GET",
            "/api/admin/storyboard-assets",
            headers={"Authorization": f"Bearer {token_for('member')}"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert len(business_sessions) == before_member_request
        assert all(session.commit_count == 0 for session in business_sessions)
    finally:
        engine.dispose()
