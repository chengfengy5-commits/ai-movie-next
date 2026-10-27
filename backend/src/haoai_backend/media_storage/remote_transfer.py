from __future__ import annotations

import asyncio

from haoai_backend.generation_contracts import ReferenceInput, StoredMedia, TransferFailure

from .owned_write_values import OwnedObjectMaterial
from .pinned_http import PinnedPublicMediaFetcher
from .r2_storage import R2Storage
from .remote_staging import RemoteStaging
from .transfer_types import MAX_TRANSFER_SECONDS, MAX_TRANSFER_BYTES, OwnedDownload


class RemoteMediaTransfer:
    def __init__(
        self,
        fetcher: PinnedPublicMediaFetcher,
        staging: RemoteStaging,
        storage: R2Storage,
        *,
        shared_slots: asyncio.Semaphore | None,
    ) -> None:
        self._fetcher = fetcher
        self._staging = staging
        self._storage = storage
        self._shared_slots = shared_slots
        self._pending_lease_count = 0

    async def transfer(
        self,
        reference: ReferenceInput,
        *,
        max_bytes: int,
        timeout_seconds: float = MAX_TRANSFER_SECONDS,
    ) -> StoredMedia | TransferFailure:
        if self._shared_slots is None:
            return _failure("not_ready", "公网媒体共享并发能力未配置")
        if (
            reference.kind not in {"public_http", "managed_url"}
            or not isinstance(reference.locator, str)
            or not reference.locator
            or reference.content is not None
            or reference.provider_operation_ref is not None
            or reference.media_type is not None
        ):
            return _failure("unsupported_reference", "仅支持完整公网媒体地址")
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_TRANSFER_BYTES:
            return _failure("invalid_limits", "公网媒体限制无效")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= MAX_TRANSFER_SECONDS
        ):
            return _failure("invalid_limits", "公网媒体限制无效")

        download = None
        result: StoredMedia | TransferFailure | None = None
        timed_out = False
        cancellation: asyncio.CancelledError | None = None
        cleanup_error: BaseException | None = None
        lease_acquired = False
        keep_lease = False
        try:
            async with asyncio.timeout(timeout_seconds):
                await self._shared_slots.acquire()
                lease_acquired = True
                download = await self._fetcher.fetch_to_owned(
                    reference.locator,
                    max_bytes=max_bytes,
                    timeout_seconds=timeout_seconds,
                )
                if isinstance(download, TransferFailure):
                    result = download
                else:
                    result = await self._storage.store_download(download)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except TimeoutError:
            timed_out = True
        except Exception:
            result = _failure("transfer_failure", "公网媒体传输失败")

        try:
            if download is not None and not isinstance(download, TransferFailure):
                try:
                    cleanup = self._staging.release(download)
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        if cancellation is None:
                            cancellation = exc
                    else:
                        cleanup_error = exc
                else:
                    if not cleanup.released:
                        result = _failure(
                            "cleanup_failure", "公网媒体暂存文件清理失败"
                        )

            if lease_acquired and (
                self._staging.pending_cleanup_count > 0
                or _storage_pending_count(self._storage) > 0
            ):
                # A retained semaphore slot conservatively bounds all unresolved
                # spool, reader, and SDK owners, even when another retry changes
                # the global pending count during this request.
                self._pending_lease_count += 1
                keep_lease = True

            if cancellation is not None:
                raise cancellation
            if cleanup_error is not None:
                raise cleanup_error
            if timed_out:
                return _failure("timeout", "公网媒体传输超时")
            if result is None:
                return _failure("transfer_failure", "公网媒体传输失败")
            return result
        finally:
            if lease_acquired and not keep_lease:
                self._shared_slots.release()

    async def prepare_owned(
        self,
        reference: ReferenceInput,
        *,
        owner_ref: str,
        submission_ref: str,
        slot: str,
        provider_result_digest: str,
        max_bytes: int,
        timeout_seconds: float = MAX_TRANSFER_SECONDS,
    ) -> OwnedObjectMaterial | TransferFailure:
        if self._shared_slots is None:
            return _failure("not_ready", "公网媒体共享并发能力未配置")
        if (
            reference.kind not in {"public_http", "managed_url"}
            or not isinstance(reference.locator, str)
            or not reference.locator
            or reference.content is not None
            or reference.provider_operation_ref is not None
            or reference.media_type is not None
        ):
            return _failure("unsupported_reference", "仅支持完整公网媒体地址")
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_TRANSFER_BYTES:
            return _failure("invalid_limits", "公网媒体限制无效")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= MAX_TRANSFER_SECONDS
        ):
            return _failure("invalid_limits", "公网媒体限制无效")

        download = None
        result: OwnedObjectMaterial | TransferFailure | None = None
        timed_out = False
        cancellation: asyncio.CancelledError | None = None
        cleanup_error: BaseException | None = None
        lease_acquired = False
        keep_lease = False
        try:
            async with asyncio.timeout(timeout_seconds):
                await self._shared_slots.acquire()
                lease_acquired = True
                download = await self._fetcher.fetch_to_owned(
                    reference.locator,
                    max_bytes=max_bytes,
                    timeout_seconds=timeout_seconds,
                )
                if isinstance(download, TransferFailure):
                    result = download
                else:
                    result = await self._storage.prepare_download(
                        download,
                        owner_ref=owner_ref,
                        submission_ref=submission_ref,
                        slot=slot,
                        provider_result_digest=provider_result_digest,
                        max_bytes=max_bytes,
                    )
        except asyncio.CancelledError as exc:
            cancellation = exc
        except TimeoutError:
            timed_out = True
        except Exception:
            result = _failure("transfer_failure", "公网媒体准备失败")

        try:
            if download is not None and not isinstance(download, TransferFailure):
                try:
                    cleanup = self._staging.release(download)
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        if cancellation is None:
                            cancellation = exc
                    else:
                        cleanup_error = exc
                else:
                    if not cleanup.released:
                        result = _failure(
                            "cleanup_failure", "公网媒体暂存文件清理失败"
                        )

            if lease_acquired and (
                self._staging.pending_cleanup_count > 0
                or _storage_pending_count(self._storage) > 0
            ):
                self._pending_lease_count += 1
                keep_lease = True

            if cancellation is not None:
                raise cancellation
            if cleanup_error is not None:
                raise cleanup_error
            if timed_out:
                return _failure("timeout", "公网媒体准备超时")
            if result is None:
                return _failure("transfer_failure", "公网媒体准备失败")
            return result
        finally:
            if lease_acquired and not keep_lease:
                self._shared_slots.release()

    async def retry_pending_cleanup(self) -> int:
        first_error: BaseException | None = None
        try:
            self._staging.retry_pending_cleanup()
        except BaseException as exc:
            first_error = _prefer_cleanup_error(first_error, exc)
        try:
            retry_storage = getattr(
                self._storage, "retry_pending_resource_cleanup_async", None
            )
            if callable(retry_storage):
                await retry_storage()
        except BaseException as exc:
            first_error = _prefer_cleanup_error(first_error, exc)

        released = 0
        if (
            self._staging.pending_cleanup_count == 0
            and _storage_pending_count(self._storage) == 0
        ):
            released = self._pending_lease_count
            self._pending_lease_count = 0
            for _ in range(released):
                self._shared_slots.release()
        if first_error is not None:
            raise first_error
        return released


def _prefer_cleanup_error(
    current: BaseException | None, candidate: BaseException
) -> BaseException:
    if current is None:
        return candidate
    if not isinstance(candidate, asyncio.CancelledError):
        return current
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        return candidate
    if not isinstance(current, asyncio.CancelledError):
        return candidate
    return current


def _failure(code: str, safe_detail: str) -> TransferFailure:
    return TransferFailure(code=code, safe_detail=safe_detail)


def _storage_pending_count(storage: object) -> int:
    value = getattr(storage, "pending_resource_cleanup_count", 0)
    return value if type(value) is int and value >= 0 else 0
