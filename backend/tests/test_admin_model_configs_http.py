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
from haoai_backend.admin_model_configs.http import build_admin_model_configs_router
from haoai_backend.admin_model_configs.tables import ModelConfigRow, metadata as model_config_metadata
from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata as authentication_metadata, users

NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "admin-model-configs-http-test-secret"


class ObservedSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.refresh_count = 0
        self.closed = False
        self.info["events"].append((self.info["kind"], "open", id(self), threading.get_ident()))

    def commit(self):
        self.commit_count += 1
        self.info["events"].append((self.info["kind"], "commit", id(self), threading.get_ident()))
        return super().commit()

    def refresh(self, instance, *args, **kwargs):
        self.refresh_count += 1
        self.info["events"].append((self.info["kind"], "refresh", id(self), threading.get_ident()))
        return super().refresh(instance, *args, **kwargs)

    def close(self):
        self.closed = True
        self.info["events"].append((self.info["kind"], "close", id(self), threading.get_ident()))
        return super().close()


def request(app: FastAPI, method: str, path: str, *, body: Any = None, headers=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://admin-model-configs.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)
    return asyncio.run(send())


def build_system(path: Path, *, integrated: bool = False):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    model_config_metadata.create_all(engine)
    events: list[tuple[str, str, int, int]] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []
    def make_factory(kind: str, opened: list[ObservedSession]):
        maker = sessionmaker(
            engine, class_=ObservedSession, expire_on_commit=False, future=True,
            info={"kind": kind, "events": events},
        )
        def factory():
            events.append((kind, "factory", 0, threading.get_ident()))
            session = maker()
            opened.append(session)
            return session
        return factory
    auth_factory = make_factory("auth", auth_sessions)
    business_factory = make_factory("business", business_sessions)
    with Session(engine) as session:
        for user_id, is_admin, membership, expiry in (
            ("admin", True, "free", None),
            ("member", False, "premium", None),
            ("expired-admin", True, "premium", NOW - timedelta(seconds=1)),
        ):
            session.execute(insert(users).values(
                id=user_id, username=user_id, email=f"{user_id}@example.test",
                hashed_password="unused", is_superuser=is_admin, membership_type=membership,
                membership_expires_at=expiry, avatar_url=None, bio=None,
                created_at=NOW - timedelta(days=1), password_updated_at=None,
            ))
        session.commit()
    runtime = AuthenticationRuntime(session_factory=auth_factory, secret_key=SECRET, clock=lambda: NOW)
    service = AuthenticationService(runtime, lambda: SqlAlchemyAuthenticationUnitOfWork(auth_factory()))
    if integrated:
        app = create_app(
            session_factory=business_factory,
            authentication_runtime=runtime,
        )
    else:
        app = FastAPI()
        app.include_router(build_admin_model_configs_router(
            authentication_service=service, session_factory=business_factory
        ))
    def token(user_id: str):
        return jwt.encode({"sub": user_id, "exp": NOW + timedelta(minutes=5)}, SECRET, algorithm="HS256")
    return engine, app, events, auth_sessions, business_sessions, token


def test_public_auth_admin_crud_worker_uow_order_exact_dto_and_auth_failures(tmp_path: Path):
    engine, app, events, auth_sessions, business_sessions, token = build_system(
        tmp_path / "model-configs.sqlite", integrated=True
    )
    admin = {"Authorization": f"Bearer {token('admin')}"}
    def traced_request(method: str, path: str, *, body: Any = None, headers=None):
        event_start = len(events)
        business_start = len(business_sessions)
        response = request(app, method, path, body=body, headers=headers)
        return response, events[event_start:], business_sessions[business_start:]

    def assert_business_request(
        request_events, opened_sessions, expected_commits: int, expected_refreshes: int
    ):
        assert len(opened_sessions) == 1
        business_open = next(
            index
            for index, item in enumerate(request_events)
            if item[0] == "business" and item[1] == "factory"
        )
        assert request_events[business_open - 1][0:2] == ("auth", "close")
        assert opened_sessions[0].closed
        assert opened_sessions[0].commit_count == expected_commits
        assert opened_sessions[0].refresh_count == expected_refreshes
        assert all(
            item[3] != threading.get_ident()
            for item in request_events
            if item[0] == "business"
        )

    try:
        missing, missing_events, missing_business = traced_request("GET", "/api/admin/model-configs")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert not missing_business
        assert missing_events[-1][0:2] == ("auth", "close")

        forbidden, forbidden_events, forbidden_business = traced_request(
            "GET", "/api/admin/model-configs",
            headers={"Authorization": f"Bearer {token('member')}"},
        )
        assert forbidden.status_code == 403 and not forbidden_business
        assert forbidden_events[-1][0:2] == ("auth", "close")

        expired_admin, expired_events, expired_sessions = traced_request(
            "POST", "/api/admin/model-configs",
            headers={"Authorization": f"Bearer {token('expired-admin')}"}, body={},
        )
        assert expired_admin.status_code == 200
        assert expired_admin.json()["user_id"] == "expired-admin"
        assert_business_request(expired_events, expired_sessions, 1, 1)

        created, create_events, create_sessions = traced_request(
            "POST", "/api/admin/model-configs", headers=admin, body={"api_key": "plain-secret"}
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["api_key"] == "plain-secret"
        assert payload["user_id"] == "admin"
        assert set(payload) == {
            "id", "user_id", "category", "name", "provider", "model", "api_key",
            "base_url", "sort_order", "created_at", "updated_at",
        }
        assert_business_request(create_events, create_sessions, 1, 1)

        updated, update_events, update_sessions = traced_request(
            "PUT", f"/api/admin/model-configs/{payload['id']}",
            headers=admin, body={"model": None, "params": "{}"},
        )
        assert updated.status_code == 200 and updated.json()["model"] is None
        assert "params" not in updated.json()
        assert_business_request(update_events, update_sessions, 1, 1)

        listed, list_events, list_sessions = traced_request(
            "GET", "/api/admin/model-configs?skip=0&limit=10", headers=admin
        )
        assert listed.status_code == 200 and listed.json()["total"] == 2
        assert_business_request(list_events, list_sessions, 0, 0)

        missing_detail, detail_events, detail_sessions = traced_request(
            "GET", "/api/admin/model-configs/not-found", headers=admin
        )
        assert missing_detail.status_code == 404 and missing_detail.json()["detail"] == "模型配置不存在"
        assert_business_request(detail_events, detail_sessions, 0, 0)

        deleted, delete_events, delete_sessions = traced_request(
            "DELETE", f"/api/admin/model-configs/{payload['id']}", headers=admin
        )
        assert deleted.status_code == 200 and deleted.json() == {"message": "删除成功"}
        assert_business_request(delete_events, delete_sessions, 1, 0)
        assert all(session.closed for session in auth_sessions + business_sessions)
    finally:
        engine.dispose()


def test_router_cold_503_and_auth_precedes_valid_json_shape_validation(tmp_path: Path):
    calls = 0
    def no_session():
        nonlocal calls
        calls += 1
        raise AssertionError("cold route must not open a session")
    app = FastAPI()
    app.include_router(build_admin_model_configs_router(authentication_service=None, session_factory=no_session))
    cold = request(app, "GET", "/api/admin/model-configs")
    assert cold.status_code == 503 and calls == 0
    integrated_cold = request(create_app(), "GET", "/api/admin/model-configs")
    assert integrated_cold.status_code == 503

    engine, ready_app, _events, auth_sessions, business_sessions, token = build_system(tmp_path / "validation.sqlite")
    try:
        invalid_shape = request(ready_app, "POST", "/api/admin/model-configs", headers={}, body=[])
        assert invalid_shape.status_code == 401
        assert invalid_shape.headers["www-authenticate"] == "Bearer"
        assert not business_sessions
        assert len(auth_sessions) == 1
        assert auth_sessions[0].closed
    finally:
        engine.dispose()
