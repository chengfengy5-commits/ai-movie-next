"""Real JWT, SQLite, and HTTP checks for all chapter lock routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.chapter_locks import application as chapter_lock_application
from haoai_backend.chapter_locks.domain import ChapterLockActor
from haoai_backend.chapter_locks.tables import (
    chapter_locks,
    chapters,
    metadata as chapter_locks_metadata,
    system_configs,
)
from haoai_backend.series_access.tables import (
    metadata as series_access_metadata,
    series,
    team_members,
)

NOW = datetime(2031, 1, 2, 3, 4, 5)
SECRET = "chapter-lock-http-test-secret"


class TrackingSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_calls = 0
        self.closed_successfully = False

    def commit(self) -> None:
        self.commit_calls += 1
        super().commit()

    def close(self) -> None:
        super().close()
        self.closed_successfully = True


def create_database(path: Path):
    engine = create_engine(f"sqlite:///{path}", future=True)
    authentication_metadata.create_all(engine)
    series_access_metadata.create_all(engine)
    chapter_locks_metadata.create_all(engine)
    return engine


def seed_user(
    connection,
    *,
    user_id: str,
    username: str,
    membership_type: str,
    membership_expires_at: datetime | None,
    active_session: bool = True,
) -> str:
    jti = f"jti-{user_id}"
    connection.execute(
        insert(users).values(
            id=user_id,
            username=username,
            email=f"{user_id}@example.test",
            hashed_password="test-hash",
            is_superuser=False,
            membership_type=membership_type,
            membership_expires_at=membership_expires_at,
            avatar_url=None,
            bio=None,
            created_at=NOW - timedelta(days=30),
            password_updated_at=None,
        )
    )
    connection.execute(
        insert(user_sessions).values(
            id=f"session-{user_id}",
            user_id=user_id,
            jti=jti,
            user_agent="test-agent",
            ip_address="192.0.2.10",
            device_name="test device",
            is_active=active_session,
            created_at=NOW - timedelta(days=1),
            last_seen_at=NOW - timedelta(hours=1),
            revoked_at=None if active_session else NOW - timedelta(hours=1),
        )
    )
    return jti


def bearer(codec: JoseTokenCodec, user_id: str, jti: str) -> str:
    expires_at = int(
        (NOW + timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp()
    )
    token = codec.encode({"sub": user_id, "jti": jti, "exp": expires_at})
    return f"Bearer {token}"


def request(
    app,
    method: str,
    path: str,
    *,
    authorization: str | None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers = (
            {"authorization": authorization}
            if authorization is not None
            else {}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://chapter-locks.test",
        ) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(send())


def test_real_jwt_gate_and_all_lock_routes_use_one_real_sqlite_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_database(tmp_path / "chapter-locks-http.sqlite")
    codec = JoseTokenCodec(SECRET)
    with engine.begin() as connection:
        active_jti = seed_user(
            connection,
            user_id="user-a",
            username="Alice",
            membership_type="premium",
            membership_expires_at=NOW + timedelta(days=10),
        )
        bob_jti = seed_user(
            connection,
            user_id="user-b",
            username="Bob",
            membership_type="premium",
            membership_expires_at=NOW + timedelta(days=10),
        )
        free_jti = seed_user(
            connection,
            user_id="user-free",
            username="Free",
            membership_type="free",
            membership_expires_at=None,
        )
        expired_jti = seed_user(
            connection,
            user_id="user-expired",
            username="Expired",
            membership_type="premium",
            membership_expires_at=NOW - timedelta(seconds=1),
        )
        revoked_jti = seed_user(
            connection,
            user_id="user-revoked",
            username="Revoked",
            membership_type="premium",
            membership_expires_at=NOW + timedelta(days=10),
            active_session=False,
        )
        connection.execute(
            insert(series).values(
                id="series-a",
                user_id="series-owner",
                team_id="team-a",
                claimed_by=None,
            )
        )
        connection.execute(
            insert(team_members),
            [
                {
                    "id": "member-a",
                    "team_id": "team-a",
                    "user_id": "user-a",
                    "role": "member",
                    "permissions": None,
                },
                {
                    "id": "member-b",
                    "team_id": "team-a",
                    "user_id": "user-b",
                    "role": "member",
                    "permissions": None,
                },
            ],
        )
        connection.execute(
            insert(chapters).values(id="chapter-a", series_id="series-a")
        )
        connection.execute(
            insert(system_configs).values(
                id="config-a",
                chapter_lock_idle_minutes=5,
            )
        )

    auth_sessionmaker = sessionmaker(
        engine,
        class_=TrackingSession,
        expire_on_commit=True,
        future=True,
    )
    business_sessionmaker = sessionmaker(
        engine,
        class_=TrackingSession,
        expire_on_commit=True,
        future=True,
    )
    authentication_sessions: list[TrackingSession] = []
    business_sessions: list[TrackingSession] = []

    def open_authentication_session() -> TrackingSession:
        session = auth_sessionmaker()
        authentication_sessions.append(session)
        return session

    def open_business_session() -> TrackingSession:
        assert authentication_sessions
        assert authentication_sessions[-1].closed_successfully
        session = business_sessionmaker()
        business_sessions.append(session)
        return session

    runtime = AuthenticationRuntime(
        session_factory=open_authentication_session,
        secret_key=SECRET,
        token_codec=codec,
        clock=lambda: NOW,
        epoch=lambda: NOW.replace(tzinfo=timezone.utc).timestamp(),
    )
    app = create_app(
        session_factory=open_business_session,
        authentication_runtime=runtime,
    )

    times = iter(
        [
            NOW,
            NOW + timedelta(seconds=10),
            NOW + timedelta(seconds=20),
            NOW + timedelta(seconds=30),
            NOW + timedelta(seconds=50),
            NOW + timedelta(seconds=60),
            NOW + timedelta(seconds=70),
        ]
    )

    class DeterministicDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = next(times)
            return value.replace(tzinfo=tz) if tz is not None else value

    lock_ids = iter(
        [
            UUID("11111111-1111-4111-8111-111111111111"),
            UUID("22222222-2222-4222-8222-222222222222"),
        ]
    )
    monkeypatch.setattr(chapter_lock_application, "datetime", DeterministicDateTime)
    monkeypatch.setattr(chapter_lock_application, "uuid4", lambda: next(lock_ids))

    try:
        free = request(
            app,
            "POST",
            "/api/chapters/chapter-a/lock",
            authorization=bearer(codec, "user-free", free_jti),
        )
        assert free.status_code == 403
        assert free.json() == {
            "detail": "需要会员才能使用此功能，请前往购买会员"
        }
        expired = request(
            app,
            "POST",
            "/api/chapters/chapter-a/lock",
            authorization=bearer(codec, "user-expired", expired_jti),
        )
        assert expired.status_code == 403
        assert expired.json() == {"detail": "会员已过期，请续费后使用"}
        revoked = request(
            app,
            "POST",
            "/api/chapters/chapter-a/lock",
            authorization=bearer(codec, "user-revoked", revoked_jti),
        )
        assert revoked.status_code == 401
        assert len(business_sessions) == 0

        alice = bearer(codec, "user-a", active_jti)
        bob = bearer(codec, "user-b", bob_jti)

        acquired = request(
            app,
            "POST",
            "/api/chapters/chapter-a/lock",
            authorization=alice,
        )
        assert acquired.status_code == 200
        assert acquired.json() == {
            "locked": True,
            "username": "Alice",
            "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
            "idle_minutes": 5,
        }
        assert business_sessions[-1].commit_calls == 1

        renewed = request(
            app,
            "POST",
            "/api/chapters/chapter-a/lock",
            authorization=alice,
        )
        assert renewed.status_code == 200
        assert renewed.json() == {
            "locked": True,
            "username": "Alice",
            "expires_at": (NOW + timedelta(seconds=10, minutes=5)).isoformat(),
            "idle_minutes": 5,
        }
        assert business_sessions[-1].commit_calls == 1

        conflict = request(
            app,
            "POST",
            "/api/chapters/chapter-a/lock",
            authorization=bob,
        )
        assert conflict.status_code == 409
        assert conflict.json() == {
            "detail": "该章节正在被 Alice 编辑",
            "locked_by": "Alice",
            "expires_at": (NOW + timedelta(seconds=10, minutes=5)).isoformat(),
        }
        assert business_sessions[-1].commit_calls == 0

        current = request(
            app,
            "GET",
            "/api/chapters/chapter-a/lock",
            authorization=bob,
        )
        assert current.status_code == 200
        assert current.json() == {
            "locked": True,
            "user_id": "user-a",
            "username": "Alice",
            "acquired_at": NOW.isoformat(),
            "last_active_at": (NOW + timedelta(seconds=10)).isoformat(),
            "expires_at": (NOW + timedelta(seconds=10, minutes=5)).isoformat(),
            "is_mine": False,
        }
        assert business_sessions[-1].commit_calls == 0

        with engine.begin() as connection:
            connection.execute(
                update(chapter_locks)
                .where(chapter_locks.c.chapter_id == "chapter-a")
                .values(expires_at=NOW + timedelta(seconds=40))
            )

        replaced = request(
            app,
            "POST",
            "/api/chapters/chapter-a/lock",
            authorization=bob,
        )
        assert replaced.status_code == 200
        assert replaced.json() == {
            "locked": True,
            "username": "Bob",
            "expires_at": (NOW + timedelta(minutes=5, seconds=50)).isoformat(),
            "idle_minutes": 5,
        }
        assert business_sessions[-1].commit_calls == 1

        bob_lock = request(
            app,
            "GET",
            "/api/chapters/chapter-a/lock",
            authorization=bob,
        )
        assert bob_lock.status_code == 200
        assert bob_lock.json() == {
            "locked": True,
            "user_id": "user-b",
            "username": "Bob",
            "acquired_at": (NOW + timedelta(seconds=50)).isoformat(),
            "last_active_at": (NOW + timedelta(seconds=50)).isoformat(),
            "expires_at": (NOW + timedelta(minutes=5, seconds=50)).isoformat(),
            "is_mine": True,
        }

        foreign_release = request(
            app,
            "DELETE",
            "/api/chapters/chapter-a/lock",
            authorization=alice,
        )
        assert foreign_release.status_code == 403
        assert foreign_release.json() == {"detail": "只能释放自己持有的锁"}
        assert business_sessions[-1].commit_calls == 0

        released = request(
            app,
            "DELETE",
            "/api/chapters/chapter-a/lock",
            authorization=bob,
        )
        assert released.status_code == 200
        assert released.json() == {"message": "锁已释放"}
        assert business_sessions[-1].commit_calls == 1

        unlocked = request(
            app,
            "GET",
            "/api/chapters/chapter-a/lock",
            authorization=bob,
        )
        assert unlocked.status_code == 200
        assert unlocked.json() == {"locked": False}
        assert business_sessions[-1].commit_calls == 0
    finally:
        engine.dispose()


def test_unwired_business_factory_returns_503_before_resolving_actor() -> None:
    resolver_calls: list[str] = []

    def resolver(_request):
        resolver_calls.append("called")
        return ChapterLockActor("user-a", "Alice")

    response = request(
        create_app(resolve_chapter_lock_actor=resolver),
        "POST",
        "/api/chapters/chapter-a/lock",
        authorization="Bearer ignored",
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "章节锁服务尚未接线"}
    assert resolver_calls == []
