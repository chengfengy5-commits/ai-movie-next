"""Composition-level checks for runtime status and series maintenance."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.asset_data.tables import metadata as asset_metadata
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.runtime_status.configuration import RuntimeStatusConfiguration
from haoai_backend.runtime_status.ports import PublicImageFetchResult
from haoai_backend.series_access.tables import (
    metadata as series_access_metadata,
    team_members,
)
from haoai_backend.series_data.tables import (
    chapters,
    metadata as series_metadata,
    series,
)
from haoai_backend.series_maintenance.persistence import metadata as maintenance_metadata


SECRET = "composition-test-secret"
NOW = datetime(2026, 10, 14, 12, 0, 0)


def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: Any = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://composition.test",
        ) as client:
            kwargs = {"headers": headers}
            if body is not None:
                kwargs["json"] = body
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def _tracked_session_factory(
    engine,
    events: list[tuple[str, str, int]],
    kind: str,
):
    class TrackedSession(Session):
        def close(self) -> None:
            events.append(("close", kind, threading.get_ident()))
            super().close()

    maker = sessionmaker(
        engine,
        class_=TrackedSession,
        expire_on_commit=False,
        future=True,
    )

    def create_session() -> TrackedSession:
        events.append(("open", kind, threading.get_ident()))
        return maker()

    return create_session


@pytest.fixture
def composition_system(tmp_path: Path):
    events: list[tuple[str, str, int]] = []
    probe_calls: list[str] = []
    engine = create_engine(
        f"sqlite:///{tmp_path / 'app-composition.sqlite3'}",
        future=True,
    )
    authentication_metadata.create_all(engine)
    series_metadata.create_all(engine, tables=[series, chapters])
    series_access_metadata.create_all(engine, tables=[team_members])
    asset_metadata.create_all(engine)
    maintenance_metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {
                    "id": "free-user",
                    "username": "free-user",
                    "email": "free-user@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW - timedelta(days=30),
                    "password_updated_at": None,
                },
                {
                    "id": "expired-user",
                    "username": "expired-user",
                    "email": "expired-user@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "premium",
                    "membership_expires_at": NOW - timedelta(days=1),
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW - timedelta(days=30),
                    "password_updated_at": None,
                },
                {
                    "id": "member-user",
                    "username": "member-user",
                    "email": "member-user@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "premium",
                    "membership_expires_at": NOW + timedelta(days=30),
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW - timedelta(days=30),
                    "password_updated_at": None,
                },
            ],
        )
        connection.execute(
            insert(user_sessions).values(
                id="revoked-session",
                user_id="free-user",
                jti="revoked-jti",
                user_agent=None,
                ip_address=None,
                device_name=None,
                is_active=False,
                created_at=NOW - timedelta(days=2),
                last_seen_at=NOW - timedelta(days=2),
                revoked_at=NOW - timedelta(days=1),
            )
        )
        connection.execute(
            insert(series).values(
                id="series-a",
                user_id="member-user",
                name="Composition series",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=NOW - timedelta(days=1),
                updated_at=NOW,
            )
        )

    authentication_sessions = _tracked_session_factory(engine, events, "auth")
    business_sessions = _tracked_session_factory(engine, events, "business")
    runtime = AuthenticationRuntime(
        session_factory=authentication_sessions,
        secret_key=SECRET,
        clock=lambda: NOW,
        epoch=lambda: NOW.timestamp(),
    )
    fetch_calls: list[tuple[str, int]] = []

    class ImageFetcher:
        async def fetch(
            self,
            url: str,
            *,
            max_bytes: int,
            timeout_seconds: int,
            allowed_content_prefixes: Sequence[str],
        ) -> PublicImageFetchResult:
            assert url == "https://images.example.test/cover.png"
            assert max_bytes == 10 * 1024 * 1024
            assert timeout_seconds == 30
            assert allowed_content_prefixes == ("image/",)
            assert events and events[-1][:2] == ("close", "auth")
            thread_id = threading.get_ident()
            events.append(("fetch", "image", thread_id))
            fetch_calls.append((url, thread_id))
            return PublicImageFetchResult(b"image-bytes", "image/png")

    async def storage_probe() -> None:
        probe_calls.append("storage")

    def database_probe() -> None:
        probe_calls.append("database")

    app = create_app(
        session_factory=business_sessions,
        authentication_runtime=runtime,
        runtime_status_image_fetcher=ImageFetcher(),
        runtime_status_database_probe=database_probe,
        runtime_status_storage_probe=storage_probe,
    )

    codec = JoseTokenCodec(SECRET)

    def token_for(
        user_id: str,
        *,
        jti: str | None = None,
        expired: bool = False,
    ) -> str:
        expiration = datetime.now(timezone.utc) + timedelta(days=1)
        if expired:
            expiration = datetime.now(timezone.utc) - timedelta(days=1)
        claims: dict[str, Any] = {
            "sub": user_id,
            "exp": int(expiration.timestamp()),
        }
        if jti is not None:
            claims["jti"] = jti
        return codec.encode(claims)

    tokens = {
        "free": token_for("free-user"),
        "expired_membership": token_for("expired-user"),
        "member": token_for("member-user"),
        "expired_jwt": token_for("member-user", expired=True),
        "revoked": token_for("free-user", jti="revoked-jti"),
    }
    events.clear()
    system = {
        "app": app,
        "engine": engine,
        "events": events,
        "fetch_calls": fetch_calls,
        "probe_calls": probe_calls,
        "tokens": tokens,
    }
    try:
        yield system
    finally:
        engine.dispose()


def test_default_status_routes_keep_legacy_wire_shape_and_readiness_fails_closed(
    composition_system,
) -> None:
    app = composition_system["app"]

    root = _request(app, "GET", "/")
    health = _request(app, "GET", "/health")
    ready = _request(app, "GET", "/ready")

    assert root.status_code == 200
    assert root.json() == {
        "name": "Hao AI Backend",
        "version": "0.1.0",
        "status": "running",
        "docs_url": "/docs",
        "admin_url": "/admin",
    }
    assert health.status_code == 200
    assert health.json() == {"status": "healthy", "service": "Hao AI Backend"}
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready"}
    assert composition_system["probe_calls"] == []

    class UnusedImageFetcher:
        async def fetch(
            self,
            url: str,
            *,
            max_bytes: int,
            timeout_seconds: int,
            allowed_content_prefixes: Sequence[str],
        ) -> PublicImageFetchResult:
            raise AssertionError("the readiness route must not fetch images")

    disabled_readiness = RuntimeStatusConfiguration(
        service_name="Hao AI Backend",
        version="0.1.0",
        readiness_initialized=False,
        object_storage_configured=False,
        probe_timeout_seconds=1.0,
        ready_cache_seconds=5.0,
        initial_failure_backoff_seconds=1.0,
        max_failure_backoff_seconds=30.0,
    )
    uninitialized_app = create_app(
        runtime_status_configuration=disabled_readiness,
        runtime_status_image_fetcher=UnusedImageFetcher(),
    )
    uninitialized_ready = _request(uninitialized_app, "GET", "/ready")
    assert uninitialized_ready.status_code == 200
    assert uninitialized_ready.json() == {"status": "ready"}


def test_runtime_proxy_uses_account_only_jwt_auth_and_closes_before_fetch(
    composition_system,
) -> None:
    app = composition_system["app"]
    events = composition_system["events"]
    fetch_calls = composition_system["fetch_calls"]
    tokens = composition_system["tokens"]
    url = "/api/chat/proxy-image?url=https%3A%2F%2Fimages.example.test%2Fcover.png"

    for name in ("free", "expired_membership"):
        events.clear()
        response = _request(app, "GET", url, token=tokens[name])
        assert response.status_code == 200
        assert response.content == b"image-bytes"
        assert [event[:2] for event in events] == [
            ("open", "auth"),
            ("close", "auth"),
            ("fetch", "image"),
        ]
        assert events[-1][:2] == ("fetch", "image")
        assert events[-2][:2] == ("close", "auth")
        assert events[-2][2] != events[-1][2]

    successful_fetches = len(fetch_calls)
    for token in (None, tokens["expired_jwt"], tokens["revoked"]):
        events.clear()
        response = _request(app, "GET", url, token=token)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert not any(event[0] == "fetch" for event in events)
    assert len(fetch_calls) == successful_fetches


def test_series_maintenance_uses_membership_auth_and_request_scoped_sqlite(
    composition_system,
) -> None:
    app = composition_system["app"]
    events = composition_system["events"]
    tokens = composition_system["tokens"]
    path = "/api/series/series-a/assets/duplicates?type=scene"

    for token in (tokens["free"], tokens["expired_membership"]):
        events.clear()
        response = _request(app, "GET", path, token=token)
        assert response.status_code == 403
        assert [event[:2] for event in events] == [
            ("open", "auth"),
            ("close", "auth"),
        ]

    events.clear()
    missing_header = _request(app, "GET", path)
    assert missing_header.status_code == 401
    assert missing_header.headers["www-authenticate"] == "Bearer"
    assert not any(event[:2] == ("open", "business") for event in events)

    events.clear()
    invalid_body = _request(
        app,
        "POST",
        "/api/series/series-a/assets/orphan-check",
        token=tokens["member"],
        body={"items": [{"type": "character"}]},
    )
    assert invalid_body.status_code == 422
    assert not any(event[:2] == ("open", "business") for event in events)

    events.clear()
    response = _request(app, "GET", path, token=tokens["member"])
    assert response.status_code == 200
    assert response.json() == {
        "type": "scene",
        "total": 0,
        "ignored_total": 0,
        "groups": [],
    }
    auth_close = next(
        index
        for index, event in enumerate(events)
        if event[:2] == ("close", "auth")
    )
    business_open = next(
        index
        for index, event in enumerate(events)
        if event[:2] == ("open", "business")
    )
    business_close = next(
        index
        for index, event in enumerate(events)
        if event[:2] == ("close", "business")
    )
    assert auth_close < business_open < business_close
    assert events[business_open][2] == events[business_close][2]


def test_unwired_routes_remain_inert_and_register_exact_new_identities() -> None:
    session_calls: list[str] = []

    def session_factory():
        session_calls.append("open")
        raise AssertionError("unwired routes must not create sessions")

    app = create_app(session_factory=session_factory)
    routes = {
        (method, route.path, route.name)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    expected = {
        ("GET", "/api/chat/proxy-image", "proxy_image"),
        ("GET", "/", "read_root"),
        ("GET", "/health", "health_check"),
        ("GET", "/ready", "ready_check"),
        ("POST", "/api/series/{series_id}/assets/orphan-check", "check_orphans"),
        ("GET", "/api/series/{series_id}/assets/duplicates", "list_duplicates"),
        ("POST", "/api/series/{series_id}/assets/duplicates/ignore", "ignore_duplicates"),
        ("GET", "/api/series/{series_id}/assets/duplicates/ignored", "list_ignored"),
        ("POST", "/api/series/{series_id}/assets/merge", "merge_assets"),
        ("POST", "/api/storyboard/extract-characters-stream", "extract_characters_stream"),
        ("POST", "/api/storyboard/judge-character-appearance-stream", "judge_character_appearance_stream"),
        ("POST", "/api/storyboard/extract-scenes-stream", "extract_scenes_stream"),
        ("POST", "/api/storyboard/judge-scene-appearance-stream", "judge_scene_appearance_stream"),
        ("POST", "/api/storyboard/extract-props-stream", "extract_props_stream"),
        ("POST", "/api/storyboard/judge-prop-appearance-stream", "judge_prop_appearance_stream"),
    }
    assert len(routes) == 242
    assert expected <= routes
    series_response = _request(
        app,
        "GET",
        "/api/series/series-a/assets/duplicates",
    )
    image_response = _request(
        app,
        "GET",
        "/api/chat/proxy-image?url=https%3A%2F%2Fimages.example.test%2Fcover.png",
    )
    assert series_response.status_code == 503
    assert image_response.status_code == 503
    assert session_calls == []
