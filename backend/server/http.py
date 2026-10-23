"""Production aiohttp adapter for the generation runtime HTTP port."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Mapping
from typing import Any


class AiohttpAsyncHttpClientFactory:
    """Create one isolated aiohttp session for each provider request context."""

    def __call__(self) -> _AiohttpClientContext:
        return _AiohttpClientContext()


class _AiohttpClientContext:
    def __init__(self) -> None:
        self._session: Any | None = None
        self._responses: set[_AiohttpResponse] = set()

    async def __aenter__(self) -> _AiohttpAsyncHttpClient:
        if self._session is not None:
            raise RuntimeError("HTTP client context cannot be entered twice")
        try:
            import aiohttp
        except ImportError:
            raise RuntimeError("aiohttp is required for provider HTTP") from None
        self._session = aiohttp.ClientSession(
            trust_env=False,
            raise_for_status=False,
        )
        return _AiohttpAsyncHttpClient(self)

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        failures = False
        for response in tuple(self._responses):
            try:
                response.close()
            except Exception:
                failures = True
        session, self._session = self._session, None
        if session is not None:
            try:
                await session.close()
            except Exception:
                failures = True
        if failures and exc_type is None:
            raise RuntimeError("provider HTTP resources could not be closed") from None

    def _track(self, response: _AiohttpResponse) -> None:
        self._responses.add(response)

    def _untrack(self, response: _AiohttpResponse) -> None:
        self._responses.discard(response)


class _AiohttpAsyncHttpClient:
    def __init__(self, context: _AiohttpClientContext) -> None:
        self._context = context

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        timeout_seconds: float,
        follow_redirects: bool = False,
    ) -> _AiohttpResponse:
        if follow_redirects:
            raise ValueError("provider redirects are disabled")
        if (
            not isinstance(method, str)
            or not method
            or not isinstance(url, str)
            or not url
            or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or (headers is not None and any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in headers.items()
            ))
            or (content is not None and not isinstance(content, bytes))
        ):
            raise ValueError("provider HTTP request is invalid")
        session = self._context._session
        if session is None:
            raise RuntimeError("provider HTTP client is closed")
        try:
            import aiohttp

            response = await session.request(
                method,
                url,
                headers=headers,
                data=content,
                timeout=aiohttp.ClientTimeout(total=float(timeout_seconds)),
                allow_redirects=False,
            )
        except Exception:
            raise RuntimeError("provider HTTP request failed") from None
        wrapped = _AiohttpResponse(response, self._context)
        self._context._track(wrapped)
        return wrapped


class _AiohttpResponse:
    def __init__(self, response: Any, context: _AiohttpClientContext) -> None:
        self._response = response
        self._context = context
        self._closed = False

    @property
    def status_code(self) -> int:
        return int(self._response.status)

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    async def iter_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        if type(chunk_size) is not int or chunk_size < 1:
            raise ValueError("response chunk size is invalid")
        try:
            async for chunk in self._response.content.iter_chunked(chunk_size):
                yield bytes(chunk)
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._context._untrack(self)
        self._response.release()


__all__ = ("AiohttpAsyncHttpClientFactory",)
