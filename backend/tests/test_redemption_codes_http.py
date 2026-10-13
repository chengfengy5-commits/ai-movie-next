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
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)
from haoai_backend.redemption_codes.http import build_redemption_codes_router
from haoai_backend.redemption_codes.persistence import (
    Base,
    CreditLogRow,
    RedemptionCodeRow,
    UserCreditRow,
    UserRow,
    billing_units_reference,
)

NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "redemption-codes-http-test-secret"


class ObservedSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.refresh_count = 0
        self.rollback_count = 0
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

    def rollback(self):
        self.rollback_count += 1
        self.info["events"].append(
            (self.info["kind"], "rollback", id(self), threading.get_ident())
        )
        return super().rollback()

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
            base_url="http://redemption-codes.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def build_system(path: Path, *, code_generator=None):
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    billing_units_reference.create(engine, checkfirst=True)
    authentication_metadata.create_all(engine)
    Base.metadata.create_all(
        engine,
        tables=[
            UserRow.__table__,
            UserCreditRow.__table__,
            CreditLogRow.__table__,
            RedemptionCodeRow.__table__,
        ],
    )
    events: list[tuple[str, str, int, int]] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def make_factory(kind: str, opened: list[ObservedSession]):
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            expire_on_commit=True,
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

    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {
                    "id": "admin",
                    "username": "admin",
                    "email": "admin@example.test",
                    "hashed_password": "unused",
                    "is_superuser": True,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW,
                    "password_updated_at": None,
                },
                {
                    "id": "free-user",
                    "username": "free-user",
                    "email": "free-user@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW,
                    "password_updated_at": None,
                },
                {
                    "id": "expired-user",
                    "username": "expired-user",
                    "email": "expired-user@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "premium",
                    "membership_expires_at": NOW - timedelta(days=10),
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW,
                    "password_updated_at": None,
                },
                {
                    "id": "revoked-user",
                    "username": "revoked-user",
                    "email": "revoked-user@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW,
                    "password_updated_at": None,
                },
            ],
        )
        connection.execute(
            insert(user_sessions).values(
                id="revoked-session",
                user_id="revoked-user",
                jti="revoked-jti",
                user_agent="test",
                ip_address="127.0.0.1",
                device_name="test",
                is_active=False,
                created_at=NOW,
                last_seen_at=NOW,
                revoked_at=NOW,
            )
        )
        connection.execute(
            insert(RedemptionCodeRow.__table__),
            [
                {
                    "id": "credit-code",
                    "code": "HAOAI-CREDIT-0001",
                    "type": "credits",
                    "value": 8,
                    "is_used": False,
                    "used_by": None,
                    "used_at": None,
                    "created_by": "admin",
                    "expires_at": None,
                    "created_at": NOW,
                },
                {
                    "id": "membership-code",
                    "code": "HAOAI-MEMBER-0001",
                    "type": "membership",
                    "value": 5,
                    "is_used": False,
                    "used_by": None,
                    "used_at": None,
                    "created_by": "admin",
                    "expires_at": None,
                    "created_at": NOW,
                },
            ],
        )

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: datetime.now(timezone.utc),
    )
    authentication_service = AuthenticationService(
        runtime,
        lambda: SqlAlchemyAuthenticationUnitOfWork(auth_factory()),
    )
    app = FastAPI()
    app.include_router(
        build_redemption_codes_router(
            authentication_service=authentication_service,
            session_factory=business_factory,
            code_generator=code_generator,
        )
    )

    def token(user_id: str, *, jti: str | None = None) -> str:
        issued_at = datetime.now(timezone.utc)
        claims: dict[str, Any] = {
            "sub": user_id,
            "exp": issued_at + timedelta(minutes=5),
        }
        if jti is not None:
            claims["jti"] = jti
        return jwt.encode(claims, SECRET, algorithm="HS256")

    return engine, app, events, auth_sessions, business_sessions, token


def trace_request(app, events, business_sessions, method, path, *, body=None, headers=None):
    start = len(events)
    business_start = len(business_sessions)
    response = request(app, method, path, body=body, headers=headers)
    return response, events[start:], business_sessions[business_start:]


def assert_success_lifecycle(request_events, sessions, *, commits: int, refreshes: int):
    assert len(sessions) == 1
    factory_index = next(
        index
        for index, event_item in enumerate(request_events)
        if event_item[0:2] == ("business", "factory")
    )
    assert request_events[factory_index - 1][0:2] == ("auth", "close")
    assert sessions[0].closed
    assert sessions[0].commit_count == commits
    assert sessions[0].refresh_count == refreshes
    caller_thread = threading.get_ident()
    assert all(
        event_item[3] != caller_thread
        for event_item in request_events
        if event_item[0] in {"auth", "business"}
    )


def test_real_jwt_generation_and_free_or_expired_account_redemption(tmp_path: Path):
    class FalseyGenerator:
        def __init__(self):
            self.calls = 0

        def __bool__(self):
            return False

        def __call__(self):
            self.calls += 1
            return "HAOAI-1A2B-3C4D"

    generator = FalseyGenerator()
    engine, app, events, auth_sessions, business_sessions, token = build_system(
        tmp_path / "redemption-http.sqlite",
        code_generator=generator,
    )
    try:
        admin_headers = {"Authorization": f"Bearer {token('admin')}"}
        generated, generated_events, generated_sessions = trace_request(
            app,
            events,
            business_sessions,
            "POST",
            "/api/redemption-codes/generate",
            body={
                "type": "credits",
                "value": 4,
                "count": 1,
                "expires_at": "",
                "id": "caller-id",
                "code": "caller-code",
                "created_by": "free-user",
                "is_used": True,
            },
            headers=admin_headers,
        )
        assert generated.status_code == 200
        payload = generated.json()
        assert payload["count"] == 1 and payload["type"] == "credits" and payload["value"] == 4
        assert payload["codes"][0]["code"] == "HAOAI-1A2B-3C4D"
        assert payload["codes"][0]["is_used"] is False
        assert payload["codes"][0]["created_by"] == "admin"
        assert payload["codes"][0]["expires_at"] is None
        assert generator.calls == 1
        assert_success_lifecycle(generated_events, generated_sessions, commits=1, refreshes=1)

        free_headers = {"Authorization": f"Bearer {token('free-user')}"}
        credits, credit_events, credit_sessions = trace_request(
            app,
            events,
            business_sessions,
            "POST",
            "/api/redemption-codes/redeem",
            body={"code": "  haoai-credit-0001  "},
            headers=free_headers,
        )
        assert credits.status_code == 200
        assert credits.json() == {
            "message": "兑换成功！获得 8 积分",
            "type": "credits",
            "value": 8,
            "detail": "当前积分余额：8",
        }
        assert_success_lifecycle(credit_events, credit_sessions, commits=1, refreshes=0)

        expired_headers = {"Authorization": f"Bearer {token('expired-user')}"}
        membership, membership_events, membership_sessions = trace_request(
            app,
            events,
            business_sessions,
            "POST",
            "/api/redemption-codes/redeem",
            body={"code": "HAOAI-MEMBER-0001"},
            headers=expired_headers,
        )
        expected_expiry = NOW - timedelta(days=5)
        assert membership.status_code == 200
        assert membership.json() == {
            "message": "兑换成功！会员时长增加 5 天",
            "type": "membership",
            "value": 5,
            "detail": f"会员有效期至：{expected_expiry.strftime('%Y-%m-%d %H:%M')}",
        }
        assert_success_lifecycle(membership_events, membership_sessions, commits=1, refreshes=0)
        assert all(session.closed for session in auth_sessions)

        with Session(engine) as verify:
            wallet = verify.query(UserCreditRow).filter_by(user_id="free-user").one()
            code = verify.query(RedemptionCodeRow).filter_by(code="HAOAI-CREDIT-0001").one()
            logs = verify.query(CreditLogRow).filter_by(user_id="free-user").all()
            expired = verify.query(UserRow).filter_by(id="expired-user").one()
            assert wallet.credits == 8
            assert code.is_used is True and code.used_by == "free-user"
            assert len(logs) == 1 and logs[0].business_key is None
            assert expired.membership_expires_at == expected_expiry
    finally:
        engine.dispose()


def test_authentication_precedes_invalid_body_and_closes_without_business_session(tmp_path: Path):
    engine, app, events, auth_sessions, business_sessions, token = build_system(
        tmp_path / "redemption-auth-order.sqlite"
    )

    def assert_auth_only(request_events, opened, *, expected_status, bearer=False):
        assert not opened
        assert request_events[-1][0:2] == ("auth", "close")
        assert all(session.closed for session in auth_sessions)
        assert all(event_item[0] != "business" for event_item in request_events)
        if bearer:
            assert expected_status == 401

    try:
        missing, missing_events, missing_business = trace_request(
            app,
            events,
            business_sessions,
            "POST",
            "/api/redemption-codes/generate",
            body=[],
        )
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert_auth_only(missing_events, missing_business, expected_status=401, bearer=True)

        free_headers = {"Authorization": f"Bearer {token('free-user')}"}
        forbidden, forbidden_events, forbidden_business = trace_request(
            app,
            events,
            business_sessions,
            "POST",
            "/api/redemption-codes/generate",
            body=[],
            headers=free_headers,
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert_auth_only(forbidden_events, forbidden_business, expected_status=403)

        expired_headers = {"Authorization": f"Bearer {token('expired-user')}"}
        invalid_redeem, invalid_events, invalid_business = trace_request(
            app,
            events,
            business_sessions,
            "POST",
            "/api/redemption-codes/redeem",
            body={},
            headers=expired_headers,
        )
        assert invalid_redeem.status_code == 422
        assert_auth_only(invalid_events, invalid_business, expected_status=422)

        revoked_headers = {
            "Authorization": f"Bearer {token('revoked-user', jti='revoked-jti')}"
        }
        revoked, revoked_events, revoked_business = trace_request(
            app,
            events,
            business_sessions,
            "POST",
            "/api/redemption-codes/redeem",
            body=[],
            headers=revoked_headers,
        )
        assert revoked.status_code == 401
        assert revoked.headers["www-authenticate"] == "Bearer"
        assert_auth_only(revoked_events, revoked_business, expected_status=401, bearer=True)
    finally:
        engine.dispose()


def test_authenticated_schema_422_and_invalid_iso_400(tmp_path: Path):
    engine, app, events, _auth_sessions, business_sessions, token = build_system(
        tmp_path / "redemption-schema.sqlite"
    )
    headers = {"Authorization": f"Bearer {token('admin')}"}
    try:
        invalid_schema, invalid_events, invalid_business = trace_request(
            app,
            events,
            business_sessions,
            "POST",
            "/api/redemption-codes/generate",
            body={"type": "other", "value": 1},
            headers=headers,
        )
        assert invalid_schema.status_code == 422
        assert not invalid_business
        assert invalid_events[-1][0:2] == ("auth", "close")

        invalid_iso, iso_events, iso_business = trace_request(
            app,
            events,
            business_sessions,
            "POST",
            "/api/redemption-codes/generate",
            body={"type": "membership", "value": 3, "expires_at": "not-iso"},
            headers=headers,
        )
        assert invalid_iso.status_code == 400
        assert invalid_iso.json()["detail"] == "expires_at 格式不正确，请使用 ISO 格式"
        assert len(iso_business) == 1 and iso_business[0].closed
        assert iso_business[0].commit_count == 0
        assert iso_business[0].rollback_count == 1
        rollback_events = [
            event_item
            for event_item in iso_events
            if event_item[0:2] == ("business", "rollback")
        ]
        assert len(rollback_events) == 1
        assert rollback_events[0][2] == id(iso_business[0])
        assert rollback_events[0][3] != threading.get_ident()
        close_index = next(
            index
            for index, event_item in enumerate(iso_events)
            if event_item[0:2] == ("business", "close")
        )
        rollback_index = next(
            index
            for index, event_item in enumerate(iso_events)
            if event_item[0:2] == ("business", "rollback")
        )
        assert rollback_index < close_index
        business_index = next(
            index for index, item in enumerate(iso_events) if item[0:2] == ("business", "factory")
        )
        assert iso_events[business_index - 1][0:2] == ("auth", "close")
    finally:
        engine.dispose()


def test_unwired_router_returns_503_without_opening_a_business_session():
    calls = 0

    def session_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("unwired endpoint must not create a business session")

    app = FastAPI()
    app.include_router(
        build_redemption_codes_router(
            authentication_service=None,
            session_factory=session_factory,
        )
    )

    response = request(
        app,
        "POST",
        "/api/redemption-codes/redeem",
        body={"code": "HAOAI-TEST-0001"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "兑换码服务尚未接线"}
    assert calls == 0
