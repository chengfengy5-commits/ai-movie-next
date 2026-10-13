"""Lazy aiohttp implementation of the explicitly injected request port."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .ports import ProviderQuotaHttpResponse


class AiohttpProviderQuotaTransport:
    """Open one short-lived aiohttp session per explicitly requested call."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        timeout_seconds: float,
    ) -> ProviderQuotaHttpResponse:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            request = getattr(session, method.lower())
            async with request(
                url,
                headers=dict(headers or {}),
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                text = await response.text()
                cookies = {
                    cookie.key: cookie.value
                    for cookie in response.cookies.values()
                    if cookie.value
                }
                set_cookie = tuple(response.headers.getall("Set-Cookie", []))
                return ProviderQuotaHttpResponse(
                    status=response.status,
                    text=text,
                    headers={"Set-Cookie": set_cookie},
                    cookies=cookies,
                )
