"""Runtime-status auth and database adapters use isolated SQLite only."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata, user_sessions, users
from haoai_backend.authentication.tokens import ExpiredTokenError, TokenDecodeError
from haoai_backend.runtime_status.authentication import (
    RuntimeStatusActor,
    create_account_only_current_user_resolver,
)
from haoai_backend.runtime_status.configuration import RuntimeStatusConfiguration
from haoai_backend.runtime_status.database import (
    create_generation_schema_readiness_probe,
)
from haoai_backend.runtime_status.http import build_runtime_status_router
from haoai_backend.runtime_status.ports import PublicImageFetchResult


NOW = datetime(2026, 10, 13, 10, 0, 0)


class FakeTokenCodec:
    def __init__(self, claims: Mapping[str, Mapping[str, Any]]) -> None:
        self._claims = {key: dict(value) for key, value in claims.items()}

    def encode(self, claims: Mapping[str, Any]) -> str:
        raise AssertionError("tests only decode supplied bearer tokens")

    def decode(self, token: str) -> dict[str, Any]:
        if token == "expired-jwt":
            raise ExpiredTokenError()
        if token not in self._claims:
            raise TokenDecodeError()
        return dict(self._claims[token])


class TrackingSession(Session):
    def __init__(
        self,
        *args: Any,
        events: list[tuple[str, int]],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._events = events
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self._events.append(("close", threading.get_ident()))
        super().close()


def _user(
    user_id: str,
    *,
    membership_type: str,
    expiry: datetime | None,
) -> dict[str, Any]:
    return {
        "id": user_id,
        "username": user_id,
        "email": f"{user_id}@example.test",
        "hashed_password": "test-hash",
        "is_superuser": False,
        "membership_type": membership_type,
        "membership_expires_at": expiry,
        "avatar_url": None,
        "bio": None,
        "created_at": NOW - timedelta(days=20),
        "password_updated_at": None,
    }


def _config(*, initialized: bool = True) -> RuntimeStatusConfiguration:
    return RuntimeStatusConfiguration(
        service_name="Hao AI test backend",
        version="test-version",
        readiness_initialized=initialized,
        object_storage_configured=True,
        probe_timeout_seconds=1,
        ready_cache_seconds=5,
        initial_failure_backoff_seconds=1,
        max_failure_backoff_seconds=30,
    )


def _send(app: FastAPI, token: str) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://runtime-status.test",
        ) as client:
            return await client.get(
                "/api/chat/proxy-image?url=https%3A%2F%2Fimages.test%2Fcover.png",
                headers={"Authorization": f"Bearer {token}"},
            )

    return asyncio.run(request())


def test_account_only_auth_allows_free_and_expired_membership_and_closes_before_fetch(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, int]] = []
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-status-auth.sqlite3'}")
    metadata.create_all(engine)
    maker = sessionmaker(
        engine,
        class_=TrackingSession,
        expire_on_commit=False,
        future=True,
        events=events,
    )

    def session_factory() -> TrackingSession:
        events.append(("open", threading.get_ident()))
        return maker()

    free_user = _user("free-user", membership_type="free", expiry=None)
    expired_user = _user(
        "expired-member",
        membership_type="premium",
        expiry=NOW - timedelta(days=1),
    )
    with session_factory() as session:
        session.execute(insert(users), [free_user, expired_user])
        session.commit()
    events.clear()

    claims = {
        "free": {"sub": "free-user"},
        "expired-member": {"sub": "expired-member"},
        "expired-jwt": {},
        "revoked": {"sub": "free-user", "jti": "revoked-jti"},
        "stale-password": {"sub": "free-user", "password_version": "stale"},
    }
    revoked_record = {
        "id": "session-revoked",
        "user_id": "free-user",
        "jti": "revoked-jti",
        "user_agent": None,
        "ip_address": None,
        "device_name": None,
        "is_active": False,
        "created_at": NOW - timedelta(days=2),
        "last_seen_at": NOW - timedelta(days=2),
        "revoked_at": NOW - timedelta(days=1),
    }
    with session_factory() as session:
        session.execute(insert(user_sessions).values(**revoked_record))
        session.commit()
    events.clear()

    runtime = AuthenticationRuntime(
        session_factory=session_factory,
        secret_key="isolated-test-secret",
        token_codec=FakeTokenCodec(claims),
        clock=lambda: NOW,
        epoch=lambda: NOW.timestamp(),
    )
    service = AuthenticationService(
        runtime,
        lambda: SqlAlchemyAuthenticationUnitOfWork(session_factory()),
    )
    resolver = create_account_only_current_user_resolver(service)

    class Fetcher:
        async def fetch(self, _url: str, **_kwargs: Any) -> PublicImageFetchResult:
            events.append(("fetch", threading.get_ident()))
            assert events[-2][0] == "close"
            return PublicImageFetchResult(b"image", "image/png")

    app = FastAPI()
    app.include_router(
        build_runtime_status_router(
            configuration=_config(),
            image_fetcher=Fetcher(),
            resolve_current_user=resolver,
            database_probe=None,
            object_storage_probe=None,
        )
    )

    free_response = _send(app, "free")
    expired_membership_response = _send(app, "expired-member")

    assert free_response.status_code == 200
    assert expired_membership_response.status_code == 200
    assert free_response.content == b"image"
    assert expired_membership_response.content == b"image"
    assert [name for name, _thread_id in events].count("fetch") == 2
    for index, (name, thread_id) in enumerate(events):
        if name != "fetch":
            continue
        assert events[index - 1][0] == "close"
        assert events[index - 1][1] != thread_id

    expired_jwt = _send(app, "expired-jwt")
    revoked = _send(app, "revoked")
    stale_password = _send(app, "stale-password")
    assert expired_jwt.status_code == 401
    assert expired_jwt.headers["www-authenticate"] == "Bearer"
    assert revoked.status_code == 401
    assert stale_password.status_code == 401
    assert [name for name, _thread_id in events].count("fetch") == 2
    engine.dispose()


def test_resolver_returns_only_a_minimal_snapshot_after_auth_context_closes() -> None:
    events: list[str] = []
    principal = type("Principal", (), {"user": type("User", (), {"id": "user-1"})()})()

    class Service:
        @contextmanager
        def authenticated(
            self,
            authorization: str,
            *,
            require_membership: bool,
        ) -> Iterator[Any]:
            assert authorization == "Bearer token"
            assert require_membership is False
            events.append("open")
            try:
                yield principal
            finally:
                events.append("close")

    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/chat/proxy-image",
            "headers": [(b"authorization", b"Bearer token")],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
            "http_version": "1.1",
        }
    )

    actor = create_account_only_current_user_resolver(Service())(request)

    assert actor == RuntimeStatusActor("user-1")
    assert events == ["open", "close"]


def test_database_probe_is_lazy_owns_session_and_fails_closed_for_target_schema(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-status-db.sqlite3'}")
    sessions: list[Session] = []
    closed: list[bool] = []

    class TrackingDatabaseSession(Session):
        def close(self) -> None:
            closed.append(True)
            super().close()

    def factory() -> TrackingDatabaseSession:
        session = TrackingDatabaseSession(engine, future=True)
        sessions.append(session)
        return session

    probe = create_generation_schema_readiness_probe(factory)
    assert sessions == []

    with pytest.raises(RuntimeError, match="target generation schema is not ready"):
        probe()

    assert len(sessions) == 1
    assert closed == [True]
    assert not sessions[0].in_transaction()
    engine.dispose()


def test_database_probe_closes_session_when_schema_checker_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from haoai_backend.runtime_status import database

    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-status-db-error.sqlite3'}")
    closed: list[bool] = []

    class ClosingSession(Session):
        def close(self) -> None:
            closed.append(True)
            super().close()

    def fail(_session: Session) -> Any:
        raise RuntimeError("injected schema error")

    monkeypatch.setattr(database, "check_generation_readiness", fail)
    probe = create_generation_schema_readiness_probe(
        lambda: ClosingSession(engine, future=True)
    )

    with pytest.raises(RuntimeError, match="injected schema error"):
        probe()

    assert closed == [True]
    engine.dispose()
