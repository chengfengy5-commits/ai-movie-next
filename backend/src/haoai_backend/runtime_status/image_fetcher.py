"""SSRF-resistant byte fetcher for the legacy public image proxy."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import math
import socket
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlparse, urlsplit

from haoai_backend.media_storage.aiohttp_library import AiohttpMatureHttpLibrary
from haoai_backend.media_storage.pinned_http import (
    CheckedNumericResolver,
    EncodedRequestTarget,
    HttpResponse,
    HttpSession,
    MatureHttpLibrary,
    NumericResolver,
    OwnedConnector,
)
from haoai_backend.media_storage.public_fetch_policy import (
    AsyncGetAddrInfo,
    PublicFetchPolicy,
    UnsafeRemoteURL as PolicyUnsafeRemoteURL,
)
from haoai_backend.media_storage.transfer_types import TRANSFER_CHUNK_BYTES

from .ports import (
    PublicImageFetchResult,
    RemoteContentTooLarge,
    RemoteContentTypeRejected,
    RemoteFetchError,
    UnsafeRemoteURL,
)


_BLOCKED_CONTENT_TYPE_PREFIXES = (
    "text/html",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "image/svg+xml",
    "application/xml",
    "text/xml",
)


class _TrackedGetAddrInfo:
    def __init__(self, getaddrinfo: AsyncGetAddrInfo) -> None:
        self._getaddrinfo = getaddrinfo
        self.failure: BaseException | None = None

    async def __call__(
        self,
        host: str,
        port: int,
        *,
        family: int,
        type: int,
    ) -> Sequence[object]:
        try:
            return await self._getaddrinfo(
                host,
                port,
                family=family,
                type=type,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self.failure = exc
            raise


@dataclass(slots=True)
class _OwnedResources:
    resolver: NumericResolver | None = None
    connector: OwnedConnector | None = None
    session: HttpSession | None = None
    response: HttpResponse | None = None
    iterator: AsyncIterator[bytes] | None = None


class PinnedPublicImageFetcher:
    """Fetch exactly one checked public HTTP response and own all its resources."""

    def __init__(
        self,
        *,
        getaddrinfo: AsyncGetAddrInfo | None = None,
        http_library: MatureHttpLibrary | None = None,
    ) -> None:
        self._getaddrinfo = (
            _system_getaddrinfo if getaddrinfo is None else getaddrinfo
        )
        self._http_library = http_library

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: int,
        allowed_content_prefixes: Sequence[str],
    ) -> PublicImageFetchResult:
        if (
            type(max_bytes) is not int
            or max_bytes <= 0
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 600
        ):
            raise ValueError("image fetch limits are invalid")
        if self._http_library is None:
            raise RemoteFetchError("公网资源请求失败")

        tracker = _TrackedGetAddrInfo(self._getaddrinfo)
        policy = PublicFetchPolicy(tracker)
        resources = _OwnedResources()
        result: PublicImageFetchResult | None = None
        failure: BaseException | None = None

        try:
            async with asyncio.timeout(timeout_seconds):
                _validate_legacy_url_basics(url)
                try:
                    resolved = await policy.resolve(url)
                except PolicyUnsafeRemoteURL as exc:
                    if tracker.failure is not None:
                        raise RemoteFetchError("公网资源请求失败") from None
                    raise UnsafeRemoteURL(str(exc)) from None

                library = self._http_library
                target = library.encoded_url(url, preserve_encoded=True)
                original_authority = urlsplit(url).netloc
                if (
                    resolved.target.url != url
                    or target.raw_url != url
                    or not target.encoded
                    or target.authority != original_authority
                    or _canonical_hostname(target.tls_hostname)
                    != _canonical_hostname(resolved.target.hostname)
                ):
                    raise RemoteFetchError("公网资源请求失败")

                resolver = CheckedNumericResolver(resolved)
                resources.resolver = resolver
                connector = library.connector(
                    resolver,
                    tls_hostname=resolved.target.hostname,
                    verify_tls=True,
                    use_dns_cache=False,
                )
                resources.connector = connector
                session = library.session(
                    connector,
                    trust_env=False,
                    cookies_enabled=False,
                    auto_decompress=True,
                    connector_owned_by_session=False,
                )
                resources.session = session
                response = await session.get(
                    target,
                    headers={"Host": original_authority},
                    timeout_seconds=float(timeout_seconds),
                    allow_redirects=False,
                )
                resources.response = response

                if response.status != 200:
                    raise RemoteFetchError("公网资源请求失败")

                content_type = (_header(response.headers, "content-type") or "")
                content_type = content_type.split(";", 1)[0].strip().lower()
                if any(
                    content_type.startswith(prefix)
                    for prefix in _BLOCKED_CONTENT_TYPE_PREFIXES
                ) or not any(
                    content_type.startswith(prefix.lower())
                    for prefix in allowed_content_prefixes
                ):
                    raise RemoteContentTypeRejected("上游资源类型不受支持")

                content_length = _header(response.headers, "content-length")
                if content_length is not None:
                    content_length = content_length.strip()
                    if not content_length.isascii() or not content_length.isdecimal():
                        raise RemoteFetchError("公网资源请求失败")
                    if int(content_length) > max_bytes:
                        raise RemoteContentTooLarge("上游资源超过大小限制")

                iterator_result = response.iter_chunks(TRANSFER_CHUNK_BYTES)
                if inspect.isawaitable(iterator_result):
                    iterator_result = await cast(
                        Awaitable[AsyncIterator[bytes]], iterator_result
                    )
                iterator = cast(AsyncIterator[bytes], iterator_result)
                resources.iterator = iterator
                body = bytearray()
                while True:
                    try:
                        chunk = await iterator.__anext__()
                    except StopAsyncIteration:
                        break
                    if (
                        not isinstance(chunk, bytes)
                        or len(chunk) > TRANSFER_CHUNK_BYTES
                    ):
                        raise RemoteFetchError("公网资源请求失败")
                    if len(body) + len(chunk) > max_bytes:
                        raise RemoteContentTooLarge("上游资源超过大小限制")
                    body.extend(chunk)

                # The legacy proxy returns HTTP 200 for an empty image body.
                result = PublicImageFetchResult(bytes(body), content_type)
        except asyncio.CancelledError as exc:
            failure = exc
        except (
            UnsafeRemoteURL,
            RemoteContentTooLarge,
            RemoteContentTypeRejected,
        ) as exc:
            failure = exc
        except TimeoutError:
            failure = RemoteFetchError("公网资源请求超时")
        except RemoteFetchError as exc:
            failure = exc
        except Exception:
            failure = RemoteFetchError("公网资源请求失败")

        cleanup_error = await _close_resources(resources)
        if isinstance(failure, asyncio.CancelledError):
            raise failure
        if isinstance(cleanup_error, asyncio.CancelledError):
            raise cleanup_error
        if cleanup_error is not None:
            raise RemoteFetchError("公网资源请求失败") from None
        if failure is not None:
            raise failure
        if result is None:
            raise RemoteFetchError("公网资源请求失败")
        return result


def build_aiohttp_public_image_fetcher(
    *,
    getaddrinfo: AsyncGetAddrInfo | None = None,
) -> PinnedPublicImageFetcher:
    """Build the production transport lazily without opening a session."""

    import aiohttp
    from yarl import URL

    library = AiohttpMatureHttpLibrary(
        url_factory=URL,
        connector_factory=aiohttp.TCPConnector,
        session_factory=aiohttp.ClientSession,
        timeout_factory=aiohttp.ClientTimeout,
        cookie_jar_factory=aiohttp.DummyCookieJar,
    )
    return PinnedPublicImageFetcher(
        getaddrinfo=getaddrinfo,
        http_library=library,
    )


async def _system_getaddrinfo(
    host: str,
    port: int,
    *,
    family: int,
    type: int,
) -> Sequence[object]:
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(host, port, family=family, type=type)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _canonical_hostname(hostname: str) -> str:
    try:
        return str(ipaddress.ip_address(hostname))
    except ValueError:
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()


def _validate_legacy_url_basics(url: str) -> None:
    """Preserve the legacy parser's public error categories before SSRF checks."""

    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise UnsafeRemoteURL("URL 格式不正确") from None

    if parsed.scheme not in {"http", "https"}:
        raise UnsafeRemoteURL("仅支持 http/https URL")
    if not parsed.hostname:
        raise UnsafeRemoteURL("URL 缺少主机名")
    if parsed.username or parsed.password:
        raise UnsafeRemoteURL("URL 不允许包含账号信息")
    if parsed.fragment:
        raise UnsafeRemoteURL("URL 不允许包含片段")
    if port not in {None, 80, 443}:
        raise UnsafeRemoteURL("仅允许访问标准 HTTP/HTTPS 端口")


async def _close_resources(resources: _OwnedResources) -> BaseException | None:
    first: BaseException | None = None
    if resources.iterator is not None:
        close_iterator = getattr(resources.iterator, "aclose", None)
        if callable(close_iterator):
            try:
                outcome = close_iterator()
                if inspect.isawaitable(outcome):
                    await outcome
            except BaseException as exc:
                first = exc

    for resource in (
        resources.response,
        resources.session,
        resources.connector,
        resources.resolver,
    ):
        if resource is None:
            continue
        try:
            await resource.close()
        except BaseException as exc:
            if first is None or (
                not isinstance(first, asyncio.CancelledError)
                and isinstance(exc, asyncio.CancelledError)
            ):
                first = exc
    return first


__all__ = ("PinnedPublicImageFetcher", "build_aiohttp_public_image_fetcher")
