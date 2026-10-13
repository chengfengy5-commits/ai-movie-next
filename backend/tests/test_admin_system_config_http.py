from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.admin_system_config.tables import (
    metadata as system_config_metadata,
    system_configs,
)
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    users,
)

SECRET = "admin-system-config-test-only-secret"


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
            base_url="http://admin-system-config.test",
        ) as client:
            if raw_body is not None:
                raw_headers = dict(headers or {})
                raw_headers.setdefault("content-type", "application/json")
                return await client.request(
                    method, path, content=raw_body, headers=raw_headers
                )
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def build_system(path: Path):
    from sqlalchemy import create_engine

    now = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    system_config_metadata.create_all(engine)

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
        seed.execute(
            insert(users),
            [
                {
                    "id": "free-expired-admin",
                    "username": "free-expired-admin",
                    "email": "free-admin@example.test",
                    "hashed_password": "unused",
                    "is_superuser": True,
                    "membership_type": "free",
                    "membership_expires_at": now - timedelta(seconds=1),
                    "avatar_url": None,
                    "bio": None,
                    "created_at": now - timedelta(days=1),
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
                    "created_at": now - timedelta(days=1),
                    "password_updated_at": None,
                },
            ],
        )
        seed.execute(
            insert(system_configs),
            [
                {
                    "id": "config-a",
                    "site_title": "A",
                    "oss_access_key_secret": "fixture-secret-a",
                    "zhangyuge_billing_pat": "fixture-pat-a",
                },
                {
                    "id": "config-b",
                    "site_title": "B",
                    "oss_access_key_secret": "fixture-secret-b",
                    "zhangyuge_billing_pat": "fixture-pat-b",
                },
            ],
        )
        seed.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: now,
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
    )

    def token_for(user_id: str) -> str:
        return jwt.encode(
            {"sub": user_id, "exp": now + timedelta(minutes=5)},
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token_for


def test_real_jwt_routes_keep_admin_boundary_lifecycle_and_legacy_projection(
    tmp_path: Path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "system-config-http.sqlite"
    )
    try:
        routes = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
            if route.path.startswith("/api/admin/system-config")
        }
        assert routes == {
            ("GET", "/api/admin/system-config"),
            ("GET", "/api/admin/system-config/{config_id}"),
            ("PUT", "/api/admin/system-config/{config_id}"),
        }
        cold = create_app()
        for method, path, body in (
            ("GET", "/api/admin/system-config", None),
            ("GET", "/api/admin/system-config/config-a", None),
            ("PUT", "/api/admin/system-config/config-a", {}),
        ):
            cold_response = request(cold, method, path, body=body)
            assert cold_response.status_code == 503

        missing_auth = request(app, "GET", "/api/admin/system-config")
        assert missing_auth.status_code == 401
        assert missing_auth.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        member = request(
            app,
            "GET",
            "/api/admin/system-config",
            headers={"Authorization": f"Bearer {token_for('member')}"},
        )
        assert member.status_code == 403
        assert member.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert not business_sessions

        admin_headers = {
            "Authorization": f"Bearer {token_for('free-expired-admin')}"
        }
        listed = request(app, "GET", "/api/admin/system-config", headers=admin_headers)
        assert listed.status_code == 200
        list_body = listed.json()
        assert list_body["total"] == 1
        assert len(list_body["data"]) == 1
        payload = list_body["data"][0]
        expected_fields = {
            "id",
            "muse_token",
            "site_title",
            "site_tagline",
            "storyboard_cost_multiplier",
            "membership_benefits",
            "payments_enabled",
            "tutorial_url",
            "purchase_qr_code_url",
            "purchase_contact_info",
            "team_created_limit",
            "team_joined_limit",
            "team_member_limit",
            "invite_code_ttl_hours",
            "chapter_lock_idle_minutes",
            "aistarslab_credit_multiplier",
            "frontend_url",
            "allowed_origins",
            "upload_dir",
            "worker_max_concurrency",
        }
        assert set(payload) == expected_fields
        assert not {"oss_access_key_secret", "fc_transfer_url", "zhangyuge_billing_pat"} & set(payload)

        get_detail = request(
            app,
            "GET",
            "/api/admin/system-config/config-a",
            headers=admin_headers,
        )
        assert get_detail.status_code == 200
        assert get_detail.json()["id"] == "config-a"
        assert len(business_sessions) == 2
        assert all(session.commit_count == 0 for session in business_sessions)
        assert all(session.refresh_count == 0 for session in business_sessions)

        updated = request(
            app,
            "PUT",
            "/api/admin/system-config/config-a",
            headers=admin_headers,
            body={
                "site_title": "Updated",
                "storyboard_cost_multiplier": "7",
                "payments_enabled": [],
                "team_created_limit": "9",
                "aistarslab_credit_multiplier": "not-a-float",
                "frontend_url": "  https://front.example.test  ",
                "allowed_origins": None,
                "upload_dir": "  /tmp/uploads  ",
                "worker_max_concurrency": None,
                "oss_access_key_secret": "ignored-write",
                "zhangyuge_billing_pat": "ignored-pat",
                "unknown": "ignored",
            },
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["site_title"] == "Updated"
        assert body["storyboard_cost_multiplier"] == 7
        assert body["payments_enabled"] is False
        assert body["team_created_limit"] == 9
        assert body["aistarslab_credit_multiplier"] == 1.0
        assert body["frontend_url"] == "https://front.example.test"
        assert body["allowed_origins"] == ""
        assert body["upload_dir"] == "/tmp/uploads"
        assert body["worker_max_concurrency"] == "0"
        assert set(body) == expected_fields
        with Session(engine, future=True) as verification:
            stored_hidden = verification.execute(
                select(
                    system_configs.c.oss_access_key_secret,
                    system_configs.c.zhangyuge_billing_pat,
                ).where(system_configs.c.id == "config-a")
            ).one()
        assert stored_hidden == ("fixture-secret-a", "fixture-pat-a")
        business_write = business_sessions[-1]
        assert business_write.commit_count == 1
        assert business_write.refresh_count == 1

        unknown_only = request(
            app,
            "PUT",
            "/api/admin/system-config/config-a",
            headers=admin_headers,
            body={"unknown": "only"},
        )
        assert unknown_only.status_code == 200
        assert business_sessions[-1].commit_count == 1
        assert business_sessions[-1].refresh_count == 1

        missing_row = request(
            app,
            "PUT",
            "/api/admin/system-config/missing",
            headers=admin_headers,
            body={"storyboard_cost_multiplier": "not-an-int"},
        )
        assert missing_row.status_code == 404
        assert missing_row.json()["detail"] == "配置不存在"
        assert business_sessions[-1].commit_count == 0

        auth_close_indices = [
            index
            for index, event in enumerate(events)
            if event[0] == "auth" and event[1] == "close"
        ]
        business_factory_indices = [
            index
            for index, event in enumerate(events)
            if event[0] == "business" and event[1] == "factory"
        ]
        assert len(business_factory_indices) >= 5
        assert len(auth_close_indices) >= len(business_factory_indices)
        for factory_index in business_factory_indices:
            assert any(index < factory_index for index in auth_close_indices)

        for session in business_sessions:
            relevant = [
                event
                for event in events
                if event[0] == "business" and event[2] == id(session)
            ]
            assert relevant
            assert len({event[3] for event in relevant}) == 1
            assert session.closed
    finally:
        engine.dispose()
