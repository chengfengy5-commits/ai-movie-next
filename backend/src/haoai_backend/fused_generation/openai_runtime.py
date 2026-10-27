"""OpenAI multipart runtime for explicitly configured fused generation."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from urllib.parse import urlsplit

from haoai_backend.generation_contracts import (
    AsyncHttpClient,
    AsyncHttpResponse,
    PreparedReference,
    PublicMediaFetcher,
    ReferenceInput,
    TransferFailure,
)
from haoai_backend.generation_providers.image.openai_codec import (
    OpenAIImageCodecError,
    OpenAIImageEditReference,
    build_openai_image_edit_request,
    parse_openai_image_response,
)
from haoai_backend.media_storage.public_fetch_policy import (
    UnsafeRemoteURL,
    parse_public_http_url,
)

from .domain import FusedGenerationInput, FusedGenerationOutput, FusedGenerationUnknown
from .errors import FusedGenerationDispatchedCancellation, ProviderGenerationFailure

_DEFAULT_PROVIDER_ORIGINS = ("https://api.openai.com",)
_MAX_REFERENCE_IMAGES = 8
_MAX_REFERENCE_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_REFERENCE_TIMEOUT_SECONDS = 30.0
_PROVIDER_TIMEOUT_SECONDS = 180.0
_RESPONSE_CHUNK_BYTES = 64 * 1024
_REFERENCE_MEDIA_TYPES = frozenset(
    {"image/bmp", "image/gif", "image/jpeg", "image/png", "image/webp"}
)
_UNKNOWN = FusedGenerationUnknown()

HttpClientFactory = Callable[
    [], AbstractAsyncContextManager[AsyncHttpClient]
]


class _ProviderRejected(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FusedOpenAIImageRuntime:
    """Submit one bounded multipart edit to an explicitly trusted provider origin.

    The caller supplies the existing pinned public-media fetcher and the shared
    asynchronous HTTP client factory. Constructing this adapter opens no
    network resources; requests occur only when ``generate`` is called.
    """

    def __init__(
        self,
        *,
        http_client_factory: HttpClientFactory,
        public_media_fetcher: PublicMediaFetcher,
        allowed_provider_origins: tuple[str, ...] = _DEFAULT_PROVIDER_ORIGINS,
        provider_timeout_seconds: float = _PROVIDER_TIMEOUT_SECONDS,
    ) -> None:
        if not callable(http_client_factory):
            raise TypeError("http_client_factory must be callable")
        if not callable(getattr(public_media_fetcher, "fetch_public", None)):
            raise TypeError("public_media_fetcher must provide fetch_public")
        if type(allowed_provider_origins) is not tuple or not allowed_provider_origins:
            raise TypeError("allowed_provider_origins must be a non-empty tuple")
        if (
            isinstance(provider_timeout_seconds, bool)
            or not isinstance(provider_timeout_seconds, (int, float))
            or not 0 < provider_timeout_seconds <= 1800
        ):
            raise ValueError("provider_timeout_seconds must be between 0 and 1800")
        self._http_client_factory = http_client_factory
        self._public_media_fetcher = public_media_fetcher
        self._provider_timeout_seconds = float(provider_timeout_seconds)
        self._allowed_provider_origins = frozenset(
            _parse_origin(origin, allow_path=False)
            for origin in allowed_provider_origins
        )

    async def generate(
        self, request: FusedGenerationInput
    ) -> FusedGenerationOutput | FusedGenerationUnknown:
        base_url = self._validated_base_url(request)
        references = await self._prepare_references(request.reference_images)
        try:
            edit = build_openai_image_edit_request(
                base_url=base_url,
                model=request.server_model.wire_model,
                prompt=request.prompt,
                size=request.size,
                references=references,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderGenerationFailure("图片编辑请求无效") from exc

        # Recheck the fully composed target before attaching the model secret.
        if (
            _parse_origin(edit.url, allow_path=True)
            not in self._allowed_provider_origins
        ):
            raise ProviderGenerationFailure("图片 provider 地址未获授权")

        dispatched = False
        try:
            async with asyncio.timeout(self._provider_timeout_seconds):
                async with self._http_client_factory() as client:
                    dispatched = True
                    response = await client.request(
                        "POST",
                        edit.url,
                        headers={
                            "Authorization": f"Bearer {request.server_model.api_key}",
                            "Content-Type": edit.content_type,
                        },
                        content=edit.body,
                        timeout_seconds=self._provider_timeout_seconds,
                        follow_redirects=False,
                    )
                    status_code = response.status_code
                    if status_code != 200:
                        if 400 <= status_code < 500 and status_code != 408:
                            raise _ProviderRejected(status_code)
                        return _UNKNOWN

                    body_bytes = await _read_bounded_response(response)
                    try:
                        body = json.loads(body_bytes)
                        output = parse_openai_image_response(status_code, body)
                    except (
                        UnicodeDecodeError,
                        json.JSONDecodeError,
                        OpenAIImageCodecError,
                    ):
                        return _UNKNOWN
                    if output.kind != "url" or not _is_safe_image_url(output.value):
                        # A base64 or malformed success may represent a paid create.
                        return _UNKNOWN
                    return FusedGenerationOutput(image_url=output.value)
        except _ProviderRejected as rejected:
            raise ProviderGenerationFailure(
                f"图片 provider 拒绝了请求 (HTTP {rejected.status_code})"
            ) from None
        except asyncio.CancelledError as exc:
            if dispatched:
                raise FusedGenerationDispatchedCancellation() from exc
            raise
        except Exception as exc:
            if dispatched:
                return _UNKNOWN
            raise ProviderGenerationFailure("图片 provider 传输未能启动") from exc

    def _validated_base_url(self, request: FusedGenerationInput) -> str:
        model = request.server_model
        if (
            type(model.provider) is not str
            or model.provider.lower() not in {"openai", "haoai"}
            or type(model.api_key) is not str
            or not model.api_key.strip()
            or type(model.wire_model) is not str
            or not model.wire_model.strip()
            or type(request.prompt) is not str
            or (request.size is not None and type(request.size) is not str)
        ):
            raise ProviderGenerationFailure("图片 provider 配置不完整")

        base_url = model.base_url
        if base_url is None:
            default_base_url = "https://api.openai.com/v1"
            default_origin = _parse_origin(default_base_url, allow_path=True)
            if default_origin not in self._allowed_provider_origins:
                raise ProviderGenerationFailure("图片 provider 地址未获授权")
            return default_base_url
        if type(base_url) is not str or not base_url or base_url != base_url.strip():
            raise ProviderGenerationFailure("图片 provider 地址无效")
        try:
            origin = _parse_origin(base_url, allow_path=True)
        except ValueError as exc:
            raise ProviderGenerationFailure("图片 provider 地址无效") from exc
        if origin not in self._allowed_provider_origins:
            raise ProviderGenerationFailure("图片 provider 地址未获授权")
        return base_url.rstrip("/")

    async def _prepare_references(
        self, reference_images: tuple[str, ...]
    ) -> tuple[OpenAIImageEditReference, ...]:
        if (
            type(reference_images) is not tuple
            or not 1 <= len(reference_images) <= _MAX_REFERENCE_IMAGES
        ):
            raise ProviderGenerationFailure("参考图数量超出支持范围")

        prepared: list[OpenAIImageEditReference] = []
        for url in reference_images:
            if type(url) is not str or not url or url != url.strip():
                raise ProviderGenerationFailure("参考图地址无效")
            reference = ReferenceInput(kind="public_http", locator=url)
            try:
                result = await self._public_media_fetcher.fetch_public(
                    reference,
                    max_bytes=_MAX_REFERENCE_IMAGE_BYTES,
                    timeout_seconds=_REFERENCE_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise ProviderGenerationFailure("参考图无法安全读取") from exc
            if (
                isinstance(result, TransferFailure)
                or type(result) is not PreparedReference
                or result.kind != "bytes"
                or type(result.content) is not bytes
                or result.locator is not None
                or not isinstance(result.media_type, str)
                or result.media_type not in _REFERENCE_MEDIA_TYPES
                or not 1 <= len(result.content) <= _MAX_REFERENCE_IMAGE_BYTES
            ):
                raise ProviderGenerationFailure("参考图无法安全读取")
            prepared.append(
                OpenAIImageEditReference(
                    content=result.content,
                    media_type=result.media_type,
                )
            )
        return tuple(prepared)


def _parse_origin(value: str, *, allow_path: bool) -> tuple[str, str, int]:
    if type(value) is not str or not value:
        raise ValueError("an HTTPS URL is required")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("URL authority is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or "?" in value
        or "#" in value
        or parsed.query
        or parsed.fragment
        or (not allow_path and parsed.path not in {"", "/"})
    ):
        raise ValueError("URL must be an uncredentialed HTTPS origin")
    try:
        canonical_host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("URL hostname is invalid") from exc
    return parsed.scheme, canonical_host, port


def _is_safe_image_url(value: str) -> bool:
    if type(value) is not str:
        return False
    try:
        parse_public_http_url(value)
    except UnsafeRemoteURL:
        return False
    return True


async def _read_bounded_response(response: AsyncHttpResponse) -> bytes:
    headers = response.headers
    if not isinstance(headers, Mapping):
        raise ValueError("provider response headers are invalid")
    content_length = _header(headers, "content-length")
    if content_length is not None:
        if not content_length.isascii() or not content_length.isdecimal():
            raise ValueError("provider response length is invalid")
        if int(content_length) > _MAX_RESPONSE_BYTES:
            raise ValueError("provider response exceeds local limit")

    iterator = await response.iter_bytes(chunk_size=_RESPONSE_CHUNK_BYTES)
    body = bytearray()
    try:
        async for chunk in iterator:
            if (
                not isinstance(chunk, bytes)
                or len(body) + len(chunk) > _MAX_RESPONSE_BYTES
            ):
                raise ValueError("provider response exceeds local limit")
            body.extend(chunk)
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            close_result = close()
            if inspect.isawaitable(close_result):
                await close_result
    return bytes(body)


def _header(headers: Mapping[str, str], wanted: str) -> str | None:
    for name, value in headers.items():
        if isinstance(name, str) and name.lower() == wanted:
            if not isinstance(value, str):
                return ""
            return value
    return None
