"""Explicit asynchronous URL transformation port."""

from __future__ import annotations

from typing import Protocol


class DownloadURLResolver(Protocol):
    async def resolve(self, url: str, filename: str) -> str:
        """Return the URL to place in the corresponding response item."""
        ...

