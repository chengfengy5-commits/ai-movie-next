from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication import AuthenticationRuntime
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.user_preferences.tables import (
    metadata as preferences_metadata,
    users as preference_users,
)
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    users as authentication_users,
)


def make_database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'preferences-http.sqlite'}")
    preferences_metadata.create_all(engine)
    session_factory = sessionmaker(
        engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    with engine.begin() as connection:
        connection.execute(
            insert(preference_users),
            [{"id": "user-a"}, {"id": "user-b"}],
        )
    return engine, session_factory


def make_authenticated_app(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'authenticated-preferences.sqlite'}")
    authentication_metadata.create_all(engine)
    preferences_metadata.create_all(engine)
    authentication_factory = sessionmaker(
        engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            insert(authentication_users),
            [
                {
                    "id": "free-user",
                    "username": "free-user",
                    "email": "free@example.test",
                    "hashed_password": "test-only",
                    "is_superuser": False,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": now,
                    "password_updated_at": None,
                },
                {
                    "id": "expired-user",
                    "username": "expired-user",
                    "email": "expired@example.test",
                    "hashed_password": "test-only",
                    "is_superuser": False,
                    "membership_type": "premium",
                    "membership_expires_at": now - timedelta(days=1),
                    "avatar_url": None,
                    "bio": None,
                    "created_at": now,
                    "password_updated_at": None,
                },
            ],
        )

    secret = "user-preferences-http-test-secret"
    codec = JoseTokenCodec(secret)
    expiry = int(datetime.now(timezone.utc).timestamp()) + 3600
    tokens = {
        user_id: codec.encode({"sub": user_id, "exp": expiry})
        for user_id in ("free-user", "expired-user")
    }
    business_sessions: list[Session] = []

    def business_session_factory() -> Session:
        session = authentication_factory()
        business_sessions.append(session)
        return session

    runtime = AuthenticationRuntime(
        session_factory=authentication_factory,
        secret_key=secret,
        token_codec=codec,
    )
    app = create_app(
        session_factory=business_session_factory,
        authentication_runtime=runtime,
    )
    return engine, app, tokens, business_sessions


def send(
    app,
    method: str,
    path: str,
    *,
    user: str | None = "user-a",
    authorization: str | None = "Bearer valid",
    body: Any = None,
):
    async def request():
        headers = {}
        if user is not None:
            headers["x-test-user"] = user
        if authorization is not None:
            headers["authorization"] = authorization
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://preferences.test",
        ) as client:
            return await client.request(method, path, headers=headers, json=body)

    return asyncio.run(request())


def send_token(app, method: str, path: str, token: str | None, *, body: Any = None):
    authorization = f"Bearer {token}" if token is not None else None
    return send(
        app,
        method,
        path,
        user=None,
        authorization=authorization,
        body=body,
    )


def test_account_only_routes_isolate_users_and_ignore_untrusted_user_fields(
    tmp_path: Path,
) -> None:
    engine, session_factory = make_database(tmp_path)

    def resolve_account(request) -> TrustedActor:
        return TrustedActor(request.headers.get("x-test-user", ""))

    app = create_app(
        session_factory=session_factory,
        resolve_user_preferences_account=resolve_account,
    )

    try:
        assert send(app, "GET", "/api/user/preferences?user_id=user-b").json() == {}

        saved = send(
            app,
            "PUT",
            "/api/user/preferences",
            body={
                "preferences": {"quick_ref_settings": {"enabled": True}},
                "user_id": "user-b",
            },
        )
        assert saved.status_code == 200
        assert saved.json() == {
            "ok": True,
            "updated_keys": ["quick_ref_settings"],
        }
        assert send(app, "GET", "/api/user/preferences").json() == {
            "quick_ref_settings": {"enabled": True}
        }
        assert send(app, "GET", "/api/user/preferences", user="user-b").json() == {}

        methods = {
            (route.path, method)
            for route in app.routes
            if getattr(route, "path", None) == "/api/user/preferences"
            for method in getattr(route, "methods", set())
        }
        assert methods == {
            ("/api/user/preferences", "GET"),
            ("/api/user/preferences", "PUT"),
        }
    finally:
        engine.dispose()


def test_default_account_auth_accepts_free_and_expired_users_and_keeps_errors(
    tmp_path: Path,
) -> None:
    engine, app, tokens, business_sessions = make_authenticated_app(tmp_path)

    try:
        missing = send_token(app, "GET", "/api/user/preferences", None)
        assert missing.status_code == 401
        assert missing.json() == {"detail": "Not authenticated"}
        assert missing.headers["www-authenticate"] == "Bearer"
        assert business_sessions == []

        invalid_token = send_token(
            app,
            "GET",
            "/api/user/preferences",
            "malformed",
        )
        assert invalid_token.status_code == 401
        assert invalid_token.json() == {"detail": "无法验证凭据"}
        assert invalid_token.headers["www-authenticate"] == "Bearer"
        assert business_sessions == []

        invalid_body = send_token(
            app,
            "PUT",
            "/api/user/preferences",
            tokens["free-user"],
            body={"extra": True},
        )
        assert invalid_body.status_code == 422
        assert invalid_body.json() == {
            "detail": [
                {
                    "type": "missing",
                    "loc": ["body", "preferences"],
                    "msg": "Field required",
                    "input": {"extra": True},
                    "url": "https://errors.pydantic.dev/2.5/v/missing",
                }
            ]
        }
        assert business_sessions == []

        free_saved = send_token(
            app,
            "PUT",
            "/api/user/preferences",
            tokens["free-user"],
            body={"preferences": {"theme": "free"}},
        )
        expired_saved = send_token(
            app,
            "PUT",
            "/api/user/preferences",
            tokens["expired-user"],
            body={"preferences": {"theme": "expired"}},
        )
        assert free_saved.status_code == 200
        assert expired_saved.status_code == 200
        assert send_token(
            app,
            "GET",
            "/api/user/preferences",
            tokens["free-user"],
        ).json() == {"theme": "free"}
        assert send_token(
            app,
            "GET",
            "/api/user/preferences",
            tokens["expired-user"],
        ).json() == {"theme": "expired"}
        assert len(business_sessions) == 4
        assert all(not session.in_transaction() for session in business_sessions)
    finally:
        for session in business_sessions:
            session.close()
        engine.dispose()


def test_missing_business_session_fails_closed() -> None:

    def resolve_account(request) -> TrustedActor:
        return TrustedActor(request.headers.get("x-test-user", ""))

    app = create_app(resolve_user_preferences_account=resolve_account)

    response = send(app, "GET", "/api/user/preferences")
    assert response.status_code == 503
    assert response.json() == {"detail": "用户偏好设置服务尚未接线"}
