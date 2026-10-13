"""ASGI coverage for upload responses, authentication order, and worker I/O."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from jose import jwt
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)
from haoai_backend.upload_files.authentication import create_upload_file_identity_resolver
from haoai_backend.upload_files.domain import UploadFileActor
from haoai_backend.upload_files.errors import UploadFilesUnavailable
from haoai_backend.upload_files.http import build_upload_files_router
from haoai_backend.upload_files.local_files import LocalApprovedFileReader
from haoai_backend.upload_files.persistence import (
    SqlAlchemyUploadDirectoryProvider,
    metadata as upload_files_metadata,
    system_configs,
)

SECRET = "upload-files-asgi-test-secret"
NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.info["events"].append(
            (self.info["kind"], "open", threading.get_ident())
        )

    def close(self) -> None:
        self.info["events"].append(
            (self.info["kind"], "close", threading.get_ident())
        )
        super().close()


def _request(app: FastAPI, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://uploads.test",
        ) as client:
            return await client.get(path, headers=headers)

    return asyncio.run(send())


def _app(
    *,
    resolver,
    directory_provider,
    file_reader=None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_upload_files_router(
            directory_provider=directory_provider,
            file_reader=file_reader or LocalApprovedFileReader(),
            resolve_active_actor=resolver,
        )
    )
    return app


def test_file_response_streams_a_url_encoded_owned_upload(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    expected = b"owned test upload\n"
    (upload_root / "字幕 角色.txt").write_bytes(expected)
    events: list[str] = []

    class Provider:
        def get_upload_directory(self) -> Path:
            events.append("directory")
            return upload_root

    def resolve_actor(_request: Request) -> UploadFileActor:
        events.append("authenticated")
        return UploadFileActor(user_id="member")

    app = _app(resolver=resolve_actor, directory_provider=Provider())
    response = _request(app, f"/uploads/{quote('字幕 角色.txt', safe='')}")

    assert response.status_code == 200
    assert response.content == expected
    assert response.headers["content-length"] == str(len(expected))
    assert events == ["authenticated", "directory"]


def test_file_response_mime_constructor_runs_on_a_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import haoai_backend.upload_files.http as upload_files_http

    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    (upload_root / "image.png").write_bytes(b"owned test image")
    event_loop_thread = threading.get_ident()
    constructor_threads: list[int] = []
    original_file_response = upload_files_http.FileResponse

    def record_constructor_thread(*args: Any, **kwargs: Any):
        constructor_threads.append(threading.get_ident())
        return original_file_response(*args, **kwargs)

    monkeypatch.setattr(
        upload_files_http,
        "FileResponse",
        record_constructor_thread,
    )

    def resolve_actor(_request: Request) -> UploadFileActor:
        return UploadFileActor(user_id="member")

    class Provider:
        def get_upload_directory(self) -> Path:
            return upload_root

    app = _app(resolver=resolve_actor, directory_provider=Provider())
    response = _request(app, "/uploads/image.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(constructor_threads) == 1
    assert constructor_threads[0] != event_loop_thread


def test_authentication_errors_precede_path_and_business_storage_checks() -> None:
    events: list[str] = []

    class UnusedProvider:
        def get_upload_directory(self) -> Path:
            events.append("directory")
            raise AssertionError("business storage must not run before authentication")

    def reject_member(_request: Request) -> UploadFileActor:
        events.append("authentication")
        from haoai_backend.authentication.errors import credentials_error

        raise credentials_error()

    app = _app(resolver=reject_member, directory_provider=UnusedProvider())
    response = _request(app, "/uploads/%2E%2E%2Fprivate.txt")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert events == ["authentication"]


def test_missing_auth_resolver_fails_closed_without_storage_access() -> None:
    class UnusedProvider:
        def get_upload_directory(self) -> Path:
            raise AssertionError("unwired route must not access storage")

    app = _app(resolver=None, directory_provider=UnusedProvider())
    response = _request(app, "/uploads/file.txt")

    assert response.status_code == 503
    assert response.json()["detail"] == UploadFilesUnavailable.detail


def _build_real_auth_app(
    path: Path,
    upload_root: Path,
) -> tuple[Engine, FastAPI, list[tuple[str, str, int]], dict[str, str]]:
    engine = create_engine(f"sqlite:///{path}", future=True)
    authentication_metadata.create_all(engine)
    upload_files_metadata.create_all(engine)
    events: list[tuple[str, str, int]] = []

    def factory_for(kind: str):
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def factory() -> ObservedSession:
            return maker()

        return factory

    auth_factory = factory_for("auth")
    business_factory = factory_for("business")
    with Session(engine, future=True) as session:
        for user_id, membership, expires_at in (
            ("member", "premium", None),
            ("free", "free", None),
            ("expired", "premium", NOW - timedelta(seconds=1)),
        ):
            session.execute(
                insert(users).values(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.test",
                    hashed_password="unused",
                    is_superuser=False,
                    membership_type=membership,
                    membership_expires_at=expires_at,
                    avatar_url=None,
                    bio=None,
                    created_at=NOW - timedelta(days=1),
                    password_updated_at=NOW - timedelta(days=1),
                )
            )
        session.execute(
            insert(user_sessions).values(
                [
                    {
                        "id": "member-session",
                        "user_id": "member",
                        "jti": "member-jti",
                        "user_agent": "test",
                        "ip_address": "127.0.0.1",
                        "device_name": "test",
                        "is_active": True,
                        "created_at": NOW - timedelta(hours=1),
                        "last_seen_at": NOW - timedelta(minutes=1),
                        "revoked_at": None,
                    },
                    {
                        "id": "revoked-session",
                        "user_id": "member",
                        "jti": "revoked-jti",
                        "user_agent": "test",
                        "ip_address": "127.0.0.1",
                        "device_name": "test",
                        "is_active": False,
                        "created_at": NOW - timedelta(hours=1),
                        "last_seen_at": NOW - timedelta(minutes=1),
                        "revoked_at": NOW - timedelta(minutes=1),
                    },
                    {
                        "id": "free-session",
                        "user_id": "free",
                        "jti": "free-jti",
                        "user_agent": "test",
                        "ip_address": "127.0.0.1",
                        "device_name": "test",
                        "is_active": True,
                        "created_at": NOW - timedelta(hours=1),
                        "last_seen_at": NOW - timedelta(minutes=1),
                        "revoked_at": None,
                    },
                ]
            )
        )
        session.execute(
            insert(system_configs).values(
                id="upload-config",
                upload_dir=str(upload_root),
            )
        )
        session.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: NOW,
        epoch=lambda: NOW.replace(tzinfo=timezone.utc).timestamp(),
    )
    auth_uow_factory = lambda: SqlAlchemyAuthenticationUnitOfWork(auth_factory())
    service = AuthenticationService(runtime, auth_uow_factory)
    resolve_identity = create_upload_file_identity_resolver(service)

    def resolve_actor(request: Request) -> UploadFileActor:
        return resolve_identity(request.headers.get("authorization", ""))

    provider = SqlAlchemyUploadDirectoryProvider(business_factory, str(upload_root))

    class TrackingReader(LocalApprovedFileReader):
        def read_approved_file(self, upload_directory, file_path):
            events.append(("filesystem", "resolve", threading.get_ident()))
            return super().read_approved_file(upload_directory, file_path)

    app = _app(
        resolver=resolve_actor,
        directory_provider=provider,
        file_reader=TrackingReader(),
    )
    tokens: dict[str, str] = {}
    for user_id, jti in (
        ("member", "member-jti"),
        ("member", "revoked-jti"),
        ("free", "free-jti"),
        ("expired", "expired-jti"),
    ):
        expiry = int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp())
        tokens[jti] = jwt.encode(
            {"sub": user_id, "jti": jti, "exp": expiry},
            SECRET,
            algorithm="HS256",
        )
    return engine, app, events, tokens


def test_real_jwt_membership_precedes_business_sql_and_file_stat(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    (upload_root / "ready.txt").write_text("ready", encoding="utf-8")
    engine, app, events, tokens = _build_real_auth_app(
        tmp_path / "upload-files.sqlite",
        upload_root,
    )
    try:
        invalid_without_auth = _request(app, "/uploads/%2E%2E%2Foutside.txt")
        free_member = _request(
            app,
            "/uploads/%2E%2E%2Foutside.txt",
            {"Authorization": f"Bearer {tokens['free-jti']}"},
        )
        revoked = _request(
            app,
            "/uploads/%2E%2E%2Foutside.txt",
            {"Authorization": f"Bearer {tokens['revoked-jti']}"},
        )
        expired_member = _request(
            app,
            "/uploads/%2E%2E%2Foutside.txt",
            {"Authorization": f"Bearer {tokens['expired-jti']}"},
        )
        rejected_path = _request(
            app,
            "/uploads/%2E%2E%2Foutside.txt",
            {"Authorization": f"Bearer {tokens['member-jti']}"},
        )
        accepted = _request(
            app,
            "/uploads/ready.txt",
            {"Authorization": f"Bearer {tokens['member-jti']}"},
        )

        assert invalid_without_auth.status_code == 401
        assert invalid_without_auth.headers["www-authenticate"] == "Bearer"
        assert free_member.status_code == 403
        assert revoked.status_code == 401
        assert revoked.headers["www-authenticate"] == "Bearer"
        assert expired_member.status_code == 403
        assert rejected_path.status_code == 403
        assert accepted.status_code == 200
        assert accepted.content == b"ready"

        business_sessions = [
            event for event in events if event[:2] == ("business", "open")
        ]
        assert len(business_sessions) == 2
        for index, event in enumerate(events):
            if event[:2] == ("business", "open"):
                prior_auth_closes = [
                    position
                    for position, candidate in enumerate(events[:index])
                    if candidate[:2] == ("auth", "close")
                ]
                assert prior_auth_closes
        assert all(event[2] != threading.get_ident() for event in events)
        filesystem_events = [
            event for event in events if event[:2] == ("filesystem", "resolve")
        ]
        assert len(filesystem_events) == 2
        assert all(event[2] != threading.get_ident() for event in filesystem_events)
    finally:
        engine.dispose()
