from __future__ import annotations

import asyncio
import concurrent.futures
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import BinaryIO, Callable, Literal
from urllib.parse import quote

from haoai_backend.generation_contracts import PreparedReference, StoredMedia, TransferFailure

from .transfer_types import (
    IMMUTABLE_CACHE_CONTROL,
    MAX_TRANSFER_BYTES,
    S3Client,
    OwnedDownload,
    R2ClientSettings,
    R2Configuration,
    S3ClientFactory,
    SafeMediaPolicy,
    SecretHandle,
    WriteReceipt,
)


class R2Storage:
    def __init__(
        self,
        configuration: R2Configuration | None,
        media_policy: SafeMediaPolicy | None,
        *,
        access_key: SecretHandle | None,
        secret_key: SecretHandle | None,
        client_factory: S3ClientFactory | None,
        executor: concurrent.futures.Executor | None,
    ) -> None:
        self._configuration = configuration
        self._media_policy = media_policy
        self._access_key = access_key
        self._secret_key = secret_key
        self._client_factory = client_factory
        self._executor = executor
        self._pending_resources: list[tuple[OwnedDownload | None, BinaryIO | S3Client]] = []
        self._pending_lock = threading.RLock()

    @property
    def pending_resource_cleanup_count(self) -> int:
        with self._pending_lock:
            return len(self._pending_resources)

    def has_pending_download_cleanup(self, download: OwnedDownload) -> bool:
        with self._pending_lock:
            return any(owner is download for owner, _resource in self._pending_resources)

    async def store_download(self, download: OwnedDownload) -> StoredMedia | TransferFailure:
        unavailable = self._readiness_failure()
        if unavailable is not None:
            return unavailable
        try:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(self._executor, self._write_download, download)
        except (RuntimeError, TypeError):
            return _failure("storage_unavailable", "R2 写入能力未配置")
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
            raise cancellation
        except BaseException:
            return _failure("storage_outcome_unverified", "R2 写入结果无法确认")

    def store_prepared(self, reference: PreparedReference) -> StoredMedia | TransferFailure:
        if (
            reference.kind != "bytes"
            or reference.locator is not None
            or not isinstance(reference.content, bytes)
        ):
            return _failure("unsupported_reference", "R2 仅接受已准备的媒体字节")
        if not 1 <= len(reference.content) <= MAX_TRANSFER_BYTES:
            return _failure("reference_too_large", "媒体内容为空或超过大小限制")
        if self._readiness_failure() is not None:
            return _failure("storage_unavailable", "R2 写入能力未配置")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return _failure("storage_unavailable", "R2 同步写入不得阻塞运行中的事件循环")
        try:
            if self._executor is None:
                return _failure("storage_unavailable", "R2 写入能力未配置")
            return self._executor.submit(
                self._write_bytes,
                reference.content,
                reference.media_type,
            ).result()
        except asyncio.CancelledError:
            raise
        except BaseException:
            return _failure("storage_outcome_unverified", "R2 写入结果无法确认")

    def release(
        self, stored: StoredMedia
    ) -> Literal["released", "already_released", "not_owned", "failed"]:
        return "failed"

    def _readiness_failure(self) -> TransferFailure | None:
        if (
            self._configuration is None
            or self._media_policy is None
            or self._access_key is None
            or self._secret_key is None
            or self._client_factory is None
            or self._executor is None
        ):
            return _failure("storage_unavailable", "R2 写入能力未配置")
        return None

    def _write_download(self, download: OwnedDownload) -> StoredMedia | TransferFailure:
        configuration = self._configuration
        policy = self._media_policy
        factory = self._client_factory
        access_key = self._access_key
        secret_key = self._secret_key
        if (
            configuration is None
            or policy is None
            or factory is None
            or access_key is None
            or secret_key is None
        ):
            return _failure("storage_unavailable", "R2 写入能力未配置")
        rule = policy.resolve(download.media_type)
        if rule is None or download.size_bytes <= 0:
            return _failure("unsupported_media_type", "媒体类型不受支持")
        key = _object_key(configuration.business_type, rule.extension)
        return self._put(
            configuration,
            factory,
            access_key,
            secret_key,
            key,
            rule.media_type,
            download.size_bytes,
            download.content_digest,
            lambda: download.open_reader(),
            pending_owner=download,
        )

    def _write_bytes(self, content: bytes, media_type: str | None) -> StoredMedia | TransferFailure:
        configuration = self._configuration
        policy = self._media_policy
        factory = self._client_factory
        access_key = self._access_key
        secret_key = self._secret_key
        if (
            configuration is None
            or policy is None
            or factory is None
            or access_key is None
            or secret_key is None
        ):
            return _failure("storage_unavailable", "R2 写入能力未配置")
        rule = policy.resolve(media_type)
        if rule is None:
            return _failure("unsupported_media_type", "媒体类型不受支持")
        key = _object_key(configuration.business_type, rule.extension)
        import hashlib
        import io

        digest = hashlib.sha256(content).hexdigest()
        return self._put(
            configuration,
            factory,
            access_key,
            secret_key,
            key,
            rule.media_type,
            len(content),
            digest,
            lambda: io.BytesIO(content),
        )

    async def retry_pending_resource_cleanup_async(
        self, download: OwnedDownload | None = None
    ) -> tuple[bool, ...]:
        if self._executor is None:
            raise RuntimeError("R2 cleanup executor is unavailable")
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._executor, self.retry_pending_resource_cleanup, download
        )
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
            raise cancellation

    def retry_pending_resource_cleanup(
        self, download: OwnedDownload | None = None
    ) -> tuple[bool, ...]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("R2 cleanup retry must run on its injected executor")
        with self._pending_lock:
            pending = tuple(
                item for item in self._pending_resources
                if download is None or item[0] is download
            )
        outcomes: list[bool] = []
        cancellation: asyncio.CancelledError | None = None
        for owner, resource in pending:
            try:
                resource.close()
            except BaseException as exc:
                outcomes.append(False)
                if isinstance(exc, asyncio.CancelledError) and cancellation is None:
                    cancellation = exc
            else:
                outcomes.append(True)
                with self._pending_lock:
                    self._pending_resources = [
                        item for item in self._pending_resources
                        if not (item[0] is owner and item[1] is resource)
                    ]
        if cancellation is not None:
            raise cancellation
        return tuple(outcomes)

    def retry_pending_download_cleanup(self, download: OwnedDownload) -> bool:
        outcomes = self.retry_pending_resource_cleanup(download)
        return all(outcomes) and not self.has_pending_download_cleanup(download)

    def _retain_pending(
        self, resource: BinaryIO | S3Client, owner: OwnedDownload | None = None
    ) -> None:
        with self._pending_lock:
            if all(item[1] is not resource for item in self._pending_resources):
                self._pending_resources.append((owner, resource))

    def _put(
        self,
        configuration: R2Configuration,
        factory: S3ClientFactory,
        access_key: SecretHandle,
        secret_key: SecretHandle,
        key: str,
        media_type: str,
        size_bytes: int,
        digest: str,
        body_factory: Callable[[], BinaryIO],
        *,
        pending_owner: OwnedDownload | None = None,
    ) -> StoredMedia | TransferFailure:
        settings = R2ClientSettings(
            endpoint_url=configuration.endpoint_url,
            bucket=configuration.bucket,
            connect_timeout_seconds=configuration.connect_timeout_seconds,
            read_timeout_seconds=configuration.read_timeout_seconds,
        )
        client: S3Client | None = None
        body: BinaryIO | None = None
        receipt: WriteReceipt | None = None
        operation_error: BaseException | None = None
        cleanup_failed = False
        cleanup_cancel: asyncio.CancelledError | None = None
        try:
            client = factory.create(settings, access_key.reveal(), secret_key.reveal())
            body = body_factory()
            receipt = client.put_object(
                Bucket=configuration.bucket,
                Key=key,
                Body=body,
                ContentType=media_type,
                CacheControl=IMMUTABLE_CACHE_CONTROL,
            )
        except BaseException as exc:
            operation_error = exc
        finally:
            if body is not None:
                try:
                    body.close()
                except BaseException as exc:
                    cleanup_failed = True
                    self._retain_pending(body, pending_owner)
                    if isinstance(exc, asyncio.CancelledError) and cleanup_cancel is None:
                        cleanup_cancel = exc
            if client is not None:
                try:
                    client.close()
                except BaseException as exc:
                    cleanup_failed = True
                    self._retain_pending(client, pending_owner)
                    if isinstance(exc, asyncio.CancelledError) and cleanup_cancel is None:
                        cleanup_cancel = exc
        if isinstance(operation_error, asyncio.CancelledError):
            raise operation_error
        if cleanup_cancel is not None:
            raise cleanup_cancel
        if cleanup_failed:
            return _failure("cleanup_failure", "R2 资源清理结果未确认")
        if (
            operation_error is not None
            or receipt is None
            or not receipt.is_positive
            or receipt.key != key
        ):
            return _failure("storage_outcome_unverified", "R2 写入结果无法确认")
        locator = f"{configuration.public_base}/{quote(key, safe='/')}"
        return StoredMedia(
            locator=locator,
            content_digest=digest,
            media_type=media_type,
            size_bytes=size_bytes,
        )

def _object_key(business_type: str, extension: str) -> str:
    business = re.sub(
        r"[^a-z0-9_-]+", "-", business_type.strip().lower()
    ).strip("-_")[:32] or "media"
    now = datetime.now(timezone.utc)
    return f"{now:%Y/%m/%d}/{business}/{uuid.uuid4()}{extension}"


def _failure(code: str, safe_detail: str) -> TransferFailure:
    return TransferFailure(code=code, safe_detail=safe_detail)
