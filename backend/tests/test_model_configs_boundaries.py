from __future__ import annotations

import ast
import asyncio
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.domain import UserRecord, UserSessionRecord
from haoai_backend.authentication.tokens import ExpiredTokenError, TokenDecodeError
from haoai_backend.model_configs.http import build_model_configs_router
from haoai_backend.model_configs.persistence import (
    model_configs_unit_of_work_factory,
)
from haoai_backend.model_configs.tables import Base, UserRow


PACKAGE = Path(__file__).parents[1] / "src" / "haoai_backend" / "model_configs"
NOW = datetime(2026, 10, 13, 9, 30)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_application_and_ports_keep_frameworks_out() -> None:
    for filename in ("domain.py", "application.py", "ports.py"):
        modules = imported_modules(PACKAGE / filename)
        assert not any(
            name == "fastapi"
            or name.startswith("fastapi.")
            or name == "sqlalchemy"
            or name.startswith("sqlalchemy.")
            or name.startswith("app.")
            or name.startswith("haoai_backend.app")
            for name in modules
        )

    for path in PACKAGE.glob("*.py"):
        assert not any(name.startswith("app.") for name in imported_modules(path))


def test_unwired_router_returns_503_without_auth_or_session_and_test_route_stays_open() -> None:
    opened: list[str] = []

    def session_factory():
        opened.append("business")
        raise AssertionError("cold route opened a database session")

    app = FastAPI()
    app.include_router(
        build_model_configs_router(
            authentication_service=None,
            session_factory=session_factory,
        )
    )

    list_response = request(app, "GET", "/api/models")
    assert list_response.status_code == 503
    assert list_response.json() == {"detail": "模型配置服务尚未接线"}

    malformed_create = request(app, "POST", "/api/models", json=[])
    assert malformed_create.status_code == 503
    assert malformed_create.json() == {"detail": "模型配置服务尚未接线"}

    connection = request(
        app,
        "POST",
        "/api/models/test-connection",
        json={"provider": "custom", "api_key": ""},
    )
    assert connection.status_code == 200
    assert connection.json() == {"success": False, "message": "API Key 不能为空"}
    assert opened == []


def request(app: FastAPI, method: str, path: str, **kwargs: Any) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://models-boundary.test",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


class TokenCodec:
    def decode(self, token: str) -> dict[str, str]:
        if token == "expired-access":
            raise ExpiredTokenError()
        if token == "invalid":
            raise TokenDecodeError()
        claims = {
            "member": {"sub": "member"},
            "free": {"sub": "free"},
            "expired-membership": {"sub": "expired-membership"},
            "revoked": {"sub": "revoked", "jti": "revoked-jti"},
            "free-admin": {"sub": "free-admin"},
        }
        return claims[token]


class AuthRuntime:
    secret_key = "test-secret"
    clock = staticmethod(lambda: NOW)

    def get_token_codec(self) -> TokenCodec:
        return TokenCodec()


def user_record(
    user_id: str,
    *,
    admin: bool = False,
    membership: str = "premium",
    expires: datetime | None = None,
) -> UserRecord:
    return UserRecord(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.test",
        hashed_password="unused",
        is_superuser=admin,
        membership_type=membership,
        membership_expires_at=expires,
        avatar_url=None,
        bio=None,
        created_at=datetime(2026, 1, 1),
        password_updated_at=None,
    )


class AuthUnitOfWork:
    def __init__(self, events: list[tuple[str, int]]) -> None:
        self.events = events
        self.users = {
            "member": user_record("member"),
            "free": user_record("free", membership="free"),
            "expired-membership": user_record(
                "expired-membership",
                expires=NOW - timedelta(seconds=1),
            ),
            "revoked": user_record("revoked"),
            "free-admin": user_record("free-admin", admin=True, membership="free"),
        }
        self.sessions = {
            "revoked-jti": UserSessionRecord(
                id="revoked-session",
                user_id="revoked",
                jti="revoked-jti",
                user_agent=None,
                ip_address=None,
                device_name=None,
                is_active=False,
                created_at=NOW - timedelta(days=2),
                last_seen_at=NOW - timedelta(days=1),
                revoked_at=NOW - timedelta(days=1),
            )
        }

    def load_user_by_id(self, user_id: str) -> UserRecord | None:
        return self.users.get(user_id)

    def load_session_by_jti(self, user_id: str, jti: str):
        session = self.sessions.get(jti)
        return session if session is not None and session.user_id == user_id else None

    def rollback(self) -> None:
        self.events.append(("auth-rollback", threading.get_ident()))

    def close(self) -> None:
        self.events.append(("auth-close", threading.get_ident()))


def test_authentication_errors_precede_invalid_body_and_close_before_business_session(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, int]] = []

    def auth_factory() -> AuthUnitOfWork:
        events.append(("auth-open", threading.get_ident()))
        return AuthUnitOfWork(events)

    service = AuthenticationService(AuthRuntime(), auth_factory)
    engine = create_engine(f"sqlite:///{tmp_path / 'model-configs-auth.sqlite'}")
    Base.metadata.create_all(engine)
    raw_sessions = sessionmaker(engine, expire_on_commit=False, future=True)
    with raw_sessions.begin() as session:
        session.add(
            UserRow(
                id="member",
                username="member",
                email="member@example.test",
                hashed_password="hash",
                is_superuser=False,
                membership_type="premium",
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=NOW,
                password_updated_at=None,
            )
        )
        session.add(
            UserRow(
                id="free-admin",
                username="free-admin",
                email="free-admin@example.test",
                hashed_password="hash",
                is_superuser=True,
                membership_type="free",
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=NOW,
                password_updated_at=None,
            )
        )

    def business_factory():
        session = raw_sessions()
        original_close = session.close

        def close() -> None:
            original_close()
            events.append(("business-close", threading.get_ident()))

        session.close = close  # type: ignore[method-assign]
        events.append(("business-open", threading.get_ident()))
        return session

    app = FastAPI()
    app.include_router(
        build_model_configs_router(
            authentication_service=service,
            session_factory=business_factory,
        )
    )
    path = "/api/models"
    cases = [
        (None, 401, "Not authenticated", True),
        ("free", 403, "需要会员才能使用此功能，请前往购买会员", False),
        ("expired-membership", 403, "会员已过期，请续费后使用", False),
        ("revoked", 401, "无法验证凭据", True),
        ("expired-access", 401, "无法验证凭据", True),
        ("invalid", 401, "无法验证凭据", True),
    ]
    try:
        for token, status, detail, bearer in cases:
            events.clear()
            headers = {} if token is None else {"Authorization": f"Bearer {token}"}
            response = request(app, "POST", path, headers=headers, json=[])
            assert response.status_code == status
            assert response.json() == {"detail": detail}
            assert (response.headers.get("www-authenticate") == "Bearer") is bearer
            assert not any(event[0] == "business-open" for event in events)
            assert any(event[0] == "auth-close" for event in events)

        events.clear()
        valid_but_invalid_body = request(
            app,
            "POST",
            path,
            headers={"Authorization": "Bearer member"},
            json=[],
        )
        assert valid_but_invalid_body.status_code == 422
        assert any(event[0] == "auth-close" for event in events)
        assert not any(event[0] == "business-open" for event in events)

        events.clear()
        valid = request(
            app,
            "GET",
            path,
            headers={"Authorization": "Bearer member"},
        )
        assert valid.status_code == 200
        assert valid.json() == []
        names = [event[0] for event in events]
        assert names.index("auth-close") < names.index("business-open")
        assert names.index("business-open") < names.index("business-close")
        auth_threads = {
            thread_id
            for name, thread_id in events
            if name in {"auth-open", "auth-close"}
        }
        business_threads = {
            thread_id
            for name, thread_id in events
            if name in {"business-open", "business-close"}
        }
        assert len(auth_threads) == 1
        assert len(business_threads) == 1

        events.clear()
        free_admin = request(
            app,
            "POST",
            "/api/models/not-found/promote-to-system",
            headers={"Authorization": "Bearer free-admin"},
        )
        assert free_admin.status_code == 404
        assert free_admin.json() == {"detail": "用户模型不存在"}
        auth_close_index = next(
            index for index, event in enumerate(events) if event[0] == "auth-close"
        )
        business_open_index = next(
            index for index, event in enumerate(events) if event[0] == "business-open"
        )
        assert auth_close_index < business_open_index
    finally:
        engine.dispose()


def test_unit_of_work_factory_defers_session_creation() -> None:
    calls: list[str] = []

    def session_factory():
        calls.append("session")
        raise AssertionError("the factory should stay inert until called")

    factory = model_configs_unit_of_work_factory(session_factory)
    assert callable(factory)
    assert calls == []
