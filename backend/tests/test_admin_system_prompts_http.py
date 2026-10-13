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

from haoai_backend.admin_system_prompts.http import build_admin_system_prompts_router
from haoai_backend.admin_system_prompts.tables import metadata as system_prompt_metadata
from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)
from haoai_backend.admin_system_prompts.tables import system_prompts

NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "admin-system-prompts-http-test-secret"


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
    body: Any = None,
    headers: dict[str, str] | None = None,
    raw_body: bytes | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://admin-prompts.test",
        ) as client:
            if raw_body is not None:
                raw_headers = dict(headers or {})
                raw_headers.setdefault("content-type", "application/json")
                return await client.request(
                    method, path, content=raw_body, headers=raw_headers
                )
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def build_system(path: Path, *, integrated: bool = False):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    system_prompt_metadata.create_all(engine)
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
        for user_id, is_admin, membership, membership_expires_at in (
            ("admin", True, "free", None),
            ("member", False, "premium", None),
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
                    membership_expires_at=membership_expires_at,
                    avatar_url=None,
                    bio=None,
                    created_at=NOW - timedelta(days=1),
                    password_updated_at=None,
                )
            )
        session.execute(
            insert(user_sessions).values(
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
                        "id": "revoked-session",
                        "user_id": "admin",
                        "jti": "revoked-jti",
                        "user_agent": "test",
                        "ip_address": "127.0.0.1",
                        "device_name": "test",
                        "is_active": False,
                        "created_at": NOW - timedelta(hours=1),
                        "last_seen_at": NOW - timedelta(minutes=1),
                        "revoked_at": NOW - timedelta(seconds=1),
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
                ]
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
            build_admin_system_prompts_router(
                authentication_service=authentication_service,
                session_factory=business_session_factory,
            )
        )

    def token_for(user_id: str, *, jti: str | None = None, password_version: str | None = None):
        claims: dict[str, object] = {
            "sub": user_id,
            "exp": NOW + timedelta(minutes=5),
        }
        if jti is not None:
            claims["jti"] = jti
        if password_version is not None:
            claims["password_version"] = password_version
        return jwt.encode(claims, SECRET, algorithm="HS256")

    return engine, app, events, auth_sessions, business_sessions, token_for


def test_public_jwt_admin_crud_auth_priority_and_worker_session_order(tmp_path: Path) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-system-prompts-http.sqlite"
    )
    admin_header = {"Authorization": f"Bearer {token_for('admin', jti='admin-jti')}"}
    try:
        created = request(
            app,
            "POST",
            "/api/admin/system-prompts",
            body={"category": "text", "name": "HTTP"},
            headers=admin_header,
        )
        assert created.status_code == 200, created.text
        payload = created.json()
        assert payload["name"] == "HTTP"
        assert payload["sort_order"] == 0
        assert payload["is_active"] is True
        assert len(business_sessions) == 1
        assert business_sessions[0].commit_count == 1
        assert business_sessions[0].closed

        auth_close = max(
            i for i, event in enumerate(events)
            if event[0] == "auth" and event[1] == "close"
        )
        business_open = min(
            i for i, event in enumerate(events)
            if event[0] == "business" and event[1] == "open"
        )
        assert auth_close < business_open
        for session in [*auth_sessions, *business_sessions]:
            session_events = [event for event in events if event[2] == id(session)]
            thread_ids = {event[3] for event in session_events}
            assert thread_ids == {session_events[0][3]}

        fetched = request(
            app,
            "GET",
            f"/api/admin/system-prompts/{payload['id']}",
            headers=admin_header,
        )
        assert fetched.status_code == 200
        assert set(fetched.json()) == set(payload)

        updated = request(
            app,
            "PUT",
            f"/api/admin/system-prompts/{payload['id']}",
            body={"is_active": False, "is_system": True, "id": "client-id"},
            headers=admin_header,
        )
        assert updated.status_code == 200
        assert updated.json()["is_active"] is False
        assert updated.json()["id"] == payload["id"]
        assert business_sessions[-1].commit_count == 1

        listed = request(
            app,
            "GET",
            "/api/admin/system-prompts?is_active=false",
            headers=admin_header,
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["data"][0]["is_active"] is False

        deleted = request(
            app,
            "DELETE",
            f"/api/admin/system-prompts/{payload['id']}",
            headers=admin_header,
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"message": "删除成功"}
        assert business_sessions[-1].commit_count == 1
    finally:
        engine.dispose()


def test_authentication_errors_preserve_headers_and_precede_body_validation(tmp_path: Path) -> None:
    engine, app, _events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-system-prompts-auth.sqlite"
    )
    path = "/api/admin/system-prompts"
    try:
        missing = request(app, "POST", path, body=[])
        assert missing.status_code == 401
        assert missing.json()["detail"] == "Not authenticated"
        assert missing.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        invalid_token = request(
            app,
            "POST",
            path,
            body={},
            headers={"Authorization": "Bearer invalid-signature"},
        )
        assert invalid_token.status_code == 401
        assert invalid_token.json()["detail"] == "无法验证凭据"
        assert invalid_token.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        nonadmin = request(
            app,
            "POST",
            path,
            body={"category": None},
            headers={
                "Authorization": f"Bearer {token_for('member', jti='member-jti')}",
                "X-User-ID": "admin",
                "X-Admin": "true",
            },
        )
        assert nonadmin.status_code == 403
        assert nonadmin.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert not business_sessions

        revoked = request(
            app,
            "POST",
            path,
            body={},
            headers={"Authorization": f"Bearer {token_for('admin', jti='revoked-jti')}"},
        )
        assert revoked.status_code == 401
        assert revoked.json()["detail"] == "无法验证凭据"
        assert revoked.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        stale_password_version = request(
            app,
            "POST",
            path,
            body={},
            headers={
                "Authorization": f"Bearer {token_for('admin', jti='admin-jti', password_version='stale')}"
            },
        )
        assert stale_password_version.status_code == 401
        assert stale_password_version.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        invalid_shape = request(
            app,
            "POST",
            path,
            body=[],
            headers={"Authorization": f"Bearer {token_for('admin', jti='admin-jti')}"},
        )
        assert invalid_shape.status_code == 422
        assert not business_sessions

        invalid_query_without_auth = request(
            app, "GET", f"{path}?skip=not-an-integer"
        )
        assert invalid_query_without_auth.status_code == 401
        assert invalid_query_without_auth.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        invalid_query_with_auth = request(
            app,
            "GET",
            f"{path}?skip=not-an-integer",
            headers={"Authorization": f"Bearer {token_for('admin', jti='admin-jti')}"},
        )
        assert invalid_query_with_auth.status_code == 422
        assert not business_sessions

        valid_admin_header = {
            "Authorization": f"Bearer {token_for('admin', jti='admin-jti')}"
        }
        for method, route, body in (
            ("GET", f"{path}/absent", None),
            ("PUT", f"{path}/absent", {}),
            ("DELETE", f"{path}/absent", None),
        ):
            missing_route = request(
                app, method, route, body=body, headers=valid_admin_header
            )
            assert missing_route.status_code == 404
            assert missing_route.json()["detail"] == "系统提示词不存在"
            assert business_sessions[-1].commit_count == 0
            assert business_sessions[-1].closed

        auth_count_before_malformed = len(auth_sessions)
        business_count_before_malformed = len(business_sessions)
        malformed_json = request(app, "POST", path, raw_body=b"{")
        assert malformed_json.status_code == 422
        assert len(auth_sessions) == auth_count_before_malformed
        assert len(business_sessions) == business_count_before_malformed

        invalid_database_value = request(
            app,
            "POST",
            path,
            body={"category": None},
            headers={"Authorization": f"Bearer {token_for('admin', jti='admin-jti')}"},
        )
        assert invalid_database_value.status_code == 500
        assert business_sessions[-1].commit_count == 1
        assert business_sessions[-1].closed

        expired_admin = request(
            app,
            "POST",
            path,
            body={"category": "text", "name": "expired admin allowed"},
            headers={
                "Authorization": f"Bearer {token_for('expired-admin', jti='expired-admin-jti')}"
            },
        )
        assert expired_admin.status_code == 200
        assert expired_admin.json()["name"] == "expired admin allowed"
        assert business_sessions[-1].commit_count == 1
    finally:
        engine.dispose()


def test_cold_router_returns_503_without_opening_sessions(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(
        build_admin_system_prompts_router(
            authentication_service=None,
            session_factory=None,
        )
    )
    response = request(
        app,
        "POST",
        "/api/admin/system-prompts",
        body=[],
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "系统提示词服务尚未接线"



def test_create_app_composes_system_prompt_routes_with_public_auth_and_supplied_sessions(
    tmp_path: Path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-system-prompts-composed.sqlite", integrated=True
    )
    try:
        assert len(app.routes) == 179
        system_prompt_routes = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
            if route.path.startswith("/api/admin/system-prompts")
        }
        assert system_prompt_routes == {
            ("GET", "/api/admin/system-prompts"),
            ("GET", "/api/admin/system-prompts/{prompt_id}"),
            ("POST", "/api/admin/system-prompts"),
            ("PUT", "/api/admin/system-prompts/{prompt_id}"),
            ("DELETE", "/api/admin/system-prompts/{prompt_id}"),
        }
        assert events == []
        assert auth_sessions == [] and business_sessions == []

        from haoai_backend.app import create_app

        cold = request(
            create_app(),
            "POST",
            "/api/admin/system-prompts",
            body={"category": "text", "name": "cold"},
        )
        assert cold.status_code == 503
        assert cold.json() == {"detail": "系统提示词服务尚未接线"}
        assert events == []

        missing = request(app, "GET", "/api/admin/system-prompts")
        assert missing.status_code == 401
        assert missing.json()["detail"] == "Not authenticated"
        assert missing.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        invalid = request(
            app,
            "GET",
            "/api/admin/system-prompts",
            headers={"Authorization": "Bearer invalid-signature"},
        )
        assert invalid.status_code == 401
        assert invalid.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        non_admin = request(
            app,
            "POST",
            "/api/admin/system-prompts",
            body=[],
            headers={
                "Authorization": f"Bearer {token_for('member', jti='member-jti')}",
                "X-User-ID": "admin",
                "X-Admin": "true",
            },
        )
        assert non_admin.status_code == 403
        assert non_admin.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert "www-authenticate" not in non_admin.headers
        assert not business_sessions

        admin_header = {
            "Authorization": f"Bearer {token_for('admin', jti='admin-jti')}"
        }
        invalid_admin_body = request(
            app,
            "POST",
            "/api/admin/system-prompts",
            body=[],
            headers=admin_header,
        )
        assert invalid_admin_body.status_code == 422
        assert not business_sessions

        event_start = len(events)
        created = request(
            app,
            "POST",
            "/api/admin/system-prompts",
            body={"category": "text", "name": "Composed"},
            headers=admin_header,
        )
        assert created.status_code == 200, created.text
        prompt_id = created.json()["id"]
        assert len(business_sessions) == 1
        assert business_sessions[-1].commit_count == 1
        assert business_sessions[-1].closed

        request_events = events[event_start:]
        auth_close = max(
            index for index, event in enumerate(request_events)
            if event[0] == "auth" and event[1] == "close"
        )
        business_open = min(
            index for index, event in enumerate(request_events)
            if event[0] == "business" and event[1] == "open"
        )
        assert auth_close < business_open
        for session in [*auth_sessions, *business_sessions]:
            session_events = [event for event in events if event[2] == id(session)]
            thread_ids = {event[3] for event in session_events}
            assert len(thread_ids) == 1

        listed = request(
            app, "GET", "/api/admin/system-prompts", headers=admin_header
        )
        assert listed.status_code == 200 and listed.json()["total"] == 1
        fetched = request(
            app,
            "GET",
            f"/api/admin/system-prompts/{prompt_id}",
            headers=admin_header,
        )
        assert fetched.status_code == 200 and fetched.json()["id"] == prompt_id
        updated = request(
            app,
            "PUT",
            f"/api/admin/system-prompts/{prompt_id}",
            body={"name": "Updated"},
            headers=admin_header,
        )
        assert updated.status_code == 200 and updated.json()["name"] == "Updated"
        assert business_sessions[-1].commit_count == 1
        deleted = request(
            app,
            "DELETE",
            f"/api/admin/system-prompts/{prompt_id}",
            headers=admin_header,
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"message": "删除成功"}
        assert business_sessions[-1].commit_count == 1
        assert all(session.closed for session in business_sessions)
    finally:
        engine.dispose()
