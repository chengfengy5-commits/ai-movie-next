"""Small ports owned by the media upload compatibility module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from haoai_backend.generation_contracts import TransferFailure


@dataclass(frozen=True, slots=True)
class FetchedPublicMedia:
    content: bytes
    content_type: str


class MediaUploadStorage(Protocol):
    def store_media(
        self,
        content: bytes,
        *,
        business_type: str,
        extension: str,
        content_type: str,
    ) -> str: ...


class PublicMediaFetcher(Protocol):
    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> FetchedPublicMedia | TransferFailure: ...
