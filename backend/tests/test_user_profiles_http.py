"""ASGI tests for authentication ordering and both user-profile routes."""

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

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)
from haoai_backend.user_profiles.http import build_user_profiles_router
from haoai_backend.user_profiles.tables import (
    characters,
    metadata as user_profiles_metadata,
    props,
    scenes,
    series,
    storyboard_assets,
    team_members,
    teams,
)


NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "user-profile-asgi-test-secret"


class ObservedSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.closed = False

    def commit(self) -> None:
        self.commit_count += 1
        self.info["events"].append(f"{self.info['kind']}-commit")
        super().commit()

    def close(self) -> None:
        self.closed = True
        self.info["events"].append(f"{self.info['kind']}-close")
        super().close()


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
            base_url="http://profiles.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def build_system(path: Path):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}", future=True)
    authentication_metadata.create_all(engine)
    user_profiles_metadata.create_all(engine)
    events: list[str] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def factory_for(kind: str, opened: list[ObservedSession]):
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def factory() -> ObservedSession:
            events.append(f"{kind}-factory")
            session = maker()
            opened.append(session)
            return session

        return factory

    auth_session_factory = factory_for("auth", auth_sessions)
    business_session_factory = factory_for("business", business_sessions)

    with Session(engine, future=True) as session:
        for user_id, email, bio, membership, expires_at in (
            ("viewer", "123456@qq.com", "before", "premium", None),
            ("target", "target@example.test", "target bio", "premium", None),
            ("stranger", "stranger@example.test", None, "premium", None),
            ("free", "free@example.test", None, "free", None),
            ("expired", "expired@example.test", None, "premium", NOW - timedelta(seconds=1)),
        ):
            session.execute(
                insert(users).values(
                    id=user_id,
                    username=user_id,
                    email=email,
                    hashed_password="unused",
                    is_superuser=False,
                    membership_type=membership,
                    membership_expires_at=expires_at,
                    avatar_url="stored-avatar",
                    bio=bio,
                    created_at=NOW - timedelta(days=1),
                    password_updated_at=NOW - timedelta(days=1),
                )
            )

        session.execute(insert(user_sessions).values([
            {
                "id": "viewer-session",
                "user_id": "viewer",
                "jti": "viewer-jti",
                "user_agent": "test",
                "ip_address": "127.0.0.1",
                "device_name": "test",
                "is_active": True,
                "created_at": NOW - timedelta(hours=1),
                "last_seen_at": NOW - timedelta(minutes=30),
                "revoked_at": None,
            },
            {
                "id": "revoked-session",
                "user_id": "viewer",
                "jti": "revoked-jti",
                "user_agent": "test",
                "ip_address": "127.0.0.1",
                "device_name": "test",
                "is_active": False,
                "created_at": NOW - timedelta(hours=1),
                "last_seen_at": NOW - timedelta(minutes=30),
                "revoked_at": NOW - timedelta(minutes=20),
            },
        ]))
        session.execute(insert(teams).values([
            {"id": "team-a", "name": "A"},
            {"id": "team-b", "name": "B"},
        ]))
        session.execute(insert(team_members).values([
            {"id": "m-viewer", "team_id": "team-a", "user_id": "viewer", "role": "owner"},
            {"id": "m-target", "team_id": "team-a", "user_id": "target", "role": "admin"},
            {"id": "m-stranger", "team_id": "team-b", "user_id": "stranger", "role": "member"},
        ]))
        session.execute(insert(series).values(
            {"id": "target-series", "user_id": "target", "name": "Target series", "team_id": "team-a"}
        ))
        session.execute(insert(characters).values(
            {"id": "target-character", "series_id": "target-series"}
        ))
        session.execute(insert(scenes).values(
            {"id": "target-scene", "series_id": "target-series"}
        ))
        session.execute(insert(props).values(
            {"id": "target-prop", "series_id": "target-series"}
        ))
        session.execute(insert(storyboard_assets).values(
            {"id": "target-frame", "series_id": "target-series"}
        ))
        session.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_session_factory,
        secret_key=SECRET,
        clock=lambda: NOW,
    )
    app = create_app(
        session_factory=business_session_factory,
        authentication_runtime=runtime,
    )

    def token_for(user_id: str, *, jti: str | None = None) -> str:
        claims: dict[str, object] = {
            "sub": user_id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
        if jti is not None:
            claims["jti"] = jti
        return jwt.encode(claims, SECRET, algorithm="HS256")

    return engine, app, events, auth_sessions, business_sessions, token_for


def test_real_member_get_closes_auth_context_before_opening_business_session(
    tmp_path: Path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "profiles-get.sqlite"
    )
    try:
        response = request(
            app,
            "GET",
            "/api/users/target/card",
            headers={"Authorization": f"Bearer {token_for('viewer', jti='viewer-jti')}"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["user"]["id"] == "target"
        assert payload["user"]["email_masked"] == "t***t@example.test"
        assert payload["teams"] == [
            {"id": "team-a", "name": "A", "role": "admin", "member_count": 2}
        ]
        assert payload["series"][0]["asset_counts"] == {
            "characters": 1,
            "scenes": 1,
            "props": 1,
            "storyboard": 1,
        }
        assert payload["asset_totals"] == {
            "characters": 1,
            "scenes": 1,
            "props": 1,
            "storyboard": 1,
        }
        assert len(auth_sessions) == len(business_sessions) == 1
        assert auth_sessions[0].commit_count == 1
        assert business_sessions[0].commit_count == 0
        assert events.index("auth-close") < events.index("business-factory")

        with Session(engine, future=True) as session:
            last_seen = session.execute(
                select(user_sessions.c.last_seen_at).where(
                    user_sessions.c.jti == "viewer-jti"
                )
            ).scalar_one()
        assert last_seen == NOW
    finally:
        engine.dispose()


def test_put_ignores_valid_avatar_and_extra_fields_but_reads_fresh_profile(
    tmp_path: Path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "profiles-put.sqlite"
    )
    try:
        response = request(
            app,
            "PUT",
            "/api/users/me/profile",
            headers={
                "Authorization": f"Bearer {token_for('viewer', jti='viewer-jti')}",
                "X-User-ID": "target",
            },
            body={
                "bio": "updated bio",
                "avatar_url": "https://attacker.example/avatar.png",
                "username": "forged username",
                "user_id": "target",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "id": "viewer",
            "username": "viewer",
            "avatar_url": "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100",
            "bio": "updated bio",
        }
        assert len(auth_sessions) == len(business_sessions) == 1
        assert auth_sessions[0].commit_count == 1
        assert business_sessions[0].commit_count == 1
        assert events.index("auth-close") < events.index("business-factory")

        with Session(engine, future=True) as session:
            row = session.execute(
                select(users.c.username, users.c.email, users.c.avatar_url, users.c.bio)
                .where(users.c.id == "viewer")
            ).one()
        assert row == ("viewer", "123456@qq.com", "stored-avatar", "updated bio")
    finally:
        engine.dispose()


def test_membership_and_revocation_failures_do_not_open_business_sessions(
    tmp_path: Path,
) -> None:
    engine, app, _events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "profiles-auth-denials.sqlite"
    )
    try:
        free = request(
            app,
            "GET",
            "/api/users/target/card",
            headers={"Authorization": f"Bearer {token_for('free')}"},
        )
        expired = request(
            app,
            "GET",
            "/api/users/target/card",
            headers={"Authorization": f"Bearer {token_for('expired')}"},
        )
        revoked = request(
            app,
            "GET",
            "/api/users/target/card",
            headers={"Authorization": f"Bearer {token_for('viewer', jti='revoked-jti')}"},
        )

        assert free.status_code == 403
        assert "需要会员" in free.json()["detail"]
        assert expired.status_code == 403
        assert "已过期" in expired.json()["detail"]
        assert revoked.status_code == 401
        assert revoked.headers["www-authenticate"] == "Bearer"
        assert business_sessions == []
        assert len(auth_sessions) == 3
        assert all(session.closed for session in auth_sessions)
    finally:
        engine.dispose()


def test_missing_card_target_and_nonmember_target_keep_legacy_errors(
    tmp_path: Path,
) -> None:
    engine, app, _events, _auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "profiles-card-errors.sqlite"
    )
    try:
        headers = {"Authorization": f"Bearer {token_for('viewer')}"}
        missing = request(app, "GET", "/api/users/missing/card", headers=headers)
        unrelated = request(app, "GET", "/api/users/stranger/card", headers=headers)

        assert (missing.status_code, missing.json()) == (404, {"detail": "用户不存在"})
        assert (unrelated.status_code, unrelated.json()) == (
            403,
            {"detail": "无权查看该用户信息"},
        )
        assert len(business_sessions) == 2
        assert all(session.closed for session in business_sessions)
    finally:
        engine.dispose()


def test_profile_schema_validates_deprecated_avatar_and_unwired_router_returns_503(
    tmp_path: Path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "profiles-validation.sqlite"
    )
    try:
        invalid = request(
            app,
            "PUT",
            "/api/users/me/profile",
            headers={"Authorization": f"Bearer {token_for('viewer')}"},
            body={"avatar_url": []},
        )
        assert invalid.status_code == 422
        assert len(auth_sessions) == 1 and auth_sessions[0].closed
        assert events.count("auth-factory") == 1
        assert events.count("auth-close") == 1
        assert business_sessions == []

        unwired_app = FastAPI()
        unwired_app.include_router(build_user_profiles_router())
        unavailable = request(
            unwired_app,
            "GET",
            "/api/users/target/card",
            headers={"Authorization": f"Bearer {token_for('viewer')}"},
        )
        assert unavailable.status_code == 503
        assert unavailable.json() == {"detail": "用户资料服务尚未接线"}
        assert len(auth_sessions) == 1 and auth_sessions[0].closed
        assert business_sessions == []
    finally:
        engine.dispose()


def test_invalid_profile_body_keeps_authentication_precedence(tmp_path: Path) -> None:
    engine, app, _events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "profiles-invalid-body-precedence.sqlite"
    )
    cases = (
        ({}, 401),
        ({"Authorization": f"Bearer {token_for('free')}"}, 403),
        ({"Authorization": f"Bearer {token_for('viewer', jti='revoked-jti')}"}, 401),
    )
    try:
        for headers, expected_status in cases:
            response = request(
                app,
                "PUT",
                "/api/users/me/profile",
                headers=headers,
                body={"avatar_url": []},
            )
            assert response.status_code == expected_status, response.text
            if expected_status == 401:
                assert response.headers["www-authenticate"] == "Bearer"
        assert business_sessions == []
        assert len(auth_sessions) == len(cases)
        assert all(session.closed for session in auth_sessions)
    finally:
        engine.dispose()


def test_create_app_registers_user_routes_without_startup_io_and_cold_app_is_unavailable(
    tmp_path: Path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for = build_system(
        tmp_path / "profiles-app-composition.sqlite"
    )
    try:
        assert len(app.routes) == 250
        assert events == []
        assert auth_sessions == []
        assert business_sessions == []

        cold_app = create_app()
        unavailable = request(
            cold_app,
            "PUT",
            "/api/users/me/profile",
            headers={"X-User-ID": "viewer"},
            body={"bio": "unavailable"},
        )
        assert unavailable.status_code == 503
        assert unavailable.json() == {"detail": "用户资料服务尚未接线"}
        assert auth_sessions == [] and business_sessions == []
    finally:
        engine.dispose()


def test_missing_credentials_keep_authentication_response_shape(tmp_path: Path) -> None:
    engine, app, _events, _auth_sessions, business_sessions, _token_for = build_system(
        tmp_path / "profiles-no-auth.sqlite"
    )
    try:
        response = request(app, "GET", "/api/users/target/card")

        assert response.status_code == 401
        assert response.json() == {"detail": "Not authenticated"}
        assert response.headers["www-authenticate"] == "Bearer"
        assert business_sessions == []
    finally:
        engine.dispose()


def test_profile_card_uses_fresh_token_expiry_with_historic_business_clock(
    tmp_path: Path, monkeypatch
) -> None:
    historic_now = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).replace(microsecond=0, tzinfo=None)
    monkeypatch.setitem(globals(), "NOW", historic_now)
    engine, app, _events, _auth_sessions, _business_sessions, token_for = build_system(
        tmp_path / "profiles-historic-business-clock.sqlite"
    )
    try:
        response = request(
            app,
            "GET",
            "/api/users/target/card",
            headers={"Authorization": f"Bearer {token_for('viewer', jti='viewer-jti')}"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["user"]["id"] == "target"
    finally:
        engine.dispose()
