"""Lazy aiohttp adapter for the legacy Zhangyuge billing endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class AiohttpBalanceTransport:
    """Open one owned HTTP session for each explicitly requested JSON call."""

    async def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 8,
    ) -> Any:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=dict(headers) if headers is not None else None,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                if response.status >= 400:
                    body = (await response.text())[:300]
                    raise Exception(f"HTTP {response.status}: {body}")
                return await response.json()
