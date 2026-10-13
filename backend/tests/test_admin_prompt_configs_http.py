from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.admin_prompt_configs.http import build_admin_prompt_configs_router
from haoai_backend.admin_prompt_configs.tables import (
    PromptConfigRow,
    metadata as prompt_config_metadata,
)
from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)


SECRET = "admin-prompt-configs-http-test-secret"
NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.refresh_count = 0
        self.closed = False
        self.info["events"].append(
            (self.info["kind"], "open", id(self), threading.get_ident())
        )

    def query(self, *args: Any, **kwargs: Any):
        self.info["events"].append(
            (self.info["kind"], "query", id(self), threading.get_ident())
        )
        return super().query(*args, **kwargs)

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
            base_url="http://prompt-configs.test",
        ) as client:
            if raw_body is not None:
                request_headers = dict(headers or {})
                request_headers.setdefault("content-type", "application/json")
                return await client.request(
                    method, path, content=raw_body, headers=request_headers
                )
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def build_system(path: Path, *, integrated: bool = False):
    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    prompt_config_metadata.create_all(engine)
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

    auth_factory = factory_for("auth", auth_sessions)
    business_factory = factory_for("business", business_sessions)
    with Session(engine, future=True) as session:
        for user_id, is_admin, membership, expires in (
            ("admin", True, "free", None),
            ("expired-admin", True, "premium", NOW - timedelta(days=1)),
            ("member", False, "premium", None),
        ):
            session.execute(
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
                    created_at=NOW - timedelta(days=30),
                    password_updated_at=None,
                )
            )
        session.commit()
        session.add_all(
            [
                PromptConfigRow(
                    id=f"seed-{index:02d}",
                    user_id="admin" if index % 2 == 0 else "member",
                    category="text" if index % 2 == 0 else "image",
                    name=f"Seed {index:02d}",
                    system_prompt=f"system-{index:02d}",
                    user_prompt=None if index == 3 else f"user-{index:02d}",
                    sort_order=index,
                    created_at=NOW - timedelta(minutes=index),
                    updated_at=NOW - timedelta(minutes=index),
                )
                for index in range(11)
            ]
        )
        session.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: NOW,
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
            build_admin_prompt_configs_router(
                authentication_service=authentication_service,
                session_factory=business_factory,
            )
        )

    def token_for(user_id: str) -> str:
        return jwt.encode(
            {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token_for


def test_public_auth_raw_routes_crud_query_order_and_worker_lifecycle(tmp_path: Path) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-prompt-configs-http.sqlite", integrated=True
    )
    admin_headers = {"Authorization": f"Bearer {token_for('admin')}"}
    expired_admin_headers = {"Authorization": f"Bearer {token_for('expired-admin')}"}
    try:
        missing_auth = request(app, "GET", "/api/admin/prompt-configs")
        assert missing_auth.status_code == 401
        assert missing_auth.headers.get("www-authenticate") == "Bearer"
        assert business_sessions == []
        member_auth = request(
            app,
            "GET",
            "/api/admin/prompt-configs",
            headers={"Authorization": f"Bearer {token_for('member')}"},
        )
        assert member_auth.status_code == 403
        assert business_sessions == []

        page = request(app, "GET", "/api/admin/prompt-configs", headers=admin_headers)
        assert page.status_code == 200, page.text
        assert page.json()["total"] == 11
        assert len(page.json()["data"]) == 10
        assert set(page.json()["data"][0]) == {
            "id", "user_id", "category", "name", "system_prompt", "user_prompt",
            "sort_order", "created_at", "updated_at",
        }
        filtered = request(
            app,
            "GET",
            "/api/admin/prompt-configs?user_id=member&sort_field=id&sort_order=asc",
            headers=admin_headers,
        )
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 5
        assert {item["user_id"] for item in filtered.json()["data"]} == {"member"}
        empty_filter = request(
            app,
            "GET",
            "/api/admin/prompt-configs?user_id=",
            headers=admin_headers,
        )
        assert empty_filter.json()["total"] == 11

        created = request(
            app,
            "POST",
            "/api/admin/prompt-configs",
            body={
                "id": "client-id",
                "user_id": "member",
                "created_at": "2000-01-01T00:00:00",
            },
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        payload = created.json()
        assert payload["id"] != "client-id"
        assert payload["user_id"] == "admin"
        assert payload["category"] == ""
        assert payload["name"] == "提示词"
        assert payload["system_prompt"] == ""
        assert payload["user_prompt"] == ""
        assert payload["sort_order"] == 0
        business = business_sessions[-1]
        assert business.commit_count == 1 and business.refresh_count == 1 and business.closed

        update_data = {
            "id": "changed-id",
            "user_id": "member",
            "category": "updated-category",
            "name": "Updated",
            "system_prompt": None,
            "user_prompt": "updated user text",
            "sort_order": 41,
            "unknown": "ignored",
        }
        updated = request(
            app,
            "PUT",
            f"/api/admin/prompt-configs/{payload['id']}",
            body=update_data,
            headers=admin_headers,
        )
        assert updated.status_code == 200, updated.text
        changed = updated.json()
        assert changed["id"] == payload["id"]
        assert changed["user_id"] == "admin"
        assert changed["category"] == "updated-category"
        assert changed["system_prompt"] is None
        assert changed["created_at"] == payload["created_at"]
        assert changed["updated_at"] != payload["updated_at"]
        assert business_sessions[-1].commit_count == 1
        assert business_sessions[-1].refresh_count == 1

        fetched = request(
            app,
            "GET",
            f"/api/admin/prompt-configs/{payload['id']}",
            headers=expired_admin_headers,
        )
        assert fetched.status_code == 200
        assert fetched.json() == changed
        assert business_sessions[-1].commit_count == 0
        assert business_sessions[-1].refresh_count == 0
        assert business_sessions[-1].closed

        same_value = request(
            app,
            "PUT",
            f"/api/admin/prompt-configs/{payload['id']}",
            body={"name": changed["name"]},
            headers=admin_headers,
        )
        assert same_value.status_code == 200
        assert same_value.json()["name"] == changed["name"]
        assert business_sessions[-1].commit_count == 1
        assert business_sessions[-1].refresh_count == 1

        unknown_only = request(
            app,
            "PUT",
            f"/api/admin/prompt-configs/{payload['id']}",
            body={"not_a_mapped_attribute": True},
            headers=admin_headers,
        )
        assert unknown_only.status_code == 200
        assert unknown_only.json()["id"] == payload["id"]
        assert business_sessions[-1].commit_count == 1
        assert business_sessions[-1].refresh_count == 1

        # A JSON datetime string reaches SQLAlchemy unchanged; the legacy DateTime
        # binding rejects it instead of silently coercing or normalizing it.
        invalid_date = request(
            app,
            "PUT",
            f"/api/admin/prompt-configs/{payload['id']}",
            body={"updated_at": "2021-02-03T04:05:06"},
            headers=admin_headers,
        )
        assert invalid_date.status_code == 500
        assert business_sessions[-1].commit_count == 1
        assert business_sessions[-1].closed
        still_present = request(
            app,
            "GET",
            f"/api/admin/prompt-configs/{payload['id']}",
            headers=admin_headers,
        )
        assert still_present.status_code == 200
        assert still_present.json()["name"] == changed["name"]

        deleted = request(
            app,
            "DELETE",
            f"/api/admin/prompt-configs/{payload['id']}",
            headers=admin_headers,
        )
        assert deleted.status_code == 200 and deleted.json() == {"message": "删除成功"}
        deleted_session = business_sessions[-1]
        assert deleted_session.commit_count == 1
        assert deleted_session.refresh_count == 0
        deleted_events = [
            (index, event)
            for index, event in enumerate(events)
            if event[2] == id(deleted_session)
        ]
        commit_index = max(index for index, event in deleted_events if event[1] == "commit")
        assert not any(
            event[1] == "query"
            for index, event in deleted_events
            if index > commit_index
        )

        missing = request(
            app,
            "GET",
            "/api/admin/prompt-configs/absent",
            headers=admin_headers,
        )
        assert missing.status_code == 404 and missing.json()["detail"] == "提示词不存在"

        business_open_indices = [
            index for index, event in enumerate(events) if event[0:2] == ("business", "open")
        ]
        for index in business_open_indices:
            assert events[index - 1][0:2] == ("business", "factory")
            assert events[index - 2][0:2] == ("auth", "close")
        for session in [*auth_sessions, *business_sessions]:
            session_events = [event for event in events if event[2] == id(session)]
            assert len({event[3] for event in session_events}) == 1
            assert session.closed
            if session.info["kind"] == "business":
                assert session_events[0][3] != threading.get_ident()
    finally:
        engine.dispose()


def test_authentication_precedes_invalid_raw_json_and_keeps_legacy_denials(tmp_path: Path) -> None:
    engine, app, _events, _auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-prompt-configs-auth.sqlite"
    )
    try:
        invalid_json_shape = request(
            app,
            "POST",
            "/api/admin/prompt-configs",
            raw_body=b"[]",
        )
        assert invalid_json_shape.status_code == 401
        assert invalid_json_shape.json()["detail"] == "Not authenticated"
        assert invalid_json_shape.headers["www-authenticate"] == "Bearer"
        assert business_sessions == []

        member = request(
            app,
            "POST",
            "/api/admin/prompt-configs",
            body={"anything": True},
            headers={"Authorization": f"Bearer {token_for('member')}"},
        )
        assert member.status_code == 403
        assert member.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert business_sessions == []
    finally:
        engine.dispose()


def test_cold_router_returns_503_without_opening_sessions() -> None:
    app = FastAPI()
    app.include_router(
        build_admin_prompt_configs_router(
            authentication_service=None,
            session_factory=None,
        )
    )
    response = request(app, "GET", "/api/admin/prompt-configs")
    assert response.status_code == 503
    assert response.json()["detail"] == "提示词配置服务尚未接线"
    integrated_response = request(
        create_app(), "GET", "/api/admin/prompt-configs"
    )
    assert integrated_response.status_code == 503
