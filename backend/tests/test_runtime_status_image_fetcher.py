"""Image proxy transport tests use a fake numeric connector, never the network."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI, Request

from haoai_backend.media_storage.pinned_http import (
    EncodedRequestTarget,
    HttpResponse,
    HttpSession,
    MatureHttpLibrary,
    NumericResolver,
    OwnedConnector,
)
from haoai_backend.media_storage.public_fetch_policy import AsyncGetAddrInfo
from haoai_backend.runtime_status.image_fetcher import (
    PinnedPublicImageFetcher,
    build_aiohttp_public_image_fetcher,
)
from haoai_backend.runtime_status.configuration import RuntimeStatusConfiguration
from haoai_backend.runtime_status.http import build_runtime_status_router
from haoai_backend.runtime_status.ports import (
    RemoteContentTooLarge,
    RemoteContentTypeRejected,
    RemoteFetchError,
    UnsafeRemoteURL,
)


def _dns_record(host: str, ip: str) -> tuple[int, int, int, str, tuple[str, int]]:
    address = socket.inet_pton(socket.AF_INET, ip)
    del address
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, host, (ip, 443))


class FakeResponse(HttpResponse):
    def __init__(
        self,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        chunks: Sequence[bytes] = (b"image",),
        events: list[str],
    ) -> None:
        self.status = status
        self.headers = dict(headers or {"Content-Type": "image/png"})
        self._chunks = tuple(chunks)
        self._events = events

    def iter_chunks(self, _chunk_size: int) -> AsyncIterator[bytes]:
        async def chunks() -> AsyncIterator[bytes]:
            try:
                for chunk in self._chunks:
                    yield chunk
            finally:
                self._events.append("iterator-close")

        return chunks()

    async def close(self) -> None:
        self._events.append("response-close")


class FakeSession(HttpSession):
    def __init__(
        self,
        resolver: NumericResolver,
        response: FakeResponse,
        events: list[str],
        *,
        failure: Exception | None = None,
    ) -> None:
        self._resolver = resolver
        self._response = response
        self._events = events
        self._failure = failure
        self.call: (
            tuple[EncodedRequestTarget, dict[str, str], float, bool] | None
        ) = None
        self.numeric_dials: tuple[Any, ...] = ()

    async def get(
        self,
        target: EncodedRequestTarget,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        allow_redirects: bool,
    ) -> HttpResponse:
        self.call = (target, dict(headers), timeout_seconds, allow_redirects)
        parsed = urlsplit(target.raw_url)
        self.numeric_dials = await self._resolver.resolve(
            target.tls_hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
        if self._failure is not None:
            raise self._failure
        return self._response

    async def close(self) -> None:
        self._events.append("session-close")


class FakeConnector(OwnedConnector):
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def close(self) -> None:
        self._events.append("connector-close")


class FakeLibrary(MatureHttpLibrary):
    def __init__(
        self,
        response: FakeResponse,
        events: list[str],
        *,
        failure: Exception | None = None,
    ) -> None:
        self._response = response
        self._events = events
        self._failure = failure
        self.connector_options: dict[str, Any] = {}
        self.session_options: dict[str, Any] = {}
        self.session_instance: FakeSession | None = None

    def encoded_url(
        self,
        raw_url: str,
        *,
        preserve_encoded: bool,
    ) -> EncodedRequestTarget:
        assert preserve_encoded is True
        parsed = urlsplit(raw_url)
        return EncodedRequestTarget(
            raw_url=raw_url,
            authority=parsed.netloc,
            tls_hostname=parsed.hostname or "",
            encoded=True,
        )

    def connector(
        self,
        resolver: NumericResolver,
        *,
        tls_hostname: str,
        verify_tls: bool,
        use_dns_cache: bool,
    ) -> OwnedConnector:
        self.connector_options = {
            "resolver": resolver,
            "tls_hostname": tls_hostname,
            "verify_tls": verify_tls,
            "use_dns_cache": use_dns_cache,
        }
        return FakeConnector(self._events)

    def session(
        self,
        connector: OwnedConnector,
        *,
        trust_env: bool,
        cookies_enabled: bool,
        auto_decompress: bool,
        connector_owned_by_session: bool,
    ) -> HttpSession:
        self.session_options = {
            "trust_env": trust_env,
            "cookies_enabled": cookies_enabled,
            "auto_decompress": auto_decompress,
            "connector_owned_by_session": connector_owned_by_session,
        }
        resolver = self.connector_options["resolver"]
        self.session_instance = FakeSession(
            resolver,
            self._response,
            self._events,
            failure=self._failure,
        )
        return self.session_instance


def _make_fetcher(
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    chunks: Sequence[bytes] = (b"image",),
    dns_answers: Sequence[object] | None = None,
    dns_failure: Exception | None = None,
    http_failure: Exception | None = None,
) -> tuple[
    PinnedPublicImageFetcher,
    FakeLibrary,
    list[str],
    list[tuple[str, int, int, int]],
]:
    events: list[str] = []
    response = FakeResponse(
        status=status,
        headers=headers,
        chunks=chunks,
        events=events,
    )
    library = FakeLibrary(response, events, failure=http_failure)
    dns_calls: list[tuple[str, int, int, int]] = []

    async def getaddrinfo(
        host: str,
        port: int,
        *,
        family: int,
        type: int,
    ) -> Sequence[object]:
        dns_calls.append((host, port, family, type))
        if dns_failure is not None:
            raise dns_failure
        return tuple(dns_answers or (_dns_record(host, "8.8.8.8"),))

    return (
        PinnedPublicImageFetcher(getaddrinfo=getaddrinfo, http_library=library),
        library,
        events,
        dns_calls,
    )


def _fetch(fetcher: PinnedPublicImageFetcher, *, max_bytes: int = 10) -> Any:
    return asyncio.run(
        fetcher.fetch(
            "https://images.example.test/%2Fcover.png?sig=a%2Fb",
            max_bytes=max_bytes,
            timeout_seconds=30,
            allowed_content_prefixes=("image/",),
        )
    )


def test_success_preserves_encoded_request_pins_dns_and_closes_all_resources() -> None:
    fetcher, library, events, dns_calls = _make_fetcher(
        headers={"Content-Type": "IMAGE/PNG; charset=binary", "Content-Length": "5"},
        chunks=(b"im", b"age"),
    )

    result = _fetch(fetcher)

    assert result.content == b"image"
    assert result.content_type == "image/png"
    assert dns_calls[0][0:2] == ("images.example.test", 443)
    session = library.session_instance
    assert session is not None and session.call is not None
    target, headers, timeout, redirects = session.call
    assert target.raw_url == "https://images.example.test/%2Fcover.png?sig=a%2Fb"
    assert headers == {"Host": "images.example.test"}
    assert timeout == 30
    assert redirects is False
    assert [(item.host, item.flags) for item in session.numeric_dials] == [
        ("8.8.8.8", socket.AI_NUMERICHOST)
    ]
    assert library.connector_options["verify_tls"] is True
    assert library.connector_options["use_dns_cache"] is False
    assert library.session_options == {
        "trust_env": False,
        "cookies_enabled": False,
        "auto_decompress": True,
        "connector_owned_by_session": False,
    }
    assert events == [
        "iterator-close",
        "response-close",
        "session-close",
        "connector-close",
    ]


def test_empty_image_body_preserves_legacy_200_result() -> None:
    fetcher, _library, events, _dns_calls = _make_fetcher(chunks=())

    result = _fetch(fetcher)

    assert result.content == b""
    assert result.content_type == "image/png"
    assert "response-close" in events


@pytest.mark.parametrize(
    ("url", "answers"),
    [
        ("http://127.0.0.1/private.png", None),
        (
            "https://mixed.example.test/image.png",
            (_dns_record("mixed.example.test", "8.8.8.8"),
             _dns_record("mixed.example.test", "10.0.0.1")),
        ),
    ],
)
def test_private_or_mixed_dns_targets_are_rejected_before_transport(
    url: str,
    answers: Sequence[object] | None,
) -> None:
    fetcher, library, _events, _dns_calls = _make_fetcher(dns_answers=answers)

    with pytest.raises(UnsafeRemoteURL):
        asyncio.run(
            fetcher.fetch(
                url,
                max_bytes=10,
                timeout_seconds=30,
                allowed_content_prefixes=("image/",),
            )
        )

    assert library.session_instance is None


def test_dns_oserror_maps_to_fetch_error_502_class() -> None:
    fetcher, library, _events, _dns_calls = _make_fetcher(
        dns_failure=socket.gaierror("fake DNS outage")
    )

    with pytest.raises(RemoteFetchError):
        _fetch(fetcher)

    assert library.session_instance is None


@pytest.mark.parametrize("status", [206, 301, 302, 404])
def test_non_200_response_is_a_fetch_error(status: int) -> None:
    fetcher, _library, events, _dns_calls = _make_fetcher(status=status)

    with pytest.raises(RemoteFetchError):
        _fetch(fetcher)

    assert "response-close" in events


@pytest.mark.parametrize(
    "content_type",
    [
        "image/svg+xml",
        "text/html",
        "text/javascript",
        "application/javascript",
        "application/x-javascript",
        "application/xml",
        "text/xml",
        "application/octet-stream",
    ],
)
def test_active_or_non_image_mime_types_are_rejected(content_type: str) -> None:
    fetcher, _library, events, _dns_calls = _make_fetcher(
        headers={"Content-Type": content_type}
    )

    with pytest.raises(RemoteContentTypeRejected):
        _fetch(fetcher)

    assert "response-close" in events


def test_declared_and_streamed_byte_limits_map_to_too_large() -> None:
    declared, _library, declared_events, _calls = _make_fetcher(
        headers={"Content-Type": "image/png", "Content-Length": "11"}
    )
    with pytest.raises(RemoteContentTooLarge):
        _fetch(declared, max_bytes=10)
    assert "response-close" in declared_events

    streamed, _library, streamed_events, _calls = _make_fetcher(
        headers={"Content-Type": "image/png"},
        chunks=(b"123456", b"78901"),
    )
    with pytest.raises(RemoteContentTooLarge):
        _fetch(streamed, max_bytes=10)
    assert "response-close" in streamed_events


def test_transport_timeout_maps_to_fetch_error_and_closes_owned_resources() -> None:
    fetcher, _library, events, _dns_calls = _make_fetcher(
        http_failure=TimeoutError("fake upstream timeout")
    )

    with pytest.raises(RemoteFetchError):
        _fetch(fetcher)

    assert events == ["session-close", "connector-close"]


def test_production_fetcher_factory_is_inert_until_a_fetch() -> None:
    calls: list[str] = []

    async def no_network(*_args: Any, **_kwargs: Any) -> Sequence[object]:
        calls.append("dns")
        raise AssertionError("factory construction must not resolve hosts")

    fetcher = build_aiohttp_public_image_fetcher(getaddrinfo=no_network)

    assert isinstance(fetcher, PinnedPublicImageFetcher)
    assert calls == []


@pytest.mark.parametrize(
    ("url", "detail"),
    [
        (
            "ftp://images.example.test/cover.png",
            "仅支持 http/https URL",
        ),
        (
            "http:///cover.png",
            "URL 缺少主机名",
        ),
        (
            "https://images.example.test/cover.png#part",
            "URL 不允许包含片段",
        ),
    ],
)
def test_router_preserves_legacy_url_parse_errors_without_network_calls(
    url: str,
    detail: str,
) -> None:
    fetcher, library, events, dns_calls = _make_fetcher()
    app = FastAPI()

    def accept_user(_request: Request) -> object:
        return object()

    app.include_router(
        build_runtime_status_router(
            configuration=RuntimeStatusConfiguration(
                service_name="test",
                version="test",
                readiness_initialized=True,
                object_storage_configured=False,
                probe_timeout_seconds=1,
                ready_cache_seconds=1,
                initial_failure_backoff_seconds=1,
                max_failure_backoff_seconds=2,
            ),
            image_fetcher=fetcher,
            resolve_current_user=accept_user,
            database_probe=None,
            object_storage_probe=None,
        )
    )

    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://runtime-status.test",
        ) as client:
            return await client.get(
                "/api/chat/proxy-image",
                params={"url": url},
            )

    response = asyncio.run(send())

    assert response.status_code == 400
    assert response.json() == {"detail": detail}
    assert dns_calls == []
    assert library.connector_options == {}
    assert library.session_instance is None
    assert events == []
