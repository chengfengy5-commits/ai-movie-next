from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users as authentication_users,
)
from haoai_backend.model_configs.connection import (
    ConnectionTester,
    UrllibConnectionTransport,
)
from haoai_backend.model_configs.domain import ConnectionConfig
from haoai_backend.model_configs.http import build_model_configs_router
from haoai_backend.model_configs.tables import (
    Base,
    ModelConfigRow,
    UserModelOrderRow,
    UserRow,
)


NOW = datetime(2026, 10, 13, 9, 30)
EXPECTED_RESPONSE_FIELDS = {
    "id",
    "user_id",
    "category",
    "name",
    "provider",
    "api_key",
    "model",
    "base_url",
    "sort_order",
    "is_system",
    "cost_per_call",
    "description",
    "params",
    "is_active",
    "created_at",
    "updated_at",
}


class FakeAuthenticationService:
    def __init__(self, *, user_id: str = "member", admin: bool = False) -> None:
        self.user_id = user_id
        self.admin = admin
        self.calls: list[tuple[str, bool]] = []

    @contextmanager
    def authenticated(self, authorization: str, *, require_membership: bool = False):
        self.calls.append((authorization, require_membership))
        yield SimpleNamespace(
            user=SimpleNamespace(id=self.user_id, is_superuser=self.admin)
        )


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class FalseyConnectionTester:
    def __bool__(self) -> bool:
        return False

    def test(self, _config: ConnectionConfig) -> dict[str, object]:
        return {"success": True, "message": "injected"}


class FalseyOpener:
    def __bool__(self) -> bool:
        return False

    def __call__(self, _request, *, timeout: int) -> FakeResponse:
        assert timeout == 15
        return FakeResponse(200)


def request(app: FastAPI, method: str, path: str, **kwargs: Any) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://models.test",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_router_construction_is_inert_and_registers_exact_nine_routes() -> None:
    opened: list[str] = []

    def session_factory():
        opened.append("session")
        raise AssertionError("router construction opened a session")

    service = FakeAuthenticationService()
    router = build_model_configs_router(
        authentication_service=service,  # type: ignore[arg-type]
        session_factory=session_factory,
        connection_tester=ConnectionTester(UrllibConnectionTransport(opener=lambda *_a, **_k: FakeResponse(200))),
    )
    app = FastAPI()
    app.include_router(router)

    routes = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }
    assert routes == {
        ("GET", "/api/models"),
        ("GET", "/api/models/{model_id}"),
        ("POST", "/api/models"),
        ("PUT", "/api/models/{model_id}"),
        ("DELETE", "/api/models/{model_id}"),
        ("POST", "/api/models/{model_id}/promote-to-system"),
        ("POST", "/api/models/{model_id}/demote-to-personal"),
        ("POST", "/api/models/reorder"),
        ("POST", "/api/models/test-connection"),
    }
    assert opened == []
    assert service.calls == []


def test_http_crud_response_shape_and_unmapped_personal_update_fields(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'model-configs-http.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False, future=True)
    with sessions.begin() as session:
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

    service = FakeAuthenticationService()
    app = FastAPI()
    app.include_router(
        build_model_configs_router(
            authentication_service=service,  # type: ignore[arg-type]
            session_factory=sessions,
        )
    )
    headers = {"Authorization": "Bearer member-token"}
    try:
        created = request(
            app,
            "POST",
            "/api/models",
            headers=headers,
            json={
                "category": "image",
                "name": "custom",
                "provider": "custom",
                "api_key": "test-api-key",
                "model": "model-a",
                "base_url": "https://provider.example.test",
                "sort_order": 900,
                "is_system": True,
            },
        )
        assert created.status_code == 200
        payload = created.json()
        assert set(payload) == EXPECTED_RESPONSE_FIELDS
        assert payload["user_id"] == "member"
        assert payload["is_system"] is False
        assert payload["sort_order"] == 1
        assert payload["cost_per_call"] == 0
        assert payload["is_active"] is True
        model_id = payload["id"]

        updated = request(
            app,
            "PUT",
            f"/api/models/{model_id}",
            headers=headers,
            json={"cost_per_call": 19, "is_active": False},
        )
        assert updated.status_code == 200
        assert updated.json()["cost_per_call"] == 19
        assert updated.json()["is_active"] is False

        fetched = request(app, "GET", f"/api/models/{model_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["cost_per_call"] == 0
        assert fetched.json()["is_active"] is True
        assert set(fetched.json()) == EXPECTED_RESPONSE_FIELDS

        listed = request(app, "GET", "/api/models?category=image", headers=headers)
        assert listed.status_code == 200
        assert [row["id"] for row in listed.json()] == [model_id]

        deleted = request(app, "DELETE", f"/api/models/{model_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json() == {"message": "模型配置已删除"}
    finally:
        engine.dispose()


def test_connection_route_has_no_auth_or_database_dependency() -> None:
    calls: list[tuple[str, str, dict[str, str], int]] = []
    opened: list[str] = []

    def opener(request, *, timeout: int):
        calls.append(
            (
                request.get_method(),
                request.full_url,
                dict(request.header_items()),
                timeout,
            )
        )
        return FakeResponse(200)

    def session_factory():
        opened.append("session")
        raise AssertionError("connection test should not create a database session")

    app = FastAPI()
    app.include_router(
        build_model_configs_router(
            authentication_service=None,
            session_factory=session_factory,
            connection_tester=ConnectionTester(
                UrllibConnectionTransport(opener=opener)
            ),
        )
    )

    success = request(
        app,
        "POST",
        "/api/models/test-connection",
        json={
            "provider": "openai",
            "api_key": "key-for-test-only",
            "base_url": "",
            "model": "gpt-test",
            "category": "text",
        },
    )
    assert success.status_code == 200
    assert success.json() == {
        "success": True,
        "message": "连接成功！API 配置正确。",
        "status_code": 200,
    }
    assert calls == [
        (
            "GET",
            "https://api.openai.com/v1/models",
            {"Authorization": "Bearer key-for-test-only"},
            15,
        )
    ]
    assert opened == []


def test_router_keeps_falsey_connection_tester_injection() -> None:
    app = FastAPI()
    app.include_router(
        build_model_configs_router(
            authentication_service=None,
            session_factory=None,
            connection_tester=FalseyConnectionTester(),  # type: ignore[arg-type]
        )
    )

    response = request(
        app,
        "POST",
        "/api/models/test-connection",
        json={"provider": "custom", "api_key": "test-key"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "injected"}


def test_urllib_transport_keeps_falsey_opener_injection() -> None:
    transport = UrllibConnectionTransport(opener=FalseyOpener())  # type: ignore[arg-type]

    assert transport.get("https://test.invalid", {}, timeout=15) == 200


class RecordingTransport:
    def __init__(self, result: int | BaseException) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, str], int]] = []

    def get(self, url: str, headers, *, timeout: int) -> int:
        self.calls.append((url, dict(headers), timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@pytest.mark.parametrize(
    ("config", "expected_url", "expected_headers"),
    [
        (
            ConnectionConfig("openai", "k", "", "", "text"),
            "https://api.openai.com/v1/models",
            {"Authorization": "Bearer k"},
        ),
        (
            ConnectionConfig("anthropic", "k", "", "", "text"),
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": "k",
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        ),
        (
            ConnectionConfig("google", "k", "", "", "text"),
            "https://generativelanguage.googleapis.com/v1/models",
            {"x-goog-api-key": "k"},
        ),
        (
            ConnectionConfig("custom", "k", "https://host.test/base/", "m", "image"),
            "https://host.test/base//models",
            {"Authorization": "Bearer k"},
        ),
    ],
)
def test_connection_paths_and_headers_keep_provider_legacy_rules(
    config: ConnectionConfig,
    expected_url: str,
    expected_headers: dict[str, str],
) -> None:
    transport = RecordingTransport(200)

    result = ConnectionTester(transport).test(config)

    assert result["success"] is True
    assert transport.calls == [(expected_url, expected_headers, 15)]


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "认证失败：API Key 无效或已过期"),
        (403, "权限不足：API Key 没有访问权限"),
        (404, "端点不存在：请检查 Base URL 是否正确 (HTTP 404)"),
        (429, "连接失败：HTTP 429"),
    ],
)
def test_connection_maps_http_errors_without_exposing_url(
    status: int,
    message: str,
) -> None:
    from urllib.error import HTTPError

    transport = RecordingTransport(
        HTTPError("https://sensitive.example.test", status, "failure", None, None)
    )

    result = ConnectionTester(transport).test(
        ConnectionConfig("custom", "secret-key", "https://sensitive.example.test", "", "text")
    )

    assert result == {"success": False, "message": message, "status_code": status}
    assert "sensitive.example.test" not in str(result)
    assert "secret-key" not in str(result)


def test_connection_maps_url_and_unexpected_errors_and_skips_empty_key() -> None:
    from urllib.error import URLError

    empty_transport = RecordingTransport(200)
    empty = ConnectionTester(empty_transport).test(
        ConnectionConfig("custom", "", "https://host.test", "", "text")
    )
    assert empty == {"success": False, "message": "API Key 不能为空"}
    assert empty_transport.calls == []

    no_base_transport = RecordingTransport(200)
    no_base = ConnectionTester(no_base_transport).test(
        ConnectionConfig("custom", "key", "", "", "image")
    )
    assert no_base == {"success": False, "message": "请配置 Base URL"}
    assert no_base_transport.calls == []

    url_error = ConnectionTester(
        RecordingTransport(URLError("offline"))
    ).test(ConnectionConfig("custom", "key", "https://host.test", "", "text"))
    assert url_error == {
        "success": False,
        "message": "无法连接到服务器：offline",
    }

    unexpected = ConnectionTester(
        RecordingTransport(RuntimeError("x" * 300))
    ).test(ConnectionConfig("custom", "key", "https://host.test", "", "text"))
    assert unexpected["success"] is False
    assert len(str(unexpected["message"]).removeprefix("测试失败：")) == 200


class IntegratedObservedSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.closed = False
        self.info["events"].append(f"{self.info['kind']}-open")

    def commit(self) -> None:
        self.commit_count += 1
        self.info["events"].append(f"{self.info['kind']}-commit")
        super().commit()

    def close(self) -> None:
        self.closed = True
        self.info["events"].append(f"{self.info['kind']}-close")
        super().close()


def build_integrated_app(
    path: Path, *, connection_tester: ConnectionTester | None = None
):
    engine = create_engine(f"sqlite:///{path}", future=True)
    authentication_metadata.create_all(engine)
    Base.metadata.create_all(engine)

    events: list[str] = []
    auth_sessions: list[IntegratedObservedSession] = []
    business_sessions: list[IntegratedObservedSession] = []

    def make_factory(kind: str, opened: list[IntegratedObservedSession]):
        maker = sessionmaker(
            engine,
            class_=IntegratedObservedSession,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def factory() -> IntegratedObservedSession:
            events.append(f"{kind}-factory")
            session = maker()
            opened.append(session)
            return session

        return factory

    auth_factory = make_factory("auth", auth_sessions)
    business_factory = make_factory("business", business_sessions)
    with Session(engine, future=True) as session:
        session.execute(
            insert(authentication_users),
            [
                {
                    "id": user_id,
                    "username": user_id,
                    "email": f"{user_id}@example.test",
                    "hashed_password": "unused",
                    "is_superuser": is_admin,
                    "membership_type": membership,
                    "membership_expires_at": expiry,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW,
                    "password_updated_at": None,
                }
                for user_id, is_admin, membership, expiry in (
                    ("admin", True, "free", None),
                    ("member", False, "premium", None),
                    ("free", False, "free", None),
                )
            ],
        )
        session.execute(
            insert(user_sessions),
            [
                {
                    "id": session_id,
                    "user_id": user_id,
                    "jti": jti,
                    "user_agent": "model-config-test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": active,
                    "created_at": NOW,
                    "last_seen_at": NOW,
                    "revoked_at": revoked_at,
                }
                for session_id, user_id, jti, active, revoked_at in (
                    ("member-session", "member", "member-jti", True, None),
                    (
                        "revoked-session",
                        "member",
                        "revoked-jti",
                        False,
                        NOW - timedelta(minutes=1),
                    ),
                    ("admin-session", "admin", "admin-jti", True, None),
                    ("free-session", "free", "free-jti", True, None),
                )
            ],
        )
        session.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key="model-config-integrated-test-secret",
        clock=lambda: NOW,
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
        model_connection_tester=connection_tester,
    )

    def token_for(user_id: str, jti: str) -> str:
        return jwt.encode(
            {"sub": user_id, "jti": jti, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "model-config-integrated-test-secret",
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token_for


def test_integrated_create_app_personal_crud_reorder_and_admin_transitions(
    tmp_path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_integrated_app(
        tmp_path / "integrated-model-configs.sqlite"
    )
    member_headers = {
        "Authorization": f"Bearer {token_for('member', 'member-jti')}"
    }
    admin_headers = {
        "Authorization": f"Bearer {token_for('admin', 'admin-jti')}"
    }
    try:
        first = request(
            app,
            "POST",
            "/api/models",
            headers=member_headers,
            json={
                "category": "image",
                "name": "first",
                "provider": "custom",
                "api_key": "fake-test-key",
                "model": "model-a",
                "base_url": "https://provider.example.test",
                "sort_order": 80,
                "is_system": True,
            },
        )
        assert first.status_code == 200, first.text
        first_value = first.json()
        first_id = first_value["id"]
        assert first_value["user_id"] == "member"
        assert first_value["is_system"] is False
        assert first_value["sort_order"] == 1
        assert len(auth_sessions) == len(business_sessions) == 1
        assert auth_sessions[0].commit_count == 1
        assert business_sessions[0].commit_count == 1
        assert all(session.closed for session in auth_sessions + business_sessions)
        assert events.index("auth-close") < events.index("business-factory")

        second = request(
            app,
            "POST",
            "/api/models",
            headers=member_headers,
            json={
                "category": "image",
                "name": "second",
                "provider": "custom",
                "api_key": "fake-test-key",
                "model": "model-b",
                "base_url": "https://provider.example.test",
            },
        )
        assert second.status_code == 200, second.text
        second_id = second.json()["id"]

        updated = request(
            app,
            "PUT",
            f"/api/models/{first_id}",
            headers=member_headers,
            json={"cost_per_call": 19, "is_active": False},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["cost_per_call"] == 19
        assert updated.json()["is_active"] is False
        assert "cost_per_call" not in ModelConfigRow.__table__.columns
        assert "is_active" not in ModelConfigRow.__table__.columns

        fetched = request(
            app,
            "GET",
            f"/api/models/{first_id}",
            headers=member_headers,
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["cost_per_call"] == 0
        assert fetched.json()["is_active"] is True
        assert set(fetched.json()) == EXPECTED_RESPONSE_FIELDS

        reordered = request(
            app,
            "POST",
            "/api/models/reorder",
            headers=member_headers,
            json={"category": "image", "model_ids": [first_id, "missing-id", first_id]},
        )
        assert reordered.status_code == 200, reordered.text
        with Session(engine, future=True) as session:
            order_rows = (
                session.query(UserModelOrderRow)
                .filter(UserModelOrderRow.user_id == "member")
                .order_by(UserModelOrderRow.sort_order)
                .all()
            )
        assert [(row.model_id, row.sort_order) for row in order_rows] == [
            (first_id, 0),
            ("missing-id", 1),
            (first_id, 2),
        ]
        listed = request(
            app,
            "GET",
            "/api/models?category=image",
            headers=member_headers,
        )
        assert listed.status_code == 200, listed.text
        assert [row["id"] for row in listed.json()] == [first_id, first_id, second_id]

        promoted = request(
            app,
            "POST",
            f"/api/models/{first_id}/promote-to-system",
            headers=admin_headers,
        )
        assert promoted.status_code == 200, promoted.text
        system_id = promoted.json()["id"]
        assert promoted.json()["is_system"] is True
        assert promoted.json()["user_id"] == "system"
        with Session(engine, future=True) as session:
            remaining_refs = (
                session.query(UserModelOrderRow)
                .filter(UserModelOrderRow.model_id == first_id)
                .count()
            )
        assert remaining_refs == 0

        demoted = request(
            app,
            "POST",
            f"/api/models/{system_id}/demote-to-personal",
            headers=admin_headers,
        )
        assert demoted.status_code == 200, demoted.text
        assert demoted.json()["is_system"] is False
        assert demoted.json()["user_id"] == "admin"

        deleted = request(
            app,
            "DELETE",
            f"/api/models/{second_id}",
            headers=member_headers,
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"message": "模型配置已删除"}
        assert all(session.closed for session in auth_sessions + business_sessions)
    finally:
        engine.dispose()


def test_integrated_public_connection_route_uses_fake_transport_without_sessions(
    tmp_path,
) -> None:
    calls: list[tuple[str, str, dict[str, str], int]] = []

    def opener(request, *, timeout: int):
        calls.append(
            (
                request.get_method(),
                request.full_url,
                dict(request.header_items()),
                timeout,
            )
        )
        return FakeResponse(200)

    tester = ConnectionTester(UrllibConnectionTransport(opener=opener))
    engine, app, _events, auth_sessions, business_sessions, _token_for = build_integrated_app(
        tmp_path / "integrated-model-connection.sqlite",
        connection_tester=tester,
    )
    try:
        response = request(
            app,
            "POST",
            "/api/models/test-connection",
            json={
                "provider": "openai",
                "api_key": "fake-test-key",
                "model": "model-a",
                "category": "text",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "success": True,
            "message": "连接成功！API 配置正确。",
            "status_code": 200,
        }
        assert calls == [
            (
                "GET",
                "https://api.openai.com/v1/models",
                {"Authorization": "Bearer fake-test-key"},
                15,
            )
        ]
        assert auth_sessions == []
        assert business_sessions == []
    finally:
        engine.dispose()


def test_integrated_auth_denials_precede_model_body_validation_and_business_sessions(
    tmp_path,
) -> None:
    engine, app, _events, auth_sessions, business_sessions, token_for = build_integrated_app(
        tmp_path / "integrated-model-auth.sqlite"
    )
    try:
        cases = (
            (None, 401),
            ({"Authorization": f"Bearer {token_for('free', 'free-jti')}"}, 403),
            (
                {"Authorization": f"Bearer {token_for('member', 'revoked-jti')}"},
                401,
            ),
            (
                {"Authorization": f"Bearer {token_for('member', 'member-jti')}"},
                422,
            ),
        )
        for headers, expected_status in cases:
            auth_sessions.clear()
            business_sessions.clear()
            response = request(
                app,
                "POST",
                "/api/models",
                headers=headers,
                json=[],
            )
            assert response.status_code == expected_status, response.text
            assert len(auth_sessions) == 1
            assert auth_sessions[0].closed
            assert business_sessions == []
            if expected_status == 401:
                assert response.headers["www-authenticate"] == "Bearer"
    finally:
        engine.dispose()
