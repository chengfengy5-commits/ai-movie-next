"""Application use cases over real temporary SQLite and explicit adapters."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.domain import UserRecord
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata, user_credits, user_sessions, users


class DeterministicHasher:
    def hash(self, password: str) -> str:
        return f"test-hash:{password}"

    def verify(self, password: str, encoded: str) -> bool:
        return encoded == self.hash(password)


class FakeTokenCodec:
    def __init__(self, claims: Mapping[str, Any] | None = None) -> None:
        self.claims = dict(claims or {})
        self.encoded: list[dict[str, Any]] = []

    def encode(self, claims: Mapping[str, Any]) -> str:
        self.encoded.append(dict(claims))
        return f"token-{len(self.encoded)}"

    def decode(self, token: str) -> dict[str, Any]:
        return dict(self.claims)


class TrackingSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        super().commit()


def make_database(path: Path):
    engine = create_engine(f"sqlite:///{path}", future=True)
    metadata.create_all(engine)
    sessions: list[TrackingSession] = []
    factory = sessionmaker(engine, class_=TrackingSession, expire_on_commit=False, future=True)

    def tracked_factory() -> TrackingSession:
        session = factory()
        sessions.append(session)
        return session

    return engine, tracked_factory, sessions


def make_service(runtime: AuthenticationRuntime) -> AuthenticationService:
    return AuthenticationService(
        runtime,
        lambda: SqlAlchemyAuthenticationUnitOfWork(runtime.session_factory()),
    )


def make_runtime(factory, *, codec: FakeTokenCodec | None = None, id_prefix: str = "id"):
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"{id_prefix}-{counter}"

    fixed_now = datetime(2026, 1, 2, 3, 4, 5)
    return AuthenticationRuntime(
        session_factory=factory,
        secret_key="unit-test-secret",
        password_hasher=DeterministicHasher(),
        token_codec=codec or FakeTokenCodec(),
        clock=lambda: fixed_now,
        epoch=lambda: fixed_now.timestamp(),
        id_factory=next_id,
    )


def seed_user(factory, *, user_id="user-a", username="alice", email="alice@example.test",
              password="Password123", is_superuser=False, membership_type="premium",
              membership_expires_at=None, created_at=None, password_updated_at=None, bio="hello"):
    now = created_at or datetime(2025, 1, 1)
    record = {
        "id": user_id,
        "username": username,
        "email": email,
        "hashed_password": DeterministicHasher().hash(password),
        "is_superuser": is_superuser,
        "membership_type": membership_type,
        "membership_expires_at": membership_expires_at,
        "avatar_url": None,
        "bio": bio,
        "created_at": now,
        "password_updated_at": password_updated_at,
    }
    with factory() as session:
        session.execute(insert(users).values(**record))
        session.commit()
    return UserRecord(**record)


def test_register_creates_first_superuser_and_zero_credits_atomically(tmp_path: Path) -> None:
    engine, factory, sessions = make_database(tmp_path / "register.sqlite")
    service = make_service(make_runtime(factory))
    try:
        first = service.register(
            username=" Alice ", email=" Alice@Example.Test ", password="Password123", email_code=""
        )
        second = service.register(
            username="bob", email="bob@example.test", password="Password123", email_code=""
        )
        assert [session.commit_count for session in sessions if session.commit_count] == [1, 1]
        assert first["username"] == " Alice "
        assert first["email"] == " Alice@Example.Test "
        assert first["is_superuser"] is True
        assert first["membership_type"] == "premium"
        assert second["is_superuser"] is False
        assert second["membership_type"] == "free"
        with factory() as session:
            assert session.execute(select(user_credits.c.credits)).scalars().all() == [0, 0]
            assert session.execute(select(users.c.id)).scalars().all() == ["id-1", "id-3"]
    finally:
        engine.dispose()


def test_login_keeps_two_recent_sessions_and_creates_real_token_claims(tmp_path: Path) -> None:
    engine, factory, sessions = make_database(tmp_path / "login.sqlite")
    seed_user(factory)
    now = datetime(2026, 1, 2, 3, 4, 5)
    with factory() as session:
        for index in range(3):
            session.execute(insert(user_sessions).values(
                id=f"session-{index}", user_id="user-a", jti=f"jti-{index}",
                user_agent="old", ip_address="127.0.0.1", device_name="old",
                is_active=True, created_at=now - timedelta(days=3 - index),
                last_seen_at=now - timedelta(days=3 - index), revoked_at=None,
            ))
        session.commit()
    codec = FakeTokenCodec()
    service = make_service(make_runtime(factory, codec=codec))
    try:
        result = service.login(
            username="alice", password="Password123", user_agent="Mozilla Windows Chrome Edg",
            headers={"x-forwarded-for": " 203.0.113.4, 10.0.0.1", "x-real-ip": "ignored"},
            client_host="127.0.0.1",
        )
        assert result["token_type"] == "bearer"
        assert result["user"]["bio"] is None
        assert result["user"]["avatar_url"] is None
        assert codec.encoded[0]["sub"] == "user-a"
        assert codec.encoded[0]["jti"] == "id-1"
        assert codec.encoded[0]["exp"] == now + timedelta(days=14)
        assert result["access_token"] == "token-1"
        with factory() as session:
            rows = session.execute(select(user_sessions).order_by(user_sessions.c.created_at)).mappings().all()
        assert [row["is_active"] for row in rows] == [False, True, True, True]
        assert rows[-1]["ip_address"] == "203.0.113.4"
        assert rows[-1]["device_name"] == "Windows · Edge"
    finally:
        engine.dispose()


def test_unknown_jti_is_committed_before_last_seen_update_and_known_jti_once(tmp_path: Path) -> None:
    engine, factory, sessions = make_database(tmp_path / "jti.sqlite")
    user = seed_user(factory)
    codec = FakeTokenCodec({"sub": user.id, "jti": "legacy-jti", "password_version": str(int(user.created_at.timestamp()))})
    service = make_service(make_runtime(factory, codec=codec))
    try:
        with service.authenticated("Bearer legacy") as principal:
            assert principal.user.id == user.id
        assert sessions[-1].commit_count == 2
        with factory() as session:
            assert session.execute(select(user_sessions.c.jti)).scalars().all() == ["legacy-jti"]
            first_last_seen = session.execute(select(user_sessions.c.last_seen_at)).scalar_one()
            session.execute(insert(user_sessions).values(
                id="known-session", user_id=user.id, jti="known-jti", user_agent=None,
                ip_address=None, device_name=None, is_active=True, created_at=user.created_at,
                last_seen_at=user.created_at, revoked_at=None,
            ))
            session.commit()
        known_codec = FakeTokenCodec({"sub": user.id, "jti": "known-jti", "password_version": str(int(user.created_at.timestamp()))})
        known_service = make_service(make_runtime(factory, codec=known_codec))
        with known_service.authenticated("Bearer known") as principal:
            assert principal.user.id == user.id
        assert sessions[-1].commit_count == 1
        with factory() as session:
            saved_unknown = session.execute(select(user_sessions).where(user_sessions.c.jti == "legacy-jti")).mappings().one()
            known = session.execute(select(user_sessions).where(user_sessions.c.jti == "known-jti")).mappings().one()
        assert saved_unknown["last_seen_at"] == datetime(2026, 1, 2, 3, 4, 5)
        assert known["last_seen_at"] == datetime(2026, 1, 2, 3, 4, 5)
    finally:
        engine.dispose()


def test_authenticated_last_seen_touch_cannot_restore_a_concurrently_revoked_session(
    tmp_path: Path,
) -> None:
    engine, factory, _sessions = make_database(tmp_path / "known-session-offline-race.sqlite")
    user = seed_user(factory)
    with factory() as session:
        session.execute(insert(user_sessions).values(
            id="known-session", user_id=user.id, jti="known-jti", user_agent=None,
            ip_address=None, device_name=None, is_active=True, created_at=user.created_at,
            last_seen_at=user.created_at, revoked_at=None,
        ))
        session.commit()

    codec = FakeTokenCodec({
        "sub": user.id,
        "jti": "known-jti",
        "password_version": str(int(user.created_at.timestamp())),
    })
    runtime = make_runtime(factory, codec=codec)
    observed_active_arguments: list[bool | None] = []
    first_connection = engine.connect()

    class ConcurrentlyOffliningUnitOfWork(SqlAlchemyAuthenticationUnitOfWork):
        def update_session(
            self,
            session_id: str,
            *,
            is_active: bool | None,
            last_seen_at: datetime | None = None,
            revoked_at: datetime | None = None,
        ) -> bool:
            if session_id == "known-session":
                first_physical = self._session.connection().connection.driver_connection
                self._session.commit()
                concurrent_connection = engine.connect()
                try:
                    assert (
                        concurrent_connection.connection.driver_connection
                        is not first_physical
                    )
                    with Session(bind=concurrent_connection, future=True) as concurrent:
                        concurrent.execute(
                            update(user_sessions)
                            .where(user_sessions.c.id == session_id)
                            .values(is_active=False, revoked_at=runtime.clock())
                        )
                        concurrent.commit()
                finally:
                    concurrent_connection.close()
            observed_active_arguments.append(is_active)
            return super().update_session(
                session_id,
                is_active=is_active,
                last_seen_at=last_seen_at,
                revoked_at=revoked_at,
            )

    service = AuthenticationService(
        runtime,
        lambda: ConcurrentlyOffliningUnitOfWork(
            Session(bind=first_connection, future=True)
        ),
    )
    try:
        with service.authenticated("Bearer known") as principal:
            assert principal.user.id == user.id

        assert observed_active_arguments == [None]
        with factory() as session:
            saved = session.execute(
                select(user_sessions).where(user_sessions.c.id == "known-session")
            ).mappings().one()
        assert saved["is_active"] is False
        assert saved["revoked_at"] == runtime.clock()
        assert saved["last_seen_at"] == runtime.clock()
    finally:
        first_connection.close()
        engine.dispose()


def test_reset_requires_password_reset_token_type_before_opening_session(tmp_path: Path) -> None:
    engine, factory, sessions = make_database(tmp_path / "reset-type.sqlite")
    sessions_created = 0

    def count_factory():
        nonlocal sessions_created
        sessions_created += 1
        return factory()

    codec = FakeTokenCodec({"email": "alice@example.test", "type": "access"})
    service = AuthenticationService(
        make_runtime(count_factory, codec=codec),
        lambda: SqlAlchemyAuthenticationUnitOfWork(count_factory()),
    )
    try:
        try:
            service.reset_password(token="wrong-kind", new_password="NewPass123")
        except AuthenticationError as exc:
            assert (exc.status_code, exc.detail) == (400, "无效的重置链接")
        else:
            raise AssertionError("non-reset token must be rejected")
        assert sessions_created == 0
    finally:
        engine.dispose()



def test_missing_jti_skips_session_query_and_commit(tmp_path: Path) -> None:
    engine, factory, sessions = make_database(tmp_path / "legacy-no-jti.sqlite")
    user = seed_user(factory)
    codec = FakeTokenCodec({"sub": user.id})
    statements: list[str] = []

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", observe_sql)
    service = make_service(make_runtime(factory, codec=codec))
    try:
        with service.authenticated("Bearer legacy-without-jti") as principal:
            assert principal.user.id == user.id
        assert sessions[-1].commit_count == 0
        assert not any("user_sessions" in statement for statement in statements)
    finally:
        event.remove(engine, "before_cursor_execute", observe_sql)
        engine.dispose()


def test_membership_denial_happens_after_unknown_jti_maintenance_commits(tmp_path: Path) -> None:
    engine, factory, sessions = make_database(tmp_path / "membership-maintenance.sqlite")
    user = seed_user(factory, membership_type="free")
    codec = FakeTokenCodec({"sub": user.id, "jti": "unknown-legacy-jti"})
    service = make_service(make_runtime(factory, codec=codec))
    try:
        try:
            service.resolve_business_actor("Bearer legacy")
        except AuthenticationError as exc:
            assert exc.status_code == 403
            assert "会员" in exc.detail
        else:
            raise AssertionError("non-member must be denied")
        assert sessions[-1].commit_count == 2
        with factory() as session:
            persisted = session.execute(
                select(user_sessions).where(user_sessions.c.jti == "unknown-legacy-jti")
            ).mappings().one()
        assert persisted["user_id"] == user.id
    finally:
        engine.dispose()


def test_change_password_updates_version_and_revokes_all_sessions(tmp_path: Path) -> None:
    engine, factory, sessions = make_database(tmp_path / "change-password.sqlite")
    user = seed_user(factory, password_updated_at=datetime(2025, 3, 1))
    with factory() as session:
        for index in range(2):
            session.execute(insert(user_sessions).values(
                id=f"change-session-{index}", user_id=user.id, jti=f"change-jti-{index}",
                user_agent=None, ip_address=None, device_name=None, is_active=True,
                created_at=datetime(2025, 1, 1), last_seen_at=datetime(2025, 1, 1), revoked_at=None,
            ))
        session.commit()
    codec = FakeTokenCodec({"sub": user.id, "jti": "change-jti-0"})
    service = make_service(make_runtime(factory, codec=codec))
    try:
        with service.authenticated("Bearer current") as principal:
            result = service.change_password(
                principal, old_password="Password123", new_password="new123"
            )
        assert result["message"] == "密码修改成功，请重新登录"
        assert sessions[-1].commit_count == 2
        with factory() as session:
            saved_user = session.execute(select(users).where(users.c.id == user.id)).mappings().one()
            saved_sessions = session.execute(select(user_sessions)).mappings().all()
        assert saved_user["hashed_password"] == DeterministicHasher().hash("new123")
        assert saved_user["password_updated_at"] == datetime(2026, 1, 2, 3, 4, 5)
        assert all(not row["is_active"] and row["revoked_at"] is not None for row in saved_sessions)
    finally:
        engine.dispose()


def test_reset_is_repeatable_and_preserves_password_version_and_sessions(tmp_path: Path) -> None:
    engine, factory, sessions = make_database(tmp_path / "reset-repeat.sqlite")
    user = seed_user(factory, password_updated_at=datetime(2025, 3, 1))
    with factory() as session:
        session.execute(insert(user_sessions).values(
            id="reset-session", user_id=user.id, jti="reset-jti", user_agent=None,
            ip_address=None, device_name=None, is_active=True, created_at=user.created_at,
            last_seen_at=user.created_at, revoked_at=None,
        ))
        session.commit()
    codec = FakeTokenCodec({"email": user.email, "type": "password_reset"})
    service = make_service(make_runtime(factory, codec=codec))
    try:
        assert service.reset_password(token="repeatable", new_password="ResetPass123")["message"] == "密码重置成功，请使用新密码登录"
        first_reset_write = sessions[-1]
        with factory() as first_read:
            first_hash = first_read.execute(select(users.c.hashed_password).where(users.c.id == user.id)).scalar_one()
        assert service.reset_password(token="repeatable", new_password="AgainPass123")["message"] == "密码重置成功，请使用新密码登录"
        second_reset_write = sessions[-1]
        with factory() as session:
            saved = session.execute(select(users).where(users.c.id == user.id)).mappings().one()
            active = session.execute(select(user_sessions).where(user_sessions.c.id == "reset-session")).mappings().one()
        assert first_hash != saved["hashed_password"]
        assert saved["hashed_password"] == DeterministicHasher().hash("AgainPass123")
        assert saved["password_updated_at"] == datetime(2025, 3, 1)
        assert active["is_active"] is True and active["revoked_at"] is None
        assert first_reset_write.commit_count == 1
        assert second_reset_write.commit_count == 1
    finally:
        engine.dispose()


def test_forgot_password_masks_mismatch_and_uses_explicit_reset_url(tmp_path: Path) -> None:
    engine, factory, _sessions = make_database(tmp_path / "forgot.sqlite")
    user = seed_user(factory, username="alice", email="alice@example.test")
    sent: list[tuple[str, str, str]] = []
    codec = FakeTokenCodec()
    runtime = make_runtime(factory, codec=codec)
    runtime.password_reset_sender = lambda email, subject, html: sent.append((email, subject, html)) or False
    runtime.frontend_url = "https://frontend.test/root///"
    service = make_service(runtime)
    try:
        mismatch = service.forgot_password(username="alice", email="wrong@example.test")
        assert mismatch == {"message": "如果信息正确，重置链接已发送"}
        assert sent == []
        result = service.forgot_password(username=" alice ", email=" ALICE@EXAMPLE.TEST ")
        assert result == {"message": "如果信息正确，重置链接已发送"}
        assert sent[0][0] == "alice@example.test"
        assert sent[0][1] == "Hao AI - 密码重置"
        assert "https://frontend.test/root/reset-password.html?token=token-1" in sent[0][2]
        assert codec.encoded[0]["type"] == "password_reset"
        assert codec.encoded[0]["exp"] == datetime(2026, 1, 2, 3, 4, 5) + timedelta(minutes=30)
    finally:
        engine.dispose()


def test_missing_reset_sender_is_a_503_before_database_access(tmp_path: Path) -> None:
    engine, factory, sessions = make_database(tmp_path / "forgot-unwired.sqlite")
    runtime = make_runtime(factory)
    runtime.password_reset_sender = None
    service = make_service(runtime)
    try:
        try:
            service.forgot_password(username="missing", email="missing@example.test")
        except AuthenticationError as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("missing sender should not claim delivery")
        assert sessions == []
    finally:
        engine.dispose()


def test_credits_create_zero_once_without_nonnegative_policy(tmp_path: Path) -> None:
    engine, factory, sessions = make_database(tmp_path / "credits.sqlite")
    user = seed_user(factory)
    service = make_service(make_runtime(factory, codec=FakeTokenCodec({"sub": user.id})))
    try:
        with service.authenticated("Bearer no-jti") as principal:
            assert service.credits(principal) == {"credits": 0}
            assert sessions[-1].commit_count == 1
            assert service.credits(principal) == {"credits": 0}
            assert sessions[-1].commit_count == 1
        with factory() as session:
            session.execute(
                update(user_credits)
                .where(user_credits.c.user_id == user.id)
                .values(credits=-7, updated_at=user.created_at)
            )
            session.commit()
        with service.authenticated("Bearer no-jti") as principal:
            assert service.credits(principal) == {"credits": -7}
    finally:
        engine.dispose()


def test_revoke_session_is_limited_to_the_authenticated_user(tmp_path: Path) -> None:
    engine, factory, _sessions = make_database(tmp_path / "revoke-session.sqlite")
    user = seed_user(factory)
    other = seed_user(
        factory,
        user_id="user-b",
        username="bob",
        email="bob@example.test",
    )
    with factory() as session:
        for session_id, owner_id in (("own-session", user.id), ("other-session", other.id)):
            session.execute(insert(user_sessions).values(
                id=session_id,
                user_id=owner_id,
                jti=f"jti-{session_id}",
                user_agent=None,
                ip_address=None,
                device_name=None,
                is_active=True,
                created_at=user.created_at,
                last_seen_at=user.created_at,
                revoked_at=None,
            ))
        session.commit()

    codec = FakeTokenCodec({"sub": user.id})
    service = make_service(make_runtime(factory, codec=codec))
    try:
        with service.authenticated("Bearer current") as principal:
            assert service.revoke_session(principal, "own-session") == {
                "message": "设备已下线",
                "session_id": "own-session",
            }
            try:
                service.revoke_session(principal, "other-session")
            except AuthenticationError as exc:
                assert (exc.status_code, exc.detail) == (404, "会话不存在")
            else:
                raise AssertionError("another user's session must not be revocable")
        with factory() as session:
            own = session.execute(
                select(user_sessions).where(user_sessions.c.id == "own-session")
            ).mappings().one()
            foreign = session.execute(
                select(user_sessions).where(user_sessions.c.id == "other-session")
            ).mappings().one()
        assert own["is_active"] is False and own["revoked_at"] is not None
        assert foreign["is_active"] is True and foreign["revoked_at"] is None
    finally:
        engine.dispose()


def test_session_payload_keeps_only_active_self_sessions_and_current_last(tmp_path: Path) -> None:
    engine, factory, _sessions = make_database(tmp_path / "sessions-payload.sqlite")
    user = seed_user(factory)
    with factory() as session:
        for index in range(19):
            timestamp = datetime(2026, 1, 1) + timedelta(minutes=index)
            session.execute(insert(user_sessions).values(
                id=f"list-session-{index}",
                user_id=user.id,
                jti=f"list-jti-{index}",
                user_agent=None,
                ip_address=None,
                device_name=None,
                is_active=True,
                created_at=timestamp,
                last_seen_at=timestamp,
                revoked_at=None,
            ))
        session.execute(insert(user_sessions).values(
            id="current-session",
            user_id=user.id,
            jti="current-jti",
            user_agent=None,
            ip_address=None,
            device_name=None,
            is_active=True,
            created_at=datetime(2025, 1, 1),
            last_seen_at=datetime(2025, 1, 1),
            revoked_at=None,
        ))
        session.execute(insert(user_sessions).values(
            id="inactive-session",
            user_id=user.id,
            jti="inactive-jti",
            user_agent=None,
            ip_address=None,
            device_name=None,
            is_active=False,
            created_at=user.created_at,
            last_seen_at=user.created_at,
            revoked_at=datetime(2025, 1, 2),
        ))
        session.commit()

    service = make_service(
        make_runtime(factory, codec=FakeTokenCodec({"sub": user.id, "jti": "current-jti"}))
    )
    try:
        with service.authenticated("Bearer current") as principal:
            result = service.sessions_payload(principal)
        assert len(result) == 20
        assert result[0]["jti"] == "list-jti-18"
        assert result[-1]["jti"] == "current-jti"
        assert result[-1]["is_current"] is True
        assert all(row["jti"] != "inactive-jti" for row in result)
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure_point", ["before_commit", "after_commit"])
def test_registration_commit_failure_requires_a_new_read_to_confirm_durable_state(
    tmp_path: Path, failure_point: str
) -> None:
    class FaultingSession(TrackingSession):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.commit_attempts = 0
            self.rollback_count = 0

        def commit(self) -> None:
            self.commit_attempts += 1
            if failure_point == "before_commit":
                raise RuntimeError("commit failed before durable write")
            super().commit()
            raise RuntimeError("commit acknowledgement lost after durable write")

        def rollback(self) -> None:
            self.rollback_count += 1
            super().rollback()

    engine = create_engine(f"sqlite:///{tmp_path / (failure_point + '.sqlite')}", future=True)
    metadata.create_all(engine)
    tracked_factory = sessionmaker(
        engine, class_=FaultingSession, expire_on_commit=False, future=True
    )
    opened: list[FaultingSession] = []

    def session_factory() -> FaultingSession:
        session = tracked_factory()
        opened.append(session)
        return session

    service = make_service(make_runtime(session_factory))
    try:
        with pytest.raises(RuntimeError, match="commit"):
            service.register(
                username="alice",
                email="alice@example.test",
                password="Password123",
                email_code="",
            )
        assert len(opened) == 1
        assert opened[0].commit_attempts == 1
        assert opened[0].rollback_count == 1

        with Session(engine, future=True) as read_session:
            users_after = read_session.execute(select(users.c.id)).scalars().all()
            credits_after = read_session.execute(select(user_credits.c.user_id)).scalars().all()

        if failure_point == "before_commit":
            assert users_after == []
            assert credits_after == []
        else:
            assert users_after == ["id-1"]
            assert credits_after == ["id-1"]
            assert opened[0].commit_count == 1
    finally:
        engine.dispose()
