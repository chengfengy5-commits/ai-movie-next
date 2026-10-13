"""Media upload use cases without FastAPI or SDK dependencies."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from typing import Any, TypeVar
from urllib.parse import urlsplit

from haoai_backend.generation_contracts import TransferFailure
from haoai_backend.media_storage.managed_urls import normalize_managed_url
from haoai_backend.media_storage.transfer_types import MAX_TRANSFER_SECONDS

from .domain import (
    MAX_RETRY_BYTES,
    MediaKind,
    retry_media_spec,
    upload_spec,
    validate_upload_content,
)
from .errors import (
    InvalidMediaUpload,
    MediaUploadStorageFailed,
    RetryDownloadFailed,
    RetryTooLarge,
    RetryTransferFailed,
    RetryUnsafeURL,
)
from .ports import FetchedPublicMedia, MediaUploadStorage, PublicMediaFetcher


_ResultT = TypeVar("_ResultT")


class MediaUploadApplication:
    def __init__(
        self,
        *,
        storage: MediaUploadStorage | None,
        public_media_fetcher: PublicMediaFetcher | None,
        managed_origin_map: Mapping[object, object] | None = None,
    ) -> None:
        self._storage = storage
        self._public_media_fetcher = public_media_fetcher
        self._managed_origin_map = dict(managed_origin_map or {})

    async def upload(
        self,
        kind: MediaKind,
        filename: str | None,
        content: bytes,
    ) -> dict[str, str]:
        spec = upload_spec(kind, filename)
        validate_upload_content(spec, content)
        return await self._store(
            content,
            business_type=spec.business_type,
            extension=spec.storage_extension,
            content_type=spec.content_type,
            failure=MediaUploadStorageFailed("云端上传失败"),
        )

    async def retry_oss_upload(self, source_url: str) -> dict[str, str]:
        source_url = source_url.strip()
        if not source_url:
            raise InvalidMediaUpload("URL 不能为空")

        if normalize_managed_url(source_url, self._managed_origin_map) is not None:
            return {"url": source_url}

        try:
            path = urlsplit(source_url).path
        except (TypeError, ValueError):
            path = ""
        extension = os.path.splitext(PurePosixPath(path).name)[1].lower() or ".mp4"
        spec = retry_media_spec(extension)
        if spec is None:
            raise InvalidMediaUpload("仅支持图片、音频和视频格式")

        fetcher = self._public_media_fetcher
        if fetcher is None:
            raise RetryDownloadFailed()
        try:
            fetched = await fetcher.fetch(
                source_url,
                max_bytes=MAX_RETRY_BYTES,
                timeout_seconds=MAX_TRANSFER_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RetryDownloadFailed() from None

        if isinstance(fetched, TransferFailure):
            self._raise_fetch_failure(fetched)
        if not isinstance(fetched, FetchedPublicMedia):
            raise RetryDownloadFailed()
        if not isinstance(fetched.content, bytes):
            raise RetryDownloadFailed()
        if len(fetched.content) > MAX_RETRY_BYTES:
            raise RetryTooLarge()
        if not fetched.content:
            raise RetryDownloadFailed()
        if not isinstance(fetched.content_type, str) or not _matches_content_type(
            fetched.content_type, spec.allowed_content_prefix
        ):
            raise RetryDownloadFailed()

        return await self._store(
            fetched.content,
            business_type="gen",
            extension=spec.storage_extension,
            content_type=spec.content_type,
            failure=RetryTransferFailed(),
        )

    async def _store(
        self,
        content: bytes,
        *,
        business_type: str,
        extension: str,
        content_type: str,
        failure: MediaUploadStorageFailed | RetryTransferFailed,
    ) -> dict[str, str]:
        storage = self._storage
        if storage is None:
            raise failure
        try:
            url = await _run_owned_off_loop(
                storage.store_media,
                content,
                business_type=business_type,
                extension=extension,
                content_type=content_type,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise failure from None
        if not isinstance(url, str) or not url:
            raise failure
        return {"url": url}

    @staticmethod
    def _raise_fetch_failure(failure: TransferFailure) -> None:
        if failure.code == "unsafe_url":
            raise RetryUnsafeURL(failure.safe_detail)
        if failure.code in {"reference_too_large", "content_too_large"}:
            raise RetryTooLarge()
        raise RetryDownloadFailed()


def _matches_content_type(content_type: str, allowed_prefix: str) -> bool:
    normalized = content_type.split(";", 1)[0].strip().lower()
    return normalized == "application/octet-stream" or normalized.startswith(allowed_prefix)


async def _run_owned_off_loop(
    callable_: Callable[..., _ResultT],
    *args: Any,
    **kwargs: Any,
) -> _ResultT:
    """Wait for an owned worker call to close its SDK resources on cancellation."""

    future = asyncio.create_task(asyncio.to_thread(callable_, *args, **kwargs))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError as cancellation:
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if future.done():
            try:
                future.exception()
            except BaseException:
                pass
        raise cancellation
