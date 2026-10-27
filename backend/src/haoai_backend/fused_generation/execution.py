"""One-shot asynchronous driver for admitted fused-image tasks."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from secrets import token_urlsafe
from typing import TypeVar

from starlette.concurrency import run_in_threadpool

from haoai_backend.generation_contracts import StoredMedia

from .domain import (
    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
    FUSED_UNKNOWN_REASON,
    FusedGenerationInput,
    FusedGenerationInlineImage,
    FusedGenerationStorageUnknown,
    FusedGenerationUnknown,
    FusedGenerationUnknownReason,
    FusedTaskClaim,
    PreparedFusedGeneration,
)
from .errors import FusedGenerationDispatchedCancellation, ProviderGenerationFailure
from .ports import (
    FusedGenerationImageStorage,
    FusedGenerationRuntime,
    FusedGenerationStorage,
    FusedGenerationUnitOfWorkFactory,
)


@dataclass(frozen=True, slots=True)
class FusedGenerationExecution:
    runtime: FusedGenerationRuntime | None
    uow_factory: FusedGenerationUnitOfWorkFactory | None
    storage: FusedGenerationStorage | None = None
    inline_image_storage: FusedGenerationImageStorage | None = None

    async def execute(self, prepared: PreparedFusedGeneration) -> None:
        if self.runtime is None or self.uow_factory is None:
            return
        claim_token = token_urlsafe(32)
        claim_task = asyncio.create_task(
            run_in_threadpool(
                _claim,
                self.uow_factory,
                prepared.task_id,
                claim_token,
                prepared.user_id,
            )
        )
        claim, cancellation_requested = await _drain_owned_worker(claim_task)
        if cancellation_requested:
            if claim is not None:
                await _finish_after_cancellation(
                    self.uow_factory,
                    prepared,
                    claim_token,
                    "任务被取消",
                )
            raise asyncio.CancelledError
        if claim is None:
            return
        if claim.cancelled:
            await self._finish(prepared, claim_token, image_url=None, error_message="任务被取消")
            return

        try:
            output = await self.runtime.generate(
                FusedGenerationInput(
                    task_id=prepared.task_id,
                    series_id=prepared.series_id,
                    member_type=prepared.member_type,
                    members=prepared.members,
                    name=prepared.name,
                    size=prepared.size,
                    prompt=prepared.prompt,
                    reference_images=prepared.reference_images,
                    server_model=prepared.server_model,
                )
            )
            if not isinstance(output, (FusedGenerationUnknown, FusedGenerationInlineImage)):
                provider_url = output.image_url if output is not None else None
                if not isinstance(provider_url, str) or not provider_url.strip():
                    raise ProviderGenerationFailure("未获取到图片")
        except FusedGenerationDispatchedCancellation:
            await self._retain_unknown(
                prepared,
                claim_token,
                FUSED_UNKNOWN_REASON,
            )
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            await _finish_after_cancellation(
                self.uow_factory,
                prepared,
                claim_token,
                "任务被取消",
            )
            raise
        except ProviderGenerationFailure as failure:
            await self._finish(
                prepared,
                claim_token,
                image_url=None,
                error_message=_safe_error(failure.detail, prepared.server_model.api_key),
            )
            return
        except Exception:
            await self._finish(
                prepared,
                claim_token,
                image_url=None,
                error_message="图片生成失败，请稍后重试",
            )
            return

        if isinstance(output, FusedGenerationUnknown):
            await self._retain_unknown(
                prepared,
                claim_token,
                FUSED_UNKNOWN_REASON,
            )
            return

        if isinstance(output, FusedGenerationInlineImage):
            await self._store_inline_image(prepared, claim_token, output)
            return

        final_url = provider_url
        if self.storage is not None:
            try:
                authorized, cancelled = await _authorize_media_storage(
                    self.uow_factory,
                    prepared.task_id,
                    prepared.user_id,
                    claim_token,
                )
            except asyncio.CancelledError:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                raise
            if cancelled:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                raise asyncio.CancelledError
            if not authorized:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                return
            try:
                storage_result = await self.storage.store_generated_image(provider_url)
            except asyncio.CancelledError:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                raise
            except Exception:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                return

            if isinstance(storage_result, FusedGenerationStorageUnknown):
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                if storage_result.cancellation_requested:
                    raise asyncio.CancelledError
                return
            if storage_result is None:
                pass
            elif type(storage_result) is str and storage_result.strip():
                final_url = storage_result
            else:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                return

            try:
                authorized, cancelled = await _authorize_media_storage(
                    self.uow_factory,
                    prepared.task_id,
                    prepared.user_id,
                    claim_token,
                )
            except asyncio.CancelledError:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    final_url,
                )
                raise
            if cancelled:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    final_url,
                )
                raise asyncio.CancelledError
            if not authorized:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    final_url,
                )
                return

        try:
            finished = await self._finish(
                prepared,
                claim_token,
                image_url=final_url,
                error_message=None,
                preserve_if_cancelled=True,
            )
        except asyncio.CancelledError:
            await self._retain_known_media_result(
                prepared,
                claim_token,
                final_url,
            )
            raise
        if not finished:
            await self._retain_known_media_result(
                prepared,
                claim_token,
                final_url,
            )

    async def _store_inline_image(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        image: FusedGenerationInlineImage,
    ) -> None:
        retention_attempted = False

        async def retain_pending() -> None:
            nonlocal retention_attempted
            if retention_attempted:
                return
            retention_attempted = True
            await self._retain_unknown(
                prepared,
                claim_token,
                FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
            )

        try:
            if self.inline_image_storage is None:
                await retain_pending()
                return

            authorized, cancelled = await _authorize_media_storage(
                self.uow_factory,
                prepared.task_id,
                prepared.user_id,
                claim_token,
            )
            if cancelled:
                raise asyncio.CancelledError
            if not authorized:
                await retain_pending()
                return

            storing = asyncio.create_task(
                run_in_threadpool(_store_inline_image, self.inline_image_storage, image)
            )
            try:
                stored, cancelled = await _drain_owned_worker(storing)
            except asyncio.CancelledError:
                raise
            except Exception:
                await retain_pending()
                return
            if cancelled:
                raise asyncio.CancelledError
            if not _stored_media_matches(stored, image):
                await retain_pending()
                return

            authorized, cancelled = await _authorize_media_storage(
                self.uow_factory,
                prepared.task_id,
                prepared.user_id,
                claim_token,
            )
            if cancelled:
                raise asyncio.CancelledError
            if not authorized:
                await retain_pending()
                return
            finished = await self._finish(
                prepared,
                claim_token,
                image_url=stored.locator,
                error_message=None,
                preserve_if_cancelled=True,
            )
            if not finished:
                await retain_pending()
        except asyncio.CancelledError:
            await retain_pending()
            raise
        except Exception:
            await retain_pending()

    async def _finish(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        *,
        image_url: str | None,
        error_message: str | None,
        preserve_if_cancelled: bool = False,
    ) -> bool:
        if self.uow_factory is None:
            return False
        # Finalization is one transaction and is never automatically retried:
        # a lost commit acknowledgement must not duplicate an asset or refund.
        finishing = asyncio.create_task(
            run_in_threadpool(
                _finish,
                self.uow_factory,
                prepared,
                claim_token,
                image_url,
                _safe_error(error_message, prepared.server_model.api_key) if error_message else None,
                preserve_if_cancelled,
            )
        )
        try:
            finished, cancellation_requested = await _drain_owned_worker(finishing)
        except Exception:
            return False
        if cancellation_requested:
            if preserve_if_cancelled and finished is True:
                # A committed inline result is terminal; do not overwrite it with retention.
                return True
            # An ambiguous finalization is never retried.
            raise asyncio.CancelledError
        return finished is True

    async def _retain_unknown(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        safe_reason: FusedGenerationUnknownReason,
    ) -> None:
        if self.uow_factory is None:
            return
        retaining = asyncio.create_task(
            run_in_threadpool(
                _retain_unknown,
                self.uow_factory,
                prepared.task_id,
                prepared.user_id,
                claim_token,
                safe_reason,
            )
        )
        try:
            _, cancellation_requested = await _drain_owned_worker(retaining)
        except Exception:
            # A failed or ambiguous retention commit must never fall through to
            # terminal finalization or be retried automatically.
            return
        if cancellation_requested:
            raise asyncio.CancelledError

    async def _retain_known_media_result(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        result_url: str,
    ) -> None:
        if self.uow_factory is None:
            return
        retaining = asyncio.create_task(
            run_in_threadpool(
                _retain_known_media_result,
                self.uow_factory,
                prepared.task_id,
                prepared.user_id,
                claim_token,
                result_url,
            )
        )
        try:
            _, cancellation_requested = await _drain_owned_worker(retaining)
        except Exception:
            # A failed or ambiguous retention commit must not fall through to
            # terminal finalization or trigger another transfer attempt.
            return
        if cancellation_requested:
            raise asyncio.CancelledError


def _claim(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    task_id: str,
    claim_token: str,
    expected_user_id: str,
) -> FusedTaskClaim | None:
    uow = uow_factory()
    try:
        claim = uow.claim_task(task_id, claim_token, expected_user_id=expected_user_id)
        if claim is None:
            uow.rollback()
            return None
        uow.commit()
        return claim
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


async def _authorize_media_storage(
    uow_factory: FusedGenerationUnitOfWorkFactory | None,
    task_id: str,
    user_id: str,
    claim_token: str,
) -> tuple[bool, bool]:
    if uow_factory is None:
        return False, False
    checking = asyncio.create_task(
        run_in_threadpool(
            _authorize_media_storage_sync,
            uow_factory,
            task_id,
            user_id,
            claim_token,
        )
    )
    try:
        authorized, cancellation_requested = await _drain_owned_worker(checking)
    except Exception:
        return False, False
    return authorized is True, cancellation_requested


def _authorize_media_storage_sync(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    task_id: str,
    user_id: str,
    claim_token: str,
) -> bool:
    uow = uow_factory()
    try:
        return uow.authorize_media_storage(task_id, user_id, claim_token)
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


def _store_inline_image(
    storage: FusedGenerationImageStorage,
    image: FusedGenerationInlineImage,
) -> StoredMedia:
    return storage.store_inline_image(image)


def _stored_media_matches(
    stored: StoredMedia | None,
    image: FusedGenerationInlineImage,
) -> bool:
    if (
        type(stored) is not StoredMedia
        or type(stored.locator) is not str
        or not stored.locator.strip()
        or type(stored.content_digest) is not str
        or type(stored.size_bytes) is not int
        or type(stored.media_type) is not str
        or type(image.content) is not bytes
    ):
        return False
    return (
        stored.content_digest == hashlib.sha256(image.content).hexdigest()
        and stored.size_bytes == len(image.content)
        and stored.media_type == image.media_type
    )


def _finish(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    image_url: str | None,
    error_message: str | None,
    preserve_if_cancelled: bool = False,
) -> bool:
    uow = uow_factory()
    try:
        finished = uow.finish_task(
            prepared,
            claim_token,
            image_url=image_url,
            error_message=error_message,
            preserve_if_cancelled=preserve_if_cancelled,
        )
        uow.commit()
        return finished is True
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def _retain_unknown(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    task_id: str,
    user_id: str,
    claim_token: str,
    safe_reason: FusedGenerationUnknownReason,
) -> None:
    uow = uow_factory()
    try:
        uow.retain_unknown(task_id, user_id, claim_token, safe_reason)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def _retain_known_media_result(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    task_id: str,
    user_id: str,
    claim_token: str,
    result_url: str,
) -> None:
    uow = uow_factory()
    try:
        uow.retain_known_media_result(
            task_id,
            user_id,
            claim_token,
            result_url,
        )
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


_WorkerResult = TypeVar("_WorkerResult")


async def _drain_owned_worker(
    worker: asyncio.Task[_WorkerResult],
) -> tuple[_WorkerResult | None, bool]:
    """Keep an owned SQL worker alive until its thread has closed its Session."""
    cancellation_requested = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            if worker.cancelled():
                if cancellation_requested:
                    return None, True
                raise
            cancellation_requested = True
        except Exception:
            if cancellation_requested:
                return None, True
            raise

    try:
        return worker.result(), cancellation_requested
    except asyncio.CancelledError:
        if cancellation_requested:
            return None, True
        raise
    except Exception:
        if cancellation_requested:
            return None, True
        raise


async def _finish_after_cancellation(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    reason: str,
) -> None:
    finishing = asyncio.create_task(
        run_in_threadpool(
            _finish,
            uow_factory,
            prepared,
            claim_token,
            None,
            _safe_error(reason, prepared.server_model.api_key),
        )
    )
    try:
        await _drain_owned_worker(finishing)
    except Exception:
        # Keep the original cancellation and never retry a possibly committed finish.
        pass


def _safe_error(detail: str, secret: str) -> str:
    safe_detail = detail.strip()
    if secret:
        safe_detail = safe_detail.replace(secret, "[已隐藏]")
    return safe_detail[:2000] or "图片生成失败"
