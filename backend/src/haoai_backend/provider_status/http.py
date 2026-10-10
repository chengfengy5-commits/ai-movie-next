"""Lazy aiohttp adapter for explicitly owned single-request status clients."""

from __future__ import annotations

import socket
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any


class AiohttpProviderStatusHttpClientFactory:
    """Create one fresh aiohttp session for each provider status poll."""

    def __call__(self, use_ipv4_connector: bool) -> AbstractAsyncContextManager[Any]:
        return _AiohttpClientContext(use_ipv4_connector)


class _AiohttpClientContext:
    def __init__(self, use_ipv4_connector: bool) -> None:
        self.use_ipv4_connector = use_ipv4_connector
        self._session: Any | None = None
        self.response_contexts: list[_AiohttpResponseContext] = []

    async def __aenter__(self) -> _AiohttpProviderStatusHttpClient:
        import aiohttp

        connector = (
            aiohttp.TCPConnector(family=socket.AF_INET)
            if self.use_ipv4_connector
            else None
        )
        self._session = aiohttp.ClientSession(connector=connector)
        return _AiohttpProviderStatusHttpClient(self)

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if self._session is not None:
            await self._session.close()
        return False

    @property
    def closed(self) -> bool:
        return self._session is None or self._session.closed


class _AiohttpProviderStatusHttpClient:
    def __init__(self, owner: _AiohttpClientContext) -> None:
        self._owner = owner

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> AbstractAsyncContextManager[Any]:
        import aiohttp

        if self._owner._session is None:
            raise RuntimeError("HTTP client context has not been entered")
        request_context = self._owner._session.get(
            url,
            headers=dict(headers),
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        )
        response_context = _AiohttpResponseContext(request_context)
        self._owner.response_contexts.append(response_context)
        return response_context


class _AiohttpResponseContext:
    def __init__(self, request_context: Any) -> None:
        self._request_context = request_context
        self._response: Any | None = None
        self.exited = False

    async def __aenter__(self) -> Any:
        self._response = await self._request_context.__aenter__()
        return self._response

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            return await self._request_context.__aexit__(exc_type, exc, traceback)
        finally:
            self.exited = True

    @property
    def closed(self) -> bool:
        return self._response is None or self._response.closed


def create_aiohttp_provider_status_http_client_factory() -> AiohttpProviderStatusHttpClientFactory:
    """Return an inert factory; sessions are opened only when a query polls."""

    return AiohttpProviderStatusHttpClientFactory()
