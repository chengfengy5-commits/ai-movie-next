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

from haoai_backend.app import create_app
from haoai_backend.admin_system_models.crypto import LegacyFernet
from haoai_backend.admin_system_models.http import build_admin_system_models_router
from haoai_backend.admin_system_models.tables import (
    metadata as system_model_metadata,
    system_models,
)
from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    users,
)

SECRET = "admin-system-models-test-only-secret"


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.refresh_count = 0
        self.query_count = 0
        self.closed = False
        self.info["events"].append(
            (self.info["kind"], "open", id(self), threading.get_ident())
        )

    def query(self, *entities: Any, **kwargs: Any):
        self.query_count += 1
        self.info["events"].append(
            (self.info["kind"], "query", id(self), threading.get_ident())
        )
        return super().query(*entities, **kwargs)

    def commit(self) -> None:
        self.commit_count += 1
        self.info["events"].append(
            (self.info["kind"], "commit", id(self), threading.get_ident())
        )
        super().commit()

    def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.refresh_count += 1
        self.info["events"].append(
            (self.info["kind"], "refresh", id(self), threading.get_ident())
        )
        super().refresh(instance, *args, **kwargs)

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
            base_url="http://admin-system-models.test",
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

    now = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    system_model_metadata.create_all(engine)

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
    business_factory = make_factory("business", business_sessions)
    with Session(engine, future=True) as seed:
        for user_id, is_admin, membership, expires in (
            ("admin", True, "free", None),
            ("member", False, "premium", None),
            ("expired-admin", True, "premium", now - timedelta(seconds=1)),
        ):
            seed.execute(
                insert(users).values(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.test",
                    hashed_password="unused",
                    is_superuser=is_admin,
                    membership_type=membership,
                    membership_expires_at=expires,
                    avatar_url=None,
                    bio=None,
                    created_at=now - timedelta(days=1),
                    password_updated_at=None,
                )
            )
        seed.execute(
            insert(system_models),
            [
                {
                    "id": f"seed-{index}",
                    "category": "image",
                    "name": f"Seed {index}",
                    "provider": "fixture",
                    "api_key": LegacyFernet(SECRET).encrypt(f"seed-key-{index}"),
                    "model": f"fixture-{index}",
                    "base_url": "https://example.test",
                    "cost_per_call": index,
                    "description": "seed",
                    "is_active": False,
                    "sort_order": index,
                    "params": None,
                    "created_at": now - timedelta(days=1),
                    "updated_at": now - timedelta(days=1),
                }
                for index in range(11)
            ],
        )
        seed.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: now,
    )
    authentication_service = AuthenticationService(
        runtime,
        lambda: SqlAlchemyAuthenticationUnitOfWork(auth_factory()),
    )
    if integrated:
        app = create_app(
            session_factory=business_factory,
            authentication_runtime=runtime,
        )
    else:
        app = FastAPI()
        app.include_router(
            build_admin_system_models_router(
                authentication_service=authentication_service,
                session_factory=business_factory,
                cipher=LegacyFernet(runtime.secret_key),
            )
        )

    def token_for(user_id: str) -> str:
        return jwt.encode(
            {"sub": user_id, "exp": now + timedelta(minutes=5)},
            SECRET,
            algorithm="HS256",
        )

    return (
        engine,
        app,
        events,
        auth_sessions,
        business_sessions,
        token_for,
    )


def test_asgi_routes_keep_typed_create_update_raw_list_detail_and_worker_sessions(
    tmp_path: Path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-system-models-http.sqlite", integrated=True
    )
    admin = {"Authorization": f"Bearer {token_for('admin')}"}
    try:
        routes = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
            if route.path.startswith("/api/admin/system-models")
        }
        assert routes == {
            ("GET", "/api/admin/system-models"),
            ("POST", "/api/admin/system-models"),
            ("GET", "/api/admin/system-models/{model_id}"),
            ("PUT", "/api/admin/system-models/{model_id}"),
            ("DELETE", "/api/admin/system-models/{model_id}"),
        }

        missing_auth = request(app, "GET", "/api/admin/system-models")
        assert missing_auth.status_code == 401
        assert missing_auth.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions
        member_auth = request(
            app,
            "GET",
            "/api/admin/system-models",
            headers={"Authorization": f"Bearer {token_for('member')}"},
        )
        assert member_auth.status_code == 403
        assert not business_sessions

        created_response = request(
            app,
            "POST",
            "/api/admin/system-models",
            body={
                "category": "video",
                "name": "Created",
                "provider": "test-provider",
                "model": None,
                "api_key": "gAAAAA-client-looking-value",
                "base_url": None,
                "cost_per_call": "12",
                "description": None,
                "is_active": "false",
                "params": "{\"validated\":true}",
                "ignored": "extra",
            },
            headers=admin,
        )
        assert created_response.status_code == 200, created_response.text
        created = created_response.json()
        assert len(created) == 14
        assert created["category"] == "video"
        assert created["cost_per_call"] == 12
        assert created["is_active"] is False
        assert created["model"] == ""
        assert created["base_url"] == ""
        assert created["description"] == ""
        assert created["params"] is None
        assert created["api_key"].startswith("gAAAAA")
        assert created["api_key"] != "gAAAAA-client-looking-value"
        created_id = created["id"]

        create_session = business_sessions[-1]
        assert create_session.commit_count == 1
        assert create_session.refresh_count == 1
        assert create_session.closed
        for index, event in enumerate(events):
            if event[0:2] == ("business", "factory"):
                assert events[index - 1][0:2] == ("auth", "close")
        for session in [*auth_sessions, *business_sessions]:
            session_events = [event for event in events if event[2] == id(session)]
            assert len({event[3] for event in session_events}) == 1
            if session.info["kind"] == "business":
                assert session_events[0][3] != threading.get_ident()

        listed = request(
            app,
            "GET",
            "/api/admin/system-models?category=image&provider=fixture&is_active=false&skip=0&limit=1",
            headers=admin,
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 11
        assert len(listed.json()["data"]) == 1
        assert listed.json()["data"][0]["api_key"].startswith("gAAAAA")
        assert listed.json()["data"][0]["api_key"] != "seed-key-0"
        list_session = business_sessions[-1]
        assert list_session.commit_count == 0
        assert list_session.refresh_count == 0
        assert list_session.closed

        page = request(
            app,
            "GET",
            "/api/admin/system-models?category=image&provider=fixture&is_active=false&skip=10&limit=1",
            headers=admin,
        )
        assert page.json()["total"] == 11
        assert page.json()["data"][0]["id"] == "seed-10"

        no_trim = request(
            app,
            "GET",
            "/api/admin/system-models?category=%20image&provider=fixture",
            headers=admin,
        )
        assert no_trim.json() == {"data": [], "total": 0}

        detail = request(
            app,
            "GET",
            f"/api/admin/system-models/{created_id}",
            headers=admin,
        )
        assert detail.status_code == 200
        assert detail.json()["api_key"] == "gAAAAA-client-looking-value"
        detail_session = business_sessions[-1]
        assert detail_session.commit_count == 0
        assert detail_session.refresh_count == 0

        preserved = request(
            app,
            "PUT",
            f"/api/admin/system-models/{created_id}",
            body={
                "api_key": None,
                "base_url": None,
                "description": None,
                "params": None,
            },
            headers=admin,
        )
        assert preserved.status_code == 200, preserved.text
        assert preserved.json()["api_key"] == created["api_key"]
        assert preserved.json()["base_url"] is None
        assert preserved.json()["description"] is None
        assert preserved.json()["params"] is None

        prefix_preserved = request(
            app,
            "PUT",
            f"/api/admin/system-models/{created_id}",
            body={"api_key": "gAAAAA-client-looking-value"},
            headers=admin,
        )
        assert prefix_preserved.json()["api_key"] == created["api_key"]

        changed = request(
            app,
            "PUT",
            f"/api/admin/system-models/{created_id}",
            body={"api_key": "new-secret", "name": ""},
            headers=admin,
        )
        assert changed.status_code == 200
        assert changed.json()["name"] == ""
        assert changed.json()["api_key"].startswith("gAAAAA")
        assert changed.json()["api_key"] != "new-secret"
        changed_detail = request(
            app,
            "GET",
            f"/api/admin/system-models/{created_id}",
            headers=admin,
        )
        assert changed_detail.json()["api_key"] == "new-secret"

        delete_response = request(
            app,
            "DELETE",
            "/api/admin/system-models/seed-0",
            headers=admin,
        )
        assert delete_response.status_code == 200
        assert delete_response.json() == {"message": "删除成功"}
        delete_session = business_sessions[-1]
        assert delete_session.query_count == 1
        assert delete_session.commit_count == 1
        assert delete_session.refresh_count == 0
        assert delete_session.closed

        missing = request(
            app,
            "GET",
            "/api/admin/system-models/not-found",
            headers=admin,
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "系统模型不存在"
        assert business_sessions[-1].commit_count == 0
        assert business_sessions[-1].closed
    finally:
        engine.dispose()


def test_public_auth_precedes_business_sessions_and_keeps_legacy_errors(
    tmp_path: Path,
) -> None:
    engine, app, _events, _auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-system-models-auth.sqlite"
    )
    path = "/api/admin/system-models"
    try:
        malformed = request(app, "POST", path, raw_body=b"{")
        assert malformed.status_code == 422
        assert not business_sessions

        missing = request(
            app,
            "POST",
            path,
            body={"category": "text", "name": "N", "provider": "P"},
        )
        assert missing.status_code == 401
        assert missing.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        invalid = request(
            app,
            "GET",
            path,
            headers={"Authorization": "Bearer invalid"},
        )
        assert invalid.status_code == 401
        assert invalid.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        member = request(
            app,
            "POST",
            path,
            body={"category": "text", "name": "N", "provider": "P"},
            headers={"Authorization": f"Bearer {token_for('member')}"},
        )
        assert member.status_code == 403
        assert member.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert not business_sessions

        expired_admin = request(
            app,
            "POST",
            path,
            body={"category": "text", "name": "Expired membership admin", "provider": "P"},
            headers={"Authorization": f"Bearer {token_for('expired-admin')}"},
        )
        assert expired_admin.status_code == 200
        assert expired_admin.json()["name"] == "Expired membership admin"
        assert business_sessions[-1].commit_count == 1
    finally:
        engine.dispose()


def test_cold_router_is_inert_and_returns_503(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(
        build_admin_system_models_router(
            authentication_service=None,
            session_factory=None,
        )
    )
    response = request(
        app,
        "POST",
        "/api/admin/system-models",
        body={"category": "text", "name": "N", "provider": "P"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "系统模型服务尚未接线"}

    app = create_app()
    integrated_response = request(
        app,
        "GET",
        "/api/admin/system-models",
    )
    assert integrated_response.status_code == 503
