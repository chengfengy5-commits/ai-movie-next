from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.admin_series.tables import SeriesRow, UserRow, metadata
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    users,
)

SECRET = "admin-series-http-test-secret"
NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)


class ObservedSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.rollback_count = 0
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

    def rollback(self):
        self.rollback_count += 1
        self.info["events"].append(
            (self.info["kind"], "rollback", id(self), threading.get_ident())
        )
        return super().rollback()

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
            base_url="http://admin-series.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def _enable_foreign_keys(connection, _record):
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def build_system(path: Path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _enable_foreign_keys)
    authentication_metadata.create_all(engine)
    metadata.create_all(engine)
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
            ("member", False, "premium", NOW - timedelta(seconds=1)),
            ("expired-admin", True, "premium", NOW - timedelta(days=4)),
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
                    created_at=NOW - timedelta(days=5),
                    password_updated_at=None,
                )
            )
        session.execute(
            insert(SeriesRow.__table__).values(
                id="existing",
                user_id="admin",
                name="HTTP series",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
    )

    def token(user_id: str) -> str:
        issued_at = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": user_id,
                "exp": issued_at + timedelta(minutes=5),
            },
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token


def test_public_jwt_auth_and_four_routes_use_separate_worker_sessions(tmp_path: Path):
    engine, app, events, auth_sessions, business_sessions, token = build_system(
        tmp_path / "admin-series.sqlite"
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
            "GET",
            "/api/admin/series",
        )
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"
        assert not unauth_business
        assert unauth_events[-1][0:2] == ("auth", "close")

        forbidden, forbidden_events, forbidden_business = traced_request(
            "GET",
            "/api/admin/series",
            headers={"Authorization": f"Bearer {token('member')}"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert forbidden_events[-1][0:2] == ("auth", "close")
        assert not forbidden_business

        expired_admin, expired_events, expired_business = traced_request(
            "GET",
            "/api/admin/series",
            headers={"Authorization": f"Bearer {token('expired-admin')}"},
        )
        assert expired_admin.status_code == 200
        assert expired_admin.json()["total"] == 1
        assert_business(expired_events, expired_business, 0, 0)

        listed, list_events, list_business = traced_request(
            "GET",
            "/api/admin/series?user_id=admin&name=HTTP",
            headers=admin,
        )
        assert listed.status_code == 200
        assert listed.json()["data"][0]["id"] == "existing"
        assert set(listed.json()["data"][0]) == {
            "id",
            "user_id",
            "name",
            "description",
            "image_url",
            "created_at",
            "updated_at",
        }
        assert_business(list_events, list_business, 0, 0)

        detail, detail_events, detail_business = traced_request(
            "GET",
            "/api/admin/series/existing",
            headers=admin,
        )
        assert detail.status_code == 200
        assert detail.json()["name"] == "HTTP series"
        assert_business(detail_events, detail_business, 0, 0)

        missing, missing_events, missing_business = traced_request(
            "GET",
            "/api/admin/series/missing",
            headers=admin,
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "剧集不存在"
        assert_business(missing_events, missing_business, 0, 0)
        assert missing_business[0].rollback_count >= 1

        created, create_events, create_business = traced_request(
            "POST",
            "/api/admin/series",
            headers=admin,
            body={"id": "caller-id", "user_id": "member"},
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert created_payload["id"] != "caller-id"
        assert created_payload["user_id"] == "admin"
        assert created_payload["name"] == ""
        assert created_payload["description"] == ""
        assert created_payload["image_url"] is None
        assert_business(create_events, create_business, 1, 1)

        updated, update_events, update_business = traced_request(
            "PUT",
            f"/api/admin/series/{created_payload['id']}",
            headers=admin,
            body={"id": "updated-id", "user_id": "member", "name": "Updated"},
        )
        assert updated.status_code == 200
        assert updated.json()["id"] == "updated-id"
        assert updated.json()["user_id"] == "member"
        assert updated.json()["name"] == "Updated"
        assert_business(update_events, update_business, 1, 1)

        deleted, delete_events, delete_business = traced_request(
            "DELETE",
            "/api/admin/series/updated-id",
            headers=admin,
        )
        assert deleted.status_code == 405
        assert not delete_business
        assert all(session.closed for session in auth_sessions)
        assert all(session.closed for session in business_sessions)
    finally:
        engine.dispose()
