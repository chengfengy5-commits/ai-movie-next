from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import users
from haoai_backend.zhangyuge.crypto import LegacyFernet
from haoai_backend.zhangyuge.http import build_zhangyuge_router
from haoai_backend.zhangyuge.tables import (
    metadata as zhangyuge_metadata,
    system_configs,
    system_models,
)


SECRET = "zhangyuge-http-fake-test-secret"


class FakeBalanceTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str] | None, float]] = []

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 8,
    ) -> dict[str, Any]:
        self.requests.append((url, headers, timeout_seconds))
        if url.endswith("/api/status"):
            return {
                "data": {"quota_per_unit": 1000, "usd_exchange_rate": 2},
            }
        if url.endswith("/api/user/self"):
            return {"data": {"quota": 2500}}
        if url.endswith("/v1/dashboard/billing/usage"):
            return {"total_usage": 345}
        raise AssertionError(f"unexpected fake-provider URL: {url}")


def request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://zhangyuge.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def build_system(path: Path, *, with_credentials: bool):
    from sqlalchemy import create_engine

    now = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    zhangyuge_metadata.create_all(engine)
    auth_maker = sessionmaker(engine, expire_on_commit=False, future=True)
    business_maker = sessionmaker(engine, expire_on_commit=False, future=True)
    business_session_calls = 0

    def auth_session_factory() -> Session:
        return auth_maker()

    def business_session_factory() -> Session:
        nonlocal business_session_calls
        business_session_calls += 1
        return business_maker()

    cipher = LegacyFernet(SECRET)
    with Session(engine, future=True) as seed:
        for user_id, is_admin in (("admin", True), ("member", False)):
            seed.execute(
                insert(users).values(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.test",
                    hashed_password="unused",
                    is_superuser=is_admin,
                    membership_type="free",
                    membership_expires_at=None,
                    avatar_url=None,
                    bio=None,
                    created_at=now - timedelta(days=1),
                    password_updated_at=None,
                )
            )
        if with_credentials:
            seed.execute(
                insert(system_models).values(
                    id="model-1",
                    provider="zhangyuge",
                    is_active=True,
                    api_key=cipher.encrypt("fake-api-key"),
                    base_url="https://fake-provider.example/v1/",
                )
            )
            seed.execute(
                insert(system_configs).values(
                    id="config-1",
                    zhangyuge_billing_pat=cipher.encrypt("fake-pat"),
                )
            )
        seed.commit()

    authentication_runtime = AuthenticationRuntime(
        session_factory=auth_session_factory,
        secret_key=SECRET,
        clock=lambda: now,
    )
    authentication_service = AuthenticationService(
        authentication_runtime,
        lambda: SqlAlchemyAuthenticationUnitOfWork(auth_session_factory()),
    )
    transport = FakeBalanceTransport()
    app = FastAPI()
    app.include_router(
        build_zhangyuge_router(
            authentication_service=authentication_service,
            session_factory=business_session_factory,
            balance_transport=transport,
            cipher=cipher,
        )
    )

    def token_for(user_id: str) -> str:
        return jwt.encode(
            {"sub": user_id, "exp": now + timedelta(minutes=5)},
            SECRET,
            algorithm="HS256",
        )

    return engine, app, transport, cipher, token_for, lambda: business_session_calls


def test_routes_keep_admin_pat_storage_and_balance_contract(tmp_path: Path) -> None:
    engine, app, transport, cipher, token_for, business_calls = build_system(
        tmp_path / "zhangyuge-http.sqlite",
        with_credentials=True,
    )
    admin = {"Authorization": f"Bearer {token_for('admin')}"}

    missing_auth = request(app, "GET", "/api/zhangyuge/pat")
    assert missing_auth.status_code == 401
    assert missing_auth.headers.get("www-authenticate") == "Bearer"
    assert business_calls() == 0

    member = request(
        app,
        "GET",
        "/api/zhangyuge/pat",
        headers={"Authorization": f"Bearer {token_for('member')}"},
    )
    assert member.status_code == 403
    assert member.json()["detail"] == "权限不足，仅管理员可执行此操作"
    assert business_calls() == 0

    configured = request(app, "GET", "/api/zhangyuge/pat", headers=admin)
    assert configured.status_code == 200
    assert configured.json() == {"configured": True}
    assert "fake-pat" not in configured.text

    balance = request(app, "GET", "/api/zhangyuge/balance", headers=admin)
    assert balance.status_code == 200
    result = balance.json()
    assert {key: value for key, value in result.items() if key != "updated_at"} == {
        "balance_usd": 2.5,
        "balance_cny": 5.0,
        "used_usd": 3.45,
        "used_cny": 6.9,
        "quota_per_unit": 1000.0,
        "usd_exchange_rate": 2.0,
        "pat_configured": True,
        "api_key_configured": True,
        "errors": [],
    }
    assert [item[0] for item in transport.requests] == [
        "https://fake-provider.example/api/status",
        "https://fake-provider.example/api/user/self",
        "https://fake-provider.example/v1/dashboard/billing/usage",
    ]
    assert transport.requests[1][1] == {"Authorization": "Bearer fake-pat"}
    assert transport.requests[2][1] == {"Authorization": "Bearer fake-api-key"}
    assert all(item[2] == 8 for item in transport.requests)

    saved = request(
        app,
        "POST",
        "/api/zhangyuge/pat",
        body={"pat": "  next-fake-pat  "},
        headers=admin,
    )
    assert saved.status_code == 200
    assert saved.json() == {"ok": True}
    with Session(engine) as stored:
        ciphertext = stored.execute(
            select(system_configs.c.zhangyuge_billing_pat)
            .where(system_configs.c.id == "config-1")
        ).scalar_one()
    assert ciphertext != "next-fake-pat"
    assert cipher.decrypt(ciphertext) == "next-fake-pat"

    cleared = request(
        app,
        "POST",
        "/api/zhangyuge/pat",
        body={"pat": "   "},
        headers=admin,
    )
    assert cleared.status_code == 200
    assert cleared.json() == {"ok": True}
    configured_after_clear = request(app, "GET", "/api/zhangyuge/pat", headers=admin)
    assert configured_after_clear.json() == {"configured": False}
    engine.dispose()


def test_balance_without_model_key_or_pat_keeps_legacy_400(tmp_path: Path) -> None:
    engine, app, transport, _, token_for, _ = build_system(
        tmp_path / "zhangyuge-no-credentials.sqlite",
        with_credentials=False,
    )

    response = request(
        app,
        "GET",
        "/api/zhangyuge/balance",
        headers={"Authorization": f"Bearer {token_for('admin')}"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "未配置章鱼哥AI 的 API Key / PAT，请先在模型设置添加章鱼哥模型"
    )
    assert transport.requests == []
    engine.dispose()
