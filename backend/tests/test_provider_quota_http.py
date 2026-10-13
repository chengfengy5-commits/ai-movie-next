from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.provider_quota.crypto import LegacyFernetCipher
from haoai_backend.provider_quota.http import build_provider_quota_router
from haoai_backend.provider_quota.tables import metadata, provider_quota_accounts
from test_provider_quota_support import (
    FakeAuthenticationService,
    FakeProviderQuotaTransport,
)


def request(app: FastAPI, method: str, path: str, *, body: Any = None, headers=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://provider-quota.test",
        ) as client:
            if body is None:
                return await client.request(method, path, headers=headers)
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


class ObservedSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = self.info["events"]
        self.closed = False

    def close(self):
        self.closed = True
        self.events.append(("business-close", threading.get_ident()))
        return super().close()


def make_app(path: Path):
    engine = create_engine(f"sqlite:///{path}")
    metadata.create_all(engine)
    events: list[tuple[Any, ...]] = []
    maker = sessionmaker(
        engine,
        class_=ObservedSession,
        expire_on_commit=False,
        future=True,
        info={"events": events},
    )
    sessions: list[ObservedSession] = []

    def session_factory():
        events.append(("business-open", threading.get_ident()))
        session = maker()
        sessions.append(session)
        return session

    auth = FakeAuthenticationService(events)
    transport = FakeProviderQuotaTransport()
    app = FastAPI()
    app.include_router(
        build_provider_quota_router(
            authentication_service=auth,
            session_factory=session_factory,
            cipher=LegacyFernetCipher("provider-quota-test-secret"),
            transport=transport,
        )
    )
    return engine, app, events, sessions, transport


def test_all_six_routes_preserve_auth_dto_credentials_and_fake_provider_calls(tmp_path: Path):
    engine, app, events, sessions, transport = make_app(tmp_path / "provider-quota.sqlite")
    admin = {"Authorization": "Bearer admin"}
    try:
        denied = request(app, "GET", "/api/provider-quota/accounts")
        assert denied.status_code == 401
        assert denied.headers["www-authenticate"] == "Bearer"
        assert not sessions

        forbidden = request(
            app,
            "GET",
            "/api/provider-quota/accounts",
            headers={"Authorization": "Bearer member"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert not sessions

        invalid_body = request(
            app,
            "POST",
            "/api/provider-quota/probe",
            body={"password": {"invalid": "shape"}},
        )
        assert invalid_body.status_code == 401
        assert invalid_body.headers["www-authenticate"] == "Bearer"
        assert not sessions

        created = request(
            app,
            "POST",
            "/api/provider-quota/accounts",
            headers=admin,
            body={
                "name": "Main gateway",
                "provider_type": "new_api",
                "base_url": "https://gateway.example/v1/",
                "username": "operator",
                "password": "fake-password",
            },
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert set(created_payload) == {
            "id", "name", "provider_type", "base_url", "username",
            "is_active", "sort_order", "console_url",
        }
        account_id = created_payload["id"]
        assert created_payload["username"] == "operator"
        assert created_payload["base_url"] == "https://gateway.example"

        listed = request(
            app,
            "GET",
            "/api/provider-quota/accounts",
            headers=admin,
        )
        assert listed.status_code == 200
        assert listed.json() == [created_payload]
        assert "password" not in listed.json()[0]

        updated = request(
            app,
            "PUT",
            f"/api/provider-quota/accounts/{account_id}",
            headers=admin,
            body={
                "name": "Main gateway 2",
                "base_url": "https://gateway.example/",
                "username": "",
                "password": "",
                "is_active": True,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["username"] == "operator"
        with Session(engine) as check:
            stored = check.execute(
                select(
                    provider_quota_accounts.c.username,
                    provider_quota_accounts.c.password,
                ).where(provider_quota_accounts.c.id == account_id)
            ).one()
        cipher = LegacyFernetCipher("provider-quota-test-secret")
        assert cipher.decrypt(stored.username) == "operator"
        assert cipher.decrypt(stored.password) == "fake-password"

        transport.enqueue(
            "GET",
            "https://gateway.example/api/status",
            payload={"data": {"quota_per_unit": 1000, "system_name": "Gateway"}},
        )
        transport.enqueue(
            "POST",
            "https://gateway.example/api/user/login",
            payload={
                "success": True,
                "data": {
                    "access_token": "fake-token",
                    "user": {"id": "user-1", "quota": 2500, "used_quota": 500},
                },
            },
        )
        balance = request(
            app,
            "GET",
            f"/api/provider-quota/accounts/{account_id}/balance",
            headers=admin,
        )
        assert balance.status_code == 200
        assert balance.json()["is_active"] is True
        assert balance.json()["balance"] == 2.5
        assert balance.json()["total"] == 3.0

        transport.enqueue(
            "GET",
            "https://api.deepseek.com/user/balance",
            payload={
                "balance_infos": [{"currency": "CNY", "total_balance": "80.5"}],
                "is_available": True,
            },
        )
        probe = request(
            app,
            "POST",
            "/api/provider-quota/probe",
            headers=admin,
            body={
                "provider_type": "deepseek",
                "base_url": "https://api.deepseek.com/",
                "password": "fake-api-key",
            },
        )
        assert probe.status_code == 200
        assert probe.json()["ok"] is True
        assert probe.json()["balance"] == 80.5

        deleted = request(
            app,
            "DELETE",
            f"/api/provider-quota/accounts/{account_id}",
            headers=admin,
        )
        assert deleted.status_code == 200 and deleted.json() == {"ok": True}
        missing = request(
            app,
            "GET",
            f"/api/provider-quota/accounts/{account_id}/balance",
            headers=admin,
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "监控账号不存在"

        assert all(session.closed for session in sessions)
        assert transport.calls[0][0:2] == (
            "GET",
            "https://gateway.example/api/status",
        )
        assert transport.calls[-1][0:2] == (
            "GET",
            "https://api.deepseek.com/user/balance",
        )
        for index, event in enumerate(events):
            if event[0] == "business-open":
                assert events[index - 1][0] == "auth-close"
    finally:
        engine.dispose()


def test_unconfigured_transport_never_performs_provider_requests(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'unwired.sqlite'}")
    metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(
        build_provider_quota_router(
            authentication_service=FakeAuthenticationService(),
            session_factory=maker,
            cipher=LegacyFernetCipher("test-secret"),
            transport=None,
        )
    )
    response = request(
        app,
        "POST",
        "/api/provider-quota/probe",
        headers={"Authorization": "Bearer admin"},
        body={
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com",
            "password": "fake-api-key",
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["errors"] == ["额度查询失败：供应商额度探测服务尚未接线"]

    empty_type = request(
        app,
        "POST",
        "/api/provider-quota/probe",
        headers={"Authorization": "Bearer admin"},
        body={
            "provider_type": "",
            "base_url": "https://api.deepseek.com",
            "username": "operator",
            "password": "fake-api-key",
        },
    )
    assert empty_type.status_code == 200
    assert empty_type.json()["errors"] == ["暂不支持的供应商架构：未填写"]
    engine.dispose()
