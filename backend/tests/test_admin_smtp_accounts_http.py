from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import haoai_backend.app as app_module
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_smtp_accounts.crypto import LegacyFernet
from haoai_backend.admin_smtp_accounts.daily_reset import DailyResetState
from haoai_backend.admin_smtp_accounts.tables import metadata as smtp_metadata, smtp_accounts
from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import metadata as auth_metadata, users


NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "smtp-http-fake-test-secret"


class ObservedSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = self.info["kind"]
        self.events = self.info["events"]
        self.commits = self.refreshes = 0
        self.closed = False
        self.events.append((self.kind, "open", id(self), threading.get_ident()))

    def commit(self):
        self.commits += 1
        self.events.append((self.kind, "commit", id(self), threading.get_ident()))
        return super().commit()

    def refresh(self, instance, *args, **kwargs):
        self.refreshes += 1
        self.events.append((self.kind, "refresh", id(self), threading.get_ident()))
        return super().refresh(instance, *args, **kwargs)

    def close(self):
        self.closed = True
        self.events.append((self.kind, "close", id(self), threading.get_ident()))
        return super().close()


def request(app: FastAPI, method: str, path: str, *, body: Any = None, headers=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://smtp-admin.test",
        ) as client:
            if body is None:
                return await client.request(method, path, headers=headers)
            return await client.request(method, path, json=body, headers=headers)
    return asyncio.run(send())


def make_system(path: Path, *, rollover: bool = False, monkeypatch=None):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}")
    auth_metadata.create_all(engine)
    smtp_metadata.create_all(engine)
    events: list[tuple[str, str, int, int]] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def factory_for(kind: str, opened: list[ObservedSession]):
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

    auth_factory = factory_for("auth", auth_sessions)
    business_factory = factory_for("business", business_sessions)
    with Session(engine) as seed:
        for user_id, is_admin, membership, expiry in (
            ("admin", True, "free", None),
            ("member", False, "premium", None),
            ("expired-admin", True, "premium", NOW - timedelta(seconds=1)),
        ):
            seed.execute(insert(users).values(
                id=user_id, username=user_id, email=f"{user_id}@example.test",
                hashed_password="unused", is_superuser=is_admin, membership_type=membership,
                membership_expires_at=expiry, avatar_url=None, bio=None,
                created_at=NOW - timedelta(days=1), password_updated_at=None,
            ))
        seed.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: NOW,
    )
    if rollover:
        assert monkeypatch is not None
        days = iter((NOW.date() - timedelta(days=1), NOW.date()))
        monkeypatch.setattr(
            app_module,
            "DailyResetState",
            lambda: DailyResetState(lambda: next(days)),
        )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
    )
    codec = LegacyFernet(SECRET)

    def token(user_id: str):
        return jwt.encode(
            {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token, codec


def test_public_auth_and_all_six_routes_preserve_dto_and_session_contract(tmp_path: Path):
    engine, app, events, auth_sessions, business_sessions, token, codec = make_system(
        tmp_path / "smtp-http.sqlite"
    )
    admin = {"Authorization": f"Bearer {token('admin')}"}
    start = len(events)
    try:
        unauthenticated = request(app, "GET", "/api/admin/smtp-accounts")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"
        assert len(business_sessions) == 0
        assert events[-1][0:2] == ("auth", "close")

        forbidden = request(
            app, "GET", "/api/admin/smtp-accounts",
            headers={"Authorization": f"Bearer {token('member')}"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert len(business_sessions) == 0

        expired = request(
            app, "POST", "/api/admin/smtp-accounts",
            headers={"Authorization": f"Bearer {token('expired-admin')}"},
            body={"user": "expired@example.test", "password": "fake-secret"},
        )
        assert NOW - timedelta(seconds=1) < NOW
        assert expired.status_code == 200
        assert expired.json()["user"] == "expired@example.test"

        created = request(
            app, "POST", "/api/admin/smtp-accounts", headers=admin,
            body={"user": "owner@example.test", "password": "fake-password"},
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert set(created_payload) == {"id", "user", "message"}
        assert created_payload["message"] == "添加成功"
        account_id = created_payload["id"]

        listed = request(app, "GET", "/api/admin/smtp-accounts", headers=admin)
        assert listed.status_code == 200
        assert listed.json()["total"] == 2
        listed_row = next(row for row in listed.json()["data"] if row["id"] == account_id)
        assert set(listed_row) == {
            "id", "host", "port", "user", "password", "daily_sent",
            "is_active", "created_at", "updated_at",
        }
        assert listed_row["password"] == "fake-password"
        assert listed_row["host"] == "smtp.qq.com" and listed_row["port"] == 465
        assert listed_row["daily_sent"] == 0 and listed_row["is_active"] is True

        detail = request(app, "GET", f"/api/admin/smtp-accounts/{account_id}", headers=admin)
        assert detail.status_code == 200
        assert detail.json() == listed_row
        with Session(engine) as read_session:
            stored = read_session.execute(
                select(smtp_accounts).where(smtp_accounts.c.id == account_id)
            ).one()
        assert stored.password != "fake-password"
        assert codec.decrypt(stored.password) == "fake-password"

        updated = request(
            app, "PUT", f"/api/admin/smtp-accounts/{account_id}", headers=admin,
            body={"host": "mail.example.test", "port": "587", "is_active": False,
                  "password": "****", "ignored": "ignored"},
        )
        assert updated.status_code == 200 and updated.json() == {"message": "更新成功"}
        assert business_sessions[-1].commits == 1
        assert business_sessions[-1].refreshes == 0

        missing = request(app, "GET", "/api/admin/smtp-accounts/missing", headers=admin)
        assert missing.status_code == 404 and missing.json()["detail"] == "账号不存在"

        reset = request(
            app, "POST", f"/api/admin/smtp-accounts/{account_id}/reset-count", headers=admin
        )
        assert reset.status_code == 200 and reset.json() == {"message": "已重置"}
        deleted = request(app, "DELETE", f"/api/admin/smtp-accounts/{account_id}", headers=admin)
        assert deleted.status_code == 200 and deleted.json() == {"message": "删除成功"}

        for session in auth_sessions + business_sessions:
            assert session.closed
        assert all(item[0] != "business" or item[3] != threading.get_ident() for item in events[start:])
        first_business = next(i for i, item in enumerate(events[start:]) if item[0:2] == ("business", "factory"))
        assert events[start + first_business - 1][0:2] == ("auth", "close")
        create_session = next(session for session in business_sessions if session.refreshes == 1)
        assert create_session.commits == 1
    finally:
        engine.dispose()


def test_rollover_runs_before_list_query_in_same_worker_and_cold_http_is_inert(tmp_path: Path, monkeypatch):
    engine, app, events, auth_sessions, business_sessions, token, _codec = make_system(
        tmp_path / "smtp-rollover.sqlite", rollover=True, monkeypatch=monkeypatch
    )
    admin = {"Authorization": f"Bearer {token('admin')}"}
    with Session(engine) as seed:
        seed.execute(insert(smtp_accounts).values(
            id="smtp-seeded", host="smtp.qq.com", port=465, user="seed@example.test",
            password="plain-legacy", daily_sent=8, is_active=1,
            created_at=NOW, updated_at=NOW,
        ))
        seed.commit()
    try:
        start = len(events)
        response = request(app, "GET", "/api/admin/smtp-accounts", headers=admin)
        assert response.status_code == 200
        assert next(row for row in response.json()["data"] if row["id"] == "smtp-seeded")["daily_sent"] == 0
        request_events = events[start:]
        business_factory_positions = [
            i for i, item in enumerate(request_events) if item[0:2] == ("business", "factory")
        ]
        assert len(business_factory_positions) == 2
        assert business_factory_positions[0] < business_factory_positions[1]
        assert all(
            request_events[index][3] == request_events[business_factory_positions[0]][3]
            for index in business_factory_positions
        )
        assert business_sessions[-2].commits == 0
        assert business_sessions[-2].closed
        assert business_sessions[-1].commits == 1
        assert business_sessions[-1].closed

        cold = create_app()
        cold_response = request(cold, "GET", "/api/admin/smtp-accounts")
        assert cold_response.status_code == 503
        assert not business_sessions or all(session.closed for session in business_sessions)
    finally:
        engine.dispose()
