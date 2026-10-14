"""Whole-app integration checks for authenticated media upload routes."""

from __future__ import annotations

import asyncio
import os
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.requests import Request

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users as authentication_users,
)
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.media_storage.transfer_types import (
    R2Configuration,
    SecretValue,
    WriteReceipt,
)
from haoai_backend.media_uploads import MediaUploadApplication
from haoai_backend.media_uploads.adapters import R2MediaUploadStorage
from haoai_backend.media_uploads.http import PROXY_DOWNLOAD_DISABLED_DETAIL
from haoai_backend.media_uploads.ports import FetchedPublicMedia

SECRET = "media-uploads-app-integration-synthetic-secret"
MEDIA_ROUTES = {
    ("POST", "/api/upload/image", "upload_image"),
    ("POST", "/api/upload/audio", "upload_audio"),
    ("POST", "/api/upload/video", "upload_video"),
    ("POST", "/api/upload/retry-oss", "retry_oss_upload"),
    ("GET", "/api/chat/proxy-download", "proxy_download"),
}


class _ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.close_count = 0

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.info["events"].append(
            (f"{self.info['kind']}-execute", threading.get_ident())
        )
        return super().execute(*args, **kwargs)

    def close(self) -> None:
        self.close_count += 1
        self.info["events"].append(
            (f"{self.info['kind']}-close", threading.get_ident())
        )
        super().close()


@dataclass
class _AuthenticationFixture:
    engine: Any
    runtime: AuthenticationRuntime
    business_session_factory: Callable[[], Session]
    events: list[tuple[str, int]]
    auth_sessions: list[_ObservedSession]
    business_sessions: list[_ObservedSession]
    token_for: Callable[..., str]


@pytest.fixture
def authentication_fixture(tmp_path: Path) -> Iterator[_AuthenticationFixture]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'media-uploads.sqlite'}",
        future=True,
    )
    authentication_metadata.create_all(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    events: list[tuple[str, int]] = []
    auth_sessions: list[_ObservedSession] = []
    business_sessions: list[_ObservedSession] = []

    def make_factory(
        kind: str, opened: list[_ObservedSession]
    ) -> Callable[[], _ObservedSession]:
        maker = sessionmaker(
            engine,
            class_=_ObservedSession,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def open_session() -> _ObservedSession:
            events.append((f"{kind}-open", threading.get_ident()))
            session = maker()
            opened.append(session)
            return session

        return open_session

    auth_factory = make_factory("auth", auth_sessions)
    business_factory = make_factory("business", business_sessions)
    memberships = {
        "member": ("premium", None, True),
        "free": ("free", None, True),
        "membership-expired": ("premium", now - timedelta(days=1), True),
        "session-revoked": ("premium", None, False),
        "jwt-expired": ("premium", None, True),
    }
    user_rows = []
    session_rows = []
    for user_id, (
        membership_type,
        expires_at,
        session_active,
    ) in memberships.items():
        user_rows.append(
            {
                "id": user_id,
                "username": user_id,
                "email": f"{user_id}@example.test",
                "hashed_password": "synthetic-integration-fixture",
                "is_superuser": False,
                "membership_type": membership_type,
                "membership_expires_at": expires_at,
                "avatar_url": None,
                "bio": None,
                "created_at": now - timedelta(days=10),
                "password_updated_at": now - timedelta(days=10),
            }
        )
        session_rows.append(
            {
                "id": f"session-{user_id}",
                "user_id": user_id,
                "jti": f"jti-{user_id}",
                "user_agent": "local integration test",
                "ip_address": "127.0.0.1",
                "device_name": "test",
                "is_active": session_active,
                "created_at": now - timedelta(hours=1),
                "last_seen_at": now - timedelta(minutes=5),
                "revoked_at": None if session_active else now - timedelta(minutes=1),
            }
        )
    with engine.begin() as connection:
        connection.execute(insert(authentication_users), user_rows)
        connection.execute(insert(user_sessions), session_rows)

    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: now,
    )
    codec = JoseTokenCodec(SECRET)

    def token_for(user_id: str, expired: bool = False) -> str:
        expiry = now - timedelta(minutes=1) if expired else now + timedelta(hours=1)
        return codec.encode(
            {
                "sub": user_id,
                "jti": f"jti-{user_id}",
                "exp": expiry.replace(tzinfo=timezone.utc),
            }
        )

    yield _AuthenticationFixture(
        engine=engine,
        runtime=runtime,
        business_session_factory=business_factory,
        events=events,
        auth_sessions=auth_sessions,
        business_sessions=business_sessions,
        token_for=token_for,
    )
    engine.dispose()


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> SecretValue:
        return SecretValue(self._value)


class _FakeS3Client:
    def __init__(self, events: list[tuple[str, int]]) -> None:
        self._events = events
        self.calls: list[dict[str, Any]] = []
        self.body_content: list[bytes] = []
        self.close_calls = 0

    def put_object(self, **kwargs: Any) -> WriteReceipt:
        self._events.append(("sdk-put", threading.get_ident()))
        self.calls.append(kwargs)
        body = kwargs["Body"]
        self.body_content.append(body.read())
        return WriteReceipt(kwargs["Key"], 200, "synthetic-request")

    def close(self) -> None:
        self.close_calls += 1


class _FakeS3Factory:
    def __init__(self, events: list[tuple[str, int]]) -> None:
        self._events = events
        self.clients: list[_FakeS3Client] = []
        self.create_calls: list[tuple[object, object, object]] = []

    def create(
        self,
        settings: object,
        access_key: object,
        secret_key: object,
    ) -> _FakeS3Client:
        self._events.append(("sdk-create", threading.get_ident()))
        self.create_calls.append((settings, access_key, secret_key))
        client = _FakeS3Client(self._events)
        self.clients.append(client)
        return client


class _FakePublicMediaFetcher:
    def __init__(self, events: list[tuple[str, int]]) -> None:
        self._events = events
        self.calls: list[tuple[str, int, float]] = []

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> FetchedPublicMedia:
        self._events.append(("public-fetch", threading.get_ident()))
        self.calls.append((url, max_bytes, timeout_seconds))
        return FetchedPublicMedia(b"synthetic mp4 bytes", "video/mp4")


def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    request_headers = dict(headers or {})
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"

    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://media-uploads-app.test",
        ) as client:
            return await client.request(
                method,
                path,
                headers=request_headers,
                **kwargs,
            )

    return asyncio.run(send())


def _install_body_observers(
    monkeypatch: pytest.MonkeyPatch,
    events: list[tuple[str, int]],
) -> tuple[list[Any], list[Any]]:
    forms: list[Any] = []
    json_requests: list[Request] = []
    original_form = Request.form
    original_json = Request.json
    original_upload_read = StarletteUploadFile.read

    async def observed_form(request: Request, *args: Any, **kwargs: Any) -> Any:
        events.append(("form", threading.get_ident()))
        form = await original_form(request, *args, **kwargs)
        forms.append(form)
        return form

    async def observed_json(request: Request) -> Any:
        events.append(("json", threading.get_ident()))
        json_requests.append(request)
        return await original_json(request)

    async def observed_upload_read(
        upload: StarletteUploadFile, size: int = -1
    ) -> bytes:
        events.append(("file-read", threading.get_ident()))
        return await original_upload_read(upload, size)

    monkeypatch.setattr(Request, "form", observed_form)
    monkeypatch.setattr(Request, "json", observed_json)
    monkeypatch.setattr(StarletteUploadFile, "read", observed_upload_read)
    return forms, json_requests


def _assert_auth_closed_before(
    events: list[tuple[str, int]], start: int, next_event: str
) -> None:
    names = [name for name, _thread in events[start:]]
    assert "auth-execute" in names
    assert "auth-close" in names
    assert next_event in names
    assert names.index("auth-execute") < names.index("auth-close")
    assert names.index("auth-close") < names.index(next_event)


def test_create_app_mounts_authenticated_upload_retry_and_disabled_proxy(
    authentication_fixture: _AuthenticationFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = authentication_fixture
    forms, _json_requests = _install_body_observers(monkeypatch, fixture.events)
    s3_factory = _FakeS3Factory(fixture.events)
    public_fetcher = _FakePublicMediaFetcher(fixture.events)
    identifiers = iter(("image-1", "audio-1", "video-1", "retry-id"))
    configuration = R2Configuration(
        endpoint_url="https://account.storage.test",
        bucket="media-test",
        public_base_url="https://cdn.test",
        transfer_tmp_dir=tmp_path,
        business_type="unused-global-type",
        environment="test",
        connect_timeout_seconds=2,
        read_timeout_seconds=5,
    )
    storage = R2MediaUploadStorage(
        configuration=configuration,
        access_key=_Secret("synthetic-access"),
        secret_key=_Secret("synthetic-secret"),
        client_factory=s3_factory,
        clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
        id_factory=lambda: next(identifiers),
    )
    application = MediaUploadApplication(
        storage=storage,
        public_media_fetcher=public_fetcher,
        managed_origin_map={"https://cdn.test": "https://cdn.test"},
    )
    app = create_app(
        session_factory=fixture.business_session_factory,
        authentication_runtime=fixture.runtime,
        media_upload_application=application,
    )
    registered = {
        (method, route.path, route.name)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert len(registered) == 230
    assert MEDIA_ROUTES <= registered

    upload_cases = (
        (
            "/api/upload/image",
            "cover.jpg",
            b"\xff\xd8\xffsynthetic jpeg",
            "image/jpeg",
            "upload",
            ".jpg",
            "image-1",
            "image/jpeg",
        ),
        (
            "/api/upload/audio",
            "voice.mp3",
            b"synthetic mp3 bytes",
            "audio/mpeg",
            "audio",
            ".mp3",
            "audio-1",
            "audio/mpeg",
        ),
        (
            "/api/upload/video",
            "clip.mp4",
            b"synthetic mp4 bytes",
            "video/mp4",
            "video",
            ".mp4",
            "video-1",
            "video/mp4",
        ),
    )
    main_thread = threading.get_ident()
    for (
        path,
        filename,
        content,
        request_type,
        business_type,
        extension,
        identifier,
        mime,
    ) in upload_cases:
        start = len(fixture.events)
        response = _request(
            app,
            "POST",
            path,
            token=fixture.token_for("member"),
            files={"file": (filename, content, request_type)},
        )
        assert response.status_code == 200, response.text
        expected_key = f"2026/10/14/{business_type}/{identifier}{extension}"
        expected_url = f"https://cdn.test/{expected_key}"
        assert response.json() == {"url": expected_url}
        _assert_auth_closed_before(fixture.events, start, "file-read")
        _assert_auth_closed_before(fixture.events, start, "sdk-create")
        request_events = fixture.events[start:]
        sdk_threads = [
            thread
            for name, thread in request_events
            if name in {"sdk-create", "sdk-put"}
        ]
        assert sdk_threads and all(thread != main_thread for thread in sdk_threads)
        assert len(set(sdk_threads)) == 1

        form = forms[-1]
        upload = form.get("file")
        assert isinstance(upload, StarletteUploadFile)
        assert upload.file.closed
        client = s3_factory.clients[-1]
        sdk_call = client.calls[0]
        assert sdk_call["Bucket"] == "media-test"
        assert sdk_call["Key"] == expected_key
        assert sdk_call["ContentType"] == mime
        assert client.body_content == [content]
        assert sdk_call["Body"].closed
        assert client.close_calls == 1

    managed_url = "https://cdn.test/managed/existing.mp4"
    client_count = len(s3_factory.clients)
    fetch_count = len(public_fetcher.calls)
    managed = _request(
        app,
        "POST",
        "/api/upload/retry-oss",
        token=fixture.token_for("member"),
        json={"url": managed_url},
    )
    assert managed.status_code == 200
    assert managed.json() == {"url": managed_url}
    assert len(s3_factory.clients) == client_count
    assert len(public_fetcher.calls) == fetch_count

    start = len(fixture.events)
    public = _request(
        app,
        "POST",
        "/api/upload/retry-oss",
        token=fixture.token_for("member"),
        json={"url": "https://public.test/clip.mp4"},
    )
    assert public.status_code == 200, public.text
    assert public.json() == {
        "url": "https://cdn.test/2026/10/14/gen/retry-id.mp4"
    }
    assert public_fetcher.calls[-1][0] == "https://public.test/clip.mp4"
    assert public_fetcher.calls[-1][1] == 50 * 1024 * 1024
    _assert_auth_closed_before(fixture.events, start, "public-fetch")
    _assert_auth_closed_before(fixture.events, start, "sdk-create")
    retry_client = s3_factory.clients[-1]
    assert retry_client.calls[0]["Key"] == "2026/10/14/gen/retry-id.mp4"
    assert retry_client.calls[0]["ContentType"] == "video/mp4"
    assert retry_client.body_content == [b"synthetic mp4 bytes"]
    assert retry_client.calls[0]["Body"].closed
    assert retry_client.close_calls == 1

    client_count = len(s3_factory.clients)
    fetch_count = len(public_fetcher.calls)
    proxy = _request(
        app,
        "GET",
        "/api/chat/proxy-download",
        token=fixture.token_for("member"),
    )
    assert proxy.status_code == 403
    assert proxy.json() == {"detail": PROXY_DOWNLOAD_DISABLED_DETAIL}
    assert len(s3_factory.clients) == client_count
    assert len(public_fetcher.calls) == fetch_count
    assert fixture.business_sessions == []


def test_membership_and_authentication_precede_malformed_upload_bodies(
    authentication_fixture: _AuthenticationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = authentication_fixture
    forms, json_requests = _install_body_observers(monkeypatch, fixture.events)
    app = create_app(
        session_factory=fixture.business_session_factory,
        authentication_runtime=fixture.runtime,
    )
    malformed_multipart = {
        "Content-Type": "multipart/form-data; boundary=invalid"
    }
    malformed_json = {"Content-Type": "application/json"}
    cases = (
        (None, "/api/upload/image", b"--invalid", malformed_multipart, 401),
        (
            fixture.token_for("session-revoked"),
            "/api/upload/retry-oss",
            b"{",
            malformed_json,
            401,
        ),
        (
            fixture.token_for("jwt-expired", expired=True),
            "/api/upload/image",
            b"--invalid",
            malformed_multipart,
            401,
        ),
        (
            fixture.token_for("free"),
            "/api/upload/image",
            b"--invalid",
            malformed_multipart,
            403,
        ),
        (
            fixture.token_for("membership-expired"),
            "/api/upload/retry-oss",
            b"{",
            malformed_json,
            403,
        ),
    )
    for token, path, body, headers, expected_status in cases:
        response = _request(
            app,
            "POST",
            path,
            token=token,
            headers=headers,
            content=body,
        )
        assert response.status_code == expected_status, response.text
        if expected_status == 401:
            assert response.headers.get("www-authenticate") == "Bearer"
    assert forms == []
    assert json_requests == []
    assert fixture.business_sessions == []
    assert all(session.close_count == 1 for session in fixture.auth_sessions)

    start = len(fixture.events)
    valid_member_bad_json = _request(
        app,
        "POST",
        "/api/upload/retry-oss",
        token=fixture.token_for("member"),
        headers=malformed_json,
        content=b"{",
    )
    assert valid_member_bad_json.status_code == 422
    _assert_auth_closed_before(fixture.events, start, "json")
    assert fixture.business_sessions == []


def test_default_application_returns_502_and_cold_factory_stays_unconfigured(
    authentication_fixture: _AuthenticationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = authentication_fixture
    forms, _json_requests = _install_body_observers(monkeypatch, fixture.events)
    app = create_app(
        session_factory=fixture.business_session_factory,
        authentication_runtime=fixture.runtime,
    )
    response = _request(
        app,
        "POST",
        "/api/upload/audio",
        token=fixture.token_for("member"),
        files={"file": ("voice.mp3", b"synthetic mp3 bytes", "audio/mpeg")},
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "云端上传失败"}
    upload = forms[-1].get("file")
    assert isinstance(upload, StarletteUploadFile)
    assert upload.file.closed
    assert fixture.business_sessions == []

    def forbidden_dns(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("cold app construction must not perform DNS")

    with monkeypatch.context() as cold_context:
        cold_context.setattr(os, "environ", {})
        cold_context.setattr(socket, "getaddrinfo", forbidden_dns)
        cold_app = create_app()
    forms_before_cold_request = len(forms)
    cold_response = _request(
        cold_app,
        "POST",
        "/api/upload/image",
        headers={"Content-Type": "multipart/form-data; boundary=invalid"},
        content=b"--invalid",
    )
    assert cold_response.status_code == 503
    assert cold_response.json() == {"detail": "认证服务尚未接线"}
    assert len(forms) == forms_before_cold_request
