from __future__ import annotations

import asyncio
import inspect
import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urlsplit

from haoai_backend.generation_contracts import PreparedReference, ReferenceInput, TransferFailure

from .public_fetch_policy import (
    CheckedPublicEndpoint,
    PublicFetchPolicy,
    ResolvedPublicURL,
    UnsafeRemoteURL,
)
from .remote_staging import RemoteStaging
from .transfer_types import (
    TRANSFER_CHUNK_BYTES,
    MediaTypeRule,
    OwnedDownload,
    SafeMediaPolicy,
    TransferLimits,
)


@dataclass(frozen=True, slots=True)
class EncodedRequestTarget:
    raw_url: str
    authority: str
    tls_hostname: str
    encoded: bool = True


@dataclass(frozen=True, slots=True)
class NumericDialAddress:
    family: int
    host: str
    port: int
    proto: int
    flags: int
    sockaddr: tuple[str, int] | tuple[str, int, int, int]


class NumericResolver(Protocol):
    async def resolve(
        self, host: str, port: int, *, family: int = socket.AF_UNSPEC
    ) -> tuple[NumericDialAddress, ...]: ...
    async def close(self) -> None: ...


class OwnedConnector(Protocol):
    async def close(self) -> None: ...


class HttpResponse(Protocol):
    status: int
    headers: Mapping[str, str]
    def iter_chunks(
        self, chunk_size: int
    ) -> AsyncIterator[bytes] | Awaitable[AsyncIterator[bytes]]: ...
    async def close(self) -> None: ...


class HttpSession(Protocol):
    async def get(
        self,
        target: EncodedRequestTarget,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        allow_redirects: bool,
    ) -> HttpResponse: ...
    async def close(self) -> None: ...


class MatureHttpLibrary(Protocol):
    def encoded_url(self, raw_url: str, *, preserve_encoded: bool) -> EncodedRequestTarget: ...
    def connector(
        self,
        resolver: NumericResolver,
        *,
        tls_hostname: str,
        verify_tls: bool,
        use_dns_cache: bool,
    ) -> OwnedConnector: ...
    def session(
        self,
        connector: OwnedConnector,
        *,
        trust_env: bool,
        cookies_enabled: bool,
        auto_decompress: bool,
        connector_owned_by_session: bool,
    ) -> HttpSession: ...


@dataclass(slots=True)
class _Resources:
    resolver: NumericResolver | None = None
    connector: OwnedConnector | None = None
    session: HttpSession | None = None
    response: HttpResponse | None = None
    iterator: AsyncIterator[bytes] | None = None


class CheckedNumericResolver:
    def __init__(self, resolved: ResolvedPublicURL) -> None:
        self._hostname = _canonical_hostname(resolved.target.hostname)
        self._port = resolved.target.port
        self._endpoints = tuple(resolved.endpoints)
        self._closed = False

    async def resolve(
        self, host: str, port: int, *, family: int = socket.AF_UNSPEC
    ) -> tuple[NumericDialAddress, ...]:
        if self._closed:
            raise OSError("checked resolver is closed")
        if _canonical_hostname(host) != self._hostname or port != self._port:
            raise OSError("connector requested an unchecked authority")
        selected = tuple(
            endpoint for endpoint in self._endpoints
            if family in {socket.AF_UNSPEC, endpoint.family}
        )
        if not selected:
            raise OSError("no checked numeric endpoint for requested address family")
        return tuple(_numeric_address(endpoint) for endpoint in selected)

    async def close(self) -> None:
        self._closed = True


class PinnedPublicMediaFetcher:
    def __init__(
        self,
        policy: PublicFetchPolicy,
        media_policy: SafeMediaPolicy,
        *,
        http_library: MatureHttpLibrary | None = None,
        staging: RemoteStaging | None = None,
    ) -> None:
        self._policy = policy
        self._media_policy = media_policy
        self._http_library = http_library
        self._staging = staging

    async def fetch_public(
        self,
        reference: ReferenceInput,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> PreparedReference | TransferFailure:
        failure = _validate_reference(reference, max_bytes, timeout_seconds)
        if failure is not None:
            return failure
        if self._http_library is None:
            return _failure("transport_unavailable", "公网媒体传输能力未配置")
        content = bytearray()
        media_type = await self._fetch(
            cast(str, reference.locator),
            TransferLimits(max_bytes, timeout_seconds),
            content.extend,
        )
        if isinstance(media_type, TransferFailure):
            return media_type
        return PreparedReference(
            kind="bytes",
            locator=None,
            content=bytes(content),
            media_type=media_type.media_type,
        )

    async def fetch_to_owned(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> OwnedDownload | TransferFailure:
        failure = _validate_limits(max_bytes, timeout_seconds)
        if failure is not None:
            return failure
        if self._http_library is None:
            return _failure("transport_unavailable", "公网媒体传输能力未配置")
        if self._staging is None:
            return _failure("not_ready", "公网媒体暂存能力未配置")
        try:
            download = self._staging.create(max_bytes)
        except (OSError, ValueError):
            return _failure("storage_unavailable", "公网媒体暂存目录不可用")
        try:
            media_type = await self._fetch(
                url,
                TransferLimits(max_bytes, timeout_seconds),
                lambda chunk: download.write_chunk(chunk, max_bytes=max_bytes),
            )
            if isinstance(media_type, TransferFailure):
                cleanup = self._staging.release(download)
                return _failure(
                    "cleanup_failure" if not cleanup.released else media_type.code,
                    "公网媒体资源清理失败" if not cleanup.released else media_type.safe_detail,
                )
            download.seal(media_type.media_type)
            return download
        except asyncio.CancelledError as cancellation:
            try:
                self._staging.release(download)
            except BaseException:
                pass
            raise cancellation
        except (OSError, OverflowError, TimeoutError, ValueError):
            cleanup = self._staging.release(download)
            return _failure(
                "cleanup_failure" if not cleanup.released else "transfer_failure",
                "公网媒体资源清理失败" if not cleanup.released else "公网媒体获取失败",
            )

    async def _fetch(
        self,
        url: str,
        limits: TransferLimits,
        write_chunk: Callable[[bytes], None],
    ) -> MediaTypeRule | TransferFailure:
        library = self._http_library
        if library is None:
            return _failure("transport_unavailable", "公网媒体传输能力未配置")
        resources = _Resources()
        body_failure: TransferFailure | None = None
        body_error: BaseException | None = None
        media_rule: MediaTypeRule | None = None
        try:
            async with asyncio.timeout(limits.timeout_seconds):
                resolved = await self._policy.resolve(url)
                if resolved.target.url != url:
                    body_failure = _failure("transport_unavailable", "公网媒体请求目标未保持原值")
                else:
                    request_target = library.encoded_url(url, preserve_encoded=True)
                    original_authority = urlsplit(url).netloc
                    if (
                        request_target.raw_url != url
                        or not request_target.encoded
                        or request_target.authority != original_authority
                        or _canonical_hostname(request_target.tls_hostname)
                        != _canonical_hostname(resolved.target.hostname)
                    ):
                        body_failure = _failure("transport_unavailable", "HTTP 能力未保留原始请求目标")
                    else:
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
                            request_target,
                            headers={"Host": original_authority},
                            timeout_seconds=limits.timeout_seconds,
                            allow_redirects=False,
                        )
                        resources.response = response
                        if response.status != 200:
                            body_failure = _failure("http_status", "公网媒体服务器未返回完整内容")
                        else:
                            content_length = _header(response.headers, "content-length")
                            if content_length is not None:
                                if not content_length.isascii() or not content_length.isdecimal():
                                    body_failure = _failure("invalid_content_length", "公网媒体长度无效")
                                elif int(content_length) > limits.max_bytes:
                                    body_failure = _failure("reference_too_large", "公网媒体超过大小限制")
                            content_header = _header(response.headers, "content-type")
                            media_rule = self._media_policy.resolve(content_header)
                            if media_rule is None:
                                body_failure = _failure("unsupported_media_type", "公网媒体类型不受支持")
                            if body_failure is None:
                                iterator_result = response.iter_chunks(TRANSFER_CHUNK_BYTES)
                                if inspect.isawaitable(iterator_result):
                                    iterator_result = await cast(
                                        Awaitable[AsyncIterator[bytes]], iterator_result
                                    )
                                iterator = cast(AsyncIterator[bytes], iterator_result)
                                resources.iterator = iterator
                                total = 0
                                while True:
                                    try:
                                        chunk = await iterator.__anext__()
                                    except StopAsyncIteration:
                                        break
                                    if (
                                        not isinstance(chunk, bytes)
                                        or len(chunk) > TRANSFER_CHUNK_BYTES
                                    ):
                                        body_failure = _failure("invalid_chunk", "公网媒体数据块无效")
                                        break
                                    if total + len(chunk) > limits.max_bytes:
                                        body_failure = _failure("reference_too_large", "公网媒体超过大小限制")
                                        break
                                    if not chunk:
                                        continue
                                    total += len(chunk)
                                    write_chunk(chunk)
                                if body_failure is None and total == 0:
                                    body_failure = _failure("empty_media", "公网媒体内容为空")
        except asyncio.CancelledError as cancellation:
            body_error = cancellation
        except UnsafeRemoteURL:
            body_failure = _failure("unsafe_url", "公网媒体地址未通过安全检查")
        except TimeoutError:
            body_failure = _failure("timeout", "公网媒体获取超时")
        except Exception as exc:
            body_error = exc

        cleanup_error = await _close_resources(resources)
        if isinstance(body_error, asyncio.CancelledError):
            raise body_error
        if cleanup_error is not None:
            if isinstance(cleanup_error, asyncio.CancelledError):
                raise cleanup_error
            return _failure("cleanup_failure", "公网媒体连接资源清理失败")
        if body_error is not None:
            return _failure("transport_failure", "公网媒体获取失败")
        if body_failure is not None:
            return body_failure
        if media_rule is None:
            return _failure("unsupported_media_type", "公网媒体类型不受支持")
        return media_rule


async def _close_resources(resources: _Resources) -> BaseException | None:
    first: BaseException | None = None
    iterator = resources.iterator
    if iterator is not None:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
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


def _canonical_hostname(hostname: str) -> str:
    try:
        return str(ipaddress.ip_address(hostname))
    except ValueError:
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()


def _numeric_address(endpoint: CheckedPublicEndpoint) -> NumericDialAddress:
    if endpoint.flags != socket.AI_NUMERICHOST or endpoint.proto != socket.IPPROTO_TCP:
        raise OSError("checked endpoint lacks numeric TCP identity")
    if endpoint.family not in {socket.AF_INET, socket.AF_INET6}:
        raise OSError("checked endpoint has an unsupported address family")
    if endpoint.family == socket.AF_INET and len(endpoint.sockaddr) != 2:
        raise OSError("checked IPv4 endpoint is malformed")
    if endpoint.family == socket.AF_INET6 and len(endpoint.sockaddr) != 4:
        raise OSError("checked IPv6 endpoint is malformed")
    return NumericDialAddress(
        family=endpoint.family,
        host=endpoint.host,
        port=endpoint.port,
        proto=endpoint.proto,
        flags=socket.AI_NUMERICHOST,
        sockaddr=endpoint.sockaddr,
    )


def _validate_reference(
    reference: ReferenceInput,
    max_bytes: int,
    timeout_seconds: float,
) -> TransferFailure | None:
    failure = _validate_limits(max_bytes, timeout_seconds)
    if failure is not None:
        return failure
    if (
        reference.kind not in {"public_http", "managed_url"}
        or not isinstance(reference.locator, str)
        or not reference.locator
        or reference.content is not None
        or reference.provider_operation_ref is not None
        or reference.media_type is not None
    ):
        return _failure("unsupported_reference", "仅支持完整公网媒体地址")
    return None


def _validate_limits(max_bytes: int, timeout_seconds: float) -> TransferFailure | None:
    try:
        TransferLimits(max_bytes, timeout_seconds)
    except (TypeError, ValueError):
        return _failure("invalid_limits", "公网媒体限制无效")
    return None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _failure(code: str, safe_detail: str) -> TransferFailure:
    return TransferFailure(code=code, safe_detail=safe_detail)
