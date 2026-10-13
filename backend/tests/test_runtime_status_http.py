"""HTTP behavior for the isolated legacy runtime-status routes."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.runtime_status.configuration import RuntimeStatusConfiguration
from haoai_backend.runtime_status.http import build_runtime_status_router
from haoai_backend.runtime_status.ports import (
    PublicImageFetchResult,
    RemoteContentTooLarge,
    RemoteContentTypeRejected,
    RemoteFetchError,
    UnsafeRemoteURL,
)


def _configuration(
    *,
    readiness_initialized: bool = True,
    object_storage_configured: bool = True,
    probe_timeout_seconds: float = 1.0,
    ready_cache_seconds: float = 5.0,
    initial_failure_backoff_seconds: float = 1.0,
    max_failure_backoff_seconds: float = 30.0,
) -> RuntimeStatusConfiguration:
    return RuntimeStatusConfiguration(
        service_name="Hao AI test backend",
        version="test-version",
        readiness_initialized=readiness_initialized,
        object_storage_configured=object_storage_configured,
        probe_timeout_seconds=probe_timeout_seconds,
        ready_cache_seconds=ready_cache_seconds,
        initial_failure_backoff_seconds=initial_failure_backoff_seconds,
        max_failure_backoff_seconds=max_failure_backoff_seconds,
    )


def _app(
    *,
    configuration: RuntimeStatusConfiguration | None = None,
    image_fetcher: Any = None,
    resolve_current_user: Any = None,
    database_probe: Any = None,
    object_storage_probe: Any = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_runtime_status_router(
            configuration=configuration or _configuration(),
            image_fetcher=image_fetcher,
            resolve_current_user=resolve_current_user,
            database_probe=database_probe,
            object_storage_probe=object_storage_probe,
        )
    )
    return app


def _requests(
    app: FastAPI,
    paths: Sequence[str],
    *,
    headers: dict[str, str] | None = None,
) -> list[httpx.Response]:
    async def send() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://runtime-status.test",
        ) as client:
            return [
                await client.get(path, headers=headers)
                for path in paths
            ]

    return asyncio.run(send())


def test_router_registers_only_the_four_legacy_get_routes_and_proxy_path() -> None:
    router = build_runtime_status_router(
        configuration=_configuration(),
        image_fetcher=None,
        resolve_current_user=None,
        database_probe=None,
        object_storage_probe=None,
    )
    routes = {
        (route.path, method)
        for route in router.routes
        if hasattr(route, "methods")
        for method in route.methods or ()
        if method not in {"HEAD", "OPTIONS"}
    }

    assert routes == {
        ("/api/chat/proxy-image", "GET"),
        ("/", "GET"),
        ("/health", "GET"),
        ("/ready", "GET"),
    }


def test_root_and_health_preserve_legacy_json_without_constructing_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_NAME", "ambient value must not be read")
    calls: list[str] = []

    def database_probe() -> None:
        calls.append("database")

    async def storage_probe() -> None:
        calls.append("storage")

    app = _app(database_probe=database_probe, object_storage_probe=storage_probe)

    root, health = _requests(app, ["/", "/health"])

    assert root.status_code == 200
    assert root.json() == {
        "name": "Hao AI test backend",
        "version": "test-version",
        "status": "running",
        "docs_url": "/docs",
        "admin_url": "/admin",
    }
    assert health.status_code == 200
    assert health.json() == {
        "status": "healthy",
        "service": "Hao AI test backend",
    }
    assert calls == []


def test_proxy_authenticates_before_missing_query_and_preserves_bearer_header() -> None:
    fetch_calls: list[str] = []

    class Fetcher:
        async def fetch(self, url: str, **_kwargs: Any) -> PublicImageFetchResult:
            fetch_calls.append(url)
            return PublicImageFetchResult(b"image", "image/png")

    def reject_user(_request: Request) -> None:
        raise AuthenticationError(401, "无法验证凭据", authenticate=True)

    rejected = _requests(
        _app(image_fetcher=Fetcher(), resolve_current_user=reject_user),
        ["/api/chat/proxy-image"],
    )[0]

    assert rejected.status_code == 401
    assert rejected.json() == {"detail": "无法验证凭据"}
    assert rejected.headers["www-authenticate"] == "Bearer"
    assert fetch_calls == []

    def accept_user(_request: Request) -> object:
        return object()

    missing_url = _requests(
        _app(image_fetcher=Fetcher(), resolve_current_user=accept_user),
        ["/api/chat/proxy-image"],
    )[0]

    assert missing_url.status_code == 400
    assert missing_url.json() == {"detail": "url 参数不能为空"}
    assert fetch_calls == []


def test_proxy_passes_legacy_limits_and_returns_upstream_bytes_and_content_type() -> None:
    calls: list[tuple[str, int, int, tuple[str, ...]]] = []

    class Fetcher:
        async def fetch(
            self,
            url: str,
            *,
            max_bytes: int,
            timeout_seconds: int,
            allowed_content_prefixes: Sequence[str],
        ) -> PublicImageFetchResult:
            calls.append(
                (url, max_bytes, timeout_seconds, tuple(allowed_content_prefixes))
            )
            return PublicImageFetchResult(b"fake png bytes", "image/png")

    def accept_user(_request: Request) -> object:
        return object()

    response = _requests(
        _app(image_fetcher=Fetcher(), resolve_current_user=accept_user),
        ["/api/chat/proxy-image?url=https%3A%2F%2Fimages.test%2Fcover.png"],
    )[0]

    assert response.status_code == 200
    assert response.content == b"fake png bytes"
    assert response.headers["content-type"] == "image/png"
    assert calls == [
        ("https://images.test/cover.png", 10 * 1024 * 1024, 30, ("image/",))
    ]


@pytest.mark.parametrize(
    ("error_type", "message", "status_code", "detail"),
    [
        (UnsafeRemoteURL, "禁止访问内网", 400, "禁止访问内网"),
        (RemoteContentTypeRejected, "upstream detail", 415, "目标资源不是图片"),
        (RemoteContentTooLarge, "upstream detail", 413, "图片大小超过 10MB"),
        (RemoteFetchError, "secret upstream detail", 502, "图片代理请求失败"),
    ],
)
def test_proxy_maps_upstream_failures_to_legacy_http_errors(
    error_type: type[Exception],
    message: str,
    status_code: int,
    detail: str,
) -> None:
    class Fetcher:
        async def fetch(self, _url: str, **_kwargs: Any) -> PublicImageFetchResult:
            raise error_type(message)

    def accept_user(_request: Request) -> object:
        return object()

    response = _requests(
        _app(image_fetcher=Fetcher(), resolve_current_user=accept_user),
        ["/api/chat/proxy-image?url=https%3A%2F%2Fimages.test%2Fcover.png"],
    )[0]

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


def test_ready_uses_only_isolated_sqlite_and_fake_storage_then_caches_success(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "readiness.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE readiness_probe (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO readiness_probe (value) VALUES (1)")

    events: list[str] = []

    def database_probe() -> None:
        events.append("database")
        with sqlite3.connect(
            f"file:{database_path}?mode=ro",
            uri=True,
        ) as connection:
            assert connection.execute(
                "SELECT value FROM readiness_probe"
            ).fetchone() == (1,)

    async def storage_probe() -> None:
        events.append("storage")

    app = _app(
        database_probe=database_probe,
        object_storage_probe=storage_probe,
    )
    assert events == []

    first, second = _requests(app, ["/ready", "/ready"])

    assert first.status_code == 200
    assert first.json() == {"status": "ready"}
    assert second.status_code == 200
    assert second.json() == {"status": "ready"}
    assert events == ["database", "storage"]


def test_health_stays_healthy_when_database_is_not_ready_and_reason_is_hidden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []

    def database_probe() -> None:
        events.append("database")
        raise RuntimeError("database credential detail")

    async def storage_probe() -> None:
        events.append("storage")

    app = _app(
        database_probe=database_probe,
        object_storage_probe=storage_probe,
    )

    ready, health, cached_ready = _requests(
        app,
        ["/ready", "/health", "/ready"],
    )

    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready"}
    assert health.status_code == 200
    assert health.json() == {
        "status": "healthy",
        "service": "Hao AI test backend",
    }
    assert cached_ready.status_code == 503
    assert cached_ready.json() == {"status": "not_ready"}
    assert events == ["database"]
    assert "database credential detail" not in caplog.text


def test_missing_storage_configuration_is_not_ready_without_probing_database() -> None:
    events: list[str] = []

    def database_probe() -> None:
        events.append("database")

    async def storage_probe() -> None:
        events.append("storage")

    app = _app(
        configuration=_configuration(object_storage_configured=False),
        database_probe=database_probe,
        object_storage_probe=storage_probe,
    )

    response = _requests(app, ["/ready"])[0]

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert events == []


def test_uninitialized_readiness_preserves_legacy_ready_wire_without_probe() -> None:
    events: list[str] = []

    def database_probe() -> None:
        events.append("database")

    async def storage_probe() -> None:
        events.append("storage")

    app = _app(
        configuration=_configuration(readiness_initialized=False),
        database_probe=database_probe,
        object_storage_probe=storage_probe,
    )

    response = _requests(app, ["/ready"])[0]

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert events == []


def test_production_default_without_r2_or_database_probe_fails_closed() -> None:
    app = _app(
        configuration=_configuration(
            readiness_initialized=True,
            object_storage_configured=False,
        ),
        database_probe=None,
        object_storage_probe=None,
    )

    response = _requests(app, ["/ready"])[0]

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_missing_database_port_fails_closed_after_explicit_initialization() -> None:
    app = _app(database_probe=None, object_storage_probe=None)

    response = _requests(app, ["/ready"])[0]

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
