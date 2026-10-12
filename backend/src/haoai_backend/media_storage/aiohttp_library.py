from __future__ import annotations

import inspect
import ipaddress
import socket
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

from .pinned_http import (
    EncodedRequestTarget,
    HttpResponse,
    HttpSession,
    MatureHttpLibrary,
    NumericResolver,
    OwnedConnector,
)
from .transfer_types import TRANSFER_CHUNK_BYTES


class UrlFactory(Protocol):
    def __call__(self, raw_url: str, *, encoded: bool) -> object: ...


class ConnectorFactory(Protocol):
    def __call__(self, *, resolver: object, ssl: object, use_dns_cache: bool) -> object: ...


class SessionFactory(Protocol):
    def __call__(
        self,
        *,
        connector: object,
        connector_owner: bool,
        trust_env: bool,
        cookie_jar: object,
        auto_decompress: bool,
    ) -> object: ...


class TimeoutFactory(Protocol):
    def __call__(self, *, total: float) -> object: ...


class AiohttpMatureHttpLibrary(MatureHttpLibrary):
    """Bridge explicit aiohttp/yarl constructors to the pinned-fetch protocol."""

    def __init__(
        self,
        *,
        url_factory: UrlFactory,
        connector_factory: ConnectorFactory,
        session_factory: SessionFactory,
        timeout_factory: TimeoutFactory,
        cookie_jar_factory: Callable[[], object],
    ) -> None:
        for name, factory in (
            ("url_factory", url_factory),
            ("connector_factory", connector_factory),
            ("session_factory", session_factory),
            ("timeout_factory", timeout_factory),
            ("cookie_jar_factory", cookie_jar_factory),
        ):
            if not callable(factory):
                raise TypeError(f"{name} must be an explicit callable")
        self._url_factory = url_factory
        self._connector_factory = connector_factory
        self._session_factory = session_factory
        self._timeout_factory = timeout_factory
        self._cookie_jar_factory = cookie_jar_factory

    def encoded_url(
        self, raw_url: str, *, preserve_encoded: bool
    ) -> EncodedRequestTarget:
        if not preserve_encoded or not isinstance(raw_url, str) or not raw_url:
            raise ValueError("an original encoded URL is required")
        parsed = urlsplit(raw_url)
        if (
            not parsed.scheme
            or not parsed.netloc
            or not parsed.hostname
            or parsed.fragment
            or "@" in parsed.netloc
        ):
            raise ValueError("URL authority is missing")
        url = self._url_factory(raw_url, encoded=True)
        self._check_encoded_target(url, raw_url)
        return EncodedRequestTarget(
            raw_url=raw_url,
            authority=parsed.netloc,
            tls_hostname=parsed.hostname,
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
        if not tls_hostname or not verify_tls or use_dns_cache:
            raise ValueError("pinned TLS and uncached numeric DNS are required")
        adapter = _AiohttpResolver(resolver)
        connector = self._connector_factory(
            resolver=adapter,
            ssl=True,
            use_dns_cache=False,
        )
        return _AiohttpConnector(connector, adapter, tls_hostname)

    def session(
        self,
        connector: OwnedConnector,
        *,
        trust_env: bool,
        cookies_enabled: bool,
        auto_decompress: bool,
        connector_owned_by_session: bool,
    ) -> HttpSession:
        if (
            trust_env
            or cookies_enabled
            or not auto_decompress
            or connector_owned_by_session
            or not isinstance(connector, _AiohttpConnector)
        ):
            raise ValueError("HTTP session options exceed the pinned-fetch contract")
        raw_session = self._session_factory(
            connector=connector.raw,
            connector_owner=False,
            trust_env=False,
            cookie_jar=self._cookie_jar_factory(),
            auto_decompress=True,
        )
        return _AiohttpSession(
            raw_session,
            connector,
            self._url_factory,
            self._timeout_factory,
        )

    @staticmethod
    def _check_encoded_target(url: object, raw_url: str) -> None:
        parsed = urlsplit(raw_url)
        expected = parsed.path or "/"
        if parsed.query:
            expected += "?" + parsed.query
        raw_path_qs = getattr(url, "raw_path_qs", None)
        if not isinstance(raw_path_qs, str) or raw_path_qs != expected:
            raise ValueError("URL constructor changed the encoded request target")


class _AiohttpResolver:
    def __init__(self, resolver: NumericResolver) -> None:
        self._resolver = resolver

    async def resolve(
        self, host: str, port: int, family: int = socket.AF_UNSPEC
    ) -> list[dict[str, Any]]:
        addresses = await self._resolver.resolve(host, port, family=family)
        resolved: list[dict[str, Any]] = []
        for address in addresses:
            try:
                numeric = ipaddress.ip_address(address.host)
            except ValueError:
                raise OSError("resolver returned a nonnumeric dial address") from None
            expected_family = socket.AF_INET if numeric.version == 4 else socket.AF_INET6
            if (
                address.family != expected_family
                or address.port != port
                or address.proto != socket.IPPROTO_TCP
                or address.flags != socket.AI_NUMERICHOST
            ):
                raise OSError("resolver returned an unchecked dial address")
            resolved.append(
                {
                    "hostname": host,
                    "host": address.host,
                    "port": address.port,
                    "family": address.family,
                    "proto": address.proto,
                    "flags": address.flags,
                }
            )
        if not resolved:
            raise OSError("resolver returned no checked dial addresses")
        return resolved

    async def close(self) -> None:
        await self._resolver.close()


class _AiohttpConnector(OwnedConnector):
    def __init__(self, raw: object, resolver: _AiohttpResolver, tls_hostname: str) -> None:
        self.raw = raw
        self.resolver = resolver
        self.tls_hostname = tls_hostname

    async def close(self) -> None:
        await _await_if_needed(self.raw.close())  # type: ignore[attr-defined]


class _AiohttpSession(HttpSession):
    def __init__(
        self,
        raw: object,
        connector: _AiohttpConnector,
        url_factory: UrlFactory,
        timeout_factory: TimeoutFactory,
    ) -> None:
        self._raw = raw
        self._connector = connector
        self._url_factory = url_factory
        self._timeout_factory = timeout_factory

    async def get(
        self,
        target: EncodedRequestTarget,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        allow_redirects: bool,
    ) -> HttpResponse:
        if (
            not target.encoded
            or not isinstance(target.raw_url, str)
            or not target.raw_url
            or urlsplit(target.raw_url).netloc != target.authority
            or urlsplit(target.raw_url).hostname != target.tls_hostname
            or target.tls_hostname.lower().rstrip(".")
            != self._connector.tls_hostname.lower().rstrip(".")
            or dict(headers) != {"Host": target.authority}
            or isinstance(timeout_seconds, bool)
            or not 0 < timeout_seconds <= 600
            or allow_redirects
        ):
            raise ValueError("request target changed its checked authority")
        url = self._url_factory(target.raw_url, encoded=True)
        AiohttpMatureHttpLibrary._check_encoded_target(url, target.raw_url)
        response = await self._raw.get(
            url,
            headers=dict(headers),
            timeout=self._timeout_factory(total=timeout_seconds),
            allow_redirects=False,
        )
        return _AiohttpResponse(response)

    async def close(self) -> None:
        await _await_if_needed(self._raw.close())  # type: ignore[attr-defined]


class _AiohttpResponse(HttpResponse):
    def __init__(self, raw: object) -> None:
        self._raw = raw
        self.status = int(getattr(raw, "status"))
        self.headers = getattr(raw, "headers")

    def iter_chunks(self, chunk_size: int) -> AsyncIterator[bytes]:
        if chunk_size != TRANSFER_CHUNK_BYTES:
            raise ValueError("media response chunks must use the fixed 64 KiB size")
        return getattr(self._raw, "content").iter_chunked(chunk_size)

    async def close(self) -> None:
        release = getattr(self._raw, "release", None)
        if callable(release):
            release()
        wait_for_close = getattr(self._raw, "wait_for_close", None)
        if callable(wait_for_close):
            await _await_if_needed(wait_for_close())


async def _await_if_needed(value: object) -> None:
    if inspect.isawaitable(value):
        await value
