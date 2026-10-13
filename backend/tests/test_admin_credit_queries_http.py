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
from haoai_backend.admin_credit_queries.tables import (
    Base,
    CreditLogRow,
    UserCreditRow,
    UserRow,
)
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)

NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "admin-credit-queries-test-secret"


class ObservedSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.rollback_count = 0
        self.refresh_count = 0
        self.closed_marker = False
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
        self.rollback_count += 1
        self.info["events"].append(
            (self.info["kind"], "rollback", id(self), threading.get_ident())
        )
        super().rollback()

    def refresh(self, instance, *args, **kwargs) -> None:
        self.refresh_count += 1
        self.info["events"].append(
            (self.info["kind"], "refresh", id(self), threading.get_ident())
        )
        super().refresh(instance, *args, **kwargs)

    def close(self) -> None:
        self.closed_marker = True
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
    json_body: Any = None,
    content: bytes | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://admin-credit-queries.test",
        ) as client:
            return await client.request(
                method,
                path,
                headers=headers,
                json=json_body if content is None else None,
                content=content,
            )

    return asyncio.run(send())


def _user(
    user_id: str,
    *,
    is_admin: bool = False,
    membership_type: str = "free",
    membership_expires_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "id": user_id,
        "username": user_id,
        "email": f"{user_id}@example.test",
        "hashed_password": "unused",
        "is_superuser": is_admin,
        "membership_type": membership_type,
        "membership_expires_at": membership_expires_at,
        "avatar_url": None,
        "bio": None,
        "created_at": NOW - timedelta(days=1),
        "password_updated_at": None,
    }


def build_system(path: Path):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    Base.metadata.create_all(
        engine,
        tables=[UserRow.__table__, UserCreditRow.__table__, CreditLogRow.__table__],
    )
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
    now = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                _user("admin", is_admin=True, membership_type="premium"),
                _user("free-admin", is_admin=True),
                _user("member", membership_type="premium"),
                _user(
                    "expired-admin",
                    is_admin=True,
                    membership_type="premium",
                    membership_expires_at=now - timedelta(seconds=5),
                ),
            ],
        )
        connection.execute(
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
                for user_id in ("admin", "free-admin", "member", "expired-admin")
            ],
        )
        connection.execute(
            insert(UserCreditRow.__table__),
            [
                {
                    "id": "credit-admin",
                    "user_id": "admin",
                    "credits": 10,
                    "created_at": NOW - timedelta(days=1),
                    "updated_at": NOW + timedelta(minutes=2),
                },
                {
                    "id": "credit-member",
                    "user_id": "member",
                    "credits": 20,
                    "created_at": NOW - timedelta(days=1),
                    "updated_at": NOW + timedelta(minutes=1),
                },
            ],
        )
        connection.execute(
            insert(CreditLogRow.__table__),
            [
                {
                    "id": "log-admin",
                    "user_id": "admin",
                    "amount": 10,
                    "balance_after": 10,
                    "type": "charge",
                    "description": "admin top-up",
                    "task_id": None,
                    "business_key": "business-admin",
                    "billing_unit_id": None,
                    "related_debit_id": None,
                    "created_at": NOW,
                },
                {
                    "id": "log-member",
                    "user_id": "member",
                    "amount": -1,
                    "balance_after": 19,
                    "type": "usage",
                    "description": None,
                    "task_id": None,
                    "business_key": "business-member",
                    "billing_unit_id": None,
                    "related_debit_id": None,
                    "created_at": NOW + timedelta(minutes=1),
                },
            ],
        )

    runtime = AuthenticationRuntime(
        session_factory=auth_session_factory,
        secret_key=SECRET,
        clock=lambda: datetime.now(timezone.utc),
    )
    app = create_app(
        session_factory=business_session_factory,
        authentication_runtime=runtime,
    )

    def token_for(user_id: str) -> str:
        issued_at = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": user_id,
                "jti": f"{user_id}-jti",
                "exp": issued_at + timedelta(minutes=5),
            },
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token_for


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_business_lifecycle(
    events: list[tuple[str, str, int, int]],
    start: int,
    auth_sessions: list[ObservedSession],
    business_sessions: list[ObservedSession],
    *,
    request_thread: int,
) -> ObservedSession:
    new_events = events[start:]
    auth_session = auth_sessions[-1]
    business_session = business_sessions[-1]
    auth_close = next(
        i
        for i, event in enumerate(new_events)
        if event[0] == "auth" and event[1] == "close" and event[2] == id(auth_session)
    )
    business_open = next(
        i
        for i, event in enumerate(new_events)
        if event[0] == "business" and event[1] == "open"
    )
    assert auth_close < business_open
    business_events = [
        event for event in new_events
        if event[0] == "business" and event[2] == id(business_session)
    ]
    assert business_events
    assert {event[3] for event in business_events} == {business_events[0][3]}
    assert business_events[0][3] != request_thread
    assert business_session.closed_marker
    return business_session


def test_real_routes_keep_dtos_queries_refresh_and_worker_lifecycle(tmp_path: Path) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-credit-queries.sqlite"
    )
    headers = _headers(token_for("admin"))
    request_thread = threading.get_ident()
    try:
        start = len(events)
        listed = request(
            app,
            "GET",
            "/api/admin/user-credits?user_id=member",
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        page = listed.json()
        assert page["total"] == 2
        assert [row["id"] for row in page["data"]] == [
            "credit-admin",
            "credit-member",
        ]
        assert set(page["data"][0]) == {
            "id",
            "user_id",
            "credits",
            "created_at",
            "updated_at",
        }
        list_session = _assert_business_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=request_thread,
        )
        assert list_session.commit_count == 0

        start = len(events)
        detail = request(
            app,
            "GET",
            "/api/admin/user-credits/credit-member",
            headers=headers,
        )
        assert detail.status_code == 200
        assert detail.json()["credits"] == 20
        detail_session = _assert_business_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=request_thread,
        )
        assert detail_session.commit_count == 0

        start = len(events)
        updated = request(
            app,
            "PUT",
            "/api/admin/user-credits/credit-member",
            headers=headers,
            json_body={"credits": "-4"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["credits"] == -4
        update_session = _assert_business_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=request_thread,
        )
        assert update_session.commit_count == 1
        assert update_session.refresh_count == 1

        start = len(events)
        unchanged = request(
            app,
            "PUT",
            "/api/admin/user-credits/credit-member",
            headers=headers,
            json_body={"unknown": "ignored"},
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["credits"] == -4
        unknown_session = _assert_business_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=request_thread,
        )
        assert unknown_session.commit_count == 1
        assert unknown_session.refresh_count == 1

        start = len(events)
        logs = request(
            app,
            "GET",
            "/api/admin/credit-logs?user_id=member",
            headers=headers,
        )
        assert logs.status_code == 200, logs.text
        log_page = logs.json()
        assert log_page["total"] == 1
        assert [row["id"] for row in log_page["data"]] == ["log-member"]
        assert set(log_page["data"][0]) == {
            "id",
            "user_id",
            "amount",
            "balance_after",
            "type",
            "description",
            "task_id",
            "created_at",
        }
        assert "business_key" not in log_page["data"][0]
        assert "billing_unit_id" not in log_page["data"][0]
        assert "related_debit_id" not in log_page["data"][0]
        log_session = _assert_business_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=request_thread,
        )
        assert log_session.commit_count == 0

        missing = request(
            app,
            "GET",
            "/api/admin/user-credits/missing",
            headers=headers,
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "积分记录不存在"

        null_update = request(
            app,
            "PUT",
            "/api/admin/user-credits/credit-member",
            headers=headers,
            json_body={"credits": None},
        )
        assert null_update.status_code == 500
        failed = business_sessions[-1]
        assert failed.rollback_count == 1
        assert failed.commit_count == 0
        assert failed.closed_marker

        all_logs = request(
            app,
            "GET",
            "/api/admin/credit-logs?user_id=",
            headers=headers,
        )
        assert all_logs.status_code == 200
        assert all_logs.json()["total"] == 2
    finally:
        engine.dispose()


def test_auth_precedes_valid_query_and_body_shape_errors_and_cold_router_stays_idle(
    tmp_path: Path,
) -> None:
    engine, app, _events, _auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "admin-credit-queries-auth.sqlite"
    )
    try:
        missing = request(app, "GET", "/api/admin/user-credits?skip=invalid")
        assert missing.status_code == 401
        assert missing.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        missing_body = request(
            app,
            "PUT",
            "/api/admin/user-credits/credit-member",
            json_body={"credits": None},
        )
        assert missing_body.status_code == 401
        assert missing_body.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        member = request(
            app,
            "PUT",
            "/api/admin/user-credits/credit-member",
            headers=_headers(token_for("member")),
            json_body={"credits": 1},
        )
        assert member.status_code == 403
        assert member.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert not business_sessions

        wrong_shape = request(
            app,
            "PUT",
            "/api/admin/user-credits/credit-member",
            headers=_headers(token_for("admin")),
            json_body=["not", "a", "mapping"],
        )
        assert wrong_shape.status_code == 422
        assert not business_sessions

        expired_membership_admin = request(
            app,
            "GET",
            "/api/admin/credit-logs",
            headers=_headers(token_for("expired-admin")),
        )
        assert expired_membership_admin.status_code == 200
        with Session(engine) as verification:
            membership_type, membership_expires_at = verification.execute(
                select(users.c.membership_type, users.c.membership_expires_at).where(
                    users.c.id == "expired-admin"
                )
            ).one()
        assert membership_type == "premium"
        assert membership_expires_at is not None
        assert membership_expires_at < datetime.now(timezone.utc).replace(tzinfo=None)

        free_membership_admin = request(
            app,
            "GET",
            "/api/admin/credit-logs",
            headers=_headers(token_for("free-admin")),
        )
        assert free_membership_admin.status_code == 200
        assert len(business_sessions) == 2
    finally:
        engine.dispose()

    calls: list[str] = []

    def forbidden_factory():
        calls.append("factory")
        raise AssertionError("a cold router must not create a business session")

    cold = create_app(session_factory=forbidden_factory)
    for method, path, body in (
        ("GET", "/api/admin/user-credits", None),
        ("GET", "/api/admin/user-credits/credit-1", None),
        ("PUT", "/api/admin/user-credits/credit-1", {}),
        ("GET", "/api/admin/credit-logs", None),
    ):
        response = request(cold, method, path, json_body=body)
        assert response.status_code == 503
    assert calls == []
