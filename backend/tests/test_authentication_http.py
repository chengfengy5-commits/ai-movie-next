"""Authentication HTTP boundary tests, including local validation behavior."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.rate_limit import InMemoryFixedWindowLimiter
from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import users, user_sessions
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from haoai_backend.personal_production.rough_cut.tables import (
    chapters,
    metadata as rough_cut_metadata,
    storyboard_assets,
)
from haoai_backend.series_access.rough_cut_policy import require_rough_cut_series_access
from haoai_backend.series_access.tables import metadata as series_access_metadata
from haoai_backend.series_access.tables import series


class CountingLimiter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def check(self, **kwargs: Any) -> int | None:
        self.calls.append(kwargs)
        return None


def request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
    raise_app_exceptions: bool = False,
    client_address: tuple[str, int] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport_options: dict[str, Any] = {
            "app": app,
            "raise_app_exceptions": raise_app_exceptions,
        }
        if client_address is not None:
            transport_options["client"] = client_address
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(**transport_options),
            base_url="http://auth.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def test_missing_runtime_is_a_503_before_limited_route_session_creation() -> None:
    app = create_app()

    response = request(
        app,
        "POST",
        "/api/auth/register",
        body={"username": "user", "email": "user@example.test", "password": "Pass1234"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务尚未接线"}


def test_invalid_auth_body_does_not_consume_rate_limit() -> None:
    limiter = CountingLimiter()
    runtime = AuthenticationRuntime(secret_key="local-test-key", rate_limiter=limiter)
    app = create_app(authentication_runtime=runtime)

    response = request(
        app,
        "POST",
        "/api/auth/register",
        body={"username": "user", "email": "user@example.test", "password": "short"},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert "密码长度不能少于8位" in response.json()["detail"]
    assert limiter.calls == []


def test_auth_validation_handler_is_local_to_authentication_routes() -> None:
    app = create_app()

    auth_response = request(
        app,
        "POST",
        "/api/auth/register",
        body={"username": "user", "email": "user@example.test", "password": "short"},
    )

    assert auth_response.status_code == 422
    assert isinstance(auth_response.json()["detail"], str)
    assert not any(
        getattr(handler, "__name__", "") == "auth_validation_handler"
        for handler in app.exception_handlers.values()
    )


class RejectingTokenCodec:
    def encode(self, claims: dict[str, Any]) -> str:
        raise AssertionError("the HTTP error test does not encode tokens")

    def decode(self, token: str) -> dict[str, Any]:
        from haoai_backend.authentication.tokens import TokenDecodeError

        raise TokenDecodeError()


class ClosableSession:
    def __init__(self) -> None:
        self.closed = False

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_missing_non_bearer_and_invalid_bearer_keep_distinct_legacy_responses() -> None:
    opened: list[ClosableSession] = []

    def session_factory() -> ClosableSession:
        session = ClosableSession()
        opened.append(session)
        return session

    runtime = AuthenticationRuntime(
        secret_key="explicit-test-key",
        session_factory=session_factory,
        token_codec=RejectingTokenCodec(),
    )
    app = create_app(authentication_runtime=runtime)

    missing = request(app, "GET", "/api/auth/me")
    non_bearer = request(app, "GET", "/api/auth/me", headers={"Authorization": "Basic abc"})
    empty_bearer = request(app, "GET", "/api/auth/me", headers={"Authorization": "Bearer "})
    undecodable = request(
        app, "GET", "/api/auth/me", headers={"Authorization": "Bearer malformed"}
    )

    assert (missing.status_code, missing.json()) == (401, {"detail": "Not authenticated"})
    assert missing.headers["www-authenticate"] == "Bearer"
    assert (non_bearer.status_code, non_bearer.json()) == (401, {"detail": "Not authenticated"})
    assert non_bearer.headers["www-authenticate"] == "Bearer"
    assert (empty_bearer.status_code, empty_bearer.json()) == (
        401, {"detail": "无法验证凭据"}
    )
    assert empty_bearer.headers["www-authenticate"] == "Bearer"
    assert (undecodable.status_code, undecodable.json()) == (
        401, {"detail": "无法验证凭据"}
    )
    assert undecodable.headers["www-authenticate"] == "Bearer"
    assert len(opened) == 4 and all(session.closed for session in opened)


def test_existing_rough_cut_validation_remains_the_framework_default() -> None:
    from haoai_backend.shared.identity import TrustedActor

    app = create_app(
        session_factory=ClosableSession,
        resolve_actor=lambda _request: TrustedActor("actor-1"),
        series_access_policy=lambda *_args: None,
    )
    response = request(
        app,
        "PUT",
        "/api/chapters/chapter-1/rough-cut",
        body={"expected_revision": 0, "frames": [{"asset_id": "frame-1", "included": "yes"}]},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_login_and_reset_without_secret_fail_before_database_access() -> None:
    opened: list[ClosableSession] = []

    def session_factory() -> ClosableSession:
        session = ClosableSession()
        opened.append(session)
        return session

    runtime = AuthenticationRuntime(
        session_factory=session_factory,
        password_reset_sender=lambda *_args: True,
        frontend_url="https://frontend.test",
    )
    app = create_app(authentication_runtime=runtime)

    login = request(
        app,
        "POST",
        "/api/auth/login",
        body={"username": "alice", "password": "Password123"},
    )
    reset = request(
        app,
        "POST",
        "/api/auth/forgot-password",
        body={"username": "alice", "email": "alice@example.test"},
    )

    assert login.status_code == 503 and login.json() == {"detail": "认证服务尚未接线"}
    assert reset.status_code == 503 and reset.json() == {"detail": "认证服务尚未接线"}
    assert opened == []


class BlockingLimiter(CountingLimiter):
    def check(self, **kwargs: Any) -> int | None:
        self.calls.append(kwargs)
        return 13


def test_only_three_paths_use_rate_limit_and_limited_response_keeps_error_shape() -> None:
    limiter = CountingLimiter()
    app = create_app(authentication_runtime=AuthenticationRuntime(rate_limiter=limiter))
    unthrottled = [
        ("GET", "/api/auth/me", None),
        ("GET", "/api/auth/sessions", None),
        ("DELETE", "/api/auth/sessions/session-1", None),
        ("PUT", "/api/auth/password", {"old_password": "old", "new_password": "new123"}),
        ("POST", "/api/auth/reset-password", {"token": "token", "new_password": "Password123"}),
        ("GET", "/api/auth/credits/me", None),
        ("POST", "/api/auth/send-code", {"email": "a@example.test"}),
        ("POST", "/api/auth/verify-code", {"email": "a@example.test", "code": "123456"}),
    ]
    for method, path, body in unthrottled:
        request(app, method, path, body=body)
    assert limiter.calls == []

    blocking = BlockingLimiter()
    limited_app = create_app(authentication_runtime=AuthenticationRuntime(rate_limiter=blocking))
    response = request(
        limited_app,
        "POST",
        "/api/auth/login",
        body={"username": "alice", "password": "Password123"},
    )
    assert response.status_code == 429
    assert response.json() == {"error": "Rate limit exceeded: 10 per 1 minute"}
    assert "retry-after" not in response.headers
    assert len(blocking.calls) == 1
    assert blocking.calls[0]["key"] == "127.0.0.1:/api/auth/login"
    assert blocking.calls[0]["limit"] == 10
    assert blocking.calls[0]["window_seconds"] == 60


def _build_combined_database(path: Path):
    engine = create_engine(f"sqlite:///{path}", future=True)
    authentication_metadata.create_all(engine)
    series_access_metadata.create_all(engine)
    rough_cut_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    return engine


class _ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.closed = False

    def commit(self) -> None:
        self.commit_count += 1
        super().commit()

    def close(self) -> None:
        self.closed = True
        super().close()


def _observed_factory(engine):
    opened: list[_ObservedSession] = []
    maker = sessionmaker(engine, class_=_ObservedSession, expire_on_commit=False, future=True)

    def factory() -> _ObservedSession:
        session = maker()
        opened.append(session)
        return session

    return factory, opened


def test_real_jwt_member_actor_reaches_all_four_business_methods_in_separate_sessions(
    tmp_path: Path,
) -> None:
    from jose import jwt

    engine = _build_combined_database(tmp_path / "auth-business-asgi.sqlite")
    now = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
    with Session(engine, future=True) as session:
        for user_id, username, membership_type in (
            ("owner-user", "owner", "premium"),
            ("free-user", "free-user", "free"),
        ):
            session.execute(insert(users).values(
                id=user_id,
                username=username,
                email=f"{username}@example.test",
                hashed_password="not-used-by-bearer-test",
                is_superuser=False,
                membership_type=membership_type,
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=now,
                password_updated_at=None,
            ))
        session.execute(insert(series).values(
            id="series-1", user_id="owner-user", team_id=None, claimed_by=None
        ))
        session.execute(insert(chapters).values(
            id="chapter-1",
            series_id="series-1",
            title="chapter",
            content='[{"storyboard":["asset-1"],"text":"frame","preview":"clip.mp4"}]',
        ))
        session.execute(insert(storyboard_assets).values(
            id="asset-1",
            series_id="series-1",
            chapter_id="chapter-1",
            frame_index=0,
            image_url="image.png",
        ))
        session.commit()

    auth_factory, auth_sessions = _observed_factory(engine)
    business_factory, business_sessions = _observed_factory(engine)
    secret = "only-for-this-asgi-test"
    generated_ids = iter((f"generated-{index}" for index in range(1, 20)))
    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=secret,
        clock=lambda: now,
        epoch=lambda: now.timestamp(),
        id_factory=lambda: next(generated_ids),
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
        series_access_policy=require_rough_cut_series_access,
    )

    def token_for(user_id: str, jti: str) -> str:
        return jwt.encode(
            {
                "sub": user_id,
                "password_version": str(int(now.timestamp())),
                "jti": jti,
                "exp": now + timedelta(minutes=5),
            },
            secret,
            algorithm="HS256",
        )

    denied = request(
        app,
        "GET",
        "/api/chapters/chapter-1/rough-cut",
        headers={"Authorization": f"Bearer {token_for('free-user', 'free-jti')}"},
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "需要会员才能使用此功能，请前往购买会员"}
    assert len(auth_sessions) == 1 and auth_sessions[0].commit_count == 2
    assert business_sessions == []

    owner_token = token_for("owner-user", "owner-jti")
    rough_read = request(
        app,
        "GET",
        "/api/chapters/chapter-1/rough-cut",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    rough_write = request(
        app,
        "PUT",
        "/api/chapters/chapter-1/rough-cut",
        headers={"Authorization": f"Bearer {owner_token}"},
        body={"expected_revision": 0, "frames": [{"asset_id": "asset-1", "included": False}]},
    )
    notes_read = request(
        app,
        "GET",
        "/api/chapters/chapter-1/personal-production-notes",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    notes_write = request(
        app,
        "PUT",
        "/api/chapters/chapter-1/personal-production-notes",
        headers={"Authorization": f"Bearer {owner_token}"},
        body={
            "expected_revision": 0,
            "frames": [{
                "storyboard_asset_id": "asset-1",
                "expected_media_revision": 1,
                "status": "needs_revision",
                "note": "review",
            }],
        },
    )

    responses = (rough_read, rough_write, notes_read, notes_write)
    assert [response.status_code for response in responses] == [200, 200, 200, 200], [
        response.text for response in responses
    ]
    assert rough_read.json()["revision"] == 0 and rough_read.json()["saved"] is False
    assert rough_write.json()["revision"] == 1 and rough_write.json()["frames"][0]["included"] is False
    assert notes_read.json()["revision"] == 0
    assert notes_write.json()["revision"] == 1
    assert len(auth_sessions) == 5
    assert [session.commit_count for session in auth_sessions[1:]] == [2, 1, 1, 1]
    assert len(business_sessions) == 4
    assert [session.commit_count for session in business_sessions] == [0, 1, 1, 1]
    assert all(session.closed for session in auth_sessions + business_sessions)

    with Session(engine, future=True) as session:
        assert session.execute(select(user_sessions.c.jti)).scalars().all() == [
            "free-jti", "owner-jti"
        ]
    engine.dispose()


def test_explicit_actor_resolver_keeps_priority_over_authentication_runtime(tmp_path: Path) -> None:
    from haoai_backend.shared.identity import TrustedActor

    engine = _build_combined_database(tmp_path / "explicit-actor.sqlite")
    now = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
    with Session(engine, future=True) as session:
        session.execute(insert(chapters).values(
            id="chapter-1", series_id="series-1", title="chapter", content="[]"
        ))
        session.commit()

    auth_calls = 0

    def forbidden_auth_factory():
        nonlocal auth_calls
        auth_calls += 1
        raise AssertionError("explicit actor should take precedence")

    business_factory, opened_business = _observed_factory(engine)
    runtime = AuthenticationRuntime(
        session_factory=forbidden_auth_factory,
        secret_key="unused-test-secret",
        clock=lambda: now,
    )
    app = create_app(
        session_factory=business_factory,
        resolve_actor=lambda _request: TrustedActor("explicit-user"),
        series_access_policy=lambda _session, _actor, _series_id: None,
        authentication_runtime=runtime,
    )

    response = request(app, "GET", "/api/chapters/chapter-1/rough-cut")

    assert response.status_code == 200
    assert response.json()["chapter_id"] == "chapter-1"
    assert auth_calls == 0
    assert len(opened_business) == 1 and opened_business[0].closed
    engine.dispose()



class _TestPasswordHasher:
    def hash(self, password: str) -> str:
        return f"test-hash:{password}"

    def verify(self, password: str, encoded: str) -> bool:
        return encoded == self.hash(password)


def test_real_fixed_windows_are_scoped_by_route_and_asgi_client_host(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'auth-fixed-windows.sqlite'}", future=True)
    authentication_metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, future=True)
    now = datetime(2026, 2, 3, 4, 5, 6)
    epoch = [1000.0]
    generated_id = 0

    def next_id() -> str:
        nonlocal generated_id
        generated_id += 1
        return f"rate-user-{generated_id}"

    limiter = InMemoryFixedWindowLimiter()
    runtime = AuthenticationRuntime(
        session_factory=factory,
        secret_key="fixed-window-test-secret",
        password_hasher=_TestPasswordHasher(),
        rate_limiter=limiter,
        clock=lambda: now,
        epoch=lambda: epoch[0],
        code_generator=lambda: "123456",
        id_factory=next_id,
        verification_email_sender=lambda _email, _code: True,
        password_reset_sender=lambda *_args: True,
        frontend_url="https://frontend.test",
    )
    app = create_app(authentication_runtime=runtime)
    try:
        unthrottled = (
            ("GET", "/api/auth/me", None),
            ("GET", "/api/auth/sessions", None),
            ("DELETE", "/api/auth/sessions/session-missing", None),
            ("PUT", "/api/auth/password", {
                "old_password": "OldPassword1", "new_password": "NewPassword1"
            }),
            ("POST", "/api/auth/reset-password", {
                "token": "invalid-token", "new_password": "NewPassword1"
            }),
            ("GET", "/api/auth/credits/me", None),
            ("POST", "/api/auth/send-code", {"email": "quota@example.test"}),
            ("POST", "/api/auth/verify-code", {
                "email": "quota@example.test", "code": "000000"
            }),
        )
        for method, path, body in unthrottled:
            response = request(app, method, path, body=body)
            assert response.status_code != 429, (method, path, response.text)
        assert limiter._windows == {}

        invalid_body = request(app, "POST", "/api/auth/login", body={"username": "missing"})
        assert invalid_body.status_code == 422
        assert limiter._windows == {}

        for index in range(10):
            response = request(
                app,
                "POST",
                "/api/auth/login",
                body={"username": "missing", "password": "Password123"},
                headers={"X-Forwarded-For": f"198.51.100.{index + 1}"},
            )
            assert response.status_code == 401
        login_limited = request(
            app,
            "POST",
            "/api/auth/login",
            body={"username": "missing", "password": "Password123"},
            headers={"X-Forwarded-For": "203.0.113.99"},
        )
        assert login_limited.status_code == 429
        assert login_limited.json() == {"error": "Rate limit exceeded: 10 per 1 minute"}
        assert "retry-after" not in login_limited.headers

        second_client = request(
            app,
            "POST",
            "/api/auth/login",
            body={"username": "missing", "password": "Password123"},
            headers={"X-Forwarded-For": "127.0.0.1"},
            client_address=("198.51.100.77", 32000),
        )
        assert second_client.status_code == 401

        for index in range(5):
            response = request(
                app,
                "POST",
                "/api/auth/register",
                body={
                    "username": f"quota-user-{index}",
                    "email": f"quota-user-{index}@example.test",
                    "password": "Password123",
                },
            )
            assert response.status_code == 201, response.text
        registration_limited = request(
            app,
            "POST",
            "/api/auth/register",
            body={
                "username": "quota-user-over",
                "email": "quota-user-over@example.test",
                "password": "Password123",
            },
        )
        assert registration_limited.status_code == 429
        assert registration_limited.json() == {
            "error": "Rate limit exceeded: 5 per 1 hour"
        }
        assert "retry-after" not in registration_limited.headers

        for _ in range(3):
            response = request(
                app,
                "POST",
                "/api/auth/forgot-password",
                body={"username": "unknown", "email": "unknown@example.test"},
            )
            assert response.status_code == 200
        forgot_limited = request(
            app,
            "POST",
            "/api/auth/forgot-password",
            body={"username": "unknown", "email": "unknown@example.test"},
        )
        assert forgot_limited.status_code == 429
        assert forgot_limited.json() == {
            "error": "Rate limit exceeded: 3 per 1 hour"
        }
        assert "retry-after" not in forgot_limited.headers

        assert limiter._windows == {
            "127.0.0.1:/api/auth/login": (1000.0, 10),
            "198.51.100.77:/api/auth/login": (1000.0, 1),
            "127.0.0.1:/api/auth/register": (1000.0, 5),
            "127.0.0.1:/api/auth/forgot-password": (1000.0, 3),
        }

        epoch[0] = 1060.0
        reset_login_window = request(
            app,
            "POST",
            "/api/auth/login",
            body={"username": "missing", "password": "Password123"},
        )
        assert reset_login_window.status_code == 401
        assert limiter._windows["127.0.0.1:/api/auth/login"] == (1060.0, 1)

        epoch[0] = 4660.0
        reset_registration_window = request(
            app,
            "POST",
            "/api/auth/register",
            body={
                "username": "quota-reset",
                "email": "quota-reset@example.test",
                "password": "Password123",
            },
        )
        reset_forgot_window = request(
            app,
            "POST",
            "/api/auth/forgot-password",
            body={"username": "unknown", "email": "unknown@example.test"},
        )
        assert reset_registration_window.status_code == 201
        assert reset_forgot_window.status_code == 200
        assert limiter._windows["127.0.0.1:/api/auth/register"] == (4660.0, 1)
        assert limiter._windows["127.0.0.1:/api/auth/forgot-password"] == (4660.0, 1)
    finally:
        engine.dispose()
