"""Real JWT and SQLite checks for the narrow task identity facade."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.authentication import (
    AuthenticationError,
    AuthenticationRuntime,
    create_task_identity_resolvers,
)
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata, user_sessions, users
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.shared.identity import TrustedActor

NOW = datetime(2031, 1, 2, 3, 4, 5)
SECRET = "task-identity-test-secret"


class TrackingSession(Session):
    """Track successful Session.close calls without replacing SQL behavior."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.closed_successfully = False

    def close(self) -> None:
        super().close()
        self.closed_successfully = True


class FailingAcknowledgementUnitOfWork:
    """Delegate every database operation and fail after a selected real action."""

    def __init__(self, delegate: SqlAlchemyAuthenticationUnitOfWork, stage: str) -> None:
        self._delegate = delegate
        self._stage = stage

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def commit(self) -> None:
        self._delegate.commit()
        if self._stage == "commit":
            raise RuntimeError("authentication commit acknowledgement lost")

    def close(self) -> None:
        self._delegate.close()
        if self._stage == "close":
            raise RuntimeError("authentication close acknowledgement lost")


def make_harness(path: Path, *, failure_stage: str | None = None):
    engine = create_engine(f"sqlite:///{path}", future=True)
    metadata.create_all(engine)
    session_factory = sessionmaker(
        engine, class_=TrackingSession, expire_on_commit=False, future=True
    )
    auth_sessions: list[TrackingSession] = []
    codec = JoseTokenCodec(SECRET)

    def auth_unit_of_work():
        session = session_factory()
        auth_sessions.append(session)
        delegate = SqlAlchemyAuthenticationUnitOfWork(session)
        if failure_stage is None:
            return delegate
        return FailingAcknowledgementUnitOfWork(delegate, failure_stage)

    runtime = AuthenticationRuntime(
        session_factory=session_factory,
        secret_key=SECRET,
        token_codec=codec,
        clock=lambda: NOW,
        epoch=lambda: NOW.replace(tzinfo=timezone.utc).timestamp(),
    )
    resolvers = create_task_identity_resolvers(
        runtime, unit_of_work_factory=auth_unit_of_work
    )
    return engine, session_factory, auth_sessions, codec, resolvers


def seed_user(
    engine,
    *,
    user_id: str,
    is_superuser: bool,
    membership_type: str,
    membership_expires_at: datetime | None,
) -> str:
    jti = f"jti-{user_id}"
    with engine.begin() as connection:
        connection.execute(
            insert(users).values(
                id=user_id,
                username=f"name-{user_id}",
                email=f"{user_id}@example.test",
                hashed_password="test-hash",
                is_superuser=is_superuser,
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
                is_active=True,
                created_at=NOW - timedelta(days=1),
                last_seen_at=NOW - timedelta(hours=1),
                revoked_at=None,
            )
        )
    return jti


def bearer(codec: JoseTokenCodec, user_id: str, jti: str) -> str:
    expires = int((NOW + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    return f"Bearer {codec.encode({'sub': user_id, 'jti': jti, 'exp': expires})}"


def test_active_and_admin_return_after_real_auth_sql_session_closes(tmp_path: Path) -> None:
    engine, session_factory, auth_sessions, codec, resolvers = make_harness(
        tmp_path / "task-identity.sqlite"
    )
    try:
        active_jti = seed_user(
            engine,
            user_id="active-user",
            is_superuser=False,
            membership_type="premium",
            membership_expires_at=NOW + timedelta(days=1),
        )
        admin_jti = seed_user(
            engine,
            user_id="expired-admin",
            is_superuser=True,
            membership_type="free",
            membership_expires_at=NOW - timedelta(days=1),
        )
        assert auth_sessions == []  # Building the public resolvers is inert.

        opened_business_sessions: list[Session] = []

        def open_business_after_auth(actor: TrustedActor) -> None:
            assert auth_sessions[-1].closed_successfully
            business_session = session_factory()
            opened_business_sessions.append(business_session)
            business_session.close()

        active_actor = resolvers.active(bearer(codec, "active-user", active_jti))
        assert active_actor == TrustedActor(user_id="active-user")
        open_business_after_auth(active_actor)

        admin_actor = resolvers.admin(bearer(codec, "expired-admin", admin_jti))
        assert admin_actor == TrustedActor(user_id="expired-admin")
        open_business_after_auth(admin_actor)

        active_superuser = resolvers.active(
            bearer(codec, "expired-admin", admin_jti)
        )
        assert active_superuser == TrustedActor(user_id="expired-admin")
        open_business_after_auth(active_superuser)
        assert len(opened_business_sessions) == 3

        with engine.connect() as connection:
            persisted = connection.execute(
                select(user_sessions.c.last_seen_at).where(
                    user_sessions.c.id == "session-expired-admin"
                )
            ).scalar_one()
        assert persisted == NOW
    finally:
        engine.dispose()


def test_admin_rejects_a_real_non_superuser_without_opening_business_session(
    tmp_path: Path,
) -> None:
    engine, _session_factory, auth_sessions, codec, resolvers = make_harness(
        tmp_path / "task-identity-non-admin.sqlite"
    )
    try:
        jti = seed_user(
            engine,
            user_id="ordinary-member",
            is_superuser=False,
            membership_type="premium",
            membership_expires_at=NOW + timedelta(days=10),
        )
        business_sessions: list[Session] = []

        with pytest.raises(AuthenticationError) as caught:
            actor = resolvers.admin(bearer(codec, "ordinary-member", jti))
            business_sessions.append(_session_factory())

        assert caught.value.status_code == 403
        assert caught.value.detail == "权限不足，仅管理员可执行此操作"
        assert business_sessions == []
        assert auth_sessions[-1].closed_successfully
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure_stage", ["commit", "close"])
def test_auth_acknowledgement_failure_withholds_actor_and_business_session(
    tmp_path: Path, failure_stage: str
) -> None:
    engine, _session_factory, auth_sessions, codec, resolvers = make_harness(
        tmp_path / f"task-identity-{failure_stage}.sqlite",
        failure_stage=failure_stage,
    )
    try:
        jti = seed_user(
            engine,
            user_id="admin-user",
            is_superuser=True,
            membership_type="premium",
            membership_expires_at=NOW + timedelta(days=1),
        )
        business_sessions: list[Session] = []

        with pytest.raises(RuntimeError, match="authentication .+ acknowledgement lost"):
            actor = resolvers.admin(bearer(codec, "admin-user", jti))
            business_sessions.append(_session_factory())

        assert business_sessions == []
        assert auth_sessions[-1].closed_successfully
        with engine.connect() as connection:
            persisted = connection.execute(
                select(user_sessions.c.last_seen_at).where(
                    user_sessions.c.id == "session-admin-user"
                )
            ).scalar_one()
        # Both injected failures occur after the real authentication SQL commit.
        assert persisted == NOW
    finally:
        engine.dispose()
