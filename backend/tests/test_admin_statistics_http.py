from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import requests
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import event, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.admin_statistics.tables import metadata as statistics_metadata, system_configs
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import metadata as authentication_metadata, users


SECRET = "stats-http-fake-only-secret"
NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)


class ObservedSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = self.info["kind"]
        self.events = self.info["events"]
        self.commits = self.refreshes = self.rollbacks = 0
        self.closed = False
        self.events.append((self.kind, "open", id(self), threading.get_ident()))

    def commit(self):
        self.commits += 1
        self.events.append((self.kind, "commit", id(self), threading.get_ident()))
        return super().commit()

    def rollback(self):
        self.rollbacks += 1
        self.events.append((self.kind, "rollback", id(self), threading.get_ident()))
        return super().rollback()

    def close(self):
        self.closed = True
        self.events.append((self.kind, "close", id(self), threading.get_ident()))
        return super().close()


def request(app: FastAPI, method: str, path: str, *, headers=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://stats-admin.test",
        ) as client:
            return await client.request(method, path, headers=headers)
    return asyncio.run(send())


def make_app(path: Path):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    statistics_metadata.create_all(engine)
    events = []
    auth_sessions = []
    business_sessions = []
    def factory_for(kind, opened):
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
        for user_id, admin, membership, expiry in (
            ("admin", True, "free", None),
            ("member", False, "premium", None),
            ("expired-admin", True, "premium", NOW - timedelta(seconds=1)),
        ):
            seed.execute(insert(users).values(
                id=user_id, username=user_id, email=f"{user_id}@example.test",
                hashed_password="unused", is_superuser=admin, membership_type=membership,
                membership_expires_at=expiry, avatar_url=None, bio=None,
                created_at=NOW - timedelta(days=1), password_updated_at=None,
            ))
        seed.execute(insert(system_configs).values(id="config", muse_token=""))
        seed.commit()
    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: NOW,
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
    )
    def token(user_id):
        return jwt.encode(
            {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            SECRET,
            algorithm="HS256",
        )
    return engine, app, events, auth_sessions, business_sessions, token


def test_public_auth_stats_route_preserves_payload_worker_sessions_and_no_token_io(
    tmp_path: Path, monkeypatch
):
    calls = []

    class Response:
        ok = True

        def json(self):
            return {"balance": "balance-as-string"}

    def fake_get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        return Response()

    monkeypatch.setattr(requests, "get", fake_get)
    engine, app, events, auth_sessions, business_sessions, token = make_app(
        tmp_path / "stats-http.sqlite"
    )
    try:
        unauth = request(app, "GET", "/api/admin/stats")
        assert unauth.status_code == 401 and unauth.headers["www-authenticate"] == "Bearer"
        assert not business_sessions

        forbidden = request(
            app, "GET", "/api/admin/stats",
            headers={"Authorization": f"Bearer {token('member')}"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert not business_sessions

        expired_admin = request(
            app, "GET", "/api/admin/stats",
            headers={"Authorization": f"Bearer {token('expired-admin')}"},
        )
        assert NOW - timedelta(seconds=1) < NOW
        assert expired_admin.status_code == 200
        assert expired_admin.json()["upstream"] == {"balance": None}
        assert calls == []

        response = request(
            app, "GET", "/api/admin/stats",
            headers={"Authorization": f"Bearer {token('admin')}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"upstream", "users", "credits", "tasks", "content", "revenue"}
        assert payload["upstream"] == {"balance": None}
        assert payload["revenue"] == {"total": 0, "this_month": 0, "subscriptions": 2}
        assert calls == []

        with Session(engine) as seed:
            seed.execute(system_configs.update().values(muse_token="fake-upstream-token"))
            seed.commit()
        token_response = request(
            app, "GET", "/api/admin/stats",
            headers={"Authorization": f"Bearer {token('admin')}"},
        )
        assert token_response.status_code == 200
        assert token_response.json()["upstream"] == {"balance": "balance-as-string"}
        assert calls == [(
            "http://127.0.0.1:8765/v1/cash-accounts/mine",
            {"Authorization": "Bearer fake-upstream-token"},
            3,
        )]

        assert all(session.closed for session in auth_sessions + business_sessions)
        assert all(session.commits == 0 for session in business_sessions)
        assert all(item[0] != "business" or item[3] != threading.get_ident() for item in events)
        assert all(
            events[i - 1][0:2] == ("auth", "close")
            for i, item in enumerate(events)
            if item[0:2] == ("business", "factory")
        )
    finally:
        engine.dispose()


def test_upstream_error_is_suppressed_but_aggregate_sql_error_returns_500(
    tmp_path: Path, monkeypatch
):
    calls = []

    def failing_get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        raise OSError("fake transport error")

    monkeypatch.setattr(requests, "get", failing_get)
    engine, app, events, auth_sessions, business_sessions, token = make_app(
        tmp_path / "stats-errors.sqlite"
    )
    try:
        with Session(engine) as seed:
            seed.execute(system_configs.update().values(muse_token="fake-token"))
            seed.commit()
        response = request(
            app, "GET", "/api/admin/stats",
            headers={"Authorization": f"Bearer {token('admin')}"},
        )
        assert response.status_code == 200
        assert response.json()["upstream"] == {"balance": None}
        assert calls == [(
            "http://127.0.0.1:8765/v1/cash-accounts/mine",
            {"Authorization": "Bearer fake-token"},
            3,
        )]
        assert business_sessions[-1].commits == 0 and business_sessions[-1].closed
    finally:
        engine.dispose()


def test_early_aggregate_sql_failure_is_not_suppressed_and_closes_session(tmp_path: Path):
    engine, app, events, auth_sessions, business_sessions, token = make_app(
        tmp_path / "stats-query-error.sqlite"
    )
    def fail_first_aggregate(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if "count(users.id)" in normalized and "from users" in normalized:
            raise RuntimeError("aggregate query failed")
    event.listen(engine, "before_cursor_execute", fail_first_aggregate)
    try:
        response = request(
            app, "GET", "/api/admin/stats",
            headers={"Authorization": f"Bearer {token('admin')}"},
        )
        assert response.status_code == 500
        assert business_sessions[-1].closed
        assert business_sessions[-1].rollbacks >= 1
        assert business_sessions[-1].commits == 0
    finally:
        engine.dispose()
