"""Explicit, lazy adapters for R2 writes and guarded public media fetches."""

from __future__ import annotations

import asyncio
import io
import re
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import BinaryIO
from urllib.parse import quote, urlsplit
from uuid import uuid4

from haoai_backend.generation_contracts import PreparedReference, ReferenceInput, TransferFailure
from haoai_backend.media_storage.pinned_http import PinnedPublicMediaFetcher
from haoai_backend.media_storage.transfer_types import (
    IMMUTABLE_CACHE_CONTROL,
    R2ClientSettings,
    R2Configuration,
    S3Client,
    S3ClientFactory,
    SecretHandle,
)

from .domain import storage_media_type
from .ports import FetchedPublicMedia


class R2MediaUploadStorage:
    """Upload through explicit shared R2 interfaces without ambient config reads."""

    def __init__(
        self,
        *,
        configuration: R2Configuration | None,
        access_key: SecretHandle | None,
        secret_key: SecretHandle | None,
        client_factory: S3ClientFactory | None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_factory: Callable[[], object] = uuid4,
    ) -> None:
        if not callable(clock) or not callable(id_factory):
            raise TypeError("explicit clock and id factories are required")
        self._configuration = configuration
        self._access_key = access_key
        self._secret_key = secret_key
        self._client_factory = client_factory
        self._clock = clock
        self._id_factory = id_factory
        self._pending_lock = threading.Lock()
        self._pending_cleanup: list[BinaryIO | S3Client] = []

    @property
    def pending_cleanup_count(self) -> int:
        with self._pending_lock:
            return len(self._pending_cleanup)

    def store_media(
        self,
        content: bytes,
        *,
        business_type: str,
        extension: str,
        content_type: str,
    ) -> str:
        configuration = self._configuration
        access_key = self._access_key
        secret_key = self._secret_key
        factory = self._client_factory
        canonical_type = storage_media_type(extension)
        if (
            configuration is None
            or access_key is None
            or secret_key is None
            or factory is None
            or canonical_type is None
            or canonical_type != content_type
            or not isinstance(content, bytes)
            or not business_type
        ):
            raise RuntimeError("media storage is not configured for this upload")

        extension = _canonical_extension(extension)
        key = _make_legacy_key(business_type, extension, self._clock(), self._id_factory())
        settings = R2ClientSettings(
            endpoint_url=configuration.endpoint_url,
            bucket=configuration.bucket,
            connect_timeout_seconds=configuration.connect_timeout_seconds,
            read_timeout_seconds=configuration.read_timeout_seconds,
        )
        client: S3Client | None = None
        body: BinaryIO | None = None
        receipt = None
        operation_error: BaseException | None = None
        cleanup_error: BaseException | None = None
        try:
            client = factory.create(settings, access_key.reveal(), secret_key.reveal())
            body = io.BytesIO(content)
            receipt = client.put_object(
                Bucket=configuration.bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                CacheControl=IMMUTABLE_CACHE_CONTROL,
            )
        except BaseException as error:
            operation_error = error
        finally:
            for resource in (body, client):
                if resource is None:
                    continue
                try:
                    resource.close()
                except BaseException as error:
                    self._retain_for_cleanup(resource)
                    if cleanup_error is None:
                        cleanup_error = error

        if isinstance(operation_error, asyncio.CancelledError):
            raise operation_error
        if isinstance(cleanup_error, asyncio.CancelledError):
            raise cleanup_error
        if cleanup_error is not None:
            raise RuntimeError("media storage resource cleanup failed") from None
        if operation_error is not None:
            # The remote acknowledgement may be unknown. Never replay this write.
            raise RuntimeError("media storage outcome is unverified") from None
        if receipt is None or not receipt.is_positive or receipt.key != key:
            raise RuntimeError("media storage outcome is unverified")
        return f"{configuration.public_base}/{quote(key, safe='/')}"

    def retry_pending_cleanup(self) -> tuple[bool, ...]:
        with self._pending_lock:
            pending = tuple(self._pending_cleanup)
        outcomes: list[bool] = []
        for resource in pending:
            try:
                resource.close()
            except BaseException:
                outcomes.append(False)
            else:
                outcomes.append(True)
                with self._pending_lock:
                    self._pending_cleanup = [
                        item for item in self._pending_cleanup if item is not resource
                    ]
        return tuple(outcomes)

    def _retain_for_cleanup(self, resource: BinaryIO | S3Client) -> None:
        with self._pending_lock:
            if all(item is not resource for item in self._pending_cleanup):
                self._pending_cleanup.append(resource)


class PinnedPublicMediaFetchAdapter:
    """Adapt the public SSRF/DNS-pinned fetcher to the upload use-case port."""

    def __init__(self, fetcher: PinnedPublicMediaFetcher) -> None:
        self._fetcher = fetcher

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> FetchedPublicMedia | TransferFailure:
        result = await self._fetcher.fetch_public(
            ReferenceInput(kind="public_http", locator=url),
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )
        if isinstance(result, TransferFailure):
            return result
        if (
            not isinstance(result, PreparedReference)
            or result.kind != "bytes"
            or not isinstance(result.content, bytes)
            or not isinstance(result.media_type, str)
        ):
            return TransferFailure("invalid_fetch_result", "公网媒体获取失败")
        return FetchedPublicMedia(result.content, result.media_type)


def managed_origin_map_from_configuration(
    configuration: R2Configuration | None,
) -> Mapping[object, object]:
    """Build fast-path origins only from explicitly supplied storage settings."""

    if configuration is None:
        return {}
    mapping: dict[object, object] = dict(configuration.managed_origin_map)
    public = urlsplit(configuration.public_base_url)
    if public.scheme and public.netloc:
        public_origin = (
            public.scheme,
            public.hostname or "",
            public.port or _default_port(public.scheme),
        )
        mapping.setdefault(public_origin, public_origin)
        for source in configuration.legacy_public_origins:
            mapping.setdefault(source, public_origin)
        for source, target in configuration.legacy_acceleration_map:
            mapping.setdefault(source, target)
    return mapping


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _canonical_extension(extension: str) -> str:
    aliases = {".jpeg": ".jpg", ".tiff": ".tif", ".mpeg": ".mpg"}
    return aliases.get(extension.lower(), extension.lower())


def _make_legacy_key(
    business_type: str,
    extension: str,
    moment: datetime,
    identifier: object,
) -> str:
    business = re.sub(r"[^a-z0-9_-]+", "-", business_type.strip().lower())
    business = business.strip("-_")[:32] or "media"
    safe_extension = _canonical_extension(extension).lstrip(".")
    safe_identifier = str(identifier)
    if not safe_extension or not re.fullmatch(r"[a-z0-9]+", safe_extension):
        raise ValueError("media extension is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", safe_identifier):
        raise ValueError("media identifier is invalid")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("media clock must return an aware datetime")
    date_path = moment.astimezone(timezone.utc).strftime("%Y/%m/%d")
    return f"{date_path}/{business}/{safe_identifier}.{safe_extension}"
