"""Explicit interfaces for the runtime checks and authenticated image proxy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Request


@dataclass(frozen=True, slots=True)
class PublicImageFetchResult:
    content: bytes
    content_type: str


class UnsafeRemoteURL(ValueError):
    """The image URL failed the public-address and URL safety checks."""


class RemoteFetchError(RuntimeError):
    """The upstream image could not be fetched successfully."""


class RemoteContentTooLarge(RemoteFetchError):
    """The upstream image exceeded the configured byte limit."""


class RemoteContentTypeRejected(RemoteFetchError):
    """The upstream resource did not have an accepted image content type."""


class PublicImageFetcher(Protocol):
    """Fetch one bounded public image when explicitly called by the route."""

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: int,
        allowed_content_prefixes: Sequence[str],
    ) -> PublicImageFetchResult: ...


DatabaseReadinessProbe = Callable[[], Any]
ObjectStorageReadinessProbe = Callable[[], Awaitable[Any]]
CurrentUserResolver = Callable[
    [Request], Any | Awaitable[Any]
]
