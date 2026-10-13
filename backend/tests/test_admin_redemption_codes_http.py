from __future__ import annotations

import asyncio
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import event, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.admin_redemption_codes.tables import (
    RedemptionCodeRow,
    metadata as redemption_metadata,
)
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    users,
)

NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "admin-redemption-codes-http-test-secret"


class ObservedSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.refresh_count = 0
        self.closed = False
        self.info["events"].append(
            (self.info["kind"], "open", id(self), threading.get_ident())
        )

    def commit(self):
        self.commit_count += 1
        self.info["events"].append(
            (self.info["kind"], "commit", id(self), threading.get_ident())
        )
        return super().commit()

    def refresh(self, instance, *args, **kwargs):
        self.refresh_count += 1
        self.info["events"].append(
            (self.info["kind"], "refresh", id(self), threading.get_ident())
        )
        return super().refresh(instance, *args, **kwargs)

    def close(self):
        self.closed = True
        self.info["events"].append(
            (self.info["kind"], "close", id(self), threading.get_ident())
        )
        return super().close()


def request(app: FastAPI, method: str, path: str, *, body: Any = None, headers=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://admin-redemption-codes.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def build_system(path: Path):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    redemption_metadata.create_all(engine)
    events: list[tuple[str, str, int, int]] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def make_factory(kind: str, opened: list[ObservedSession]):
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            expire_on_commit=False,
            autoflush=False,
            future=True,
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
            session.execute(
                insert(users).values(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.test",
                    hashed_password="unused",
                    is_superuser=is_admin,
                    membership_type=membership,
                    membership_expires_at=expiry,
                    avatar_url=None,
                    bio=None,
                    created_at=NOW - timedelta(days=1),
                    password_updated_at=None,
                )
            )
        session.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: datetime.now(timezone.utc),
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
    )

    def token(user_id: str):
        issued_at = datetime.now(timezone.utc)
        return jwt.encode(
            {"sub": user_id, "exp": issued_at + timedelta(minutes=5)},
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token


def test_public_jwt_auth_worker_lifecycle_and_four_route_contract(tmp_path: Path):
    engine, app, events, auth_sessions, business_sessions, token = build_system(
        tmp_path / "redemption-http.sqlite"
    )
    admin = {"Authorization": f"Bearer {token('admin')}"}

    def traced_request(method: str, path: str, *, body: Any = None, headers=None):
        event_start = len(events)
        session_start = len(business_sessions)
        response = request(app, method, path, body=body, headers=headers)
        return response, events[event_start:], business_sessions[session_start:]

    def assert_business(request_events, opened, commits: int, refreshes: int):
        assert len(opened) == 1
        factory_index = next(
            index
            for index, event_item in enumerate(request_events)
            if event_item[0:2] == ("business", "factory")
        )
        assert request_events[factory_index - 1][0:2] == ("auth", "close")
        assert opened[0].closed
        assert opened[0].commit_count == commits
        assert opened[0].refresh_count == refreshes
        assert all(
            event_item[3] != threading.get_ident()
            for event_item in request_events
            if event_item[0] == "business"
        )

    try:
        unauthorized, unauth_events, unauth_business = traced_request(
            "GET", "/api/admin/redemption-codes"
        )
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"
        assert not unauth_business
        assert unauth_events[-1][0:2] == ("auth", "close")

        forbidden, _, forbidden_business = traced_request(
            "GET",
            "/api/admin/redemption-codes",
            headers={"Authorization": f"Bearer {token('member')}"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert not forbidden_business

        invalid_body, invalid_events, invalid_business = traced_request(
            "POST",
            "/api/admin/redemption-codes",
            body=[],
        )
        assert invalid_body.status_code == 401
        assert invalid_body.headers["www-authenticate"] == "Bearer"
        assert not invalid_business
        assert invalid_events[-1][0:2] == ("auth", "close")

        expired_admin, expired_events, expired_business = traced_request(
            "GET",
            "/api/admin/redemption-codes",
            headers={"Authorization": f"Bearer {token('expired-admin')}"},
        )
        assert expired_admin.status_code == 200
        assert_business(expired_events, expired_business, 0, 0)

        created, create_events, create_business = traced_request(
            "POST",
            "/api/admin/redemption-codes",
            headers=admin,
            body={
                "type": "credits",
                "value": "0",
                "count": 2,
                "expires_at": "2031-01-02T03:04:05Z",
                "id": "caller-id",
                "code": "caller-code",
                "created_by": "member",
                "is_used": True,
            },
        )
        assert created.status_code == 400
        assert created.json()["detail"] == "value 必须大于 0"
        assert not create_business

        created, create_events, create_business = traced_request(
            "POST",
            "/api/admin/redemption-codes",
            headers=admin,
            body={
                "type": "credits",
                "value": "5",
                "count": 2,
                "expires_at": "2031-01-02T03:04:05Z",
                "id": "caller-id",
                "code": "caller-code",
                "created_by": "member",
                "is_used": True,
            },
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert re.fullmatch(r"HAOAI-[A-Z0-9]{4}-[A-Z0-9]{4}", created_payload["code"])
        assert created_payload["id"] != "caller-id"
        assert created_payload["created_by"] == "admin"
        assert created_payload["is_used"] is False
        assert created_payload["_generated_count"] == 2
        assert_business(create_events, create_business, 1, 1)

        listed, list_events, list_business = traced_request(
            "GET",
            "/api/admin/redemption-codes?code=HAOAI&type=credits&is_used=false&value=5",
            headers=admin,
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 2
        assert_business(list_events, list_business, 0, 0)

        detail, detail_events, detail_business = traced_request(
            "GET",
            f"/api/admin/redemption-codes/{created_payload['id']}",
            headers=admin,
        )
        assert detail.status_code == 200
        assert set(detail.json()) == {
            "id",
            "code",
            "type",
            "value",
            "is_used",
            "used_by",
            "used_at",
            "created_by",
            "expires_at",
            "created_at",
        }
        assert detail.json()["expires_at"] == "2031-01-02T03:04:05Z"
        assert_business(detail_events, detail_business, 0, 0)

        missing, missing_events, missing_business = traced_request(
            "GET",
            "/api/admin/redemption-codes/not-found",
            headers=admin,
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "兑换码不存在"
        assert_business(missing_events, missing_business, 0, 0)

        with Session(engine) as seed:
            seed.execute(
                insert(RedemptionCodeRow.__table__).values(
                    id="used-code",
                    code="HAOAI-USED-0001",
                    type="credits",
                    value=1,
                    is_used=True,
                    used_by="admin",
                    used_at=NOW,
                    created_by="admin",
                    expires_at=None,
                    created_at=NOW,
                )
            )
            seed.commit()
        statements: list[str] = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            deleted, delete_events, delete_business = traced_request(
                "DELETE",
                "/api/admin/redemption-codes/used-code",
                headers=admin,
            )
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert deleted.status_code == 200
        assert deleted.json() == {"message": "删除成功"}
        redemption_selects = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
            and "FROM redemption_codes" in statement
        ]
        assert len(redemption_selects) == 1
        assert_business(delete_events, delete_business, 1, 0)
        assert all(session.closed for session in auth_sessions + business_sessions)
    finally:
        engine.dispose()


def test_cold_router_is_503_without_opening_business_session():
    calls = 0

    def unused_session_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("cold route must not open a session")

    app = create_app(session_factory=unused_session_factory)
    for method, path, body in (
        ("GET", "/api/admin/redemption-codes", None),
        ("GET", "/api/admin/redemption-codes/cold-id", None),
        ("POST", "/api/admin/redemption-codes", {}),
        ("DELETE", "/api/admin/redemption-codes/cold-id", None),
    ):
        response = request(app, method, path, body=body)
        assert response.status_code == 503
    assert calls == 0
