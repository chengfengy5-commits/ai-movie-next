from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from jose import jwt
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_system_config.tables import (
    SystemConfigRow,
    metadata as system_config_metadata,
)
from haoai_backend.admin_system_models.crypto import LegacyFernet
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import users
from haoai_backend.app import create_app
from haoai_backend.model_configs.tables import (
    SystemModelRow,
    metadata as model_configs_metadata,
)
from haoai_backend.provider_quota.tables import (
    metadata as provider_quota_metadata,
    provider_quota_accounts,
)
from test_provider_quota_support import FakeProviderQuotaTransport

AUTH_SECRET = "provider-integrations-explicit-test-secret"


class ObservedSession(Session):
    def close(self) -> None:
        trace = self.info.get("trace")
        label = self.info.get("trace_label")
        if trace is not None and label is not None:
            trace.append((f"{label}-close", threading.get_ident()))
        super().close()


class FakeBalanceTransport:
    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 8,
    ) -> dict[str, Any]:
        raise AssertionError(f"PAT integration must not query a provider: {url}")


class FakeAistarslabTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.config = {
            "videoConfig": [
                {
                    "channel": "video-main",
                    "title": "Video Main",
                    "description": "test route",
                    "defaultOption": True,
                    "models": [
                        {
                            "model": "model-v",
                            "label": "Video V",
                            "modes": ["t2v"],
                            "aspectRatios": ["16:9"],
                            "duration": {"min": 4, "max": 8},
                            "qualities": [
                                {
                                    "quality": "720p",
                                    "pricing": {
                                        "type": "per_second",
                                        "credits": 3,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
            "imageConfig": [],
        }

    async def request_text(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        json_body: dict[str, Any] | None,
        timeout: int,
    ) -> str:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json_body": json_body,
                "timeout": timeout,
            }
        )
        if url.endswith("/generation/config"):
            return json.dumps({"code": 0, "data": self.config})
        raise AssertionError(f"unexpected AIStarsLab request: {method} {url}")


def request(
    app,
    method: str,
    path: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://provider-integrations.test",
        ) as client:
            kwargs: dict[str, Any] = {"headers": headers}
            if body is not None:
                kwargs["json"] = body
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def build_harness(path: Path):
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    database_now = now.replace(tzinfo=None)
    engine = create_engine(f"sqlite:///{path}")
    authentication_metadata.create_all(engine)
    model_configs_metadata.create_all(engine)
    system_config_metadata.create_all(engine)
    provider_quota_metadata.create_all(engine)

    events: list[tuple[str, int]] = []
    auth_maker = sessionmaker(
        engine,
        class_=ObservedSession,
        expire_on_commit=False,
        future=True,
    )
    business_maker = sessionmaker(
        engine,
        class_=ObservedSession,
        expire_on_commit=False,
        future=True,
    )

    def session_factory_for(label: str):
        maker = auth_maker if label == "auth" else business_maker

        def create_session() -> ObservedSession:
            events.append((f"{label}-open", threading.get_ident()))
            session = maker()
            session.info["trace"] = events
            session.info["trace_label"] = label
            return session

        return create_session

    auth_session_factory = session_factory_for("auth")
    business_session_factory = session_factory_for("business")
    cipher = LegacyFernet(AUTH_SECRET)
    with Session(engine, future=True) as seed:
        for user_id, is_admin in (("admin", True), ("member", False)):
            seed.execute(
                insert(users).values(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.test",
                    hashed_password="unused",
                    is_superuser=is_admin,
                    membership_type="premium",
                    membership_expires_at=None,
                    avatar_url=None,
                    bio=None,
                    created_at=database_now,
                    password_updated_at=None,
                )
            )
        seed.add(
            SystemModelRow(
                id="zhangyuge-model",
                category="video",
                name="Zhangyuge integration model",
                provider="zhangyuge",
                api_key=cipher.encrypt("zhangyuge-test-api-key"),
                model="balance",
                base_url="https://billing.test/v1",
                cost_per_call=0,
                description="",
                is_active=True,
                sort_order=0,
                params=None,
            )
        )
        seed.add(
            SystemModelRow(
                id="aistarslab-model",
                category="video",
                name="AIStarsLab integration model",
                provider="aistarslab",
                api_key=cipher.encrypt("aistarslab-test-api-key"),
                model="video-main:model-v",
                base_url="https://aistarslab.test/openapi",
                cost_per_call=0,
                description="",
                is_active=True,
                sort_order=1,
                params=None,
            )
        )
        seed.add(
            SystemConfigRow(
                id="integration-config",
                aistarslab_credit_multiplier=1.5,
                zhangyuge_billing_pat="",
            )
        )
        seed.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_session_factory,
        secret_key=AUTH_SECRET,
        clock=lambda: database_now,
    )
    provider_transport = FakeProviderQuotaTransport()
    balance_transport = FakeBalanceTransport()
    aistarslab_transport = FakeAistarslabTransport()
    app = create_app(
        session_factory=business_session_factory,
        authentication_runtime=runtime,
        provider_quota_transport=provider_transport,
        zhangyuge_balance_transport=balance_transport,
        aistarslab_transport=aistarslab_transport,
    )

    def token_for(user_id: str) -> str:
        return jwt.encode(
            {
                "sub": user_id,
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            AUTH_SECRET,
            algorithm="HS256",
        )

    return (
        engine,
        app,
        events,
        token_for,
        provider_transport,
        aistarslab_transport,
        cipher,
    )


def assert_no_business_session(events: list[tuple[str, int]]) -> None:
    assert not any(name == "business-open" for name, _thread_id in events), events


def assert_authentication_closes_before_business_work(
    events: list[tuple[str, int]],
) -> None:
    auth_closes = [index for index, (name, _thread_id) in enumerate(events) if name == "auth-close"]
    business_opens = [
        index
        for index, (name, _thread_id) in enumerate(events)
        if name == "business-open"
    ]
    assert auth_closes and business_opens, events
    assert max(auth_closes) < min(business_opens), events


def test_app_integrates_provider_quota_crud_with_jwt_and_fake_transport(tmp_path: Path) -> None:
    engine, app, events, token_for, transport, _, cipher = build_harness(
        tmp_path / "provider-integrations.sqlite"
    )
    admin = {"Authorization": f"Bearer {token_for('admin')}"}
    member = {"Authorization": f"Bearer {token_for('member')}"}
    try:
        assert events == []
        assert transport.calls == []

        missing = request(
            app,
            "POST",
            "/api/provider-quota/probe",
            body={"password": {"invalid": "shape"}},
        )
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert_no_business_session(events)
        events.clear()

        forbidden = request(
            app,
            "POST",
            "/api/provider-quota/probe",
            headers=member,
            body={"provider_type": "deepseek", "base_url": "https://api.deepseek.com"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert_no_business_session(events)
        events.clear()

        invalid = request(
            app,
            "POST",
            "/api/provider-quota/probe",
            headers=admin,
            body={"password": {"invalid": "shape"}},
        )
        assert invalid.status_code == 422
        assert_no_business_session(events)
        events.clear()

        created = request(
            app,
            "POST",
            "/api/provider-quota/accounts",
            headers=admin,
            body={
                "name": "Main gateway",
                "provider_type": "new_api",
                "base_url": "https://gateway.test/v1/",
                "username": "operator",
                "password": "fake-password",
            },
        )
        assert created.status_code == 200, created.text
        created_body = created.json()
        account_id = created_body["id"]
        assert created_body["base_url"] == "https://gateway.test"
        assert_authentication_closes_before_business_work(events)
        events.clear()

        listed = request(
            app,
            "GET",
            "/api/provider-quota/accounts",
            headers=admin,
        )
        assert listed.status_code == 200
        assert listed.json() == [created_body]
        assert "password" not in listed.json()[0]
        assert_authentication_closes_before_business_work(events)
        events.clear()

        updated = request(
            app,
            "PUT",
            f"/api/provider-quota/accounts/{account_id}",
            headers=admin,
            body={
                "name": "Main gateway updated",
                "provider_type": "new_api",
                "base_url": "https://gateway.test/",
                "username": "",
                "password": "",
                "is_active": True,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["username"] == "operator"
        with Session(engine) as stored:
            credentials = stored.execute(
                select(
                    provider_quota_accounts.c.username,
                    provider_quota_accounts.c.password,
                ).where(provider_quota_accounts.c.id == account_id)
            ).one()
        assert cipher.decrypt(credentials.username) == "operator"
        assert cipher.decrypt(credentials.password) == "fake-password"

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
        assert probe.status_code == 200, probe.text
        assert probe.json()["ok"] is True
        assert probe.json()["balance"] == 80.5
        assert transport.calls[0][0:2] == (
            "GET",
            "https://api.deepseek.com/user/balance",
        )

        deleted = request(
            app,
            "DELETE",
            f"/api/provider-quota/accounts/{account_id}",
            headers=admin,
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True}
        remaining = request(
            app,
            "GET",
            "/api/provider-quota/accounts",
            headers=admin,
        )
        assert remaining.status_code == 200
        assert remaining.json() == []
    finally:
        engine.dispose()


def test_app_integrates_zhangyuge_pat_storage_with_jwt_authentication(
    tmp_path: Path,
) -> None:
    engine, app, events, token_for, _, _, cipher = build_harness(
        tmp_path / "zhangyuge-integrations.sqlite"
    )
    admin = {"Authorization": f"Bearer {token_for('admin')}"}
    member = {"Authorization": f"Bearer {token_for('member')}"}
    try:
        missing = request(
            app,
            "POST",
            "/api/zhangyuge/pat",
            body={"pat": []},
        )
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert_no_business_session(events)
        events.clear()

        forbidden = request(
            app,
            "POST",
            "/api/zhangyuge/pat",
            headers=member,
            body={"pat": "member-pat"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert_no_business_session(events)
        events.clear()

        invalid = request(
            app,
            "POST",
            "/api/zhangyuge/pat",
            headers=admin,
            body={"pat": []},
        )
        assert invalid.status_code == 422
        assert_no_business_session(events)
        events.clear()

        before = request(app, "GET", "/api/zhangyuge/pat", headers=admin)
        assert before.status_code == 200
        assert before.json() == {"configured": False}
        assert_authentication_closes_before_business_work(events)
        events.clear()

        saved = request(
            app,
            "POST",
            "/api/zhangyuge/pat",
            headers=admin,
            body={"pat": "  next-test-pat  "},
        )
        assert saved.status_code == 200
        assert saved.json() == {"ok": True}
        assert_authentication_closes_before_business_work(events)
        with Session(engine) as stored:
            ciphertext = stored.execute(
                select(SystemConfigRow.zhangyuge_billing_pat).where(
                    SystemConfigRow.id == "integration-config"
                )
            ).scalar_one()
        assert ciphertext != "next-test-pat"
        assert cipher.decrypt(ciphertext) == "next-test-pat"
        events.clear()

        after = request(app, "GET", "/api/zhangyuge/pat", headers=admin)
        assert after.status_code == 200
        assert after.json() == {"configured": True}
        assert_authentication_closes_before_business_work(events)
    finally:
        engine.dispose()


def test_app_integrates_aistarslab_read_and_sync_without_provider_network(
    tmp_path: Path,
) -> None:
    engine, app, events, token_for, _, transport, _ = build_harness(
        tmp_path / "aistarslab-integrations.sqlite"
    )
    admin = {"Authorization": f"Bearer {token_for('admin')}"}
    member = {"Authorization": f"Bearer {token_for('member')}"}
    try:
        assert events == []
        assert transport.calls == []

        missing = request(
            app,
            "POST",
            "/api/aistarslab/add-default",
            body={"api_key": []},
        )
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert_no_business_session(events)
        assert transport.calls == []
        events.clear()

        forbidden = request(
            app,
            "POST",
            "/api/aistarslab/add-default",
            headers=member,
            body={"api_key": "unused-test-key"},
        )
        assert forbidden.status_code == 403
        assert_no_business_session(events)
        assert transport.calls == []
        events.clear()

        invalid = request(
            app,
            "POST",
            "/api/aistarslab/add-default",
            headers=admin,
            body={"api_key": []},
        )
        assert invalid.status_code == 422
        assert_no_business_session(events)
        assert transport.calls == []
        events.clear()

        configured = request(app, "GET", "/api/aistarslab/config", headers=admin)
        assert configured.status_code == 200, configured.text
        assert configured.json()["videoConfig"][0]["channel"] == "video-main"
        assert_authentication_closes_before_business_work(events)
        assert transport.calls[0]["url"] == (
            "https://aistarslab.test/openapi/generation/config"
        )
        assert transport.calls[0]["headers"]["Authorization"] == (
            "Bearer aistarslab-test-api-key"
        )
        events.clear()

        synced = request(
            app,
            "POST",
            "/api/aistarslab/sync-all",
            headers=admin,
        )
        assert synced.status_code == 200, synced.text
        assert synced.json() == {"created": 0, "updated": 1, "total": 1}
        assert_authentication_closes_before_business_work(events)
        assert len(transport.calls) == 2
    finally:
        engine.dispose()
