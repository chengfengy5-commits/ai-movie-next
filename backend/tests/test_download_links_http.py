"""ASGI contract tests for the download-link compatibility routes."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from haoai_backend.app import create_app
from haoai_backend.download_links.domain import DISABLED_DOWNLOAD_DETAIL
from haoai_backend.shared.errors import BusinessError
from haoai_backend.download_links.errors import DownloadLinksTooManyItems
from haoai_backend.shared.identity import TrustedActor


class RecordingResolver:
    def __bool__(self) -> bool:
        return False

    def __init__(self, fail_at: int | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_at = fail_at

    async def resolve(self, url: str, filename: str) -> str:
        self.calls.append((url, filename))
        if self.fail_at == len(self.calls):
            raise RuntimeError("resolver failed")
        return f"{url}?signed={len(self.calls)}"


def request(app, method: str, path: str, *, body: Any = None, params=None, actor=True):
    async def send() -> httpx.Response:
        headers = {"x-test-user": "member"} if actor else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://download-links.test",
            trust_env=False,
        ) as client:
            return await client.request(method, path, headers=headers, json=body, params=params)

    return asyncio.run(send())


def trusted_actor(_request) -> TrustedActor:
    return TrustedActor("member")


def test_routes_are_mounted_without_business_storage() -> None:
    session_calls = 0

    def forbidden_session_factory():
        nonlocal session_calls
        session_calls += 1
        raise AssertionError("download routes must not open business sessions")

    app = create_app(
        session_factory=forbidden_session_factory,
        resolve_actor=trusted_actor,
        download_url_resolver=RecordingResolver(),
    )
    paths = {
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if route.path in {"/api/download", "/api/sign-download-urls"}
    }
    assert paths == {("/api/download", "GET"), ("/api/sign-download-urls", "POST")}
    assert session_calls == 0


def test_unwired_actor_returns_503_before_resolver_or_storage() -> None:
    resolver = RecordingResolver()
    session_calls = 0

    def forbidden_session_factory():
        nonlocal session_calls
        session_calls += 1
        raise AssertionError("download routes must not open business sessions")

    app = create_app(session_factory=forbidden_session_factory, download_url_resolver=resolver)
    response = request(
        app,
        "POST",
        "/api/sign-download-urls",
        body={"items": [{"url": "https://example.test/file"}]},
        actor=False,
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "可信身份端口尚未接线"}
    assert resolver.calls == []
    assert session_calls == 0


def test_legacy_download_endpoint_requires_url_then_returns_fixed_403() -> None:
    resolver = RecordingResolver()
    app = create_app(resolve_actor=trusted_actor, download_url_resolver=resolver)
    missing = request(app, "GET", "/api/download")
    assert missing.status_code == 422
    assert resolver.calls == []

    response = request(
        app,
        "GET",
        "/api/download",
        params={"url": "", "filename": "original.txt"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": DISABLED_DOWNLOAD_DETAIL}
    assert resolver.calls == []


def test_default_identity_resolution_preserves_strings_and_empty_batch() -> None:
    app = create_app(resolve_actor=trusted_actor)
    empty = request(app, "POST", "/api/sign-download-urls", body={"items": []})
    assert empty.status_code == 200
    assert empty.json() == {"items": []}

    response = request(
        app,
        "POST",
        "/api/sign-download-urls",
        body={
            "items": [
                {"url": "", "filename": ""},
                {"url": "  ", "filename": "spaces", "ignored": True},
                {"url": "not a URL", "extra": "ignored"},
            ],
            "unknown": "ignored",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"url": "", "filename": "", "signed": False},
            {"url": "  ", "filename": "spaces", "signed": False},
            {"url": "not a URL", "filename": "", "signed": False},
        ]
    }


def test_injected_resolver_runs_in_order_and_keeps_duplicates_and_filenames() -> None:
    resolver = RecordingResolver()
    app = create_app(resolve_actor=trusted_actor, download_url_resolver=resolver)
    response = request(
        app,
        "POST",
        "/api/sign-download-urls",
        body={
            "items": [
                {"url": "https://example.test/a", "filename": "first.txt"},
                {"url": "https://example.test/a", "filename": "second.txt"},
            ]
        },
    )
    assert response.status_code == 200
    assert resolver.calls == [
        ("https://example.test/a", "first.txt"),
        ("https://example.test/a", "second.txt"),
    ]
    assert response.json() == {
        "items": [
            {"url": "https://example.test/a?signed=1", "filename": "first.txt", "signed": True},
            {"url": "https://example.test/a?signed=2", "filename": "second.txt", "signed": True},
        ]
    }


def test_limit_runs_after_request_validation_and_before_resolver() -> None:
    resolver = RecordingResolver()
    app = create_app(resolve_actor=trusted_actor, download_url_resolver=resolver)
    too_many = request(
        app,
        "POST",
        "/api/sign-download-urls",
        body={"items": [{"url": "https://example.test/one"}] * 501},
    )
    assert too_many.status_code == 400
    assert too_many.json() == {"detail": "单次最多 500 条"}
    assert resolver.calls == []

    invalid = request(
        app,
        "POST",
        "/api/sign-download-urls",
        body={"items": [{"url": None}] * 501},
    )
    assert invalid.status_code == 422
    assert resolver.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"items": None},
        {"items": [{"url": None}]},
        {"items": [{"url": 42}]},
        {"items": [{"url": "https://example.test", "filename": None}]},
    ],
)
def test_invalid_items_keep_standard_422(body: dict[str, Any]) -> None:
    app = create_app(resolve_actor=trusted_actor)
    response = request(app, "POST", "/api/sign-download-urls", body=body)
    assert response.status_code == 422


def test_actor_business_error_is_mapped_without_calling_url_resolver() -> None:
    resolver = RecordingResolver()

    def denied(_request) -> TrustedActor:
        raise BusinessError(403, "会员身份无效")

    app = create_app(resolve_actor=denied, download_url_resolver=resolver)
    response = request(
        app,
        "POST",
        "/api/sign-download-urls",
        body={"items": [{"url": "https://example.test/file"}]},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "会员身份无效"}
    assert resolver.calls == []




@pytest.mark.parametrize(
    "failure",
    [
        BusinessError(403, "resolver failure"),
        DownloadLinksTooManyItems(),
        HTTPException(status_code=418, detail="resolver-controlled response"),
    ],
)
def test_resolver_exceptions_are_generic_500_without_retry(failure) -> None:
    class FailingResolver:
        def __init__(self) -> None:
            self.calls = 0

        async def resolve(self, url: str, filename: str) -> str:
            self.calls += 1
            raise failure

    resolver = FailingResolver()
    app = create_app(resolve_actor=trusted_actor, download_url_resolver=resolver)
    response = request(
        app,
        "POST",
        "/api/sign-download-urls",
        body={
            "items": [
                {"url": "https://example.test/file"},
                {"url": "https://example.test/next"},
            ]
        },
    )

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert resolver.calls == 1

def test_resolver_failure_is_generic_500_without_retry_or_partial_result() -> None:
    resolver = RecordingResolver(fail_at=2)
    app = create_app(resolve_actor=trusted_actor, download_url_resolver=resolver)
    response = request(
        app,
        "POST",
        "/api/sign-download-urls",
        body={
            "items": [
                {"url": "https://example.test/one"},
                {"url": "https://example.test/two"},
                {"url": "https://example.test/three"},
            ]
        },
    )
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert resolver.calls == [
        ("https://example.test/one", ""),
        ("https://example.test/two", ""),
    ]
