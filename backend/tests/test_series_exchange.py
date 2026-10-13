"""Local ASGI and SQLite contract tests for the series-exchange leaf."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import event, insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.asset_data.naming import dump_aliases, make_key
from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users as authentication_users,
)
from haoai_backend.series_data.tables import (
    chapters,
    characters,
    metadata as series_metadata,
    props,
    scenes,
    series,
    storyboard_assets,
)
from haoai_backend.series_exchange.http import build_series_exchange_router
from haoai_backend.series_exchange.persistence import series_exchange_uow_factory
from haoai_backend.series_exchange.tables import (
    fused_assets,
    metadata as exchange_metadata,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.authentication import resolve_active_actor
from haoai_backend.teams.tables import (
    metadata as teams_metadata,
    team_members as team_members_table,
    teams as teams_table,
)


NOW = datetime(2026, 1, 1, 12, 0, 0)
SECRET = "series-exchange-local-test-secret"


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        self.info["events"].append(
            (f"{self.info['kind']}-commit", threading.get_ident())
        )
        super().commit()

    def rollback(self) -> None:
        self.rollback_count += 1
        self.info["events"].append(
            (f"{self.info['kind']}-rollback", threading.get_ident())
        )
        super().rollback()

    def close(self) -> None:
        self.close_count += 1
        self.info["events"].append(
            (f"{self.info['kind']}-close", threading.get_ident())
        )
        super().close()


class SeriesExchangeSystem:
    def __init__(
        self,
        engine: Any,
        app: FastAPI,
        events: list[tuple[str, int]],
        auth_sessions: list[ObservedSession],
        business_sessions: list[ObservedSession],
        token_for: Callable[..., str],
    ) -> None:
        self.engine = engine
        self.app = app
        self.events = events
        self.auth_sessions = auth_sessions
        self.business_sessions = business_sessions
        self.token_for = token_for


def _http_request(
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
            base_url="http://series-exchange.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def _build_system(
    path: Path,
    *,
    id_factory: Callable[[], str] | None = None,
) -> SeriesExchangeSystem:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}", future=True)
    authentication_metadata.create_all(engine)
    teams_metadata.create_all(
        engine,
        tables=[teams_table, team_members_table],
    )
    series_metadata.create_all(
        engine,
        tables=[
            series,
            chapters,
            characters,
            scenes,
            props,
            storyboard_assets,
        ],
    )
    exchange_metadata.create_all(engine)

    events: list[tuple[str, int]] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def session_factory_for(
        kind: str,
        opened: list[ObservedSession],
    ) -> Callable[[], ObservedSession]:
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def create() -> ObservedSession:
            events.append((f"{kind}-factory", threading.get_ident()))
            session = maker()
            opened.append(session)
            return session

        return create

    auth_session_factory = session_factory_for("auth", auth_sessions)
    business_session_factory = session_factory_for("business", business_sessions)

    seeded_users = (
        ("owner", "premium", None),
        ("member", "premium", None),
        ("captain", "premium", None),
        ("claim-capable", "premium", None),
        ("outsider", "premium", None),
        ("free-user", "free", None),
        ("expired-user", "premium", NOW - timedelta(seconds=1)),
    )
    with Session(engine, future=True) as session:
        for user_id, membership_type, membership_expires_at in seeded_users:
            session.execute(
                insert(authentication_users).values(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.test",
                    hashed_password="unused",
                    is_superuser=False,
                    membership_type=membership_type,
                    membership_expires_at=membership_expires_at,
                    avatar_url=None,
                    bio=None,
                    created_at=NOW - timedelta(days=1),
                    password_updated_at=NOW - timedelta(days=1),
                )
            )

        session.execute(
            insert(user_sessions),
            [
                {
                    "id": f"session-{user_id}",
                    "user_id": user_id,
                    "jti": f"jti-{user_id}",
                    "user_agent": "local-test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": True,
                    "created_at": NOW - timedelta(hours=1),
                    "last_seen_at": NOW - timedelta(minutes=30),
                    "revoked_at": None,
                }
                for user_id, _, _ in seeded_users
            ]
            + [
                {
                    "id": "session-owner-revoked",
                    "user_id": "owner",
                    "jti": "jti-owner-revoked",
                    "user_agent": "local-test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": False,
                    "created_at": NOW - timedelta(hours=1),
                    "last_seen_at": NOW - timedelta(minutes=30),
                    "revoked_at": NOW - timedelta(minutes=20),
                }
            ],
        )
        session.execute(
            insert(teams_table).values(
                id="team-a",
                name="Team A",
                owner_id="owner",
                created_at=NOW - timedelta(days=1),
            )
        )
        session.execute(
            insert(team_members_table),
            [
                {
                    "id": "membership-owner",
                    "team_id": "team-a",
                    "user_id": "owner",
                    "role": "owner",
                    "permissions": None,
                    "joined_at": NOW - timedelta(days=1),
                },
                {
                    "id": "membership-member",
                    "team_id": "team-a",
                    "user_id": "member",
                    "role": "member",
                    "permissions": None,
                    "joined_at": NOW - timedelta(days=1),
                },
                {
                    "id": "membership-captain",
                    "team_id": "team-a",
                    "user_id": "captain",
                    "role": "owner",
                    "permissions": None,
                    "joined_at": NOW - timedelta(days=1),
                },
                {
                    "id": "membership-claim",
                    "team_id": "team-a",
                    "user_id": "claim-capable",
                    "role": "member",
                    "permissions": '["enter_claimed_series"]',
                    "joined_at": NOW - timedelta(days=1),
                },
            ],
        )
        session.execute(
            insert(series),
            [
                {
                    "id": "series-private",
                    "user_id": "owner",
                    "name": "Private",
                    "description": "private series",
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": None,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": NOW - timedelta(days=2),
                    "updated_at": NOW - timedelta(days=2),
                },
                {
                    "id": "series-team",
                    "user_id": "owner",
                    "name": "Team",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": "team-a",
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": NOW - timedelta(days=2),
                    "updated_at": NOW - timedelta(days=2),
                },
                {
                    "id": "series-claimed",
                    "user_id": "owner",
                    "name": "Claimed",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": "team-a",
                    "claimed_by": "owner",
                    "claimed_at": NOW - timedelta(days=1),
                    "created_at": NOW - timedelta(days=2),
                    "updated_at": NOW - timedelta(days=2),
                },
            ],
        )
        session.execute(
            insert(chapters).values(
                id="chapter-private",
                series_id="series-private",
                title="Existing chapter",
                content=json.dumps([{"text": "keep", "fused": ["fused-old"]}]),
                order=1,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW - timedelta(hours=1),
            )
        )
        session.execute(
            insert(storyboard_assets).values(
                id="storyboard-existing",
                series_id="series-private",
                chapter_id="chapter-private",
                frame_index=0,
                name="Existing board",
                description="keep",
                image_url="https://cdn.example.test/existing.png",
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW - timedelta(hours=1),
            )
        )
        session.execute(
            insert(fused_assets),
            [
                {
                    "id": "fused-old",
                    "series_id": "series-private",
                    "member_type": "prop",
                    "members": '[{"kind":"asset","id":"prop-old","name":"杯"}]',
                    "name": "Old fused",
                    "image_url": "https://cdn.example.test/old.png",
                    "size": "small",
                    "status": "completed",
                    "error_message": None,
                    "created_at": NOW - timedelta(days=1),
                    "updated_at": NOW - timedelta(days=1),
                },
                {
                    "id": "fused-new",
                    "series_id": "series-private",
                    "member_type": "character",
                    "members": "{invalid-json",
                    "name": "New fused",
                    "image_url": None,
                    "size": None,
                    "status": "failed",
                    "error_message": "provider error retained as source data",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "fused-team",
                    "series_id": "series-team",
                    "member_type": "prop",
                    "members": "[]",
                    "name": "Team fused",
                    "image_url": None,
                    "size": None,
                    "status": "completed",
                    "error_message": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "fused-claimed",
                    "series_id": "series-claimed",
                    "member_type": "prop",
                    "members": "[]",
                    "name": "Claimed fused",
                    "image_url": None,
                    "size": None,
                    "status": "completed",
                    "error_message": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        session.commit()

    auth_runtime = AuthenticationRuntime(
        session_factory=auth_session_factory,
        secret_key=SECRET,
        clock=lambda: NOW,
    )
    authentication = AuthenticationService(
        auth_runtime,
        lambda: SqlAlchemyAuthenticationUnitOfWork(auth_session_factory()),
    )

    def resolve_actor(request: Any) -> TrustedActor:
        events.append(("resolver-enter", threading.get_ident()))
        return resolve_active_actor(
            authentication,
            request.headers.get("authorization", ""),
        )

    uow_factory = series_exchange_uow_factory(
        business_session_factory,
        now=lambda: NOW,
        new_id=id_factory,
    )
    app = FastAPI()
    app.include_router(
        build_series_exchange_router(
            uow_factory=uow_factory,
            resolve_actor=resolve_actor,
        )
    )

    def token_for(
        user_id: str,
        *,
        jti: str | None = None,
        expires_at: datetime | None = None,
    ) -> str:
        claims: dict[str, Any] = {
            "sub": user_id,
            "exp": expires_at or datetime.now(timezone.utc) + timedelta(minutes=5),
            "jti": jti or f"jti-{user_id}",
        }
        return jwt.encode(claims, SECRET, algorithm="HS256")

    return SeriesExchangeSystem(
        engine,
        app,
        events,
        auth_sessions,
        business_sessions,
        token_for,
    )


def _auth_header(
    system: SeriesExchangeSystem,
    user_id: str = "owner",
) -> dict[str, str]:
    return {"Authorization": f"Bearer {system.token_for(user_id)}"}


def test_authentication_precedes_invalid_body_and_preserves_bearer_errors(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path / "series-exchange-auth.sqlite")
    try:
        missing = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            body={"data": []},
        )
        assert missing.status_code == 401
        assert missing.json() == {"detail": "Not authenticated"}
        assert missing.headers["www-authenticate"] == "Bearer"

        free = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system, "free-user"),
            body={"data": []},
        )
        assert free.status_code == 403
        assert free.json() == {
            "detail": "需要会员才能使用此功能，请前往购买会员"
        }

        expired = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers={
                "Authorization": (
                    "Bearer "
                    + system.token_for(
                        "expired-user",
                        expires_at=(
                            datetime.now(timezone.utc) + timedelta(minutes=5)
                        ),
                    )
                )
            },
            body={"data": []},
        )
        assert expired.status_code == 403

        expired_token = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers={
                "Authorization": (
                    "Bearer "
                    + system.token_for(
                        "owner",
                        expires_at=(
                            datetime.now(timezone.utc) - timedelta(minutes=5)
                        ),
                    )
                )
            },
            body={"data": []},
        )
        assert expired_token.status_code == 401
        assert expired_token.headers["www-authenticate"] == "Bearer"

        revoked = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers={
                "Authorization": (
                    f"Bearer {system.token_for('owner', jti='jti-owner-revoked')}"
                )
            },
            body={"data": []},
        )
        assert revoked.status_code == 401
        assert revoked.headers["www-authenticate"] == "Bearer"

        valid_identity_invalid_body = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system),
            body={"data": []},
        )
        assert valid_identity_invalid_body.status_code == 422

        assert len(system.auth_sessions) == 6
        assert all(session.close_count == 1 for session in system.auth_sessions)
        assert system.business_sessions == []
    finally:
        system.engine.dispose()


def test_auth_context_closes_before_worker_thread_business_session(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path / "series-exchange-thread.sqlite")
    event_loop_thread = threading.get_ident()
    try:
        response = _http_request(
            system.app,
            "GET",
            "/api/series/series-private/fused-assets",
            headers=_auth_header(system),
        )

        assert response.status_code == 200
        assert len(system.auth_sessions) == len(system.business_sessions) == 1
        assert system.auth_sessions[0].close_count == 1
        assert system.business_sessions[0].close_count == 1
        auth_closed = next(
            index
            for index, (kind, _) in enumerate(system.events)
            if kind == "auth-close"
        )
        business_opened = next(
            index
            for index, (kind, _) in enumerate(system.events)
            if kind == "business-factory"
        )
        assert auth_closed < business_opened

        resolver_threads = [
            thread_id
            for kind, thread_id in system.events
            if kind == "resolver-enter"
        ]
        business_threads = [
            thread_id
            for kind, thread_id in system.events
            if kind == "business-factory"
        ]
        assert resolver_threads and business_threads
        assert all(thread_id != event_loop_thread for thread_id in resolver_threads)
        assert all(thread_id != event_loop_thread for thread_id in business_threads)
        assert system.business_sessions[0].commit_count == 0
    finally:
        system.engine.dispose()


def test_fused_wire_projection_order_update_and_empty_or_same_value_put(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path / "series-exchange-fused.sqlite")
    updates: list[str] = []

    def record_fused_update(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.lstrip().lower().startswith("update fused_assets"):
            updates.append(statement)

    event.listen(system.engine, "before_cursor_execute", record_fused_update)
    try:
        listed = _http_request(
            system.app,
            "GET",
            "/api/series/series-private/fused-assets",
            headers=_auth_header(system),
        )
        assert listed.status_code == 200
        rows = listed.json()
        assert [row["id"] for row in rows] == ["fused-new", "fused-old"]
        assert set(rows[0]) == {
            "id",
            "series_id",
            "member_type",
            "members",
            "name",
            "image_url",
            "size",
            "status",
            "error_message",
            "created_at",
            "updated_at",
        }
        assert rows[0]["members"] == []
        assert rows[0]["status"] == "failed"
        assert rows[0]["error_message"] == "provider error retained as source data"
        assert rows[0]["created_at"] == "2026-01-01T12:00:00"
        assert rows[1]["members"] == [
            {"kind": "asset", "id": "prop-old", "name": "杯"}
        ]

        updated = _http_request(
            system.app,
            "PUT",
            "/api/fused-assets/fused-old",
            headers=_auth_header(system),
            body={
                "name": "Renamed",
                "member_type": "character",
                "members": [{"id": "character-old"}],
                "image_url": None,
                "size": "large",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Renamed"
        assert updated.json()["image_url"] == "https://cdn.example.test/old.png"
        assert updated.json()["members"] == [
            {
                "kind": "asset",
                "id": "character-old",
                "url": None,
                "name": None,
            }
        ]
        assert len(updates) == 1

        empty = _http_request(
            system.app,
            "PUT",
            "/api/fused-assets/fused-old",
            headers=_auth_header(system),
            body={},
        )
        same_value = _http_request(
            system.app,
            "PUT",
            "/api/fused-assets/fused-old",
            headers=_auth_header(system),
            body={"name": "Renamed"},
        )
        assert empty.status_code == same_value.status_code == 200
        assert len(updates) == 1

        with Session(system.engine, future=True) as session:
            chapter_content = session.execute(
                select(chapters.c.content).where(
                    chapters.c.id == "chapter-private"
                )
            ).scalar_one()
            storyboard_count = session.execute(
                select(storyboard_assets.c.id).where(
                    storyboard_assets.c.chapter_id == "chapter-private"
                )
            ).all()
            stored = session.execute(
                select(fused_assets).where(fused_assets.c.id == "fused-old")
            ).mappings().one()
        assert json.loads(chapter_content) == [
            {"text": "keep", "fused": ["fused-old"]}
        ]
        assert len(storyboard_count) == 1
        assert stored["updated_at"] == NOW
        assert system.business_sessions[-1].commit_count == 1
    finally:
        event.remove(system.engine, "before_cursor_execute", record_fused_update)
        system.engine.dispose()


def test_delete_returns_204_and_does_not_touch_frames_or_storyboard_assets(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path / "series-exchange-delete.sqlite")
    try:
        response = _http_request(
            system.app,
            "DELETE",
            "/api/fused-assets/fused-old",
            headers=_auth_header(system),
        )
        assert response.status_code == 204
        assert response.content == b""
        assert system.business_sessions[-1].commit_count == 1

        with Session(system.engine, future=True) as session:
            fused = session.execute(
                select(fused_assets.c.id).where(fused_assets.c.id == "fused-old")
            ).scalar_one_or_none()
            chapter_content = session.execute(
                select(chapters.c.content).where(
                    chapters.c.id == "chapter-private"
                )
            ).scalar_one()
            storyboard = session.execute(
                select(storyboard_assets.c.id).where(
                    storyboard_assets.c.id == "storyboard-existing"
                )
            ).scalar_one_or_none()
        assert fused is None
        assert json.loads(chapter_content) == [
            {"text": "keep", "fused": ["fused-old"]}
        ]
        assert storyboard == "storyboard-existing"
    finally:
        system.engine.dispose()


def test_series_member_and_claim_access_match_shared_policy(tmp_path: Path) -> None:
    system = _build_system(tmp_path / "series-exchange-access.sqlite")
    try:
        team_member = _http_request(
            system.app,
            "GET",
            "/api/series/series-team/fused-assets",
            headers=_auth_header(system, "member"),
        )
        private_denied = _http_request(
            system.app,
            "GET",
            "/api/series/series-private/fused-assets",
            headers=_auth_header(system, "member"),
        )
        claimed_denied = _http_request(
            system.app,
            "GET",
            "/api/series/series-claimed/fused-assets",
            headers=_auth_header(system, "member"),
        )
        permission_granted = _http_request(
            system.app,
            "GET",
            "/api/series/series-claimed/fused-assets",
            headers=_auth_header(system, "claim-capable"),
        )
        team_owner_granted = _http_request(
            system.app,
            "GET",
            "/api/series/series-claimed/fused-assets",
            headers=_auth_header(system, "captain"),
        )
        missing_series = _http_request(
            system.app,
            "GET",
            "/api/series/missing-series/fused-assets",
            headers=_auth_header(system),
        )

        assert team_member.status_code == 200
        assert private_denied.status_code == 403
        assert private_denied.json() == {"detail": "无权访问该剧集"}
        assert claimed_denied.status_code == 403
        assert "负责制作" in claimed_denied.json()["detail"]
        assert permission_granted.status_code == 200
        assert team_owner_granted.status_code == 200
        assert missing_series.status_code == 404
        assert missing_series.json() == {"detail": "剧集不存在"}
    finally:
        system.engine.dispose()


def test_import_checks_team_membership_and_claim_sets_owner(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path / "series-exchange-team-import.sqlite")
    payload = {"data": {"version": 1, "series": {"name": "Imported"}}}
    try:
        unsupported_version = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system),
            body={"data": {"version": 2}},
        )
        malformed_frame = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system),
            body={
                "data": {
                    "version": 1,
                    "chapters": [{"title": "bad frame", "frames": [None]}],
                }
            },
        )
        malformed_frame_session = system.business_sessions[-1]
        missing_team = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system),
            body={**payload, "team_id": "missing-team"},
        )
        not_member = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system, "outsider"),
            body={**payload, "team_id": "team-a"},
        )
        imported = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system, "member"),
            body={**payload, "team_id": "team-a", "claim": True},
        )

        assert unsupported_version.status_code == 400
        assert unsupported_version.json() == {
            "detail": "不支持的导出格式版本"
        }
        assert malformed_frame.status_code == 400
        assert malformed_frame.json() == {"detail": "导出数据格式错误"}
        assert malformed_frame_session.rollback_count == 1
        assert malformed_frame_session.close_count == 1
        assert missing_team.status_code == 404
        assert missing_team.json() == {"detail": "团队不存在"}
        assert not_member.status_code == 403
        assert not_member.json() == {"detail": "你不是该团队成员"}
        assert imported.status_code == 200
        assert imported.json()["name"] == "Imported"
        assert imported.json()["message"] == "导入成功"
        with Session(system.engine, future=True) as session:
            imported_series = session.execute(
                select(series).where(series.c.id == imported.json()["id"])
            ).mappings().one()
        assert imported_series["user_id"] == "member"
        assert imported_series["team_id"] == "team-a"
        assert imported_series["claimed_by"] == "member"
        assert imported_series["claimed_at"] == NOW
        assert system.business_sessions[-1].commit_count == 1
    finally:
        system.engine.dispose()


def test_import_export_round_trip_remaps_ids_preserves_frame_extras_and_empty_chapters(
    tmp_path: Path,
) -> None:
    generated = count(1000)
    system = _build_system(
        tmp_path / "series-exchange-round-trip.sqlite",
        id_factory=lambda: str(UUID(int=next(generated))),
    )
    imported_data = {
        "version": 1,
        "exported_at": "ignored",
        "series": {"name": "Round trip", "description": "kept"},
        "characters": [{"id": "character-old", "name": "林岚"}],
        "scenes": [{"id": "scene-old", "title": "学校室内夜"}],
        "props": [{"id": "prop-old", "name": "旧杯"}],
        "chapters": [
            {
                "title": "alpha",
                "frames": [
                    {
                        "text": "first",
                        "character": ["character-old", "unknown-character"],
                        "scene": ["scene-old"],
                        "prop": ["prop-old"],
                        "director_extra": {"keep": [1, 2]},
                    },
                    {"text": "second", "future_field": "keep"},
                ],
            },
            {"title": "empty chapter", "frames": []},
        ],
        "storyboard_assets": [
            {
                "id": "storyboard-old-1",
                "chapter_title": "alpha",
                "frame_index": 1,
                "name": "Board second",
                "description": "second description",
                "image_url": "https://cdn.example.test/second.png",
            },
            {
                "id": "storyboard-old-0",
                "chapter_title": "alpha",
                "frame_index": 0,
                "name": "Board first",
                "description": "first description",
                "image_url": "https://cdn.example.test/first.png",
            },
        ],
    }
    try:
        imported = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system),
            body={"data": imported_data, "claim": True},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["name"] == "Round trip"
        assert system.business_sessions[-1].commit_count == 1

        with Session(system.engine, future=True) as session:
            imported_series = session.execute(
                select(series).where(series.c.id == imported.json()["id"])
            ).mappings().one()
            imported_chapters = session.execute(
                select(chapters)
                .where(chapters.c.series_id == imported.json()["id"])
                .order_by(chapters.c.order.desc())
            ).mappings().all()
            imported_character = session.execute(
                select(characters).where(
                    characters.c.series_id == imported.json()["id"]
                )
            ).mappings().one()
            imported_scene = session.execute(
                select(scenes).where(scenes.c.series_id == imported.json()["id"])
            ).mappings().one()
            imported_prop = session.execute(
                select(props).where(props.c.series_id == imported.json()["id"])
            ).mappings().one()
            imported_boards = session.execute(
                select(storyboard_assets)
                .where(storyboard_assets.c.series_id == imported.json()["id"])
                .order_by(storyboard_assets.c.frame_index)
            ).mappings().all()

        assert imported_series["claimed_by"] == "owner"
        assert imported_series["claimed_at"] == NOW
        assert len(imported_chapters) == 2
        assert imported_chapters[0]["order"] == 2
        assert imported_chapters[1]["order"] == 1
        assert all(
            row["id"] not in {"storyboard-old-0", "storyboard-old-1"}
            for row in imported_boards
        )
        first_frames = json.loads(imported_chapters[0]["content"])
        assert first_frames[0]["director_extra"] == {"keep": [1, 2]}
        assert first_frames[1]["future_field"] == "keep"
        assert first_frames[0]["character"] == [
            imported_character["id"],
            "unknown-character",
        ]
        assert first_frames[0]["scene"] == [imported_scene["id"]]
        assert first_frames[0]["prop"] == [imported_prop["id"]]
        assert first_frames[0]["storyboard"] == [imported_boards[0]["id"]]
        assert first_frames[1]["storyboard"] == [imported_boards[1]["id"]]
        assert imported_boards[0]["name"] == "Board first"
        assert imported_boards[1]["name"] == "Board second"
        assert imported_boards[0]["chapter_id"] == imported_chapters[0]["id"]
        assert imported_boards[1]["chapter_id"] == imported_chapters[0]["id"]
        assert imported_scene["canonical_key"] == make_key("scene", "学校室内夜")
        assert imported_character["canonical_key"] == make_key("character", "林岚")
        assert imported_prop["canonical_key"] == make_key("prop", "旧杯")
        assert (
            imported_scene["aliases"]
            == imported_character["aliases"]
            == dump_aliases([])
        )
        assert imported_prop["aliases"] == dump_aliases([])

        exported = _http_request(
            system.app,
            "GET",
            f"/api/series/{imported.json()['id']}/export",
            headers=_auth_header(system),
        )
        assert exported.status_code == 200
        data = exported.json()
        assert set(data) == {
            "version",
            "exported_at",
            "series",
            "chapters",
            "characters",
            "scenes",
            "props",
            "storyboard_assets",
        }
        assert data["version"] == 1
        assert data["exported_at"] == NOW.isoformat()
        assert [chapter["title"] for chapter in data["chapters"]] == [
            "alpha",
            "empty chapter",
        ]
        assert data["chapters"][0]["frames"][0]["director_extra"] == {
            "keep": [1, 2]
        }
        assert (
            data["chapters"][0]["frames"][0]["character"][0]
            == imported_character["id"]
        )
        assert data["storyboard_assets"][0]["chapter_index"] == 0
        assert data["storyboard_assets"][0]["chapter_title"] == "alpha"
        assert "fused_assets" not in data
        assert system.business_sessions[-2].commit_count == 1
        assert system.business_sessions[-1].commit_count == 0
    finally:
        system.engine.dispose()


def test_export_orders_chapters_by_order_then_created_at_and_limits_projection(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path / "series-exchange-export-order.sqlite")
    with Session(system.engine, future=True) as session:
        session.execute(
            insert(chapters),
            [
                {
                    "id": "chapter-order-two",
                    "series_id": "series-private",
                    "title": "Higher order",
                    "content": "[]",
                    "order": 2,
                    "created_at": NOW - timedelta(days=3),
                    "updated_at": NOW - timedelta(days=3),
                },
                {
                    "id": "chapter-order-one-newer",
                    "series_id": "series-private",
                    "title": "Tie newest",
                    "content": "[]",
                    "order": 1,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        session.commit()

    try:
        response = _http_request(
            system.app,
            "GET",
            "/api/series/series-private/export",
            headers=_auth_header(system),
        )
        assert response.status_code == 200
        payload = response.json()
        assert [chapter["title"] for chapter in payload["chapters"]] == [
            "Higher order",
            "Tie newest",
            "Existing chapter",
        ]
        assert set(payload) == {
            "version",
            "exported_at",
            "series",
            "chapters",
            "characters",
            "scenes",
            "props",
            "storyboard_assets",
        }
        assert "fused_assets" not in payload
        assert system.business_sessions[-1].commit_count == 0
    finally:
        system.engine.dispose()


def test_import_matches_legacy_storyboard_by_empty_title_when_title_is_missing(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path / "series-exchange-empty-title.sqlite")
    data = {
        "version": 1,
        "chapters": [{"frames": [{"text": "frame text"}]}],
        "storyboard_assets": [
            {
                "id": "legacy-storyboard-id",
                "chapter_title": "",
                "frame_index": 0,
                "name": "Original board",
                "description": "Original description",
                "image_url": "https://cdn.example.test/original.png",
            }
        ],
    }

    try:
        imported = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system),
            body={"data": data},
        )
        assert imported.status_code == 200, imported.text
        with Session(system.engine, future=True) as session:
            chapter = session.execute(
                select(chapters).where(chapters.c.series_id == imported.json()["id"])
            ).mappings().one()
            storyboard = session.execute(
                select(storyboard_assets).where(
                    storyboard_assets.c.series_id == imported.json()["id"]
                )
            ).mappings().one()

        frame = json.loads(chapter["content"])[0]
        assert chapter["title"] == "章节 1"
        assert storyboard["name"] == "Original board"
        assert storyboard["description"] == "Original description"
        assert storyboard["image_url"] == "https://cdn.example.test/original.png"
        assert frame["storyboard"] == [storyboard["id"]]
        assert storyboard["id"] != "legacy-storyboard-id"
        assert system.business_sessions[-1].commit_count == 1
    finally:
        system.engine.dispose()


def test_import_empty_data_and_insert_failure_roll_back_the_single_transaction(
    tmp_path: Path,
) -> None:
    system = _build_system(
        tmp_path / "series-exchange-rollback.sqlite",
        id_factory=lambda: next(rollback_ids),
    )
    try:
        empty = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system),
            body={"data": {"version": 1, "chapters": []}},
        )
        assert empty.status_code == 200
        assert system.business_sessions[-1].commit_count == 1
        with Session(system.engine, future=True) as session:
            empty_chapters = session.execute(
                select(chapters.c.id).where(
                    chapters.c.series_id == empty.json()["id"]
                )
            ).all()
        assert empty_chapters == []

        with Session(system.engine, future=True) as session:
            session.execute(
                insert(characters).values(
                    id="duplicate-character",
                    series_id="series-private",
                    name="Existing",
                    aliases="[]",
                    canonical_key="existing",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.commit()

        failed = _http_request(
            system.app,
            "POST",
            "/api/series/import",
            headers=_auth_header(system),
            body={
                "data": {
                    "version": 1,
                    "series": {"name": "Must roll back"},
                    "characters": [{"id": "old", "name": "Duplicate"}],
                }
            },
        )
        assert failed.status_code == 500
        failed_session = system.business_sessions[-1]
        assert failed_session.commit_count == 0
        assert failed_session.rollback_count == 1
        assert failed_session.close_count == 1
        with Session(system.engine, future=True) as session:
            rolled_back = session.execute(
                select(series.c.id).where(series.c.id == "rollback-series")
            ).scalar_one_or_none()
        assert rolled_back is None
    finally:
        system.engine.dispose()


rollback_ids = iter(
    [
        "empty-series",
        "rollback-series",
        "duplicate-character",
    ]
)
